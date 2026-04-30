# ==========================================================
# ULTRA-PRO OPTION BUYING – LIVE TRADE VERSION
# (Same Framework – LIVE ORDER EXECUTION ADDED)
# ==========================================================

# ================= CONFIG =================
API_KEY ="ikzievanmkaoalmz"
ACCESS_TOKEN ="kfzLpcJyZWnlTfUdwIDB8n4QtPyEFSrJ"
# ==========================================================



from kiteconnect import KiteConnect, KiteTicker
from datetime import datetime, date, time as dtime, timedelta
import time, threading, sys

try:
    import winsound
except:
    winsound = None


from colorama import init
import logging

logging.getLogger("websocket").setLevel(logging.CRITICAL)
logging.getLogger("kiteconnect").setLevel(logging.CRITICAL)

init(autoreset=True)

GREEN="\033[92m"; RED="\033[91m"; YELLOW="\033[93m"
BLUE="\033[94m"; RESET="\033[0m"

#lock
import atexit
import os

# this is for cloud
LOCK_FILE = "/tmp/trading.lock"

# this is for local PC
#LOCK_FILE = "trading.lock"

def remove_lock():
    if os.path.exists(LOCK_FILE):
        os.remove(LOCK_FILE)

atexit.register(remove_lock)

if os.path.exists(LOCK_FILE):
    print("Script already running — exiting")
    exit(0)

open(LOCK_FILE, "w").close()


#Telegram

import requests

BOT_TOKEN = "8565948222:AAHym1kW4PCTMVAcPvZNLpKjzpsbdDWryjg"
CHAT_ID = 1412356698

def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": CHAT_ID,
            "text": f"<pre>{msg}</pre>",
            "parse_mode": "HTML"
        }
        requests.post(url, data=payload, timeout=5)
    except Exception as e:
        print("Telegram Error:", e)

# ================= CONFIG =================
MODE="LIVE"
PRODUCT="MIS"
EXCHANGE="NFO"
ORDER_TYPE="LIMIT"
POLL_INTERVAL=0.4

SPOT_TOKEN=256265
LOT_SIZE=130

PREM_SL_PTS=20
PREM_TGT_PTS=40	
MAX_ENTRY_RETRY=2
COOLDOWN=60

LAST_ENTRY_TIME=dtime(15,15)
FORCE_EXIT_TIME=dtime(15,20)

CPR_WIDE_THRESHOLD=0.6

# ================= GLOBALS =================
spot_ltp=None
option_ltp=None
trade_open=False
ACTIVE_OPTION_TOKEN=None
ACTIVE_SYMBOL=None
FIXED_SYMBOL=None
FIXED_TOKEN=None
ORDER_PLACED=False
BLOCK_MSG_SHOWN=False
LAST_BLOCK_REASON=None
day_closed = False
SCRIPT_RUNNING = True
WS_STOPPED = False
LAST_TICK_TIME = time.time()
printed_930 = False
printed_entry = False
printed_exit = False
summary_sent = False
LAST_TRADE_TIME = None
PRINTED_ONCE = False
API_FAILURE_COUNT = 0
LAST_VALID_SPOT = None


AUTO_SIGNAL="NO TRADE"
allowed_side=None
MA_SIDE=None
CPR_TYPE=None
AUTO_READY = False

trade={}

trade_taken = False
breakout_done = False
entry_price = None
exit_price = None
quantity = 0
pnl = 0
day_pnl = 0
DAILY_LOSS_LIMIT = -2500
MAX_SLIPPAGE = 5
MANUAL_HANDLED = False

candle = {"high": None, "low": None}
candle_done=False

# ================= KITE =================
kite=KiteConnect(api_key=API_KEY)
kite.set_access_token(ACCESS_TOKEN)

def safe_kite_call(api_callable, *args, **kwargs):
    global API_FAILURE_COUNT
    for attempt in range(1, 4):
        try:
            result = api_callable(*args, **kwargs)
            result_text = str(result).lower() if isinstance(result, str) else ""
            if (
                "502" in result_text
                or "bad gateway" in result_text
                or "<html" in result_text
            ):
                raise Exception(f"Gateway/HTML response detected: {result_text[:100]}")
            API_FAILURE_COUNT = 0
            return result
        except Exception as e:
            err_text = str(e).lower()
            if "502" in err_text or "bad gateway" in err_text or "<html" in err_text:
                print(f"API retry {attempt}/3 due to gateway failure: {e}")
            else:
                print(f"API retry {attempt}/3 due to API error: {e}")
            if attempt < 3:
                time.sleep(1 + (attempt % 2))

    API_FAILURE_COUNT += 1
    if API_FAILURE_COUNT >= 5:
        print("API UNSTABLE — PAUSING")
        time.sleep(30)
    return None

print("Token test:",kite.profile()["user_name"])
print("Downloading instruments...")
INSTRUMENTS=kite.instruments("NFO")
print("NFO instruments loaded")

# ================= HEADER =================
def print_header():
    print(f"{GREEN}MODE: OPTION BUYING SCRIPT - LIVE TRADE{RESET}")
    print(f"{BLUE}Execution Date: {date.today()} | {datetime.now().strftime('%H:%M:%S')}{RESET}")
    send_telegram("🚀 Script Started")

# ================= SOUND =================
def sound_entry():
    if winsound:
        winsound.Beep(1200,300)

def sound_sl():
    if winsound:
        winsound.Beep(600,700)

def sound_target():
    if winsound:
        winsound.Beep(1500,250)

# ================= CPR + AUTO SIGNAL =================
# ================= CPR + AUTO SIGNAL (OPTIMIZED SINGLE FETCH) =================
def calculate_auto_signal():

    global AUTO_SIGNAL, allowed_side, MA_SIDE, CPR_TYPE, AUTO_READY

    today = date.today()

    # ⭐ SINGLE DAILY DATA FETCH (used for CPR + MA20)
    hist = safe_kite_call(
        kite.historical_data,
        SPOT_TOKEN,
        today - timedelta(days=50),   # buffer for holidays
        today,
        "day"
    )
    if hist is None:
        print("AUTO SIGNAL skipped: historical_data unavailable")
        return

    if not hist or len(hist) < 22:
        print("Not enough daily candles — skipping AUTO SIGNAL")
        return

    # ==========================================================
    # ⭐ LAST COMPLETED DAY (avoid today running candle)
    # ==========================================================
    d = hist[-2]

    PDH = d["high"]
    PDL = d["low"]
    PDC = d["close"]

    # ================= CPR =================
    pivot = (PDH + PDL + PDC) / 3
    BC = (PDH + PDL) / 2
    TC = (pivot - BC) + pivot

    cpr_width = abs(TC - BC) / pivot * 100

    if cpr_width <= 0.2:
        CPR_TYPE = "NARROW"
    elif cpr_width <= 0.4:
        CPR_TYPE = "NORMAL"
    else:
        CPR_TYPE = "WIDE"

    # ================= MA20 =================
    closes = [i["close"] for i in hist[:-1]]   # exclude today
    ma20 = sum(closes[-20:]) / 20

    MA_SIDE = "Above" if PDC > ma20 else "Below"

    # ================= AUTO SIGNAL =================
    AUTO_SIGNAL = "NO TRADE"
    allowed_side = None

    if cpr_width < 0.2:
        if PDC > ma20 and PDC > TC:
            AUTO_SIGNAL = "CE BUY DAY"
            allowed_side = "CE"
        elif PDC < ma20 and PDC < BC:
            AUTO_SIGNAL = "PE BUY DAY"
            allowed_side = "PE"
        else:
            AUTO_SIGNAL = "NO TRADE"
    else:
        AUTO_SIGNAL = "NO TRADE"

    # Reverse logic for NO TRADE DAY
    if AUTO_SIGNAL == "NO TRADE":
        if MA_SIDE == "Above":
            allowed_side = "PE"
        else:
            allowed_side = "CE"

    msg = (f"[AUTO SIGNAL] CPR={CPR_TYPE} | 20MA={MA_SIDE} | SIGNAL={AUTO_SIGNAL} | Allowed={allowed_side}")
    print(msg)
    send_telegram(msg)

    # ⭐ Unlock ENTRY engine
    AUTO_READY = True
# ================= EXPIRY =================

def get_next_expiry():
    today = date.today()

    expiries = sorted(set(
        i["expiry"] for i in INSTRUMENTS
        if i["name"] == "NIFTY" and i["expiry"] >= today
    ))

    # ⭐ Always choose NEXT WEEK expiry
    if len(expiries) > 1:
        return expiries[1]     # second expiry = next week
    else:
        return expiries[0]     # fallback safety


def get_atm_option(spot,side):
    if spot is None:
        return None

    expiry = get_next_expiry()

    filtered = [
        i for i in INSTRUMENTS
        if i["name"] == "NIFTY"
        and i["expiry"] == expiry
        and i["instrument_type"] == side
    ]

    if not filtered:
        return None

    selected = min(filtered, key=lambda x: abs(x["strike"] - spot))
    symbol = selected["tradingsymbol"]
    return symbol, selected["instrument_token"]

# ================= FETCH =================
def fetch_spot():
    global spot_ltp
    try:
        data = safe_kite_call(kite.ltp, ["NSE:NIFTY 50"])
        if data is None:
            return
        if "NSE:NIFTY 50" in data:
            spot_ltp = data["NSE:NIFTY 50"]["last_price"]
        else:
            return
    except Exception as e:
        print("Spot fetch error:", e)
        return

# ================= 9:30 CANDLE =================
def fetch_930_candle():
    global candle_done, printed_930, PRINTED_ONCE, FIXED_SYMBOL, FIXED_TOKEN

    if candle_done:
        return

    now = datetime.now().time()
    today = date.today()

    # ⭐ WAIT UNTIL 9:35
    if now < dtime(9,35):
        return

    data = safe_kite_call(
        kite.historical_data,
        SPOT_TOKEN,
        datetime.combine(today, dtime(9,30)),
        datetime.combine(today, dtime(9,35)),
        "5minute"
    )
    if data is None:
        print("9:30 candle fetch unavailable — skipping this cycle")
        return

    # ⭐ AFTER 9:35 — if still no data → holiday
    if not data:
        print("No 9:30 candle data – possible holiday")
        sys.exit(0)

    candle["high"] = data[0]["high"]
    candle["low"] = data[0]["low"]
    candle_close = data[0]["close"]

    if candle["high"] is None or candle["low"] is None:
        return

    if candle["high"] <= 0 or candle["low"] <= 0:
        print("Invalid candle values — skipping trade")
        return

    candle_done = True

    if not printed_930:
        high_buffer = candle["high"] + 1
        low_buffer = candle["low"] - 1
        levels_msg = (
            "📊 9:30 LEVELS\n"
            f"High: {candle['high']}\n"
            f"Low: {candle['low']}\n"
            f"High Buffer: {high_buffer}\n"
            f"Low Buffer: {low_buffer}"
        )
        print(levels_msg)
        send_telegram(levels_msg)
        printed_930 = True

    calculate_auto_signal()

    if not PRINTED_ONCE:
        reference_price = candle_close
        atm_option = get_atm_option(reference_price, allowed_side)
        if atm_option:
            symbol, token = atm_option
            selected = next((i for i in INSTRUMENTS if i["tradingsymbol"] == symbol), None)
            if selected:
                FIXED_SYMBOL = symbol
                FIXED_TOKEN = token
                print(f"Reference Price (9:30 close): {reference_price}")
                print(f"Selected Strike: {selected['strike']}")
                print(f"Selected Symbol: {symbol}")
                PRINTED_ONCE = True
    

# ================= EXECUTION ENGINE =================
ORDER_BOOK_CACHE = {}
ORDER_CACHE_AT = 0.0
EXECUTION_LOCK = threading.Lock()
ENTRY_IN_PROGRESS = False
trade["entry_order_id"] = None
trade["sl_order_id"] = None
trade["target_order_id"] = None
trade["exit_reason"] = None

OPEN_ORDER_STATUSES = {"OPEN", "TRIGGER PENDING", "PUT ORDER REQ RECEIVED", "VALIDATION PENDING"}


def get_buffer(ltp):
    if ltp < 50:
        return 0.5
    if ltp < 100:
        return 1
    if ltp < 200:
        return 2
    return 3


def fetch_orders_cached(force=False):
    global ORDER_BOOK_CACHE, ORDER_CACHE_AT
    now = time.time()
    if not force and (now - ORDER_CACHE_AT) < POLL_INTERVAL and ORDER_BOOK_CACHE:
        return ORDER_BOOK_CACHE
    try:
        orders = safe_kite_call(kite.orders)
        if orders is None:
            return ORDER_BOOK_CACHE
        ORDER_BOOK_CACHE = {o["order_id"]: o for o in orders}
        ORDER_CACHE_AT = now
        return ORDER_BOOK_CACHE
    except Exception:
        return ORDER_BOOK_CACHE


def get_order_by_id(order_id, force=False):
    if not order_id:
        return None
    book = fetch_orders_cached(force=force)
    return book.get(order_id)


def has_pending_order(sym):
    try:
        for o in fetch_orders_cached(force=True).values():
            if o["tradingsymbol"] == sym and o["status"] in OPEN_ORDER_STATUSES:
                return True
    except Exception:
        return False
    return False


def has_any_pending_order():
    try:
        for o in fetch_orders_cached(force=True).values():
            if o["status"] in OPEN_ORDER_STATUSES:
                return True
    except Exception:
        return False
    return False


def has_any_open_position():
    try:
        pos_data = safe_kite_call(kite.positions)
        if pos_data is None:
            return False
        for p in pos_data.get("net", []):
            if p.get("product") == PRODUCT and abs(p.get("quantity", 0)) > 0:
                return True
    except Exception:
        return False
    return False


def place_entry_order(sym):
    global trade
    if option_ltp is None:
        return None
    price = round(option_ltp, 1)
    order_id = f"PAPER_ENTRY_{int(time.time() * 1000)}"
    trade["paper_entry_symbol"] = sym
    trade["paper_entry_price"] = price
    trade["entry_order_id"] = order_id
    msg = f"📝 PAPER ENTRY | {sym} | Qty: {LOT_SIZE} | Price: {price}"
    print(msg)
    send_telegram(msg)
    return order_id


def wait_for_order_complete(order_id, timeout_sec=20):
    """
    Wait for paper order completion.
    Returns (fill_price, status) where fill_price is None on failure.
    """
    if not order_id:
        return None, "INVALID"

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if option_ltp is not None:
            fill_price = round(option_ltp, 1)
            return fill_price, "COMPLETE"
        time.sleep(POLL_INTERVAL)

    return None, "TIMEOUT"


def place_sl_target(sym, entry_price):

    sl_trigger = max(0.5, round(entry_price - PREM_SL_PTS, 1))
    target_price = round(entry_price + PREM_TGT_PTS, 1)

    sl_id = f"PAPER_SL_{int(time.time() * 1000)}"
    tgt_id = f"PAPER_TGT_{int(time.time() * 1000)}"

    trade["prem_sl"] = sl_trigger
    trade["prem_target"] = target_price
    trade["sl_order_id"] = sl_id
    trade["target_order_id"] = tgt_id

    msg = (
        f"📝 PAPER SL/TARGET | {sym} | Entry: {round(entry_price, 2)} | "
        f"SL: {sl_trigger} | Target: {target_price}"
    )
    print(msg)
    send_telegram(msg)

    return sl_id, tgt_id, sl_trigger, target_price

def monitor_orders(sym, sl_order_id, target_order_id):
    global trade_open, ORDER_PLACED, ENTRY_IN_PROGRESS, day_closed, SCRIPT_RUNNING
    global printed_exit, exit_price, pnl, entry_price, quantity, day_pnl
    global summary_sent, trade_taken
    while trade_open and (sl_order_id or target_order_id):
        time.sleep(POLL_INTERVAL)

        if option_ltp is None:
            continue

        ltp_now = float(option_ltp)
        sl_price = float(trade.get("prem_sl") or 0)
        tgt_price = float(trade.get("prem_target") or 0)

        sl_done = sl_price > 0 and ltp_now <= sl_price
        tg_done = tgt_price > 0 and ltp_now >= tgt_price

        if sl_done:
            trade["exit_reason"] = "SL"
            exit_price = ltp_now
            pnl = (exit_price - entry_price) * quantity if entry_price is not None else 0
            day_pnl += pnl
            if day_pnl <= DAILY_LOSS_LIMIT:
                print("🚫 DAILY LOSS LIMIT HIT — STOPPING TRADING")
                send_telegram("🚫 DAILY LOSS LIMIT HIT — BOT STOPPED")
                trade_open = False
                ORDER_PLACED = False
                ENTRY_IN_PROGRESS = False
                day_closed = True
                SCRIPT_RUNNING = False
                safe_kws_stop()
                return
            sound_sl()
            break

        if tg_done:
            trade["exit_reason"] = "TARGET"
            exit_price = ltp_now
            pnl = (exit_price - entry_price) * quantity if entry_price is not None else 0
            day_pnl += pnl
            if day_pnl <= DAILY_LOSS_LIMIT:
                print("🚫 DAILY LOSS LIMIT HIT — STOPPING TRADING")
                send_telegram("🚫 DAILY LOSS LIMIT HIT — BOT STOPPED")
                trade_open = False
                ORDER_PLACED = False
                ENTRY_IN_PROGRESS = False
                day_closed = True
                SCRIPT_RUNNING = False
                safe_kws_stop()
                return
            sound_target()
            break

    if trade.get("exit_reason"):
        if not printed_exit:
            exit_label = "🎯 TARGET HIT" if trade["exit_reason"] == "TARGET" else "🛑 SL HIT"
            exit_msg = (
                f"{exit_label}\n"
                f"Exit Price: {round(exit_price, 2)}\n"
                f"Time: {datetime.now().strftime('%H:%M:%S')}"
            )
            print(exit_msg)
            send_telegram(exit_msg)
            printed_exit = True
        if not summary_sent:
            if trade_taken:
                summary_msg = (
                    "📊 TRADE SUMMARY\n"
                    f"Entry Price: {round(entry_price or 0, 2)}\n"
                    f"Exit Price: {round(exit_price or 0, 2)}\n"
                    f"Quantity: {quantity}\n"
                    f"Net P&L: {round(pnl, 2)}"
                )
            else:
                summary_msg = "📊 TRADE SUMMARY\nNo Trade Taken Today"
            print(summary_msg)
            send_telegram(summary_msg)
            summary_sent = True
        trade_open = False
        ORDER_PLACED = False
        ENTRY_IN_PROGRESS = False
        day_closed = True
        SCRIPT_RUNNING = False
        safe_kws_stop()


# ================= GET OPEN POSITION QTY =================
def get_open_qty(sym):

    try:
        pos_data = safe_kite_call(kite.positions)
        if pos_data is None:
            return None
        pos = pos_data.get("net", [])

        for p in pos:
            if p["tradingsymbol"] == sym and abs(p["quantity"]) > 0:
                return abs(p["quantity"])

        return 0

    except Exception as e:
        print("Position fetch error :", e)
        return None   # ⭐ IMPORTANT CHANGE


# ================= POSITION RECOVERY (ADD THIS BELOW) =================
def recover_position():

    global trade_open, ACTIVE_SYMBOL

    try:
        pos_data = safe_kite_call(kite.positions)
        if pos_data is None:
            return
        pos = pos_data.get("net", [])

        for p in pos:
            if abs(p["quantity"]) > 0 and p["product"] == PRODUCT:

                trade_open = True
                ACTIVE_SYMBOL = p["tradingsymbol"]

                print("Recovered existing position:", ACTIVE_SYMBOL)

                return

    except Exception as e:
        print("Recovery error:", e)


# ================= SAFE EXIT ORDER =================
def place_live_exit(sym):
    global trade_open, ORDER_PLACED, ENTRY_IN_PROGRESS, exit_price, pnl, day_pnl
    try:
        if not trade_open:
            return

        if option_ltp is None:
            return

        exit_price = round(float(option_ltp), 1)
        pnl = (exit_price - entry_price) * quantity if entry_price is not None else 0
        day_pnl += pnl

        trade["exit_reason"] = trade.get("exit_reason") or "LIVE_EXIT"

        msg = (
            f"📝 PAPER LIVE EXIT | {sym} | Exit: {exit_price} | "
            f"Qty: {quantity} | P&L: {round(pnl, 2)}"
        )
        print(msg)
        send_telegram(msg)

        trade_open = False
        ORDER_PLACED = False
        ENTRY_IN_PROGRESS = False

    except Exception:
        pass

# ================= WEBSOCKET =================
def on_connect(ws,r):
    print("WebSocket connected")
    ws.subscribe([SPOT_TOKEN])
    ws.set_mode(ws.MODE_LTP,[SPOT_TOKEN])

def on_close(ws, c, r):

    # 🚫 Never reconnect after day close
    if day_closed:
        print("WebSocket closed (day finished)")
        return

    print("WebSocket closed - waiting auto reconnect")

# ================= CORE ENGINE =================
def on_ticks(ws, ticks):

    global trade_open, ACTIVE_OPTION_TOKEN, ACTIVE_SYMBOL
    global FIXED_SYMBOL, FIXED_TOKEN
    global ORDER_PLACED, BLOCK_MSG_SHOWN, LAST_BLOCK_REASON, ENTRY_IN_PROGRESS
    global spot_ltp, option_ltp, day_closed, LAST_VALID_SPOT
    global trade_taken, breakout_done, entry_price, exit_price, quantity, pnl
    global printed_entry, summary_sent, LAST_TICK_TIME, LAST_TRADE_TIME
    global MANUAL_HANDLED

    try:
        if WS_STOPPED or not SCRIPT_RUNNING:
            return

        LAST_TICK_TIME = time.time()

        if ws is None:
            ws = kws

        now = datetime.now().time()

        # ===== UPDATE LTP =====
        for t in ticks:
            if "last_price" in t:
                new_price = t["last_price"]

                # Reject invalid values
                if new_price <= 0:
                    continue

                # Reject sudden spike (>2% move in one tick)
                if LAST_VALID_SPOT is not None:
                    change_pct = abs(new_price - LAST_VALID_SPOT) / LAST_VALID_SPOT * 100
                    if change_pct > 2:
                        print(f"⚠️ Bad tick ignored: {new_price}")
                        continue

                spot_ltp = new_price
                LAST_VALID_SPOT = new_price

            if ACTIVE_OPTION_TOKEN and t.get("instrument_token") == ACTIVE_OPTION_TOKEN:
                option_ltp = t["last_price"]

        # ================= MANUAL ENTRY DETECTION =================
        if not trade_open and not MANUAL_HANDLED:
            try:
                pos_data = safe_kite_call(kite.positions)
                if pos_data is None:
                    return
                pos = pos_data.get("net", [])

                for p in pos:
                    if p.get("product") == PRODUCT and abs(p.get("quantity", 0)) > 0:

                        sym = p["tradingsymbol"]
                        qty = abs(p["quantity"])
                        avg_price = p.get("average_price", 0)

                        if avg_price <= 0:
                            continue

                        print("⚠️ Manual entry detected, Bot entry rejected")
                        send_telegram("⚠️ Manual entry detected, Bot entry rejected")

                        trade_open = True
                        ORDER_PLACED = True
                        trade_taken = True
                        MANUAL_HANDLED = True
                        ACTIVE_SYMBOL = sym
                        quantity = qty
                        entry_price = avg_price

                        for ins in INSTRUMENTS:
                            if ins["tradingsymbol"] == sym:
                                ACTIVE_OPTION_TOKEN = ins["instrument_token"]
                                ws.subscribe([ACTIVE_OPTION_TOKEN])
                                ws.set_mode(ws.MODE_LTP, [ACTIVE_OPTION_TOKEN])
                                break

                        sl_id, tgt_id, sl_price, tgt_price = place_sl_target(sym, avg_price)

                        if not sl_id or not tgt_id:
                            print("❌ SL/Target placement failed")
                            return

                        trade["sl_order_id"] = sl_id
                        trade["target_order_id"] = tgt_id

                        threading.Thread(
                            target=monitor_orders,
                            args=(sym, sl_id, tgt_id),
                            daemon=True
                        ).start()

                        return

            except Exception as e:
                print("Manual detection error:", e)

        # ===== WAIT CONDITIONS =====
        if not candle_done or day_closed:
            return

        # ===== DAY CLOSE =====
        if now >= FORCE_EXIT_TIME and not day_closed:

            print(f"{RED}3:20 PM DAY CLOSE TRIGGERED{RESET}")

            if trade_open and ACTIVE_SYMBOL:
                qty = get_open_qty(ACTIVE_SYMBOL)
                if qty > 0:
                    place_live_exit(ACTIVE_SYMBOL)

            send_telegram("DAY COMPLETED")

            day_closed = True
            globals()["SCRIPT_RUNNING"] = False
            safe_kws_stop()
            return

        # ===== ENTRY =====
        if not trade_open and not ORDER_PLACED and spot_ltp and now < LAST_ENTRY_TIME:

            if trade_taken or day_closed:
                return

            if not AUTO_READY or breakout_done or CPR_TYPE == "WIDE":
                return

            if allowed_side is None:
                return

            side = None

            # ===== CE / PE BUY DAY =====
            if AUTO_SIGNAL in ["CE BUY DAY", "PE BUY DAY"]:

                if spot_ltp >= candle["high"] + 3 and allowed_side == "CE":
                    side = "CE"

                elif spot_ltp <= candle["low"] - 3 and allowed_side == "PE":
                    side = "PE"

                else:
                    return


            # ===== NO TRADE DAY (REVERSE LOGIC) =====
            elif AUTO_SIGNAL == "NO TRADE":

            # 🔥 IMPORTANT: Follow MA direction, not breakout direction
                if allowed_side == "CE" and spot_ltp >= candle["high"] + 3:
                    side = "CE"

                elif allowed_side == "PE" and spot_ltp <= candle["low"] - 3:
                    side = "PE"

                else:
                    return

            else:
                return
            if FIXED_SYMBOL is None or FIXED_TOKEN is None:
                return

            print(f"ENTRY USING FIXED SYMBOL: {FIXED_SYMBOL}")
            ACTIVE_SYMBOL, ACTIVE_OPTION_TOKEN = FIXED_SYMBOL, FIXED_TOKEN

            if ws:
                ws.subscribe([ACTIVE_OPTION_TOKEN])
                ws.set_mode(ws.MODE_LTP, [ACTIVE_OPTION_TOKEN])

            if get_open_qty(ACTIVE_SYMBOL) > 0:
                return

            if has_any_open_position():
                return

            if has_pending_order(ACTIVE_SYMBOL):
                return

            if has_any_pending_order():
                return

            if ENTRY_IN_PROGRESS:
                return

            

            ENTRY_IN_PROGRESS = True
            trade.clear()

            def run_execution(sym_local):
                global trade_open, ENTRY_IN_PROGRESS, entry_price, quantity, trade_taken, ORDER_PLACED

                try:
                    oid = place_entry_order(sym_local)
                    if not oid:
                        return

                    fill_price, _ = wait_for_order_complete(oid)
                    if not fill_price:
                        return

                    trade_taken = True
                    ORDER_PLACED = True

                    sl_id, tgt_id, _, _ = place_sl_target(sym_local, fill_price)
                    if not sl_id or not tgt_id:
                        place_live_exit(sym_local)
                        return

                    entry_price = fill_price
                    quantity = LOT_SIZE
                    trade_open = True

                    threading.Thread(
                        target=monitor_orders,
                        args=(sym_local, sl_id, tgt_id),
                        daemon=True
                    ).start()

                finally:
                    ENTRY_IN_PROGRESS = False

            threading.Thread(target=run_execution, args=(ACTIVE_SYMBOL,), daemon=True).start()

        # ===== POSITION CHECK =====
        if trade_open:
            qty = get_open_qty(ACTIVE_SYMBOL)
            if qty == 0:
                trade_open = False
                ORDER_PLACED = False

    except Exception as e:
        print("on_ticks error:", e)

# ⭐⭐⭐ ADD HERE ⭐⭐⭐

def safe_kws_stop():
    global WS_STOPPED, SCRIPT_RUNNING
    if WS_STOPPED:
        return
    WS_STOPPED = True
    SCRIPT_RUNNING = False
    try:
        time.sleep(0.5)
        kws.close()
        print("WebSocket safely closed")
    except Exception as e:
        print("KWS stop error:", e)

# ================= START =================

print_header()

recover_position()

kws = KiteTicker(API_KEY, ACCESS_TOKEN)
kws.on_ticks = on_ticks
kws.on_connect = on_connect
kws.on_close = on_close

kws.connect(threaded=True)


def heartbeat():

    while SCRIPT_RUNNING:
        if WS_STOPPED:
            break

        # Fetch spot price
        fetch_spot()

        if time.time() - LAST_TICK_TIME > 10:
            print("⚠️ WebSocket stalled — restarting safely")
            safe_kws_stop()
            break

        # Fetch 9:30 candle once
        if not candle_done and datetime.now().time() > dtime(9,35):
            fetch_930_candle()

        # Heartbeat delay
        time.sleep(2)

def pnl_tracker():

    while SCRIPT_RUNNING:

        if trade_open and entry_price is not None and quantity > 0:

            if option_ltp is not None:

                current_pnl = (option_ltp - entry_price) * quantity

                msg = (
                    f"📊 LIVE P&L UPDATE\n"
                    f"Symbol: {ACTIVE_SYMBOL}\n"
                    f"Entry: {round(entry_price,2)}\n"
                    f"LTP: {round(option_ltp,2)}\n"
                    f"Qty: {quantity}\n"
                    f"P&L: {round(current_pnl,2)}"
                )

                print(msg)
                send_telegram(msg)

            # ⏱ wait exactly 10 minutes
            time.sleep(600)

        else:
            # ⏳ wait before checking again
            time.sleep(10)


# Start background heartbeat
threading.Thread(target=heartbeat, daemon=True).start()
threading.Thread(target=pnl_tracker, daemon=True).start()


# Keep script alive
while SCRIPT_RUNNING:
    time.sleep(1)


print("Script exited cleanly")
send_telegram("🛑 Script Stopped")
# Remove lock file
if os.path.exists(LOCK_FILE):
    os.remove(LOCK_FILE)

sys.exit(0)

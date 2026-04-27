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
    hist = kite.historical_data(
        SPOT_TOKEN,
        today - timedelta(days=50),   # buffer for holidays
        today,
        "day"
    )

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
    """
    Return ATM option for next weekly expiry.
    Fixes:
    1) Never crash callers due to None unpacking.
    2) If exact strike is missing, choose nearest available strike.
    3) Return None only when no instruments exist for requested side/expiry.
    """
    if spot is None:
        print("ATM lookup skipped: spot is None")
        return None

    strike = round(spot / 50) * 50
    expiry = get_next_expiry()
    print(f"{YELLOW}Using NEXT WEEK Expiry: {expiry}{RESET}")

    candidates = [
        i for i in INSTRUMENTS
        if i["name"] == "NIFTY" and i["expiry"] == expiry and i["instrument_type"] == side
    ]

    if not candidates:
        print(f"ATM lookup failed: no {side} instruments for expiry {expiry}")
        return None

    exact = next((i for i in candidates if i["strike"] == strike), None)
    if exact:
        return exact["tradingsymbol"], exact["instrument_token"]

    nearest = min(candidates, key=lambda x: abs(x["strike"] - strike))
    print(
        f"{YELLOW}Exact strike {strike} not found. "
        f"Using nearest strike {nearest['strike']} ({nearest['tradingsymbol']}){RESET}"
    )
    return nearest["tradingsymbol"], nearest["instrument_token"]

# ================= FETCH =================
def fetch_spot():
    global spot_ltp
    try:
        data = kite.ltp(["NSE:NIFTY 50"])
        if "NSE:NIFTY 50" in data:
            spot_ltp = data["NSE:NIFTY 50"]["last_price"]
        else:
            return
    except Exception as e:
        print("Spot fetch error:", e)
        return

# ================= 9:30 CANDLE =================
def fetch_930_candle():
    global candle_done, printed_930

    if candle_done:
        return

    now = datetime.now().time()
    today = date.today()

    # ⭐ WAIT UNTIL 9:35
    if now < dtime(9,35):
        return

    data = kite.historical_data(
        SPOT_TOKEN,
        datetime.combine(today, dtime(9,30)),
        datetime.combine(today, dtime(9,35)),
        "5minute"
    )

    # ⭐ AFTER 9:35 — if still no data → holiday
    if not data:
        print("No 9:30 candle data – possible holiday")
        sys.exit(0)

    candle["high"] = data[0]["high"]
    candle["low"] = data[0]["low"]

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
        orders = kite.orders()
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
        for p in kite.positions()["net"]:
            if p.get("product") == PRODUCT and abs(p.get("quantity", 0)) > 0:
                return True
    except Exception:
        return False
    return False


def place_entry_order(sym):
    if option_ltp is None:
        return None
    price = round(option_ltp + get_buffer(option_ltp), 1)
    try:
        return kite.place_order(
            variety=kite.VARIETY_REGULAR,
            exchange=EXCHANGE,
            tradingsymbol=sym,
            transaction_type=kite.TRANSACTION_TYPE_BUY,
            quantity=LOT_SIZE,
            product=PRODUCT,
            order_type=kite.ORDER_TYPE_LIMIT,
            price=price,
            validity=kite.VALIDITY_DAY
        )
    except Exception:
        return None


def wait_for_order_complete(order_id, timeout_sec=20):
    """
    Wait for order completion without any modify/retry loops.
    Returns (fill_price, status) where fill_price is None on failure.
    """
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL)
        od = get_order_by_id(order_id, force=True)
        if not od:
            continue
        status = od.get("status")
        if status == "COMPLETE":
            avg_price = float(od.get("average_price") or 0)
            return (avg_price if avg_price > 0 else None), status
        if status in {"CANCELLED", "REJECTED"}:
            return None, status
    return None, "TIMEOUT"


def place_sl_target(sym, entry_price):

    sl_trigger = max(0.5, round(entry_price - PREM_SL_PTS, 1))
    target_price = round(entry_price + PREM_TGT_PTS, 1)

    sl_id = None
    tgt_id = None

    try:
        sl_id = kite.place_order(
            variety=kite.VARIETY_REGULAR,
            exchange=EXCHANGE,
            tradingsymbol=sym,
            transaction_type=kite.TRANSACTION_TYPE_SELL,
            quantity=LOT_SIZE,
            product=PRODUCT,
            order_type=kite.ORDER_TYPE_SLM,
            trigger_price=sl_trigger,
            validity=kite.VALIDITY_DAY
        )
    except Exception as e:
        print(f"SL-M placement failed: {e}")
        return None, None, sl_trigger, target_price

    try:
        tgt_id = kite.place_order(
            variety=kite.VARIETY_REGULAR,
            exchange=EXCHANGE,
            tradingsymbol=sym,
            transaction_type=kite.TRANSACTION_TYPE_SELL,
            quantity=LOT_SIZE,
            product=PRODUCT,
            order_type=kite.ORDER_TYPE_LIMIT,
            price=target_price,
            validity=kite.VALIDITY_DAY
        )
    except Exception as e:
        print(f"Target placement failed: {e}")

        try:
            kite.cancel_order(variety=kite.VARIETY_REGULAR, order_id=sl_id)
        except Exception:
            pass

        return None, None, sl_trigger, target_price

    return sl_id, tgt_id, sl_trigger, target_price

def monitor_orders(sym, sl_order_id, target_order_id):
    global trade_open, ORDER_PLACED, ENTRY_IN_PROGRESS, day_closed, SCRIPT_RUNNING
    global printed_exit, exit_price, pnl, entry_price, quantity, day_pnl
    global summary_sent, trade_taken
    while trade_open and (sl_order_id or target_order_id):
        time.sleep(POLL_INTERVAL)
        sl_od = get_order_by_id(sl_order_id, force=True) if sl_order_id else None
        tg_od = get_order_by_id(target_order_id, force=False) if target_order_id else None

        sl_done = sl_od and sl_od.get("status") == "COMPLETE"
        tg_done = tg_od and tg_od.get("status") == "COMPLETE"

        if sl_done:
            if target_order_id:
                try:
                    kite.cancel_order(variety=kite.VARIETY_REGULAR, order_id=target_order_id)
                except Exception:
                    pass
            trade["exit_reason"] = "SL"
            exit_price = float(sl_od.get("average_price") or sl_od.get("price") or trade.get("prem_sl") or 0)
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
            if sl_order_id:
                try:
                    kite.cancel_order(variety=kite.VARIETY_REGULAR, order_id=sl_order_id)
                except Exception:
                    pass
            trade["exit_reason"] = "TARGET"
            exit_price = float(tg_od.get("average_price") or tg_od.get("price") or trade.get("prem_target") or 0)
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
        pos = kite.positions()["net"]

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
        pos = kite.positions()["net"]

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
    try:
        qty = get_open_qty(sym)

        if not qty:
            return

        if option_ltp is None:
            return

        price = round(max(0.1, option_ltp - 1), 1)

        kite.place_order(
            variety=kite.VARIETY_REGULAR,
            exchange=EXCHANGE,
            tradingsymbol=sym,
            transaction_type=kite.TRANSACTION_TYPE_SELL,
            quantity=qty,
            product=PRODUCT,
            order_type=kite.ORDER_TYPE_LIMIT,
            price=price,   # ✅ IMPORTANT
            validity=kite.VALIDITY_DAY
        )

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
    global ORDER_PLACED, BLOCK_MSG_SHOWN, LAST_BLOCK_REASON, ENTRY_IN_PROGRESS
    global spot_ltp, option_ltp, day_closed
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
                spot_ltp = t["last_price"]
            if ACTIVE_OPTION_TOKEN and t.get("instrument_token") == ACTIVE_OPTION_TOKEN:
                option_ltp = t["last_price"]

        # ================= MANUAL ENTRY DETECTION =================
        if not trade_open and not MANUAL_HANDLED:
            try:
                pos = kite.positions()["net"]

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
            atm = get_atm_option(spot_ltp, side)
            if not atm:
                return

            ACTIVE_SYMBOL, ACTIVE_OPTION_TOKEN = atm

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
        time.sleep(1)


# Start background heartbeat
threading.Thread(target=heartbeat, daemon=True).start()


# Keep script alive
while SCRIPT_RUNNING:
    time.sleep(1)


print("Script exited cleanly")
send_telegram("🛑 Script Stopped")
# Remove lock file
if os.path.exists(LOCK_FILE):
    os.remove(LOCK_FILE)

sys.exit(0)

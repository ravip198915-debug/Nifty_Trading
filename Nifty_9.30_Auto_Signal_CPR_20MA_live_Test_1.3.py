# ==========================================================
# ULTRA-PRO OPTION BUYING – LIVE TRADE VERSION
# (Same Framework – LIVE ORDER EXECUTION ADDED)
# ==========================================================

# ================= CONFIG =================
API_KEY ="ikzievanmkaoalmz"
ACCESS_TOKEN ="ZfrzropxsTq5d5uxgD2Kr6bUERUG24Sx"
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
MAX_ENTRY_RETRY=6

SPOT_TOKEN=256265
LOT_SIZE=130

PREM_SL_PTS=20
PREM_TGT_PTS=40	

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
day_closed = False
SCRIPT_RUNNING = True
WS_STOPPED = False

AUTO_SIGNAL="NO TRADE"
allowed_side=None
MA_SIDE=None
CPR_TYPE=None
AUTO_READY = False

trade={}
day_closed=False

candle={"high":None,"low":None}
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

    if cpr_width >= 0.6:
        CPR_TYPE = "WIDE"
    elif cpr_width <= 0.15:
        CPR_TYPE = "NARROW"
    else:
        CPR_TYPE = "NORMAL"

    # ================= MA20 =================
    closes = [i["close"] for i in hist[:-1]]   # exclude today
    ma20 = sum(closes[-20:]) / 20

    MA_SIDE = "Above" if PDC > ma20 else "Below"

    # ================= AUTO SIGNAL =================
    if CPR_TYPE != "WIDE":

        if MA_SIDE == "Above":
            AUTO_SIGNAL = "CE BUY DAY"
            allowed_side = "CE"
        else:
            AUTO_SIGNAL = "PE BUY DAY"
            allowed_side = "PE"

    else:
        AUTO_SIGNAL = "NO TRADE"

        # ⭐ Reverse logic for NO TRADE DAY
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
    strike=round(spot/50)*50
    expiry=get_next_expiry()
    print(f"{YELLOW}Using NEXT WEEK Expiry: {expiry}{RESET}")
    for i in INSTRUMENTS:
        if i["name"]=="NIFTY" and i["expiry"]==expiry and i["strike"]==strike and i["instrument_type"]==side:
            return i["tradingsymbol"],i["instrument_token"]

# ================= FETCH =================
def fetch_spot():
    global spot_ltp
    try:
        spot_ltp=kite.ltp(["NSE:NIFTY 50"])["NSE:NIFTY 50"]["last_price"]
    except: pass

# ================= 9:30 CANDLE =================
def fetch_930_candle():
    global candle_done

    if candle_done:
        return

    now = datetime.now().time()
    today = date.today()

    # ⭐ WAIT UNTIL 9:35
    if now < dtime(9,35):
        print("Market not opened yet – waiting for 9:30 candle")
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
    candle_done = True

    print(f"{GREEN}Fetched 9:30 candle successfully{RESET}")
    
    send_telegram("Fetched 9:30 candle successfully")

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


def modify_until_filled(sym, order_id):
    for _ in range(MAX_ENTRY_RETRY):
        time.sleep(POLL_INTERVAL)
        od = get_order_by_id(order_id, force=True)
        if not od:
            continue

        status = od.get("status")
        if status == "COMPLETE":
            avg_price = float(od.get("average_price") or 0)
            if avg_price > 0:
                return avg_price
        if status in {"CANCELLED", "REJECTED"}:
            return None

        if status in OPEN_ORDER_STATUSES and option_ltp is not None:
            try:
                new_price = round(option_ltp + get_buffer(option_ltp), 1)
                kite.modify_order(
                    variety=kite.VARIETY_REGULAR,
                    order_id=order_id,
                    price=new_price,
                    order_type=kite.ORDER_TYPE_LIMIT,
                    validity=kite.VALIDITY_DAY
                )
            except Exception:
                pass

    try:
        kite.cancel_order(variety=kite.VARIETY_REGULAR, order_id=order_id)
    except Exception:
        pass
    return None


def place_sl_target(sym, entry_price):
    sl_trigger = round(entry_price - PREM_SL_PTS, 1)
    target_price = round(entry_price + PREM_TGT_PTS, 1)
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
        return sl_id, tgt_id, sl_trigger, target_price
    except Exception:
        return None, None, sl_trigger, target_price


def monitor_orders(sym, sl_order_id, target_order_id):
    global trade_open, ORDER_PLACED, ENTRY_IN_PROGRESS, day_closed, SCRIPT_RUNNING
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
            sound_sl()
            break

        if tg_done:
            if sl_order_id:
                try:
                    kite.cancel_order(variety=kite.VARIETY_REGULAR, order_id=sl_order_id)
                except Exception:
                    pass
            trade["exit_reason"] = "TARGET"
            sound_target()
            break

    if trade.get("exit_reason"):
        msg = f"EXIT TRADE\nSymbol: {sym}\nReason: {trade['exit_reason']}\nTime: {datetime.now().strftime('%H:%M:%S')}"
        send_telegram(msg)
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
    print("WebSocket connected")

def on_close(ws, c, r):

    # 🚫 Never reconnect after day close
    if day_closed:
        print("WebSocket closed (day finished)")
        return

    print("WebSocket closed - waiting auto reconnect")

# ================= CORE ENGINE =================
def on_ticks(ws, ticks):

    # ⭐ ADD THIS LINE HERE (FIRST THING INSIDE FUNCTION)
    if ws is None:
        ws = kws

    global trade_open, ACTIVE_OPTION_TOKEN, ACTIVE_SYMBOL
    global ORDER_PLACED, BLOCK_MSG_SHOWN, ENTRY_IN_PROGRESS
    global spot_ltp, option_ltp, day_closed

    now = datetime.now().time()

    # ===== UPDATE LTP =====
    for t in ticks:
        if "last_price" in t:
            spot_ltp = t["last_price"]
        if ACTIVE_OPTION_TOKEN and t.get("instrument_token") == ACTIVE_OPTION_TOKEN:
            option_ltp = t["last_price"]

    if not candle_done or day_closed:
        return

    # ===== UNIVERSAL DAY CLOSE (3:20 PM) =====
# ===== UNIVERSAL DAY CLOSE (3:20 PM) =====
    if now >= FORCE_EXIT_TIME and not day_closed:

        print(f"{RED}3:20 PM DAY CLOSE TRIGGERED{RESET}")

        if trade_open and ACTIVE_SYMBOL:
            print(f"{YELLOW}Closing active trade...{RESET}")

            qty = get_open_qty(ACTIVE_SYMBOL)
            if qty > 0:
                place_live_exit(ACTIVE_SYMBOL)
                print(f"{RED}Position closed for day end{RESET}")
            else:
                print("Position already closed manually") 
        else:
            print(f"{BLUE}No running trade - closing script for the day{RESET}")

        print(f"{GREEN}DAY COMPLETED{RESET}")

        send_telegram("DAY COMPLETED")

        day_closed = True
        globals()["SCRIPT_RUNNING"] = False

        safe_kws_stop()        # ⭐ IMPORTANT (use stop, not close)
        return


    # ===== ENTRY =====
    if not trade_open and not ORDER_PLACED and spot_ltp and now < LAST_ENTRY_TIME:

        #⭐ AUTO SIGNAL LOCK (ADD THIS)
        if not AUTO_READY:
            return


        if CPR_TYPE == "WIDE":
           return

        if allowed_side is None:
            return

        side = None

        if spot_ltp >= candle["high"] + 1:
            if allowed_side == "CE":
                side = "CE"
                BLOCK_MSG_SHOWN = False
            else:
                if not BLOCK_MSG_SHOWN:
                    print(f"{YELLOW}ENTRY BLOCKED – CE not allowed{RESET}")
                    BLOCK_MSG_SHOWN = True
                return

        elif spot_ltp <= candle["low"] - 1:
            if allowed_side == "PE":
                side = "PE"
                BLOCK_MSG_SHOWN = False
            else:
                if not BLOCK_MSG_SHOWN:
                    print(f"{YELLOW}ENTRY BLOCKED – PE not allowed{RESET}")
                    BLOCK_MSG_SHOWN = True
                return
        else:
            return

        sym, tok = get_atm_option(spot_ltp, side)

        ACTIVE_OPTION_TOKEN = tok
        ACTIVE_SYMBOL = sym
        # ⭐ ADD THIS SAFETY CHECK HERE
        if ACTIVE_SYMBOL is None:
           print("ATM option not found — skipping entry")
           return

        # ⭐ POSITION SAFETY (ADD THIS)
        if get_open_qty(sym) > 0:
            print("Position already exists — skipping entry")
            return

        if ws:
            ws.subscribe([tok])
            ws.set_mode(ws.MODE_LTP, [tok])

        print(f"{BLUE}Trade Executed Date: {date.today()} | {datetime.now().strftime('%H:%M:%S')}{RESET}")
   
        # ⭐ Pending order protection
        if has_pending_order(sym):
            print("Order already pending — skipping duplicate entry")
            return

        if ENTRY_IN_PROGRESS:
            return

        ORDER_PLACED = True
        ENTRY_IN_PROGRESS = True
        trade.clear()

        def run_execution(sym_local):
            global trade_open, ORDER_PLACED, ENTRY_IN_PROGRESS
            with EXECUTION_LOCK:
                oid = place_entry_order(sym_local)
                if not oid:
                    ORDER_PLACED = False
                    ENTRY_IN_PROGRESS = False
                    return

                trade["entry_order_id"] = oid
                fill_price = modify_until_filled(sym_local, oid)
                if not fill_price:
                    ORDER_PLACED = False
                    ENTRY_IN_PROGRESS = False
                    return

                trade["prem_entry"] = fill_price
                sl_id, tgt_id, sl_price, tgt_price = place_sl_target(sym_local, fill_price)
                if not sl_id or not tgt_id:
                    place_live_exit(sym_local)
                    ORDER_PLACED = False
                    ENTRY_IN_PROGRESS = False
                    return

                trade["sl_order_id"] = sl_id
                trade["target_order_id"] = tgt_id
                trade["prem_sl"] = sl_price
                trade["prem_target"] = tgt_price
                trade_open = True
                ENTRY_IN_PROGRESS = False
                sound_entry()
                send_telegram(f"Premium Entry: {fill_price}\nTarget: {tgt_price}\nSL: {sl_price}")
                monitor_orders(sym_local, sl_id, tgt_id)

        threading.Thread(target=run_execution, args=(sym,), daemon=True).start()

    if trade_open:
        qty = get_open_qty(ACTIVE_SYMBOL)
        if qty == 0:
            trade_open = False
            ORDER_PLACED = False

# ⭐⭐⭐ ADD HERE ⭐⭐⭐

def safe_kws_stop():
    global WS_STOPPED
    if WS_STOPPED:
        return
    try:
        kws.stop()
    except:
        pass
    WS_STOPPED = True

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

        # Fetch spot price
        fetch_spot()

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

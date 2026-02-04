import time
import requests
from coinbase.rest import RESTClient

# ==========================
# DISCORD WEBHOOKS
# ==========================

LOGS_WEBHOOK = "https://discord.com/api/webhooks/1468048482219724810/05HmuArCRBEC5ydrVDpZ37lL-0HwIhph93tUSpxk98p-nmS0-DxlV1bZY5DPEZ5tejQk"
ALERTS_WEBHOOK = "https://discord.com/api/webhooks/1468048658262917130/zBtIfCvLFMD_0XluEgI4FDDROvN8lKr1yc1VnAY19vhU7HIBWbnGoz56wEsVppyTcUyr"
TEST_WEBHOOK = "https://discord.com/api/webhooks/1468048857567989833/DQo2L_lRXTUeHhT0nLl0dUooNTLtqnJnnru7Qh0EIL10YxDOdkhbh3IbMY0XRRmHsIDE"

def send_discord(webhook, message):
    try:
        requests.post(webhook, json={"content": message})
    except Exception as e:
        print(f"[DISCORD ERROR] {e}")

# ==========================
# CONFIGURATION
# ==========================

ASSET = "XRP-USD"
WINDOW = 20
BUY_VARIANCE = -0.01
SELL_VARIANCE = 0.03
SLEEP_TIME = 30
DRY_RUN = False

# ==========================
# COINBASE API CONFIG
# ==========================

API_KEY = 
API_SECRET = 

"""

client = RESTClient(api_key=API_KEY, api_secret=API_SECRET)

# ==========================
# HYBRID PROTECTION SETTINGS
# ==========================

MAX_EXPOSURE_USD = 9.35
USD_BUFFER = 6.95
BUY_COOLDOWN_SECONDS = 20
VARIANCE_DROP_REQUIRED = 0.005

last_buy_time = 0
last_buy_price = None

# ==========================
# PRICE FETCHING
# ==========================

def get_current_price():
    url = f"https://api.coinbase.com/v2/prices/{ASSET}/spot"
    r = requests.get(url).json()
    return float(r["data"]["amount"])

# ==========================
# BALANCE FETCHING (STUBS)
# ==========================

def get_usd_balance():
    return 10.00

def get_xrp_balance():
    return 5.0

# ==========================
# OPEN ORDER CHECKING
# ==========================

def get_open_orders():
    try:
        resp = client.list_orders(product_id=ASSET)
        orders = resp.orders
        watched = {"OPEN", "PENDING", "PARTIALLY_FILLED"}
        return [o for o in orders if o.status in watched]
    except Exception as e:
        send_discord(ALERTS_WEBHOOK, f"[ERROR] Could not fetch open orders: {e}")
        return []

# ==========================
# HYBRID PROTECTION LOGIC
# ==========================

def can_buy_xrp(current_price):
    global last_buy_time, last_buy_price

    usd_balance = get_usd_balance()
    xrp_balance = get_xrp_balance()

    xrp_value = xrp_balance * current_price
    if xrp_value >= MAX_EXPOSURE_USD:
        return False, "Exposure cap reached"

    if usd_balance - current_price < USD_BUFFER:
        return False, "USD buffer protection triggered"

    if time.time() - last_buy_time < BUY_COOLDOWN_SECONDS:
        return False, "Cooldown active"

    if last_buy_price is not None:
        drop = (last_buy_price - current_price) / last_buy_price
        if drop < VARIANCE_DROP_REQUIRED:
            return False, "Variance drop not enough"

    return True, "Buy allowed"

def record_buy(price):
    global last_buy_time, last_buy_price
    last_buy_time = time.time()
    last_buy_price = price

# ==========================
# EMA CALCULATION
# ==========================

def calculate_ema(prices, window=20):
    if len(prices) < window:
        return None

    k = 2 / (window + 1)
    ema = prices[0]

    for price in prices[1:]:
        ema = (price * k) + (ema * (1 - k))

    return ema

# ==========================
# ORDER EXECUTION
# ==========================

def place_buy_order(price):
    if DRY_RUN:
        msg = f"[DRY RUN] BUY XRP at {price}"
        print(msg)
        send_discord(LOGS_WEBHOOK, msg)
    else:
        msg = f"Executing BUY XRP at {price}"
        print(msg)
        send_discord(ALERTS_WEBHOOK, msg)

    record_buy(price)

def place_sell_order(price):
    if DRY_RUN:
        msg = f"[DRY RUN] SELL XRP at {price}"
        print(msg)
        send_discord(LOGS_WEBHOOK, msg)
    else:
        msg = f"Executing SELL XRP at {price}"
        print(msg)
        send_discord(ALERTS_WEBHOOK, msg)

# ==========================
# MAIN LOOP
# ==========================

def run_bot():
    prices = []

    header = (
        "Starting EMA-Variance Trading Bot...\n"
        f"Asset: {ASSET}\n"
        f"EMA Window: {WINDOW}\n"
        f"Buy Trigger: {BUY_VARIANCE}%\n"
        f"Sell Trigger: {SELL_VARIANCE}%\n"
        f"DRY RUN: {DRY_RUN}\n"
        "----------------------------------------"
    )

    print(header)
    send_discord(LOGS_WEBHOOK, "Bot started successfully.")

    while True:
        try:
            open_orders = get_open_orders()

            if open_orders:
                msg = "[WAIT] Unprocessed orders detected. Bot pausing."
                print(msg)
                send_discord(LOGS_WEBHOOK, msg)
                time.sleep(10)
                continue

            current_price = get_current_price()
            prices.append(current_price)

            if len(prices) > WINDOW:
                prices.pop(0)

            ema = calculate_ema(prices, WINDOW)

            if ema is None:
                print("Collecting data for EMA...")
                time.sleep(SLEEP_TIME)
                continue

            variance = (current_price - ema) / ema * 100

            log_msg = f"XRP Price: {current_price:.4f} | EMA20: {ema:.4f} | Var: {variance:.2f}%"
            print(log_msg)
            send_discord(LOGS_WEBHOOK, log_msg)

            if variance <= BUY_VARIANCE:
                allowed, reason = can_buy_xrp(current_price)
                if allowed:
                    send_discord(ALERTS_WEBHOOK, f"BUY signal triggered at {current_price}")
                    place_buy_order(current_price)
                else:
                    send_discord(LOGS_WEBHOOK, f"BUY blocked: {reason}")

            elif variance >= SELL_VARIANCE:
                send_discord(ALERTS_WEBHOOK, f"SELL signal triggered at {current_price}")
                place_sell_order(current_price)

            time.sleep(SLEEP_TIME)

        except Exception as e:
            err = f"[ERROR] {e}"
            print(err)
            send_discord(ALERTS_WEBHOOK, err)
            time.sleep(5)

# ==========================
# START BOT
# ==========================

run_bot()

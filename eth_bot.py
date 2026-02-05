import time # used for cooldowns, timestamps, sleeping between cycles.
import requests # for fetching prices and sending Discord webhooks
from collections import deque # efficient list with a fixed max length for variance tracking
from coinbase.rest import RESTClient # Coinbase API client. Coinbase SDK client for authenticated API calls.

# ==========================
# DISCORD WEBHOOKS
# ==========================

LOGS_WEBHOOK = "https://discord.com/api/webhooks/1468763706790904043/xzz6sf444VJLnHiU4IOhJvnMRJCqCReZ-etjlMUZu3OFI0c3wXaHpIRjwjGrH1L_q5W-" #routine updates
ALERTS_WEBHOOK = "https://discord.com/api/webhooks/1468763873371750484/PrO5AkeJ9Eu4PhY_68zmkkgaFKiaPORg7misrXx-16vkXdcjrciEcR0AIWU_Qjb5KL3z" #buy/sell signals, errors


def send_discord(webhook, message): #send a message to a Discord webhook
    try: 
        requests.post(webhook, json={"content": message}) # jason payload is just the message content
    except Exception as e:
        print(f"[DISCORD ERROR] {e}")

# ==========================
# CONFIGURATION
# ==========================

ASSET = "ETH-USD" #trading pair
WINDOW = 20 #EMA window size

# variance is in PERCENT
BASE_BUY_VARIANCE = -0.60   # buys when below -12%
BASE_SELL_VARIANCE = 0.90   # 0 sells when over 18%

BUY_VARIANCE = BASE_BUY_VARIANCE # initialized
SELL_VARIANCE = BASE_SELL_VARIANCE# initialized

SLEEP_TIME = 30 # seconds between each cycle
DRY_RUN = False # if True, no real orders are placed

# ==========================
# COINBASE API CONFIG
# ==========================
#api keys - replace with your own from Coinbase Pro
API_KEY = "organizations/251f5945-596c-47ee-ad90-4327620bee58/apiKeys/bc0eaf4d-b414-4efd-ada7-048bb0eae74f"          # your key
API_SECRET = "-----BEGIN EC PRIVATE KEY-----\nMHcCAQEEIHcqrZ4FYSRYJeDGMOnsl03ZHht8sebPinjvQw6vkkD/oAoGCCqGSM49\nAwEHoUQDQgAEQrUwqEN5e7Mu1Emv5EB5wX3SoaTLknOZyS2qgL0uqDrWWfk8Nv07\nZFjdiQ9lQg7iwbBzgBWN1rgELepmLk2zPw==\n-----END EC PRIVATE KEY-----\n"

client = RESTClient(api_key=API_KEY, api_secret=API_SECRET) # Coinbase REST client that uses the above keys

# ==========================
# HYBRID PROTECTION SETTINGS
# ==========================

MAX_EXPOSURE_USD = 5.00     # max USD value of ETH holdings before blocking buys
USD_BUFFER = 1.00         # min USD balance to keep free
BUY_COOLDOWN_SECONDS = 180  # min seconds between buys
VARIANCE_DROP_REQUIRED = 0.010 # min drop in variance since last buy to allow new buy

last_buy_time = 0 # timestamp of last buy
last_buy_price = None # price at last buy is none before first buy

# ==========================
# PRICE FETCHING
# ==========================
# Fetches the current price of ETH-USD from Coinbase API
def get_current_price():
    url = f"https://api.coinbase.com/v2/prices/{ASSET}/spot"
    r = requests.get(url).json()
    return float(r["data"]["amount"])

# ==========================
# BALANCE FETCHING (STUBS)
# ==========================

def get_usd_balance():
    return 8.50  # replace with real balance logic

def get_eth_balance():
    return 0.00    # replace with real balance logic

# ==========================
# OPEN ORDER CHECKING
# ==========================

def get_open_orders(): #fetches open orders for the asset
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

def can_buy_eth(current_price): #determine if buying ETH is allowed under hybrid protection rules
    global last_buy_time, last_buy_price

    usd_balance = get_usd_balance() #fetch USD balance
    eth_balance = get_eth_balance() #fetch ETH balance

    eth_value = eth_balance * current_price #total value of ETH holdings in USD
    if eth_value >= MAX_EXPOSURE_USD: #exposure cap check
        return False, "Exposure cap reached" # block buy

    if usd_balance - current_price < USD_BUFFER: # ensure USD buffer
        return False, "USD buffer protection triggered" # block buy

    if time.time() - last_buy_time < BUY_COOLDOWN_SECONDS: # cooldown check
        return False, "Cooldown active" # block buy

    if last_buy_price is not None: # variance drop check
        drop = (last_buy_price - current_price) / last_buy_price # calculate variance drop since last buy
        if drop < VARIANCE_DROP_REQUIRED: # not enough drop
            return False, "Variance drop not enough" # block buy

    return True, "Buy allowed" # all checks passed, allow buy

def record_buy(price): #record the time and price of the last buy
    global last_buy_time, last_buy_price # global variables
    last_buy_time = time.time() # update last buy time
    last_buy_price = price # update last buy price

# ==========================
# EMA CALCULATION
# ==========================

def calculate_ema(prices, window=20): #calculate the Exponential Moving Average (EMA) for a list of prices
    if len(prices) < window:
        return None

    k = 2 / (window + 1) #smoothing factor
    ema = prices[0]

    for price in prices[1:]: #calculate EMA iteratively
        ema = (price * k) + (ema * (1 - k))

    return ema #return the final EMA value

# ==========================
# DYNAMIC VARIANCE ENGINE
# ==========================

variance_window = deque(maxlen=50) #track recent variance values for dynamic thresholding

def get_variance_direction(): #determine the direction of variance changes
    if len(variance_window) < 5: # not enough data
        return "flat"
    recent = list(variance_window) # get recent variance values
    if recent[-1] > recent[-5]: # rising trend
        return "rising"
    elif recent[-1] < recent[-5]: # shrinking trend
        return "shrinking"
    else:
        return "flat" # stable trend

def update_dynamic_thresholds(): #adjust buy/sell variance thresholds based on recent variance trends
    global BUY_VARIANCE, SELL_VARIANCE # global thresholds

    if len(variance_window) < 10: # not enough data
        BUY_VARIANCE = BASE_BUY_VARIANCE # reset to base
        SELL_VARIANCE = BASE_SELL_VARIANCE # reset to base
        return

    direction = get_variance_direction() # get current variance direction

    if direction == "rising": # adjust thresholds based on trend
        # momentum building: take profit earlier, buy more cautiously
        SELL_VARIANCE = BASE_SELL_VARIANCE * 0.85 # sell sooner
        BUY_VARIANCE = BASE_BUY_VARIANCE * 1.2 # buy later
    elif direction == "shrinking": 
        # momentum fading: wait more to sell, buy a bit earlier
        SELL_VARIANCE = BASE_SELL_VARIANCE * 1.25 # sell later
        BUY_VARIANCE = BASE_BUY_VARIANCE * 0.8 # buy sooner
    else:
        SELL_VARIANCE = BASE_SELL_VARIANCE # reset to base
        BUY_VARIANCE = BASE_BUY_VARIANCE # reset to base

# ==========================
# ORDER EXECUTION
# ==========================

def place_buy_order(price): # place a buy order for ETH at the given price
    if DRY_RUN: # dry run mode
        msg = f"[DRY RUN] BUY ETH at {price}" # message
        print(msg)
        send_discord(LOGS_WEBHOOK, msg) # send to logs webhook discord
    else:
        msg = f"Executing BUY ETH at {price}" # real buy message
        print(msg)
        send_discord(ALERTS_WEBHOOK, msg) # send to alerts webhook discord

    record_buy(price) # record the buy

def place_sell_order(price): # place a sell order for ETH at the given price
    if DRY_RUN: # dry run mode
        msg = f"[DRY RUN] SELL ETH at {price}" # dry run message
        print(msg)
        send_discord(LOGS_WEBHOOK, msg)
    else: # real sell
        msg = f"Executing SELL ETH at {price}" # real sell message
        print(msg)
        send_discord(ALERTS_WEBHOOK, msg)

# ==========================
# MAIN LOOP
# ==========================

def run_bot(): # main bot function
    prices = []
    # header message
    header = (
        "Starting EMA-Variance Trading Bot...\n"
        f"Asset: {ASSET}\n"
        f"EMA Window: {WINDOW}\n"
        f"Base Buy Trigger: {BASE_BUY_VARIANCE}%\n"
        f"Base Sell Trigger: {BASE_SELL_VARIANCE}%\n"
        f"DRY RUN: {DRY_RUN}\n"
        "----------------------------------------"
    )

    print(header)
    send_discord(LOGS_WEBHOOK, "ETH bot started successfully.") # notify startup

    while True: # main loop
        try:
            open_orders = get_open_orders() # check for open orders

            if open_orders: # open orders exist
                msg = "[WAIT] Unprocessed ETH orders detected. Bot pausing." # wait message
                print(msg)
                send_discord(LOGS_WEBHOOK, msg)
                time.sleep(10) # timeout before retrying
                continue

            current_price = get_current_price() # fetch current price
            prices.append(current_price) # add to price history 

            if len(prices) > WINDOW: # maintain fixed window size
                prices.pop(0) # remove oldest price

            ema = calculate_ema(prices, WINDOW) # calculate EMA

            if ema is None: # not enough data for EMA
                print("Collecting data for EMA...") # info message
                time.sleep(SLEEP_TIME) 
                continue # wait for more data

            variance = (current_price - ema) / ema * 100  # calculate variance in percent
            variance_window.append(variance) # track variance history

            # dynamic thresholds
            update_dynamic_thresholds()
            # log current status
            log_msg = (
                f"ETH Price: {current_price:.4f} | EMA20: {ema:.4f} | " # log message
                f"Var: {variance:.2f}% | BUY: {BUY_VARIANCE:.2f}% | SELL: {SELL_VARIANCE:.2f}%" # log message continued
            )
            print(log_msg) # print log
            send_discord(LOGS_WEBHOOK, log_msg) # send to logs webhook

            if variance <= BUY_VARIANCE: # check buy condition
                allowed, reason = can_buy_eth(current_price) # check hybrid protection
                if allowed: # buy allowed
                    send_discord(ALERTS_WEBHOOK, f"BUY signal triggered at {current_price}") # send buy alert
                    place_buy_order(current_price) # place buy order
                else: # buy blocked
                    send_discord(LOGS_WEBHOOK, f"BUY blocked: {reason}") # log reason

            elif variance >= SELL_VARIANCE: # check sell condition
                send_discord(ALERTS_WEBHOOK, f"SELL signal triggered at {current_price}") # send sell alert
                place_sell_order(current_price) # place sell order

            time.sleep(SLEEP_TIME) # wait before next cycle

        except Exception as e: # catch all errors
            err = f"[ERROR] {e}" # error message
            print(err) # print error
            send_discord(ALERTS_WEBHOOK, err) # send error to alerts webhook
            time.sleep(5) # short wait before retrying

# ==========================
# START BOT
# ==========================

run_bot() #start the bot
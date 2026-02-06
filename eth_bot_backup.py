import time
import requests
from collections import deque
from coinbase.rest import RESTClient
import uuid

# -----------------------------------
# Global State Variables
# -----------------------------------

last_buy_time = 0
last_buy_price = None

RECENT_WINS = 0
RECENT_LOSSES = 0
TOTAL_WINS = 0
TOTAL_LOSSES = 0

# ==========================
# DISCORD WEBHOOKS
# ==========================

LOGS_WEBHOOK = "https://discord.com/api/webhooks/1468763706790904043/xzz6sf444VJLnHiU4IOhJvnMRJCqCReZ-etjlMUZu3OFI0c3wXaHpIRjwjGrH1L_q5W-"
ALERTS_WEBHOOK = "https://discord.com/api/webhooks/1468763873371750484/PrO5AkeJ9Eu4PhY_68zmkkgaFKiaPORg7misrXx-16vkXdcjrciEcR0AIWU_Qjb5KL3z"


def send_discord(webhook, message):
    try:
        requests.post(webhook, json={"content": message})
    except Exception as e:
        print(f"[DISCORD ERROR] {e}")



# ==========================
# COINBASE API CONFIG
# ==========================

API_KEY = ""
API_SECRET = ""

client = RESTClient(api_key=API_KEY, api_secret=API_SECRET)

# ==========================
# BOT CONFIG
# ==========================

ASSET = "ETH-USD"   # trading pair used throughout the bot
BUY_COOLDOWN_SECONDS = 180 
VARIANCE_DROP_REQUIRED = 0.005
DRY_RUN = False
WINDOW = 20
SLEEP_TIME = 15
 


# ==========================
# UNIFIED VARIANCE ENGINE (ETH‑TUNED)
# ==========================

class UnifiedVarianceEngine:
    def __init__(
        self,
        base_buy=-0.0065,      # -0.65%
        base_sell=0.0075,      # +0.75%
        window=12,
        buy_multiplier=14,
        sell_multiplier=12,
        buy_clamp=(-0.012, -0.005),   # -1.2% to -0.5%
        sell_clamp=(0.006, 0.014)     # +0.6% to +1.4%
    ):
        self.base_buy = base_buy
        self.base_sell = base_sell
        self.window = window

        self.buy_multiplier = buy_multiplier
        self.sell_multiplier = sell_multiplier

        self.buy_clamp_min, self.buy_clamp_max = buy_clamp
        self.sell_clamp_min, self.sell_clamp_max = sell_clamp

        self.recent_variances = deque(maxlen=window)

    def clamp(self, value, min_v, max_v):
        return max(min_v, min(value, max_v))

    def get_direction(self):
        if len(self.recent_variances) < 4:
            return "flat"

        last = self.recent_variances[-1]
        prev = self.recent_variances[-4]

        if last > prev:
            return "rising"
        elif last < prev:
            return "shrinking"
        return "flat"

    def update(self, current_variance, current_exposure_pct):
        self.recent_variances.append(current_variance)

        if len(self.recent_variances) < 3:
            return self.base_buy, self.base_sell

        # 1. VOLATILITY SCALING
        avg_var = sum(abs(v) for v in self.recent_variances) / len(self.recent_variances)

        buy_var = self.base_buy * (1 + avg_var * self.buy_multiplier)
        sell_var = self.base_sell * (1 + avg_var * self.sell_multiplier)

        # 2. DIRECTION ADJUSTMENT
        direction = self.get_direction()

        if direction == "rising":
            buy_var *= 1.20
            sell_var *= 0.80
        elif direction == "shrinking":
            buy_var *= 0.80
            sell_var *= 1.20

        # 3. EXPOSURE WEIGHTING
        buy_var *= (1 + current_exposure_pct)

        # 4. FINAL CLAMP
        buy_var = self.clamp(buy_var, self.buy_clamp_min, self.buy_clamp_max)
        sell_var = self.clamp(sell_var, self.sell_clamp_min, self.sell_clamp_max)

        return buy_var, sell_var


ENGINE = UnifiedVarianceEngine()


# ==========================
# HYBRID PROTECTION SETTINGS
# ==========================

# -----------------------------
# Adaptive risk + behavior engine
# -----------------------------

def get_adaptive_exposure(balance):
    EXPOSURE_PERCENT = 0.30      # 30% of balance
    MIN_EXPOSURE = 3.00          # never go below this
    MAX_EXPOSURE = 25.00         # absolute ceiling

    adaptive = balance * EXPOSURE_PERCENT
    return max(MIN_EXPOSURE, min(adaptive, MAX_EXPOSURE))


def get_adaptive_buffer(balance):
    BUFFER_PERCENT = 0.08        # 8% of balance
    MIN_BUFFER = 1.00            # never keep less than $1
    MAX_BUFFER = 5.00            # never keep more than $5

    adaptive = balance * BUFFER_PERCENT
    return max(MIN_BUFFER, min(adaptive, MAX_BUFFER))


def get_adaptive_cooldown(balance):
    # Faster when small, slower when large
    if balance < 25:
        return 20          # seconds
    elif balance < 100:
        return 45
    else:
        return 90


def get_adaptive_variance(balance):
    # Range: 0.4% to 1.0%
    MIN_VAR = 0.004        # 0.4%
    MAX_VAR = 0.010        # 1.0%

    # Scale variance based on balance (caps at 100 USD)
    scale = min(balance / 100.0, 1.0)
    return MIN_VAR + (MAX_VAR - MIN_VAR) * scale



def get_adaptive_sell_threshold(balance):
    MIN_SELL = 0.008   # 0.8%
    MAX_SELL = 0.020   # 2.0%

    # Scale based on balance (caps at 100 USD)
    scale = min(balance / 100.0, 1.0)

    return MIN_SELL + (MAX_SELL - MIN_SELL) * scale

def get_adaptive_buy_threshold(balance):
    MIN_BUY = -0.003   # -0.3%
    MAX_BUY = -0.010   # -1.0%

    # Scale based on balance (caps at 100 USD)
    scale = min(balance / 100.0, 1.0)

    return MIN_BUY + (MAX_BUY - MIN_BUY) * scale

def get_adaptive_buy_size(total_equity, variance, recent_wins, recent_losses):
    # Base: 10% of equity
    BASE_PCT = 0.10

    # Volatility factor (lower volatility = bigger buys)
    # variance is usually between -0.02 and +0.02
    vol_factor = max(0.5, min(1.5, 1 - abs(variance) * 10))

    # Trend factor (winning streak = confidence)
    trend_factor = 1 + (recent_wins * 0.05) - (recent_losses * 0.05)
    trend_factor = max(0.7, min(1.3, trend_factor))

    # Combine
    pct = BASE_PCT * vol_factor * trend_factor

    # Cap at 20% of equity
    pct = min(pct, 0.20)

    return total_equity * pct




last_buy_time = 0
last_buy_price = None


# ==========================
# PRICE & BALANCE
# ==========================

def get_current_price():
    url = f"https://api.coinbase.com/v2/prices/{ASSET}/spot"
    r = requests.get(url).json()
    return float(r["data"]["amount"])


def get_usd_balance():
    # TODO: replace with real Coinbase balance call
    return 50.00


def get_eth_balance():
    # TODO: replace with real Coinbase balance call
    return 0.05


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

def can_buy_eth(current_price):
    global last_buy_time, last_buy_price

    usd_balance = get_usd_balance()
    eth_balance = get_eth_balance()

    # --- ADAPTIVE LIMITS ---
    max_exposure_usd = get_adaptive_exposure(usd_balance)
    usd_buffer = get_adaptive_buffer(usd_balance)

    # --- EXPOSURE CAP ---
    eth_value = eth_balance * current_price
    if eth_value >= max_exposure_usd:
        return False, "Exposure cap reached"

    # --- USD BUFFER PROTECTION ---
    if usd_balance - current_price < usd_buffer:
        return False, "USD buffer protection triggered"

    # --- COOLDOWN ---
    if time.time() - last_buy_time < BUY_COOLDOWN_SECONDS:
        return False, "Cooldown active"

    # --- VARIANCE DROP CHECK ---
    if last_buy_price is not None:
        drop = (last_buy_price - current_price) / last_buy_price
        if drop < VARIANCE_DROP_REQUIRED:
            return False, "Variance drop not enough"

    return True, "OK to buy"

def record_buy(price):
    global last_buy_time, last_buy_price
    last_buy_time = time.time()
    last_buy_price = price
    
def record_sell(price):
    global RECENT_WINS, RECENT_LOSSES, TOTAL_WINS, TOTAL_LOSSES, last_buy_price

    if last_buy_price is None:
        return  # No previous buy to compare

    pnl = price - last_buy_price

    if pnl > 0:
        RECENT_WINS += 1
        TOTAL_WINS += 1
        send_discord(ALERTS_WEBHOOK, f"WIN +${pnl:.2f} | Total: {TOTAL_WINS}W / {TOTAL_LOSSES}L")
    else:
        RECENT_LOSSES += 1
        TOTAL_LOSSES += 1
        send_discord(ALERTS_WEBHOOK, f"LOSS ${pnl:.2f} | Total: {TOTAL_WINS}W / {TOTAL_LOSSES}L")

    # Reset buy price after evaluating
    last_buy_price = None

def send_performance_update(current_price):
    usd_balance = get_usd_balance()
    eth_balance = get_eth_balance()
    eth_value = eth_balance * current_price
    total_equity = usd_balance + eth_value

    exposure_pct = (eth_value / total_equity * 100) if total_equity > 0 else 0

    total_trades = TOTAL_WINS + TOTAL_LOSSES
    win_rate = (TOTAL_WINS / total_trades * 100) if total_trades > 0 else 0

    # Recent streak label
    if RECENT_WINS > 0:
        streak = f"{RECENT_WINS}W"
    elif RECENT_LOSSES > 0:
        streak = f"{RECENT_LOSSES}L"
    else:
        streak = "None"

    msg = (
        "📊 **Performance Update**\n"
        f"Equity: **${total_equity:.2f}**\n"
        f"USD: ${usd_balance:.2f} | ETH Value: ${eth_value:.2f}\n"
        f"Exposure: **{exposure_pct:.1f}%**\n"
        f"Wins: {TOTAL_WINS} | Losses: {TOTAL_LOSSES}\n"
        f"Win Rate: **{win_rate:.1f}%**\n"
        f"Recent Streak: **{streak}**"
    )

    send_discord(ALERTS_WEBHOOK, msg)


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
# ORDER ID GENERATOR
# ==========================

def generate_client_order_id():
    return f"bot_{int(time.time() * 1000)}"


# ==========================
# ORDER EXECUTION (REAL COINBASE CALLS)
# ==========================

def place_buy_order(amount_usd):
    try:
        client_order_id = generate_client_order_id()

        order = client.create_order(
            client_order_id=client_order_id,
            product_id=ASSET,
            side="BUY",
            order_configuration={
                "market_market_ioc": {
                    "quote_size": str(amount_usd)
                }
            }
        )

        send_discord(LOGS_WEBHOOK, f"[BUY] ${amount_usd} market buy placed.")
        return True, "Buy order placed"

    except Exception as e:
        send_discord(ALERTS_WEBHOOK, f"[ERROR] Buy order failed: {e}")
        return False, str(e)


def place_sell_order(price):
    eth_balance = get_eth_balance()

    if eth_balance <= 0:
        send_discord(LOGS_WEBHOOK, "[SELL BLOCKED] No ETH to sell.")
        return

    if DRY_RUN:
        msg = f"[DRY RUN] SELL {eth_balance:.6f} ETH at {price}"
        print(msg)
        send_discord(LOGS_WEBHOOK, msg)
    else:
        try:
            msg = f"Executing SELL {eth_balance:.6f} ETH at {price}"
            print(msg)
            send_discord(ALERTS_WEBHOOK, msg)

            # 🔥 FIX: Coinbase requires a client_order_id
            client.market_order_sell(
                client_order_id=str(uuid.uuid4()),
                product_id="ETH-USD",
                base_size=str(eth_balance)
            )

        except Exception as e:
            send_discord(ALERTS_WEBHOOK, f"[SELL ERROR] {e}")
            return
        # 🔥 NEW: Track win/loss automatically
        record_sell(price)
        send_performance_update(price)



def place_sell_order(price):
    eth_balance = get_eth_balance()

    if eth_balance <= 0:
        send_discord(LOGS_WEBHOOK, "[SELL BLOCKED] No ETH to sell.")
        return

    if DRY_RUN:
        msg = f"[DRY RUN] SELL {eth_balance:.6f} ETH at {price}"
        print(msg)
        send_discord(LOGS_WEBHOOK, msg)
    else:
        try:
            msg = f"Executing SELL {eth_balance:.6f} ETH at {price}"
            print(msg)
            send_discord(ALERTS_WEBHOOK, msg)

            client.market_order_sell(
                product_id="ETH-USD",
                base_size=str(eth_balance)
            )

        except Exception as e:
            send_discord(ALERTS_WEBHOOK, f"[SELL ERROR] {e}")
            return


# ==========================
# MAIN LOOP
# ==========================

def run_bot():
    prices = []

    header = (
        "Starting EMA + UnifiedVarianceEngine ETH Bot...\n"
        f"Asset: {ASSET}\n"
        f"EMA Window: {WINDOW}\n"
        f"DRY RUN: {DRY_RUN}\n"
        "----------------------------------------"
    )

    print(header)
    send_discord(LOGS_WEBHOOK, "ETH bot started successfully.")

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

            variance = (current_price - ema) / ema

            usd_balance = get_usd_balance()
            eth_balance = get_eth_balance()
            eth_value = eth_balance * current_price
            total_equity = usd_balance + eth_value
            exposure_pct = eth_value / total_equity if total_equity > 0 else 0.0
            
            ADAPTIVE_BUY_THRESHOLD = get_adaptive_buy_threshold(total_equity)

            BUY_SIZE_USD = get_adaptive_buy_size(
            total_equity=total_equity,
            variance=variance,
            recent_wins=RECENT_WINS,
            recent_losses=RECENT_LOSSES
)
            
            MAX_EXPOSURE_USD = get_adaptive_exposure(total_equity)
            USD_BUFFER = get_adaptive_buffer(total_equity)
            BUY_COOLDOWN_SECONDS = get_adaptive_cooldown(total_equity)
            VARIANCE_DROP_REQUIRED = get_adaptive_variance(total_equity)
            ADAPTIVE_SELL_THRESHOLD = get_adaptive_sell_threshold(total_equity)
            ADAPTIVE_SELL_THRESHOLD = get_adaptive_sell_threshold(total_equity)




            buy_var, sell_var = ENGINE.update(
                current_variance=variance,
                current_exposure_pct=exposure_pct
            )

            log_msg = (
                f"ETH Price: {current_price:.4f} | EMA{WINDOW}: {ema:.4f} | "
                f"Var: {variance*100:.2f}% | "
                f"BUY_TH: {buy_var*100:.2f}% | SELL_TH: {sell_var*100:.2f}% | "
                f"Exposure: {exposure_pct:.2f}"
            )
            print(log_msg)
            send_discord(LOGS_WEBHOOK, log_msg)

            if variance <= buy_var:
                allowed, reason = can_buy_eth(current_price)
                if allowed:
                    send_discord(ALERTS_WEBHOOK, f"BUY signal triggered at {current_price}")
                    place_buy_order(current_price)
                else:
                    send_discord(LOGS_WEBHOOK, f"BUY blocked: {reason}")

            elif variance >= sell_var and eth_balance > 0:
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

if __name__ == "__main__":

    run_bot()

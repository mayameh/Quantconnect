# Native Trading Bot — ib_insync + IB Gateway (Headless VPS)

## Architecture

```
┌──────────────────────────┐
│     Your Laptop          │
│  (SSH / Dev / Monitor)   │
└────────────┬─────────────┘
             │ SSH (secure access)
             ▼
┌──────────────────────────────────────────────────────────┐
│  VPS / Ubuntu Server                                     │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │  Trading Layer (Python)                            │  │
│  │  main_ib.py + ib_insync                            │  │
│  │  - strategy logic (regime / MACD / RSI / EMA)      │  │
│  │  - order management                                │  │
│  │  - risk controls / circuit breakers                │  │
│  │  - email alerts                                    │  │
│  │  Runs as: tradingbot.service (systemd)             │  │
│  └───────────────┬────────────────────────────────────┘  │
│                  │ API (localhost:4002)                   │
│                  ▼                                        │
│  ┌────────────────────────────────────────────────────┐  │
│  │  IB Gateway (Headless Runtime)          [EXISTING] │  │
│  │  - API endpoint on :4002 (paper) / :4001 (live)    │  │
│  │  - session with IB servers                         │  │
│  │  - executes trades                                 │  │
│  │  Runs on: DISPLAY :1 (Xvfb virtual screen)        │  │
│  └───────────────┬────────────────────────────────────┘  │
│                  │                                        │
│  ┌────────────────────────────────────────────────────┐  │
│  │  IBC (IB Controller)                    [EXISTING] │  │
│  │  - auto login / handles dialogs / sets API config  │  │
│  └───────────────┬────────────────────────────────────┘  │
│                  │                                        │
│  ┌────────────────────────────────────────────────────┐  │
│  │  systemd (ibgateway.service)            [EXISTING] │  │
│  │  - auto start on boot / auto restart on crash      │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  ┌──────────────────┐  ┌───────────────────────────┐     │
│  │ Xvfb :1          │  │ VNC :2 (debug only)       │     │
│  │ virtual display   │  │ not needed in production  │     │
│  └──────────────────┘  └───────────────────────────┘     │
└──────────────────────────────────────────────────────────┘
             │
             ▼
┌──────────────────────────┐
│  Interactive Brokers      │
│  Servers (IBKR)           │
└──────────────────────────┘
```

**Your IB Gateway, IBC, Xvfb, and systemd are already set up and running.**
The only new piece is the **Trading Layer** (`main_ib.py` + `tradingbot.service`).

---

## Quick Start

### 1. Deploy bot files to VPS

```bash
# From your laptop
scp main_ib.py bot_config.py requirements.txt .env.bot tradingbot.service user@your-vps:/opt/tradingbot/
```

### 2. Set up Python environment on VPS

```bash
ssh user@your-vps
cd /opt/tradingbot

# Create venv
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.bot .env
nano .env   # Set IB_PORT, email password, etc.
```

### 4. Test manually first

```bash
cd /opt/tradingbot
source venv/bin/activate
python3 main_ib.py
# Watch for: "Connected. Account: ..." and "TRADING BOT STARTED"
# Ctrl+C to stop
```

### 5. Install systemd service

```bash
sudo cp tradingbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable tradingbot
sudo systemctl start tradingbot
```

### 6. Monitor

```bash
# Bot logs (systemd journal)
journalctl -u tradingbot -f

# Bot logs (file)
tail -f /home/ibtrade/trading/tradingbot/logs/tradingbot.log

# IB Gateway (your existing service)
journalctl -u ibgateway -f

# Service status
sudo systemctl status tradingbot
sudo systemctl status ibgateway
```

---

## File Structure on VPS

```
/opt/tradingbot/
├── main_ib.py              # Trading bot (ib_insync)
├── bot_config.py           # Configuration
├── requirements.txt        # Python dependencies
├── .env                    # Secrets (email password, port)
├── venv/                   # Python virtual environment
└── logs/
    └── tradingbot.log      # Rotating log file (10MB × 5)
```

Your existing IB infrastructure (unchanged):
```
/opt/ibgateway/             # IB Gateway installation
/opt/ibc/                   # IBC installation
/etc/systemd/system/
├── ibgateway.service       # Your existing IB Gateway service
└── tradingbot.service      # NEW: the trading bot
```

---

## Python Packages Required

| Package | Purpose |
|---------|---------|
| **ib_insync** | IB Gateway API client (async, thread-safe) |
| **pandas** | Data manipulation for indicator computation |
| **pandas_ta** | Technical indicators (MACD, RSI, EMA) — no TA-Lib C dep |
| **APScheduler** | Cron-style job scheduling (US/Eastern timezone) |
| **pytz** | Timezone handling |

Install all at once:
```bash
pip install ib_insync pandas pandas_ta "APScheduler>=3.10,<4" pytz
```

No Java, no Docker, no .NET, no LEAN engine needed. Pure Python.

---

## Strategy Logic (preserved from QC version)

| Feature | Implementation |
|---------|---------------|
| **Regime Detection** | SPY daily EMA20/EMA50 + RSI14 → BULL/BEAR/NEUTRAL |
| **Entry Signal** | Hourly MACD crossover + RSI 45-65 + price > EMA50 |
| **Exit: Stop Loss** | -3% from average cost |
| **Exit: Take Profit** | +8% from average cost |
| **Exit: Profit Lock** | +1.8% after 30 hours held |
| **Exit: Time Exit** | 14 days max hold |
| **Exit: Gap Protection** | -5% emergency sell |
| **Position Sizing** | 75% cash × 85%, max $6,500, min $4,000 |
| **Max Positions** | 4 (BULL+profit), 3 (BULL), 2 (NEUTRAL), 0 (BEAR) |
| **Universe** | NVDA, AAPL, MSFT, AMZN, META (core) |
| **Schedule** | 09:25/12:00/15:00 regime, 09:30/12:30/15:30 signals |
| **Emails** | Portfolio summary 4×/day + weekly on Friday 16:00 |

---

## Schedule (all times US/Eastern, Mon-Fri)

| Time | Action |
|------|--------|
| 09:25 | Detect market regime |
| 09:30 | Evaluate signals (entries + exits) |
| 09:35 | Send portfolio summary email |
| 12:00 | Detect market regime |
| 12:30 | Evaluate signals + send email |
| 15:00 | Detect market regime |
| 15:30 | Evaluate signals + send email |
| 16:00 | Daily risk summary + send email |
| Fri 16:00 | Weekly performance summary email |

---

## Switching Paper → Live

1. Edit `/opt/tradingbot/.env`:
   ```
   IB_PORT=4001
   ```

2. Update your IBC config to `TradingMode=live`, `OverrideTwsApiPort=4001`

3. Restart both services:
   ```bash
   sudo systemctl restart ibgateway
   sudo systemctl restart tradingbot
   ```

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| "Connection refused on 4002" | Verify gateway: `netstat -tlnp \| grep 4002` |
| Bot can't get market data | Need IB market data subscription for US stocks |
| "Insufficient bars" for indicators | Normal on first run before market opens; bars fill during RTH |
| 2FA timeout | Approve on IBKR Mobile within 40s |
| Bot crashes at Sunday restart | IBC handles gateway restart; bot auto-reconnects |
| "No security definition" | Symbol not available — check IB contract search |
| Stale prices / NaN | Streaming data delay; bot falls back to last close |

---

## Security Notes

- **Never commit `.env`** to git — it may contain email credentials
- IB Gateway ports (4001/4002) should be **localhost only** (firewall)
- VNC (:2) should be **SSH-tunneled** or disabled in production
- Email password should be a **Gmail App Password**, not your account password
- Bot runs as a dedicated `trader` user — not root

# Three-Sleeve Hybrid Strategy: Alpaca Port

This folder contains an Alpaca-native port of the original QuantConnect Lean
algorithm in `main.py`.

## What The Strategy Does

The algorithm allocates across three sleeves:

- `S1`: macro hedge sleeve. In live Alpaca mode it trades `BRK.B` and `NEM`
  while reading SPY/GLD/VIX-style instruments as signals.
- `S2`: value-plus-momentum equity sleeve. QuantConnect used Morningstar fine
  fundamentals; Alpaca does not expose that universe, so this port uses a
  configurable candidate list and ranks by 63-day momentum.
- `S3`: strong-bull large-cap momentum sleeve. It turns on only when SPY is
  above its 50-day and 200-day averages, 20-day SPY return is positive, VIX is
  below its 80th percentile, and VIX is below 25.

When the strong-bull gate is on, the target mix is:

- `S3`: 80%
- `S1`: 20%, split 75/25 between market proxy and gold proxy
- `S2`: 0%

When the strong-bull gate is off, the strategy runs `S1 + S2`:

- stress regimes can switch S2 off
- calm/trend regimes allow S2
- S3 is liquidated

## Files

- `main.py`: original QuantConnect Lean algorithm.
- `main_alpaca.py`: Alpaca live/paper runner for VPS deployment.
- `backtest_harness.py`: Alpaca historical-data replay harness, with optional yfinance fallback.
- `bot_config.py`: environment-driven settings and candidate universes.
- `strategy_core.py`: shared signal, feature, and indicator helpers.
- `.env.template`: environment variable template.
- `requirements.txt`: Python dependencies.

## VPS Setup

```bash
cd /path/to/QuantConnect/tradingBOT-3SleeveHybridStrategy
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.template .env
```

Fill `.env`, then export it before running, for example:

```bash
set -a
. ./.env
set +a
python main_alpaca.py
```

For email alerts, set:

```bash
BOT_EMAIL_ENABLED=true
BOT_EMAIL_USER=you@gmail.com
BOT_EMAIL_PASS="your gmail app password"
BOT_EMAIL_TO=destination@example.com
```

The live runner sends a daily portfolio email near the close and a weekly
summary after Friday close. If the configured log directory cannot be created,
the bot falls back to a local `logs/` directory. Market orders are guarded by
Alpaca's market clock, so startup outside market hours will not place orders.

## Backtest

```bash
python backtest_harness.py --start 2016-01-01 --end 2021-01-01 --capital 100000
```

The harness uses Alpaca historical daily stock bars by default, using
`APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` from your environment. VIX/VIX3M
still come from CBOE CSV files because they are index signals, not Alpaca stock
bars.

For the old yfinance behavior:

```bash
python backtest_harness.py --data-provider yfinance --start 2016-01-01 --end 2021-01-01 --capital 100000
```

Use `--live-hedges` to test BRK.B/NEM for S1 instead of SPY/GLD.

## Important Porting Notes

QuantConnect’s Morningstar fundamentals and CBOE custom data subscriptions are
not available through Alpaca in the same form. This port therefore:

- fetches VIX/VIX3M from CBOE CSV endpoints in live/paper mode;
- uses static, configurable S2/S3 candidate lists instead of QC fine universe
  selection;
- uses fractional-share market orders, which Alpaca supports for eligible US
  equities;
- schedules jobs in US/Eastern with APScheduler.

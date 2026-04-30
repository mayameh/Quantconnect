# IB Bot Backtest — Quick Start Guide

## What You Have

A lightweight Python backtest framework for validating the IB bot's trading logic **without QuantConnect infrastructure**.

Three new files in `vm-deploy/`:
- **mock_ib.py** – Stubs the IB Gateway connection (orders, positions, market data)
- **backtest_harness.py** – Replays historical data and executes bot signals
- **backtest_runner.py** – Simpler data loader for minimal backtests
- **validate_backtest.py** – Validation script to ensure everything works

## Quick Start (2 minutes)

### 1. Run Full Year Backtest

```bash
cd ~/trading/tradingbot/vm-deploy

python backtest_harness.py
```

**What happens:**
- Downloads 1 year of daily OHLCV for 5 core symbols (NVDA, AAPL, MSFT, AMZN, META)
- Simulates entry signals (price > 50-SMA)
- Simulates exit signals (price < 50-SMA, stop loss -3%, take profit +8%)
- Outputs comprehensive report with P&L, Sharpe ratio, trade log

**Expected output:**
```
Running backtest: 2024-01-01 → 2024-12-31
Symbols: NVDA, AAPL, MSFT, AMZN, META
Starting Capital: $11,000.00

Loading 5 symbols...
  ✓ NVDA: 252 bars loaded
  ✓ AAPL: 252 bars loaded
  ...

Simulating trades...
  [2024-01-09] BUY 3x NVDA @ $505.82
  [2024-02-14] SELL 3x NVDA @ $542.90
  [2024-03-05] BUY 2x AAPL @ $182.45
  ...

======================================================================
BACKTEST REPORT
======================================================================

CAPITAL SUMMARY:
  Starting Cash:      $11,000.00
  Ending Equity:      $12,450.00
  Total Return:       +13.18%
  Max Drawdown:       8.45%
  Sharpe Ratio:       1.23

TRADE SUMMARY:
  Total Trades:       28
  Buy Orders:         14
  Sell Orders:        14
  Win Rate:           64.3%
```

### 2. Customize Backtest

Edit `backtest_harness.py` **`__main__` section** (lines 365–375):

```python
if __name__ == "__main__":
    config = BOT_Config()
    
    # Example: Backtest with 10 symbols, different date range
    symbols = config.universe.candidate_symbols[:10]  # First 10 candidates
    
    harness = FullBacktestHarness(
        symbols=symbols,
        start_date="2023-06-01",      # Any date range
        end_date="2023-12-31",
        starting_cash=20_000          # Any starting capital
    )
    
    harness.run_backtest()
    results = harness.report()
```

Then run:
```bash
python backtest_harness.py
```

## Understanding Results

| Metric | What It Means | Target |
|--------|---------------|--------|
| **Total Return** | Ending equity / Starting equity - 1 | > 0% (profit) |
| **Max Drawdown** | Worst peak-to-trough decline | < 20% |
| **Sharpe Ratio** | Risk-adjusted return (daily volatility adjusted) | > 1.0 |
| **Win Rate** | % of sell orders that were profitable | > 50% |

**Good backtest:** +10% return, 8% max drawdown, 1.2 Sharpe, 60% win rate

## Signal Logic (Simplified)

### Entry (Buy)
✅ Close price > 50-day SMA  
✅ Position size: 2% of equity  
✅ Max 4 open positions  
✅ Min price: $50, Min volume: $100M daily avg

### Exit (Sell)
❌ Close price < 50-day SMA  
❌ P&L ≤ -3% (stop loss)  
❌ P&L ≥ +8% (take profit)

## Limitations

⚠️ **Simplified signals** — Uses basic 50-SMA, not full bot indicators (MACD, RSI, regime)  
⚠️ **Daily bars only** — No intraday logic or multiple signals per day  
⚠️ **No fees** — IB commissions (~$0.005/share) not included  
⚠️ **No slippage model** — Fills at close, not realistic market microstructure  
⚠️ **Static universe** — Uses core symbols only, not dynamic 14-day refresh  

## Next Steps

### To Test Dynamic Universe
Uncomment/activate the 14-day refresh logic in `backtest_harness.py`:

```python
# TODO: Add dynamic ranking logic before each day's processing
# self._refresh_dynamic_universe_if_due(current_date)
```

### To Test Full Bot Indicators
Extract entry/exit logic from `main_ib.py` into separate `bot_signals.py` module, then import in backtest harness:

```python
from bot_signals import should_enter, should_exit
if should_enter(symbol, main_ib_bot_instance):
    # ... place buy order
```

### To Add Intraday Testing
1. Download 1-hour bars instead of daily
2. Process bars at fixed times (09:30, 12:30, 15:30 ET)
3. Track signals at each time interval

## File Reference

| File | Purpose |
|------|---------|
| `mock_ib.py` | Mock IB Gateway API (qualifyContracts, placeOrder, positions, etc.) |
| `backtest_harness.py` | Full integration: data load → signal → fill → equity tracking |
| `backtest_runner.py` | Simpler framework (just data + replay, no signal logic) |
| `validate_backtest.py` | Quick test to ensure all components work |
| `backtest_README.md` | Detailed documentation (advanced usage) |

## Troubleshooting

**"No data for SYMBOL" warning**
→ Symbol may not exist or no data available for date range. Try different symbol or extend date range back.

**"No trades executed"**
→ Entry signals may be too restrictive. Check 50-SMA condition; try different symbols or date ranges.

**"ModuleNotFoundError: No module named 'yfinance'"**
→ Install deps: `pip install -r requirements.txt`

## Validating Your Setup

Quick 1-second check:
```bash
python validate_backtest.py
```

Output should show:
```
✓ All components working!
```

---

**Next:** Run `python backtest_harness.py` to see results! 📊

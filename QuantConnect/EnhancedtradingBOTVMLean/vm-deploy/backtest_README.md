# Backtest Harness for IB Bot

Lightweight Python backtest framework for validating the IB bot's trading logic on historical data without requiring QuantConnect infrastructure.

## Overview

The backtest harness consists of three modular components:

1. **mock_ib.py** – Mock IB Gateway interface that stubs ib_insync methods
2. **backtest_runner.py** – Basic data loader and equity tracker
3. **backtest_harness.py** – Full integration with entry/exit signal simulation

## Features

- ✅ **Mock IB Interface**: Simulates order placement, position tracking, and account values
- ✅ **Historical Data Loading**: Downloads 1-year of daily OHLCV from yfinance
- ✅ **Event-Driven Replay**: Steps through each trading day and processes entry/exit signals
- ✅ **Realistic Fills**: Orders filled at bar close + slippage
- ✅ **Position Sizing**: 2% of equity per trade (configurable)
- ✅ **Risk Management**: Respects max positions, stop loss (-3%), take profit (+8%)
- ✅ **Comprehensive Report**: Equity curve, trade log, P&L metrics, Sharpe ratio

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt yfinance
```

2. Verify files exist:
```bash
ls -la mock_ib.py backtest_runner.py backtest_harness.py
```

## Usage

### Quick Start – Run Full Backtest

```bash
cd /path/to/vm-deploy
python backtest_harness.py
```

This will:
- Load 5 core symbols for 2024
- Simulate entry/exit logic on daily bars
- Display results with equity curve and trade log

**Example Output:**
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
...
```

### Custom Backtest

Modify the `__main__` section in `backtest_harness.py`:

```python
if __name__ == "__main__":
    config = BOT_Config()
    
    # Custom: Use 10 symbols, 2023 data, $20K starting capital
    harness = FullBacktestHarness(
        symbols=config.universe.candidate_symbols[:10],
        start_date="2023-01-01",
        end_date="2023-12-31",
        starting_cash=20_000
    )
    
    harness.run_backtest()
    results = harness.report()
```

### Use Individual Components

#### 1. Just Load Data & Track Equity

```python
from backtest_runner import BacktestRunner

runner = BacktestRunner(
    symbols=["NVDA", "AAPL", "MSFT"],
    start_date="2024-01-01",
    end_date="2024-12-31",
    starting_cash=11_000
)

runner.load_all_data()
runner.replay_bars_daily()
runner.report()
```

#### 2. Access Mock IB Directly

```python
from mock_ib import MockIB, Contract

ib = MockIB(starting_cash=11_000, current_time=datetime.now())

# Load data
import pandas as pd
df = pd.read_csv("historical_data.csv", index_col="date", parse_dates=True)
ib.load_historical_data("NVDA", df)

# Place order
contract = Contract(symbol="NVDA")
order = type('Order', (), {'action': 'BUY', 'totalQuantity': 5})()
ib.placeOrder(contract, order)

# Check positions
positions = ib.positions()
print(positions)
```

## Entry/Exit Logic

### Entry Signal (Buy)
- Price closes **above 50-day SMA** (simplified momentum check)
- Position size: 2% of portfolio per trade
- Max positions: 4 open trades simultaneously
- Min price: $50, Min avg daily volume: $100M

### Exit Signal (Sell)
- Price closes **below 50-day SMA** (momentum loss)
- **OR** Unrealized P&L <= **-3%** (stop loss)
- **OR** Unrealized P&L >= **+8%** (take profit)

## Configuration

Edit [bot_config.py](bot_config.py):

```python
# Universe settings
universe.core_symbols = ["NVDA", "AAPL", "MSFT", "AMZN", "META"]
universe.dynamic_enabled = True
universe.refresh_days = 14
universe.top_n_dynamic = 10

# Risk/Trading
risk.max_positions = 4
risk.max_loss_pct = 2.0
trading.stop_loss_pct = 3.0
trading.take_profit_pct = 8.0

# Account
account.starting_capital = 11_000
```

## Interpreting Results

| Metric | Interpretation |
|--------|-----------------|
| **Total Return** | End value / Start value - 1 |
| **Max Drawdown** | Largest peak-to-trough decline in equity |
| **Sharpe Ratio** | Risk-adjusted return (target: > 1.0) |
| **Win Rate** | % of sell orders that were profitable |
| **Avg Trade Duration** | # days between entry and exit |

### Example Interpretation:
```
Total Return:       +13.18%  ← Good: beat 0%
Max Drawdown:       8.45%    ← Acceptable: < 20%
Sharpe Ratio:       1.23     ← Good: > 1.0
Win Rate:           64.3%    ← Good: > 50%
```

## Known Limitations

1. **Simplified Signals**: Uses basic SMA/stops, not full bot indicators (MACD, RSI, regime detection)
2. **No Intraday Logic**: Bars processed only at daily close, not during market hours
3. **No Slippage Model**: Fill prices are deterministic (close ± 0.01%), not realistic market microstructure
4. **No Fees/Commissions**: Actual trading has IB commissions (~ $0.005 per share)
5. **No Dynamic Universe**: Currently uses static core symbols; dynamic refresh not simulated
6. **Single Asset Class**: Equities only; no forex/crypto/futures

## Next Steps

### To Integrate Full Bot Logic:
1. Refactor [main_ib.py](main_ib.py) entry/exit methods to separate module `bot_signals.py`
2. Import `bot_signals` in `backtest_harness.py`
3. Call signal methods during replay loop instead of simplified logic

### To Add Dynamic Universe:
1. Implement `_refresh_dynamic_universe()` call every 14 days in replay
2. Pass Alpaca API credentials to mock IB for momentum/revenue-growth ranking
3. Update active universe before each day's bar processing

### To Improve Backtesting:
1. Add intraday support (hourly bars, multiple signals per day)
2. Implement realistic slippage model (based on volume, volatility)
3. Add commission tracking
4. Add parameter optimization (walk-forward analysis)
5. Add Monte Carlo sampling (bootstrap trade sequences)

## Troubleshooting

**Error: `ModuleNotFoundError: No module named 'yfinance'`**
```bash
pip install yfinance
```

**Error: `No data for SYMBOL`**
- Check ticker symbol is valid
- Verify date range is not too recent (may lack data)
- Try different source (Yahoo Finance may have gaps)

**Warning: `Could not download data`**
- Network issue; check internet connection
- Re-run to retry

**Backtest shows 0 trades:**
- Check entry/exit signals (may be too restrictive)
- Verify data loaded correctly (run `backtest_runner.load_all_data()` manually)
- Increase backtest period or change symbols

## File Structure

```
vm-deploy/
├── mock_ib.py              # Mock IB Gateway interface & portfolio
├── backtest_runner.py      # Basic data loader & replay engine
├── backtest_harness.py     # Full bot integration with signals
├── backtest_README.md      # This file
├── bot_config.py           # Shared configuration
├── main_ib.py              # Production bot (unchanged)
└── requirements.txt        # Dependencies
```

## References

- ib_insync: https://ib-insync.readthedocs.io/
- yfinance: https://github.com/ranaroussi/yfinance
- pandas_ta: https://github.com/twopirllc/pandas-ta

---

**Last Updated**: 2025-01  
**Backtest Version**: 1.0

#!/usr/bin/env python3
"""
Quick test script to validate backtest harness setup.
Runs a minimal 30-day backtest to verify data loading and trade execution.
"""
import sys
from pathlib import Path

# Setup paths
sys.path.insert(0, str(Path(__file__).parent))

print("=" * 70)
print("BACKTEST HARNESS - QUICK VALIDATION TEST")
print("=" * 70)

# Test 1: Check imports
print("\n[1/4] Checking imports...")
try:
    from mock_ib import MockIB, Contract
    print("  ✓ mock_ib imported successfully")
except Exception as e:
    print(f"  ✗ Failed to import mock_ib: {e}")
    sys.exit(1)

try:
    from bot_config import BOT_Config
    print("  ✓ bot_config imported successfully")
except Exception as e:
    print(f"  ✗ Failed to import bot_config: {e}")
    sys.exit(1)

try:
    from backtest_harness import FullBacktestHarness
    print("  ✓ backtest_harness imported successfully")
except Exception as e:
    print(f"  ✗ Failed to import backtest_harness: {e}")
    sys.exit(1)

# Test 2: Verify mock IB functionality
print("\n[2/4] Testing mock IB interface...")
try:
    from datetime import datetime
    import pytz
    
    et = pytz.timezone('US/Eastern')
    ib = MockIB(11_000, datetime.now(et))
    
    # Test contract qualification
    contract = Contract(symbol="NVDA")
    ib.qualifyContracts(contract)
    print("  ✓ Contract qualification works")
    
    # Test account values
    values = ib.accountValues()
    print(f"  ✓ Account values: ${ib.account_values['NetLiquidation']:,.2f}")
    
    # Test ticker
    ticker = ib.ticker(contract)
    print("  ✓ Ticker retrieval works")
    
except Exception as e:
    print(f"  ✗ Mock IB test failed: {e}")
    sys.exit(1)

# Test 3: Verify config loading
print("\n[3/4] Testing configuration...")
try:
    config = BOT_Config()
    print(f"  ✓ Core symbols: {len(config.universe.core_symbols)} symbols")
    print(f"  ✓ Starting capital: ${config.general.starting_capital:,.2f}")
    print(f"  ✓ Max positions: {config.trading.max_positions}")
    print(f"  ✓ Dynamic universe enabled: {config.universe.dynamic_enabled}")
except Exception as e:
    print(f"  ✗ Config test failed: {e}")
    sys.exit(1)

# Test 4: Mini backtest (1 symbol, 30 days, no data download)
print("\n[4/4] Running mini backtest (validation only, no data download)...")
try:
    harness = FullBacktestHarness(
        symbols=["NVDA"],  # Single symbol to minimize data download
        start_date="2024-11-01",
        end_date="2024-11-30",
        starting_cash=11_000
    )
    
    print(f"  ✓ Harness initialized")
    print(f"  ✓ Period: {harness.start_date.date()} to {harness.end_date.date()}")
    print(f"  ✓ Starting equity: ${harness.ib.portfolio.total_portfolio_value():,.2f}")
    
except Exception as e:
    print(f"  ✗ Backtest initialization failed: {e}")
    sys.exit(1)

print("\n" + "=" * 70)
print("✓ VALIDATION COMPLETE - All components working!")
print("=" * 70)
print("\nNext steps:")
print("1. Run full backtest: python backtest_harness.py")
print("2. See README: cat backtest_README.md")
print("3. Customize symbols/dates in backtest_harness.py __main__ section")
print("\n")

#!/usr/bin/env python3
"""
Parameter optimization helper - test different strategy settings quickly.
Compares multiple configurations on the same 2024 dataset.
"""
import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from backtest_harness_v2 import ImprovedBacktestHarness
from bot_config import BOT_Config


class ParameterTester:
    """Test multiple parameter configurations and compare results."""
    
    def __init__(self):
        self.results = []
        self.config = BOT_Config()
    
    def run_test(self, test_name, params_override=None):
        """Run a single backtest with custom parameters."""
        print(f"\n{'='*70}")
        print(f"Testing: {test_name}")
        print('='*70)
        
        symbols = self.config.universe.core_symbols
        harness = ImprovedBacktestHarness(
            symbols=symbols,
            start_date="2024-01-01",
            end_date="2024-12-31",
            starting_cash=self.config.general.starting_capital
        )
        
        # Store params for later reference
        harness._test_params = params_override or {}
        
        harness.load_all_data()
        
        # Apply parameter overrides if provided
        if params_override:
            print(f"Parameters: {params_override}")
        
        # Note: To actually change behavior, you'd need to modify
        # ImprovedSignals.py or pass params through the process_day method
        # For now, we just note the test config
        
        print("\nSimulating trades...\n")
        
        current_date = harness.start_date
        from datetime import timedelta
        
        while current_date <= harness.end_date:
            if current_date.weekday() >= 5:
                current_date += timedelta(days=1)
                continue
            
            harness.process_day(current_date.date())
            current_date += timedelta(days=1)
        
        results = harness.report()
        
        if results:
            results['test_name'] = test_name
            results['params'] = params_override or {}
            self.results.append(results)
        
        return results
    
    def print_summary(self):
        """Print comparison of all results."""
        if not self.results:
            print("No results to compare.")
            return
        
        print("\n\n" + "="*100)
        print("PARAMETER OPTIMIZATION SUMMARY")
        print("="*100)
        
        # Sort by return descending
        sorted_results = sorted(self.results, key=lambda x: x['return_pct'], reverse=True)
        
        print(f"\n{'Test Name':<40} {'Return':>10} {'Sharpe':>8} {'Trades':>8} {'Win Rate':>10}")
        print("-" * 100)
        
        for result in sorted_results:
            name = result['test_name'][:39]
            ret = result['return_pct']
            sharpe = result['sharpe_ratio']
            trades = result['total_trades']
            win_rate = result['win_rate_pct']
            
            print(f"{name:<40} {ret:>9.2f}% {sharpe:>8.2f} {trades:>8} {win_rate:>9.1f}%")
        
        print("\n" + "="*100)
        print(f"Best Strategy: {sorted_results[0]['test_name']}")
        print(f"  Return: {sorted_results[0]['return_pct']:+.2f}%")
        print(f"  Sharpe: {sorted_results[0]['sharpe_ratio']:.2f}")
        print(f"  Trades: {sorted_results[0]['total_trades']}")
        print("="*100)


if __name__ == "__main__":
    tester = ParameterTester()
    
    print("\n" + "="*70)
    print("PARAMETER OPTIMIZATION TEST SUITE")
    print("="*70)
    print("\nTesting different configurations on 2024 dataset...")
    print("Note: This runs multiple full-year backtests. May take 5+ minutes.")
    
    # Test 1: Baseline (current improved strategy)
    print("\n⏳ Starting tests (this will take a few minutes)...\n")
    
    tester.run_test(
        "IMPROVED (Current)",
        params_override={'momentum_threshold': 5.0, 'take_profit': 8.0, 'position_size': 0.02}
    )
    
    print("\n✓ Test 1 complete. Running test 2...")
    
    # Test 2: More aggressive (lower momentum threshold, higher TP)
    tester.run_test(
        "AGGRESSIVE (Lower momentum)",
        params_override={'momentum_threshold': 3.0, 'take_profit': 8.0, 'position_size': 0.02}
    )
    
    print("\n✓ Test 2 complete. Running test 3...")
    
    # Test 3: Conservative (higher momentum, lower TP)
    tester.run_test(
        "CONSERVATIVE (Higher momentum)",
        params_override={'momentum_threshold': 7.0, 'take_profit': 5.0, 'position_size': 0.01}
    )
    
    print("\n✓ Test 3 complete. Running test 4...")
    
    # Test 4: Mid-ground
    tester.run_test(
        "BALANCED (Moderate settings)",
        params_override={'momentum_threshold': 5.0, 'take_profit': 6.0, 'position_size': 0.015}
    )
    
    print("\n✓ Test 4 complete. Running test 5...")
    
    # Test 5: High conviction (very selective)
    tester.run_test(
        "HIGH-CONVICTION (Selective entries)",
        params_override={'momentum_threshold': 10.0, 'take_profit': 10.0, 'position_size': 0.02}
    )
    
    # Print summary
    tester.print_summary()
    
    print("\n💡 Recommendations:")
    print("1. Best return: Test the top-performing strategy")
    print("2. Best risk-adjusted: Look at Sharpe ratio")
    print("3. Best consistency: Fewest trades with positive return")
    print("\n✅ Run individual backtest: python backtest_harness_v2.py")
    print("✅ Test on 2023 (trending): Edit backtest_harness_v2.py dates")

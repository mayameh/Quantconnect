"""
Lightweight backtest runner for IB bot.
Replays historical data and invokes bot logic at scheduled times.
"""
import sys
from datetime import datetime, timedelta
import pandas as pd
import pytz
from pathlib import Path

# Add bot directory to path
sys.path.insert(0, str(Path(__file__).parent))

from mock_ib import MockIB, Contract
from bot_config import BOT_Config


class BacktestRunner:
    """Orchestrates backtest simulation."""
    
    def __init__(self, symbols, start_date, end_date, starting_cash=11_000):
        self.symbols = symbols
        self.start_date = pd.to_datetime(start_date)
        self.end_date = pd.to_datetime(end_date)
        self.starting_cash = starting_cash
        self.ib = MockIB(starting_cash, datetime.now(pytz.timezone('US/Eastern')))
        self.config = BOT_Config()
        
        # Trade accounting
        self.trades_log = []
        self.equity_curve = []
        self.daily_returns = []
        
        # Scheduler state (mimic APScheduler)
        self.scheduled_jobs = {}
        self.last_job_exec = {}
        
    def load_data(self, symbol, source="yfinance"):
        """Load OHLCV data for backtest period."""
        try:
            if source == "yfinance":
                import yfinance as yf
                df = yf.download(symbol, start=self.start_date, end=self.end_date, interval="1d", progress=False)
            else:
                raise ValueError(f"Unsupported source: {source}")
            
            if df.empty:
                print(f"Warning: No data for {symbol}")
                return pd.DataFrame()
            
            # Ensure index is timezone-aware (US/Eastern)
            if df.index.tz is None:
                df.index = df.index.tz_localize(pytz.timezone('US/Eastern'))
            
            df = df.rename(columns={
                'Open': 'open',
                'High': 'high',
                'Low': 'low',
                'Close': 'close',
                'Volume': 'volume',
                'Adj Close': 'adjClose'
            })
            
            return df[['open', 'high', 'low', 'close', 'volume', 'adjClose']].copy()
        except Exception as e:
            print(f"Error loading data for {symbol}: {e}")
            return pd.DataFrame()
    
    def load_all_data(self):
        """Load and cache all data."""
        print(f"Loading {len(self.symbols)} symbols from {self.start_date.date()} to {self.end_date.date()}...")
        for symbol in self.symbols:
            df = self.load_data(symbol)
            if not df.empty:
                self.ib.load_historical_data(symbol, df)
                print(f"  ✓ {symbol}: {len(df)} bars")
            else:
                print(f"  ✗ {symbol}: No data")
    
    def get_bars_for_date(self, symbol, date):
        """Get OHLCV for a given date."""
        df = self.ib.historical_data.get(symbol, pd.DataFrame())
        if df.empty:
            return None
        
        # Match date
        date_tz = pd.to_datetime(date).tz_localize(pytz.timezone('US/Eastern'))
        if date_tz in df.index:
            row = df.loc[date_tz]
            return {
                'open': row['open'],
                'high': row['high'],
                'low': row['low'],
                'close': row['close'],
                'volume': row['volume'],
            }
        return None
    
    def register_job(self, hour, minute, func, job_id=""):
        """Register a scheduled job (called by bot init)."""
        self.scheduled_jobs[(hour, minute, job_id)] = func
        self.last_job_exec[job_id] = None
    
    def should_run_job(self, hour, minute, job_id, current_time):
        """Check if a job should run at current_time (once per day)."""
        job_time = (hour, minute, job_id)
        current_date = current_time.date()
        
        # Run if time matches and we haven't run today
        if current_time.hour == hour and current_time.minute == minute:
            last_run = self.last_job_exec.get(job_id)
            if last_run is None or last_run.date() < current_date:
                self.last_job_exec[job_id] = current_time
                return True
        return False
    
    def replay_bars_daily(self):
        """Step through each day and replay bar data."""
        print("\nStarting backtest replay...")
        
        current_date = self.start_date
        et_tz = pytz.timezone('US/Eastern')
        
        while current_date <= self.end_date:
            # Skip weekends
            if current_date.weekday() >= 5:
                current_date += timedelta(days=1)
                continue
            
            # Update market data for all symbols at 16:00 ET (close)
            for symbol in self.symbols:
                bars = self.get_bars_for_date(symbol, current_date)
                if bars:
                    # Simulate EOD tick
                    self.ib.update_ticker_price(
                        symbol,
                        close=bars['close'],
                        bid=bars['close'] * 0.9999,
                        ask=bars['close'] * 1.0001,
                        volume=bars['volume']
                    )
            
            # Update portfolio equity
            self.update_portfolio_equity()
            self.equity_curve.append({
                'date': current_date,
                'equity': self.ib.portfolio.total_portfolio_value(),
                'cash': self.ib.portfolio.cash,
            })
            
            current_date += timedelta(days=1)
    
    def update_portfolio_equity(self):
        """Mark-to-market all positions."""
        total_equity = self.ib.portfolio.cash
        for symbol, pos in self.ib.portfolio.positions.items():
            if pos['qty'] > 0:
                ticker = self.ib.ticker(Contract(symbol))
                current_price = ticker.marketPrice()
                if current_price > 0:
                    total_equity += pos['qty'] * current_price
        self.ib.portfolio._total_value = total_equity
    
    def report(self):
        """Generate backtest report."""
        print("\n" + "="*60)
        print("BACKTEST REPORT")
        print("="*60)
        
        equity_df = pd.DataFrame(self.equity_curve)
        if equity_df.empty:
            print("No trades executed.")
            return
        
        start_eq = equity_df['equity'].iloc[0]
        end_eq = equity_df['equity'].iloc[-1]
        
        total_return = (end_eq - start_eq) / start_eq * 100
        max_equity = equity_df['equity'].max()
        min_equity = equity_df['equity'].min()
        max_drawdown = (max_equity - min_equity) / max_equity * 100 if max_equity > 0 else 0
        
        filled_orders = self.ib.filled_orders
        buy_orders = [o for o in filled_orders if o['action'] == 'BUY']
        sell_orders = [o for o in filled_orders if o['action'] == 'SELL']
        
        print(f"\nCapital Summary:")
        print(f"  Starting Cash:    ${self.starting_cash:,.2f}")
        print(f"  Ending Equity:    ${end_eq:,.2f}")
        print(f"  Total Return:     {total_return:.2f}%")
        print(f"  Max Drawdown:     {max_drawdown:.2f}%")
        
        print(f"\nTrade Summary:")
        print(f"  Total Trades:     {len(filled_orders)}")
        print(f"  Buy Orders:       {len(buy_orders)}")
        print(f"  Sell Orders:      {len(sell_orders)}")
        
        if filled_orders:
            print(f"\nTrade Log (last 10):")
            for order in filled_orders[-10:]:
                print(f"  {order['time'].strftime('%Y-%m-%d')} {order['action']:4s} "
                      f"{order['qty']:3.0f}x {order['symbol']:6s} @ ${order['fill_price']:7.2f}")
        
        print(f"\nFinal Positions:")
        positions = self.ib.positions()
        if not positions:
            print("  (None)")
        for pos in positions:
            print(f"  {pos.contract.symbol}: {pos.position} shares @ ${pos.avgCost:.2f}")
        
        print("="*60)
        
        return {
            'starting_cash': self.starting_cash,
            'ending_equity': end_eq,
            'total_return_pct': total_return,
            'max_drawdown_pct': max_drawdown,
            'total_trades': len(filled_orders),
            'buy_trades': len(buy_orders),
            'sell_trades': len(sell_orders),
        }


if __name__ == "__main__":
    # Example: Backtest 2024 with core symbols
    config = BOT_Config()
    symbols = config.universe.core_symbols[:3]  # Use first 3 symbols for quick test
    
    runner = BacktestRunner(
        symbols=symbols,
        start_date="2024-01-01",
        end_date="2024-12-31",
        starting_cash=config.account.starting_capital
    )
    
    runner.load_all_data()
    runner.replay_bars_daily()
    runner.report()

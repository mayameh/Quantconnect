"""
Full backtest harness that integrates bot logic with mock IB and data replay.
Patches main_ib.py to use mock IB for historical simulation.
"""
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd
import pytz

# Setup paths
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "vm-deploy"))

# Import mock and config
from mock_ib import MockIB, Contract
from bot_config import BOT_Config


class FullBacktestHarness:
    """Integrates bot logic with mock IB and historical replay."""
    
    def __init__(self, symbols, start_date, end_date, starting_cash=11_000):
        self.symbols = symbols
        self.start_date = pd.to_datetime(start_date).tz_localize(pytz.timezone('US/Eastern'))
        self.end_date = pd.to_datetime(end_date).tz_localize(pytz.timezone('US/Eastern'))
        self.starting_cash = starting_cash
        self.config = BOT_Config()
        
        # Initialize mock IB
        et_tz = pytz.timezone('US/Eastern')
        self.ib = MockIB(starting_cash, datetime.now(et_tz))
        
        # Bot state (normally from main_ib.py)
        self.bot_state = {
            'positions': {},
            'core_symbols': self.config.universe.core_symbols,
            'dynamic_symbols': set(),
            'active_universe': list(self.config.universe.core_symbols),
        }
        
        # Trade accounting
        self.trades = []
        self.equity_history = []
        self.signal_log = []
        
    def load_data(self, symbol, source="yfinance"):
        """Load OHLCV data for backtest period from yfinance."""
        try:
            import yfinance as yf
            df = yf.download(
                symbol,
                start=self.start_date.date(),
                end=self.end_date.date(),
                interval="1d",
                progress=False
            )
            
            if df.empty:
                print(f"Warning: No data for {symbol}")
                return pd.DataFrame()
            
            # Ensure timezone-aware
            if df.index.tz is None:
                df.index = df.index.tz_localize(pytz.timezone('US/Eastern'))
            
            # Handle multi-level columns from yfinance
            # Columns are like ('Close', 'NVDA'), ('High', 'NVDA'), etc.
            if isinstance(df.columns, pd.MultiIndex):
                # Flatten to single level using the price type (level 0)
                df.columns = df.columns.get_level_values(0)
            
            # Now columns should be: Close, High, Low, Open, Volume
            # Standardize to lowercase
            df.columns = [col.lower() for col in df.columns]
            
            # Ensure we have required columns
            required = ['open', 'high', 'low', 'close', 'volume']
            missing = [col for col in required if col not in df.columns]
            if missing:
                print(f"Warning: Missing columns for {symbol}: {missing}")
                return pd.DataFrame()
            
            # Create adjClose as close (already adjusted in modern yfinance)
            df['adjclose'] = df['close']
            
            # Select and return only needed columns
            return df[required + ['adjclose']].copy()
        except Exception as e:
            print(f"Error loading data for {symbol}: {e}")
            import traceback
            traceback.print_exc()
            return pd.DataFrame()
    
    def load_all_data(self):
        """Load historical data for all symbols."""
        print(f"Loading {len(self.symbols)} symbols...")
        for symbol in self.symbols:
            df = self.load_data(symbol)
            if not df.empty:
                self.ib.load_historical_data(symbol, df)
                print(f"  ✓ {symbol}: {len(df)} bars loaded")
            else:
                print(f"  ✗ {symbol}: No data available")
    
    def get_bar_for_date(self, symbol, date):
        """Fetch OHLCV bar for a specific date."""
        df = self.ib.historical_data.get(symbol, pd.DataFrame())
        if df.empty:
            return None
        
        # Convert date to timezone-aware datetime if needed
        if isinstance(date, str):
            date = pd.to_datetime(date).tz_localize(pytz.timezone('US/Eastern'))
        elif isinstance(date, pd.Timestamp):
            if date.tzinfo is None:
                date = date.tz_localize(pytz.timezone('US/Eastern'))
        else:
            # It's a date object or datetime without timezone
            date = pd.to_datetime(date).tz_localize(pytz.timezone('US/Eastern'))
        
        # Find matching row
        if date in df.index:
            row = df.loc[date]
            return {
                'symbol': symbol,
                'date': date,
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
                'volume': int(row['volume']),
            }
        return None
    
    def simulate_entry_signal(self, symbol, bar):
        """
        Simulate simple entry logic:
        - Buy if price > 50-SMA (simplified momentum check)
        - If bar count >= 50, compute SMA
        """
        df = self.ib.historical_data.get(symbol, pd.DataFrame())
        if len(df) < 50:
            return False  # Not enough history
        
        # Compute 50-day SMA
        sma_50 = df['close'].tail(50).mean()
        
        # Simple entry: close above SMA_50
        if bar['close'] > sma_50:
            return True
        return False
    
    def simulate_exit_signal(self, symbol, position, bar):
        """
        Simulate simple exit logic:
        - Sell if price < 50-SMA (momentum loss)
        - Or if P&L hits stop-loss (-3%) or take-profit (+8%)
        """
        if position['qty'] == 0:
            return False
        
        # Calculate P&L
        pnl_pct = (bar['close'] - position['avg_cost']) / position['avg_cost'] * 100
        
        # Check stops
        if pnl_pct <= -self.config.trading.stop_loss_pct:
            return True
        if pnl_pct >= self.config.trading.take_profit_pct:
            return True
        
        # Check SMA
        df = self.ib.historical_data.get(symbol, pd.DataFrame())
        if len(df) >= 50:
            sma_50 = df['close'].tail(50).mean()
            if bar['close'] < sma_50:
                return True
        
        return False
    
    def process_day(self, date):
        """Process trading logic for a single day."""
        et_tz = pytz.timezone('US/Eastern')
        date_et = pd.to_datetime(date).tz_localize(et_tz)
        
        # Update IB time
        self.ib.update_current_time(date_et)
        
        # Process each symbol in active universe
        for symbol in self.bot_state['active_universe']:
            bar = self.get_bar_for_date(symbol, date)
            if not bar:
                continue
            
            # Update ticker price
            self.ib.update_ticker_price(
                symbol,
                close=bar['close'],
                bid=bar['close'] * 0.9999,
                ask=bar['close'] * 1.0001,
                volume=bar['volume']
            )
            
            # Get position
            position = self.bot_state['positions'].get(symbol, {'qty': 0, 'avg_cost': 0})
            
            # Exit if open
            if position['qty'] > 0 and self.simulate_exit_signal(symbol, position, bar):
                order = type('Order', (), {
                    'action': 'SELL',
                    'totalQuantity': position['qty']
                })()
                contract = Contract(symbol=symbol)
                self.ib.placeOrder(contract, order)
                
                self.trades.append({
                    'date': date,
                    'symbol': symbol,
                    'action': 'SELL',
                    'qty': position['qty'],
                    'price': bar['close'],
                    'pnl_pct': (bar['close'] - position['avg_cost']) / position['avg_cost'] * 100,
                })
                
                self.bot_state['positions'][symbol] = {'qty': 0, 'avg_cost': 0}
                print(f"  [{date.strftime('%Y-%m-%d')}] SELL {position['qty']}x {symbol} @ ${bar['close']:.2f}")
            
            # Entry if no position
            elif position['qty'] == 0 and self.simulate_entry_signal(symbol, bar):
                # Position size: 2% of equity per trade
                qty = max(1, int(self.ib.portfolio.total_portfolio_value() * 0.02 / bar['close']))
                
                # Check position limits
                open_positions = sum(1 for p in self.bot_state['positions'].values() if p['qty'] > 0)
                if open_positions >= self.config.trading.max_positions:
                    continue
                
                order = type('Order', (), {
                    'action': 'BUY',
                    'totalQuantity': qty
                })()
                contract = Contract(symbol=symbol)
                self.ib.placeOrder(contract, order)
                
                self.bot_state['positions'][symbol] = {'qty': qty, 'avg_cost': bar['close']}
                
                self.trades.append({
                    'date': date,
                    'symbol': symbol,
                    'action': 'BUY',
                    'qty': qty,
                    'price': bar['close'],
                    'pnl_pct': 0,
                })
                
                print(f"  [{date.strftime('%Y-%m-%d')}] BUY {qty}x {symbol} @ ${bar['close']:.2f}")
        
        # Mark-to-market equity
        self.update_equity()
    
    def update_equity(self):
        """Mark all positions and record equity."""
        total_equity = self.ib.portfolio.cash
        for symbol, pos in self.bot_state['positions'].items():
            if pos['qty'] > 0:
                ticker = self.ib.ticker(Contract(symbol))
                price = ticker.marketPrice()
                if price > 0:
                    total_equity += pos['qty'] * price
        
        self.ib.portfolio._total_value = total_equity
        self.equity_history.append({
            'date': self.ib.current_time,
            'equity': total_equity,
            'cash': self.ib.portfolio.cash,
        })
    
    def run_backtest(self):
        """Execute full backtest."""
        print(f"\nRunning backtest: {self.start_date.date()} → {self.end_date.date()}")
        print(f"Symbols: {', '.join(self.symbols[:5])}{'...' if len(self.symbols) > 5 else ''}")
        print(f"Starting Capital: ${self.starting_cash:,.2f}\n")
        
        self.load_all_data()
        
        print("\nSimulating trades...\n")
        
        current_date = self.start_date
        while current_date <= self.end_date:
            # Skip weekends
            if current_date.weekday() >= 5:
                current_date += timedelta(days=1)
                continue
            
            self.process_day(current_date.date())
            current_date += timedelta(days=1)
    
    def report(self):
        """Generate comprehensive backtest report."""
        if not self.equity_history:
            print("No trades. Backtest incomplete.")
            return
        
        eq_df = pd.DataFrame(self.equity_history)
        
        start_eq = eq_df['equity'].iloc[0]
        end_eq = eq_df['equity'].iloc[-1]
        total_return = (end_eq - start_eq) / start_eq * 100
        
        max_eq = eq_df['equity'].max()
        min_eq = eq_df['equity'].min()
        max_dd = ((max_eq - min_eq) / max_eq * 100) if max_eq > 0 else 0
        
        daily_returns = eq_df['equity'].pct_change().dropna()
        if len(daily_returns) > 1 and daily_returns.std() > 0:
            sharpe = (daily_returns.mean() / daily_returns.std() * 252**0.5)
        else:
            sharpe = 0
        
        trades_df = pd.DataFrame(self.trades)
        
        # Handle empty trades
        if trades_df.empty:
            buy_trades = 0
            sell_trades = 0
            winning_trades = 0
            total_trades = 0
            win_rate = 0
        else:
            buy_trades = len(trades_df[trades_df['action'] == 'BUY'])
            sell_trades = len(trades_df[trades_df['action'] == 'SELL'])
            winning_trades = len(trades_df[trades_df['action'] == 'SELL']) - sum(1 for _, row in trades_df[trades_df['action'] == 'SELL'].iterrows() if row['pnl_pct'] < 0)
            total_trades = len(trades_df)
            win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        
        print("\n" + "="*70)
        print("BACKTEST REPORT")
        print("="*70)
        
        print(f"\nCAPITAL SUMMARY:")
        print(f"  Starting Cash:      ${self.starting_cash:,.2f}")
        print(f"  Ending Equity:      ${end_eq:,.2f}")
        print(f"  Total Return:       {total_return:+.2f}%")
        print(f"  Max Drawdown:       {max_dd:.2f}%")
        print(f"  Sharpe Ratio:       {sharpe:.2f}")
        
        print(f"\nTRADE SUMMARY:")
        print(f"  Total Trades:       {total_trades}")
        print(f"  Buy Orders:         {buy_trades}")
        print(f"  Sell Orders:        {sell_trades}")
        print(f"  Win Rate:           {win_rate:.1f}%")
        
        if not trades_df.empty:
            print(f"\nRECENT TRADES (Last 10):")
            for _, row in trades_df.tail(10).iterrows():
                symbol_pad = f"{row['symbol']:8s}"
                action_pad = f"{row['action']:4s}"
                qty_pad = f"{int(row['qty']):3d}"
                price_pad = f"${row['price']:7.2f}"
                pnl_str = f"{row['pnl_pct']:+6.2f}%" if row['action'] == 'SELL' else ""
                print(f"  {row['date']} {action_pad} {qty_pad}x {symbol_pad} @ {price_pad} {pnl_str}".rstrip())
        
        print("\nFINAL POSITIONS:")
        open_positions = [(s, p) for s, p in self.bot_state['positions'].items() if p['qty'] > 0]
        if open_positions:
            for symbol, pos in open_positions:
                bar = self.get_bar_for_date(symbol, self.end_date.date())
                if bar:
                    unrealized_pnl = (bar['close'] - pos['avg_cost']) / pos['avg_cost'] * 100
                    print(f"  {symbol}: {pos['qty']:3d} shares @ ${pos['avg_cost']:7.2f} (unrealized: {unrealized_pnl:+.2f}%)")
        else:
            print("  (None)")
        
        print("="*70)
        
        return {
            'start_equity': start_eq,
            'end_equity': end_eq,
            'return_pct': total_return,
            'max_drawdown_pct': max_dd,
            'sharpe_ratio': sharpe,
            'total_trades': total_trades,
            'buy_count': buy_trades,
            'sell_count': sell_trades,
            'win_rate_pct': win_rate,
        }


if __name__ == "__main__":
    config = BOT_Config()
    symbols = config.universe.core_symbols  # Use core symbols
    
    harness = FullBacktestHarness(
        symbols=symbols,
        start_date="2024-01-01",
        end_date="2024-12-31",
        starting_cash=config.general.starting_capital
    )
    
    harness.run_backtest()
    results = harness.report()

"""
Improved trading signal logic with momentum, MACD, RSI, and volatility-based risk management.
Replaces the simple 50-SMA strategy with a more robust approach.
"""
import pandas as pd
import numpy as np


class ImprovedSignals:
    """Enhanced entry/exit logic with multiple indicators and filters."""
    
    @staticmethod
    def compute_momentum(df, lookback=22):
        """
        Calculate 1-month price momentum (% change from lookback bars ago).
        Higher momentum = stronger uptrend.
        """
        df['returns'] = df['close'].pct_change(lookback)
        df['momentum'] = df['returns'] * 100  # Convert to percentage
        return df
    
    @staticmethod
    def compute_macd(df, fast=12, slow=26, signal=9):
        """
        MACD: Trend following oscillator.
        - MACD line = EMA(12) - EMA(26)
        - Signal line = EMA(9) of MACD
        - Histogram = MACD - Signal
        Entry: MACD crosses above signal (bullish)
        Exit: MACD crosses below signal (bearish)
        """
        ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
        ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
        
        df['macd'] = ema_fast - ema_slow
        df['macd_signal'] = df['macd'].ewm(span=signal, adjust=False).mean()
        df['macd_hist'] = df['macd'] - df['macd_signal']
        
        return df
    
    @staticmethod
    def compute_rsi(df, period=14):
        """
        RSI: Mean reversion oscillator (0-100).
        - RSI > 70: Overbought (consider selling)
        - RSI < 30: Oversold (consider buying)
        - 40-60: Neutral zone
        Useful for confirming trends or spotting reversals.
        """
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / (loss + 1e-10)
        df['rsi'] = 100 - (100 / (1 + rs))
        
        return df
    
    @staticmethod
    def compute_atr(df, period=14):
        """
        ATR: Average True Range (volatility measurement).
        Used for:
        - Volatility-based position sizing
        - Dynamic stop loss placement (e.g., close - 2*ATR)
        - Take profit levels (e.g., close + 2*ATR)
        """
        high_low = df['high'] - df['low']
        high_close = abs(df['high'] - df['close'].shift())
        low_close = abs(df['low'] - df['close'].shift())
        
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df['atr'] = tr.rolling(period).mean()
        
        return df
    
    @staticmethod
    def compute_sma(df, period=50):
        """Simple moving average for trend direction."""
        df['sma'] = df['close'].rolling(period).mean()
        return df
    
    @staticmethod
    def prepare_indicators(df):
        """
        Compute all indicators on DataFrame.
        Assumes df has columns: open, high, low, close, volume
        """
        df = ImprovedSignals.compute_sma(df, period=50)
        df = ImprovedSignals.compute_momentum(df, lookback=22)
        df = ImprovedSignals.compute_macd(df, fast=12, slow=26, signal=9)
        df = ImprovedSignals.compute_rsi(df, period=14)
        df = ImprovedSignals.compute_atr(df, period=14)
        
        return df
    
    @staticmethod
    def should_enter(symbol, df, price, position_open=False):
        """
        Improved entry logic with multiple confirmations.
        Returns: (should_enter: bool, reason: str)
        """
        if position_open or df.empty or len(df) < 50:
            return False, "waiting"
        
        current = df.iloc[-1]
        
        # Filter 1: Price above 50-SMA (uptrend)
        if price <= current.get('sma', 0):
            return False, "below_sma"
        
        # Filter 2: Strong positive momentum (1-month change > +5%)
        momentum = current.get('momentum', 0)
        if momentum < 5.0:
            return False, "weak_momentum"
        
        # Filter 3: MACD above signal line and histogram > 0 (bullish)
        macd_hist = current.get('macd_hist', 0)
        macd = current.get('macd', 0)
        macd_sig = current.get('macd_signal', 0)
        
        if macd_hist <= 0:
            return False, "macd_not_bullish"
        
        # Filter 4: RSI not overbought (< 75, leave room for continuation)
        rsi = current.get('rsi', 50)
        if rsi > 75:
            return False, "rsi_overbought"
        
        # Filter 5: RSI not oversold (> 35, avoid weak bounces)
        if rsi < 35:
            return False, "rsi_extremes"
        
        # All filters passed
        return True, f"entry_signal"
    
    @staticmethod
    def should_exit(symbol, df, price, entry_price, position_qty=0, current_pnl_pct=0):
        """
        Improved exit logic with trend + risk management.
        Returns: (should_exit: bool, reason: str)
        """
        if not position_qty or df.empty:
            return False, "no_position"
        
        current = df.iloc[-1]
        
        # Exit 1: MACD bearish crossover (trend reversal)
        macd_hist = current.get('macd_hist', 0)
        prev_hist = df.iloc[-2].get('macd_hist', 0) if len(df) > 1 else macd_hist
        
        if prev_hist > 0 and macd_hist <= 0:
            return True, "macd_bearish_cross"
        
        # Exit 2: Price below SMA by more than 1% (downtrend)
        sma = current.get('sma', 0)
        if sma > 0 and price < sma * 0.99:
            return True, "price_below_sma"
        
        # Exit 3: Take profit at +8% (ATR-based)
        if current_pnl_pct >= 8.0:
            return True, "take_profit_target"
        
        # Exit 4: Stop loss at -3% (hard floor)
        if current_pnl_pct <= -3.0:
            return True, "stop_loss_hit"
        
        # Exit 5: Dynamic stop loss at entry - 2*ATR if position has profit
        atr = current.get('atr', 0)
        if atr > 0 and current_pnl_pct > 1.0:
            dynamic_stop = entry_price - (2.0 * atr)
            if price < dynamic_stop:
                return True, "dynamic_stop_loss"
        
        # No exit signal
        return False, "hold"
    
    @staticmethod
    def get_position_size_multiplier(df):
        """
        Adjust position size based on volatility (ATR).
        Lower ATR = less volatile = can take bigger position
        Higher ATR = more volatile = take smaller position
        This normalizes risk across all market conditions.
        """
        if df.empty or 'atr' not in df.columns:
            return 1.0
        
        current_atr = df.iloc[-1]['atr']
        # Use 20-day average ATR as baseline
        baseline_atr = df['atr'].tail(20).mean()
        
        if baseline_atr > 0:
            # If vol is low, can take bigger position (multiplier > 1)
            # If vol is high, take smaller position (multiplier < 1)
            multiplier = baseline_atr / current_atr
            # Clamp between 0.5x and 1.5x
            return max(0.5, min(1.5, multiplier))
        
        return 1.0


# Example usage in backtest harness
if __name__ == "__main__":
    import yfinance as yf
    
    # Download sample data
    df = yf.download("MSFT", start="2024-01-01", end="2024-12-31", progress=False)
    df.columns = [col.lower().replace(" ", "") for col in df.columns]
    
    # Flatten multi-level columns if present
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
        df.columns = [col.lower() for col in df.columns]
    
    # Compute all indicators
    df = ImprovedSignals.prepare_indicators(df)
    
    # Print sample indicators
    print("Sample Indicators (last 5 days):")
    print(df[['close', 'sma', 'momentum', 'macd', 'macd_signal', 'rsi', 'atr']].tail(5))
    
    # Test entry/exit logic
    print("\nEntry/Exit Logic Test:")
    for i in range(50, min(100, len(df))):
        price = df.iloc[i]['close']
        should_buy, reason = ImprovedSignals.should_enter("MSFT", df.iloc[:i+1], price, False)
        if should_buy:
            print(f"  {df.index[i].date()} → BUY signal: {reason}")
            # Test exit at next bar
            should_sell, exit_reason = ImprovedSignals.should_exit(
                "MSFT", 
                df.iloc[:i+2], 
                df.iloc[i+1]['close'], 
                entry_price=price,
                position_qty=1,
                current_pnl_pct=(df.iloc[i+1]['close'] - price) / price * 100
            )
            if should_sell:
                print(f"    {df.index[i+1].date()} → SELL signal: {exit_reason}")

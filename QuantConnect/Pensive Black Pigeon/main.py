from AlgorithmImports import *
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from enum import Enum

class SignalType(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"

class TradingSignal:
    def __init__(self, symbol: str, signal_type: SignalType, confidence: float, 
                 price: float, indicators: Dict[str, float], timestamp: datetime, reason: str):
        self.symbol = symbol
        self.signal_type = signal_type
        self.confidence = confidence
        self.price = price
        self.indicators = indicators
        self.timestamp = timestamp
        self.reason = reason

class TechnicalAnalyzer:

    """Technical analysis using QuantConnect indicators"""
    
    def __init__(self, algorithm):
        self.algorithm = algorithm
        self.indicators = {}
        
    def initialize_indicators(self, symbol_obj):
        """Initialize QuantConnect technical indicators for a symbol"""
        symbol_str = str(symbol_obj)
        
        if symbol_str not in self.indicators:
            self.indicators[symbol_str] = {
                'rsi': self.algorithm.rsi(symbol_obj, 14, MovingAverageType.SIMPLE, Resolution.DAILY),
                'macd': self.algorithm.macd(symbol_obj, 12, 26, 9, MovingAverageType.EXPONENTIAL, Resolution.DAILY),
                'bb': self.algorithm.bb(symbol_obj, 20, 2, MovingAverageType.SIMPLE, Resolution.DAILY),
                'stoch': self.algorithm.sto(symbol_obj, 14, 3, 3, Resolution.DAILY),
                'atr': self.algorithm.atr(symbol_obj, 14, MovingAverageType.SIMPLE, Resolution.DAILY),
                'cci': self.algorithm.cci(symbol_obj, 20, MovingAverageType.SIMPLE, Resolution.DAILY),
                'adx': self.algorithm.adx(symbol_obj, 14, Resolution.DAILY),
                'sma_20': self.algorithm.sma(symbol_obj, 20, Resolution.DAILY),
                'sma_50': self.algorithm.sma(symbol_obj, 50, Resolution.DAILY),
                'ema_12': self.algorithm.ema(symbol_obj, 12, Resolution.DAILY),
                'ema_26': self.algorithm.ema(symbol_obj, 26, Resolution.DAILY),
                'volume_sma': self.algorithm.sma(symbol_obj, 20, Resolution.DAILY, Field.VOLUME)
            }
            
        return self.indicators[symbol_str]
    
    def get_indicator_values(self, symbol_obj) -> Dict[str, float]:
        """Get current values of all indicators for a symbol"""
        symbol_str = str(symbol_obj)
        
        if symbol_str not in self.indicators:
            return {}
            
        indicators = self.indicators[symbol_str]
        
        # Check if indicators are ready
        if not all(ind.IsReady for ind in [indicators['rsi'], indicators['macd'], indicators['bb']]):
            return {}
        
        try:
            # Get current price and volume
            current_price = float(self.algorithm.Securities[symbol_obj].Price)
            current_volume = float(self.algorithm.Securities[symbol_obj].Volume)
            
            # Calculate volume ratio
            volume_ratio = (current_volume / float(indicators['volume_sma'].Current.Value) 
                          if indicators['volume_sma'].IsReady and indicators['volume_sma'].Current.Value > 0 
                          else 1.0)
            
            return {
                'price': current_price,
                'rsi': float(indicators['rsi'].Current.Value) if indicators['rsi'].IsReady else 50.0,
                'macd': float(indicators['macd'].Current.Value) if indicators['macd'].IsReady else 0.0,
                'macd_signal': float(indicators['macd'].Signal.Current.Value) if indicators['macd'].IsReady else 0.0,
                'macd_histogram': float(indicators['macd'].Histogram.Current.Value) if indicators['macd'].IsReady else 0.0,
                'bb_upper': float(indicators['bb'].UpperBand.Current.Value) if indicators['bb'].IsReady else current_price,
                'bb_middle': float(indicators['bb'].MiddleBand.Current.Value) if indicators['bb'].IsReady else current_price,
                'bb_lower': float(indicators['bb'].LowerBand.Current.Value) if indicators['bb'].IsReady else current_price,
                'stoch_k': float(indicators['stoch'].StochK.Current.Value) if indicators['stoch'].IsReady else 50.0,
                'stoch_d': float(indicators['stoch'].StochD.Current.Value) if indicators['stoch'].IsReady else 50.0,
                'atr': float(indicators['atr'].Current.Value) if indicators['atr'].IsReady else 0.0,
                'cci': float(indicators['cci'].Current.Value) if indicators['cci'].IsReady else 0.0,
                'adx': float(indicators['adx'].Current.Value) if indicators['adx'].IsReady else 25.0,
                'adx_positive_di': float(indicators['adx'].PositiveDirectionalIndex.Current.Value) if indicators['adx'].IsReady else 25.0,
                'adx_negative_di': float(indicators['adx'].NegativeDirectionalIndex.Current.Value) if indicators['adx'].IsReady else 25.0,
                'sma_20': float(indicators['sma_20'].Current.Value) if indicators['sma_20'].IsReady else current_price,
                'sma_50': float(indicators['sma_50'].Current.Value) if indicators['sma_50'].IsReady else current_price,
                'ema_12': float(indicators['ema_12'].Current.Value) if indicators['ema_12'].IsReady else current_price,
                'ema_26': float(indicators['ema_26'].Current.Value) if indicators['ema_26'].IsReady else current_price,
                'volume_ratio': volume_ratio
            }
            
        except Exception as e:
            self.algorithm.Debug(f"Error getting indicator values for {symbol_str}: {str(e)}")
            return {}
    
    def get_previous_values(self, symbol_obj, periods_back: int = 1) -> Dict[str, float]:
        """Get previous values of indicators for comparison"""
        symbol_str = str(symbol_obj)
        
        if symbol_str not in self.indicators:
            return {}
            
        indicators = self.indicators[symbol_str]
        
        try:
            # For crossover detection, we need to check if we have enough history
            if (len(indicators['macd'].Signal) < periods_back + 1 or 
                len(indicators['macd']) < periods_back + 1):
                return {}
            
            return {
                'macd_prev': float(indicators['macd'][periods_back]),
                'macd_signal_prev': float(indicators['macd'].Signal[periods_back]),
                'adx_positive_di_prev': float(indicators['adx'].PositiveDirectionalIndex[periods_back]) if len(indicators['adx'].PositiveDirectionalIndex) > periods_back else 25.0,
                'adx_negative_di_prev': float(indicators['adx'].NegativeDirectionalIndex[periods_back]) if len(indicators['adx'].NegativeDirectionalIndex) > periods_back else 25.0,
                'cci_prev': float(indicators['cci'][periods_back]) if len(indicators['cci']) > periods_back else 0.0,
                'adx_prev': float(indicators['adx'][periods_back]) if len(indicators['adx']) > periods_back else 25.0
            }
            
        except Exception as e:
            self.algorithm.Debug(f"Error getting previous indicator values for {symbol_str}: {str(e)}")
            return {}


class TradingStrategy:

    """Advanced multi-indicator trading strategy using QuantConnect indicators"""
    
    def __init__(self, analyzer: TechnicalAnalyzer):
        self.analyzer = analyzer
    
    def analyze_stock(self, symbol_obj, algorithm) -> TradingSignal:
        """Comprehensive technical analysis using QuantConnect indicators"""
        symbol_str = str(symbol_obj).split(' ')[0]  # Clean symbol string
        
        try:
            # Initialize indicators if not already done
            self.analyzer.initialize_indicators(symbol_obj)
            
            # Get current indicator values
            current_values = self.analyzer.get_indicator_values(symbol_obj)
            
            if not current_values:
                return TradingSignal(
                    symbol=symbol_str, signal_type=SignalType.HOLD, confidence=0.0,
                    price=0.0, indicators={}, timestamp=datetime.now(), 
                    reason="Indicators not ready"
                )
            
            # Get previous values for crossover detection
            previous_values = self.analyzer.get_previous_values(symbol_obj)
            
            current_price = current_values['price']
            current_rsi = current_values['rsi']
            current_macd = current_values['macd']
            current_macd_signal = current_values['macd_signal']
            current_bb_upper = current_values['bb_upper']
            current_bb_lower = current_values['bb_lower']
            current_stoch_k = current_values['stoch_k']
            current_stoch_d = current_values['stoch_d']
            current_cci = current_values['cci']
            current_adx = current_values['adx']
            current_di_plus = current_values['adx_positive_di']
            current_di_minus = current_values['adx_negative_di']
            current_sma_20 = current_values['sma_20']
            current_sma_50 = current_values['sma_50']
            volume_ratio = current_values['volume_ratio']
            
            # Build indicators dictionary
            indicators = {
                'rsi': current_rsi,
                'macd': current_macd,
                'macd_signal': current_macd_signal,
                'bb_upper': current_bb_upper,
                'bb_lower': current_bb_lower,
                'stoch_k': current_stoch_k,
                'stoch_d': current_stoch_d,
                'cci': current_cci,
                'adx': current_adx,
                'di_plus': current_di_plus,
                'di_minus': current_di_minus,
                'sma_20': current_sma_20,
                'sma_50': current_sma_50,
                'volume_ratio': volume_ratio,
                'atr': current_values['atr']
            }
            
            # Buy signal conditions
            buy_signals = []
            buy_confidence = 0.0
            
            # RSI oversold
            if current_rsi < 30:
                buy_signals.append("RSI oversold")
                buy_confidence += 0.2
            
            # MACD bullish crossover
            if previous_values:
                prev_macd = previous_values.get('macd_prev', current_macd)
                prev_signal = previous_values.get('macd_signal_prev', current_macd_signal)
                
                if current_macd > current_macd_signal and prev_macd <= prev_signal:
                    buy_signals.append("MACD bullish crossover")
                    buy_confidence += 0.25
            
            # Price near lower Bollinger Band
            if current_price <= current_bb_lower * 1.02:
                buy_signals.append("Near lower Bollinger Band")
                buy_confidence += 0.15
            
            # Stochastic oversold
            if current_stoch_k < 20 and current_stoch_d < 20:
                buy_signals.append("Stochastic oversold")
                buy_confidence += 0.15
            
            # CCI oversold
            if current_cci < -100:
                buy_signals.append("CCI oversold")
                buy_confidence += 0.2
            
            # CCI bullish reversal
            if previous_values:
                prev_cci = previous_values.get('cci_prev', current_cci)
                if current_cci > -100 and prev_cci <= -100:
                    buy_signals.append("CCI bullish reversal")
                    buy_confidence += 0.15
            
            # ADX Buy Signals
            if current_adx > 25:
                if current_di_plus > current_di_minus:
                    if previous_values:
                        prev_di_plus = previous_values.get('adx_positive_di_prev', current_di_plus)
                        prev_di_minus = previous_values.get('adx_negative_di_prev', current_di_minus)
                        
                        if current_di_plus > current_di_minus and prev_di_plus <= prev_di_minus:
                            buy_signals.append("ADX bullish crossover (+DI > -DI)")
                            buy_confidence += 0.3
                        elif current_di_plus > current_di_minus + 5:
                            buy_signals.append("ADX strong uptrend")
                            buy_confidence += 0.2
            
            if current_adx > 30 and current_di_plus > current_di_minus:
                buy_signals.append("ADX very strong uptrend")
                buy_confidence += 0.15
            
            # Price above SMA20, potential reversal
            if current_price > current_sma_20 and current_sma_20 < current_sma_50:
                buy_signals.append("Price above SMA20, potential reversal")
                buy_confidence += 0.1
            
            # High volume confirmation
            if volume_ratio > 1.5:
                buy_confidence += 0.15
            
            # Sell signal conditions
            sell_signals = []
            sell_confidence = 0.0
            
            # RSI overbought
            if current_rsi > 70:
                sell_signals.append("RSI overbought")
                sell_confidence += 0.2
            
            # MACD bearish crossover
            if previous_values:
                prev_macd = previous_values.get('macd_prev', current_macd)
                prev_signal = previous_values.get('macd_signal_prev', current_macd_signal)
                
                if current_macd < current_macd_signal and prev_macd >= prev_signal:
                    sell_signals.append("MACD bearish crossover")
                    sell_confidence += 0.25
            
            # Price near upper Bollinger Band
            if current_price >= current_bb_upper * 0.98:
                sell_signals.append("Near upper Bollinger Band")
                sell_confidence += 0.15
            
            # Stochastic overbought
            if current_stoch_k > 80 and current_stoch_d > 80:
                sell_signals.append("Stochastic overbought")
                sell_confidence += 0.15
            
            # CCI overbought
            if current_cci > 100:
                sell_signals.append("CCI overbought")
                sell_confidence += 0.2
            
            # CCI bearish reversal
            if previous_values:
                prev_cci = previous_values.get('cci_prev', current_cci)
                if current_cci < 100 and prev_cci >= 100:
                    sell_signals.append("CCI bearish reversal")
                    sell_confidence += 0.15
            
            # ADX Sell Signals
            if current_adx > 25:
                if current_di_minus > current_di_plus:
                    if previous_values:
                        prev_di_plus = previous_values.get('adx_positive_di_prev', current_di_plus)
                        prev_di_minus = previous_values.get('adx_negative_di_prev', current_di_minus)
                        
                        if current_di_minus > current_di_plus and prev_di_minus <= prev_di_plus:
                            sell_signals.append("ADX bearish crossover (-DI > +DI)")
                            sell_confidence += 0.3
                        elif current_di_minus > current_di_plus + 5:
                            sell_signals.append("ADX strong downtrend")
                            sell_confidence += 0.2
            
            if current_adx > 30 and current_di_minus > current_di_plus:
                sell_signals.append("ADX very strong downtrend")
                sell_confidence += 0.15
            
            # ADX trend weakening
            if previous_values:
                prev_adx = previous_values.get('adx_prev', current_adx)
                if current_adx < 20 and prev_adx >= 20:
                    sell_signals.append("ADX trend weakening")
                    sell_confidence += 0.1
            
            # Price below SMA20
            if current_price < current_sma_20:
                sell_signals.append("Price below SMA20")
                sell_confidence += 0.1
            
            # High volume confirmation for sell
            if volume_ratio > 1.5:
                sell_confidence += 0.15
            
            # Determine final signal
            if buy_confidence >= 0.6 and buy_confidence > sell_confidence:
                signal_type = SignalType.BUY
                confidence = min(buy_confidence, 1.0)
                reason = f"Buy signals: {', '.join(buy_signals)}"
            elif sell_confidence >= 0.6 and sell_confidence > buy_confidence:
                signal_type = SignalType.SELL
                confidence = min(sell_confidence, 1.0)
                reason = f"Sell signals: {', '.join(sell_signals)}"
            else:
                signal_type = SignalType.HOLD
                confidence = 0.5
                reason = "Mixed or weak signals"
            
            return TradingSignal(
                symbol=symbol_str,
                signal_type=signal_type,
                confidence=confidence,
                price=current_price,
                indicators=indicators,
                timestamp=datetime.now(),
                reason=reason
            )
            
        except Exception as e:
            algorithm.Debug(f"Error analyzing {symbol_str}: {str(e)}")
            return TradingSignal(
                symbol=symbol_str, signal_type=SignalType.HOLD, confidence=0.0,
                price=0.0, indicators={}, timestamp=datetime.now(),
                reason=f"Analysis error: {str(e)}"
            )


class AgenticTradingAlgorithm(QCAlgorithm):
    """QuantConnect Algorithm Implementation"""
    
    def Initialize(self):
        """Initialize the algorithm"""
        # Set start and end dates
        self.set_start_date(2024, 1, 1)
        self.set_end_date(2024, 12, 31)
        
        # Set initial cash
        self.set_cash(100000)
        
        # Algorithm parameters
        self.min_confidence = 0.7
        self.max_positions = 5
        self.position_size = 0.05  # 5% of portfolio per position
        self.stop_loss_pct = 0.03  # 3% stop loss
        
        # Initialize components
        self.analyzer = TechnicalAnalyzer(self)
        self.strategy = TradingStrategy(self.analyzer)
        
        # Tracking variables
        self.positions_count = 0
        self.recent_trades = {}
        self.wash_trade_cooldown = timedelta(hours=4)
        
        # Define universe - S&P 100 stocks (subset for testing)
#        self.sp100_symbols = [
#            "AAPL.US", "ABBV.US", "ABT.US", "ACN.US", "ADBE.US", "AIG.US", "AMGN.US", "AMT.US", "AMZN.US", "AVGO.US",
#            "AXP.US", "BA.US", "BAC.US", "BIIB.US", "BK.US", "BKNG.US", "BLK.US", "BMY.US", "C.US", "CAT.US",
#            "CHTR.US", "CL.US", "CMCSA.US", "COF.US", "COP.US", "COST.US", "CRM.US", "CSCO.US", "CVS.US", "CVX.US",
#            "DHR.US", "DIS.US", "DOW.US", "DUK.US", "EMR.US", "EXC.US", "F.US", "FDX.US", "GD.US", "GE.US", "GILD.US",
#            "GM.US", "GOOG.US", "GOOGL.US", "GS.US", "HD.US", "HON.US", "IBM.US", "INTC.US", "JNJ.US", "JPM.US",
#            "KHC.US", "KMI.US", "KO.US", "LIN.US", "LLY.US", "LMT.US", "LOW.US", "MA.US", "MCD.US", "MDLZ.US", "MDT.US",
#            "MET.US", "META.US", "MMM.US", "MO.US", "MRK.US", "MS.US", "MSFT.US", "NEE.US", "NFLX.US", "NKE.US",
#            "NVDA.US", "ORCL.US", "PEP.US", "PFE.US", "PG.US", "PM.US", "PYPL.US", "QCOM.US", "RTX.US", "SBUX.US",
#            "SCHW.US", "SO.US", "SPG.US", "T.US", "TGT.US", "TMO.US", "TMUS.US", "TSLA.US", "TXN.US", "UNH.US",
#            "UNP.US", "UPS.US", "USB.US", "V.US", "VZ.US", "WBA.US", "WFC.US", "WMT.US", "XOM.US"
#        ]
        self.sp100_symbols = [
            "AAPL", "ABBV", "ABT", "ACN", "ADBE", "AIG", "AMGN", "AMT", "AMZN", "AVGO",
            "AXP", "BA", "BAC", "BIIB", "BK", "BKNG", "BLK", "BMY", "C", "CAT",
            "CHTR", "CL", "CMCSA", "COF", "COP", "COST", "CRM", "CSCO", "CVS", "CVX",
            "DHR", "DIS", "DOW", "DUK", "EMR", "EXC", "F", "FDX", "GD", "GE", "GILD",
            "GM", "GOOG", "GOOGL", "GS", "HD", "HON", "IBM", "INTC", "JNJ", "JPM",
            "KHC", "KMI", "KO", "LIN", "LLY", "LMT", "LOW", "MA", "MCD", "MDLZ", "MDT",
            "MET", "META", "MMM", "MO", "MRK", "MS", "MSFT", "NEE", "NFLX", "NKE",
            "NVDA", "ORCL", "PEP", "PFE", "PG", "PM", "PYPL", "QCOM", "RTX", "SBUX",
            "SCHW", "SO", "SPG", "T", "TGT", "TMO", "TMUS", "TSLA", "TXN", "UNH",
            "UNP", "UPS", "USB", "V", "VZ", "WBA", "WFC", "WMT", "XOM"
        ]        
        # Add symbols to universe
        for symbol_str in self.sp100_symbols:
            equity = self.add_equity(symbol_str, Resolution.DAILY)
            equity.set_data_normalization_mode(DataNormalizationMode.ADJUSTED)
            symbols = equity.symbol
        
        self.schedule.on(self.date_rules.every_day(), self.time_rules.after_market_open("SPY", 30), self.ScanAndTrade)
        
        # Warm up algorithm with historical data
        self.set_warm_up(timedelta(days=365))
        
        self.log(f"Algorithm initialized with {len(self.sp100_symbols)} symbols")

    def ScanAndTrade(self):
        """Main trading logic - scan stocks and execute trades"""
        if self.is_warming_up:
            return
            
        self.log(f"Starting market scan at {self.time}")
        
        signals = []

        # Scan watchlist for opportunities
        for symbol_str in self.sp100_symbols:
            try:
                symbol_obj = Symbol.create(symbol_str, SecurityType.EQUITY, Market.USA)
                
                if not self.securities.contains_key(symbol_obj):
                    continue
                
                # Get historical data (90 days)
                history = self.history(symbol_obj, 90, Resolution.DAILY)
                
                if history.empty or len(history) < 50:
                    continue
                
                # Convert to expected format
                df = history.reset_index()
                df.columns = ['symbol', 'time', 'open', 'high', 'low', 'close', 'volume']

                # Generate signal
                signal = self.strategy.analyze_stock(symbol_str, df)
                signals.append(signal)
                
                self.log(f"{symbol_str}: {signal.signal_type.value} "
                          f"(confidence: {signal.confidence:.2f}) - {signal.reason}")
                
                # Execute trades based on signals
                if signal.confidence >= self.min_confidence:
                    if signal.signal_type == SignalType.BUY:
                        if self.positions_count < self.max_positions:
                            self.ExecuteBuy(symbol_obj, signal)
                        else:
                            self.log(f"Max positions reached, skipping buy for {symbol_str}")
                    
                    elif signal.signal_type == SignalType.SELL:
                        self.ExecuteSell(symbol_obj, signal)
                        
            except Exception as e:
                self.log(f"Error analyzing {symbol_str}: {str(e)}")
        
        # Log portfolio status
        self.LogPortfolioStatus()
        
        self.log(f"Market scan completed. Found {len(signals)} signals.")

    def ExecuteBuy(self, symbol_obj, signal: TradingSignal):
        """Execute buy order"""
        try:
            # Check if we can trade this symbol (wash sale prevention)
            if not self.CanTradeSymbol(symbol_obj.Value, 'buy'):
                self.log(f"Skipping buy for {symbol_obj.Value} due to wash trade prevention")
                return
            
            # Check if already have position
            if self.portfolio[symbol_obj].invested:
                self.log(f"Already have position in {symbol_obj.Value}")
                return
            
            # Calculate position size
            portfolio_value = self.portfolio.total_portfolio_value
            target_value = portfolio_value * self.position_size
            current_price = self.securities[symbol_obj].price
            
            if current_price <= 0:
                self.log(f"Invalid price for {symbol_obj.Value}: {current_price}")
                return
            
            quantity = int(target_value / current_price)
            
            if quantity <= 0:
                self.log(f"Invalid quantity for {symbol_obj.Value}: {quantity}")
                return
            
            # Place market order
            ticket = self.market_order(symbol_obj, quantity)
            
            if ticket.status == OrderStatus.FILLED:
                self.positions_count += 1
                self.RecordTrade(symbol_obj.Value, 'buy')
                self.log(f"Successfully bought {quantity} shares of {symbol_obj.Value} at ${current_price:.2f}")
                
                # Set stop loss
                stop_price = current_price * (1 - self.stop_loss_pct)
                self.stop_market_order(symbol_obj, -quantity, stop_price)
                self.log(f"Stop loss set for {symbol_obj.value} at ${stop_price:.2f}")
            else:
                self.log(f"Buy order failed for {symbol_obj.value}. Status: {ticket.status}")
                
        except Exception as e:
            self.log(f"Error executing buy order for {symbol_obj.Value}: {str(e)}")

    def ExecuteSell(self, symbol_obj, signal: TradingSignal):
        """Execute sell order"""
        try:
            # Check if we can trade this symbol (wash sale prevention)
            if not self.CanTradeSymbol(symbol_obj.Value, 'sell'):
                self.log(f"Skipping sell for {symbol_obj.Value} due to wash trade prevention")
                return
            
            # Check if we have position to sell
            if not self.portfolio[symbol_obj].invested or self.portfolio[symbol_obj].quantity <= 0:
                self.log(f"No position to sell in {symbol_obj.value}")
                return
            
            quantity = self.portfolio[symbol_obj].quantity
            
            # Cancel any existing stop loss orders
            open_orders = self.transactions.get_open_orders(symbol_obj)
            for order in open_orders:
                if order.type == OrderType.STOP_MARKET and order.direction == OrderDirection.SELL:
                    self.transactions.cancel_order(order.id)
                    self.log(f"Cancelled stop loss order for {symbol_obj.value}")
            
            # Place market sell order
            ticket = self.market_order(symbol_obj, -quantity)
            
            if ticket.status == OrderStatus.FILLED:
                self.positions_count -= 1
                self.RecordTrade(symbol_obj.value, 'sell')
                
                # Calculate P&L
                entry_price = self.portfolio[symbol_obj].average_price
                current_price = self.securities[symbol_obj].price
                realized_pnl = (current_price - entry_price) * quantity
                
                self.log(f"Successfully sold {quantity} shares of {symbol_obj.value} at ${current_price:.2f}")
                self.log(f"Realized P&L for {symbol_obj.value}: ${realized_pnl:.2f}")
            else:
                self.log(f"Sell order failed for {symbol_obj.value}. Status: {ticket.status}")
                
        except Exception as e:
            self.log(f"Error executing sell order for {symbol_obj.value}: {str(e)}")

    def CanTradeSymbol(self, symbol: str, side: str) -> bool:
        """Check if we can trade this symbol without triggering wash trade rules"""
        if symbol not in self.recent_trades:
            return True
        
        last_trade_time, last_side = self.recent_trades[symbol]
        time_since_trade = self.time - last_trade_time
        
        # If we're trying to do the opposite side within cooldown period
        if last_side != side and time_since_trade < self.wash_trade_cooldown:
            return False
        
        return True
    
    def RecordTrade(self, symbol: str, side: str):
        """Record a trade to track for wash trade prevention"""
        self.recent_trades[symbol] = (self.time, side)

    def LogPortfolioStatus(self):
        """Log current portfolio status"""
        try:
            total_value = self.portfolio.total_portfolio_value
            cash = self.portfolio.cash
            total_unrealized_pnl = self.portfolio.total_unrealized_profit
            
            self.log("=== PORTFOLIO STATUS ===")
            self.log(f"Total Portfolio Value: ${total_value:,.2f}")
            self.log(f"Cash: ${cash:,.2f}")
            self.log(f"Open Positions: {self.positions_count}")
            self.log(f"Total Unrealized P&L: ${total_unrealized_pnl:,.2f}")
            
            # Log individual positions
            for kvp in self.portfolio:
                security = kvp.value
                if security.invested:
                    symbol_name = security.symbol.value
                    quantity = security.quantity
                    entry_price = security.average_price
                    current_price = security.price
                    unrealized_pnl = security.unrealized_profit
                    
                    if entry_price > 0:
                        pnl_pct = (unrealized_pnl / (entry_price * abs(quantity))) * 100
                    else:
                        pnl_pct = 0.0
                    
                    self.log(f"  {symbol_name}: {quantity} shares, "
                              f"Entry: ${entry_price:.2f}, "
                              f"Current: ${current_price:.2f}, "
                              f"P&L: ${unrealized_pnl:.2f} ({pnl_pct:.1f}%)")
            
            self.log("========================")
            
        except Exception as e:
            self.log(f"Error logging portfolio status: {str(e)}")

    def OnOrderEvent(self, orderEvent: OrderEvent):
        """Handle order events"""
        if orderEvent.status == OrderStatus.FILLED:
            self.log(f"Order filled: {orderEvent.symbol} {orderEvent.direction} "
                      f"{orderEvent.fill_quantity} shares at ${orderEvent.fill_price:.2f}")

    def OnData(self, data: Slice):
        """Handle incoming data"""
        # Main trading logic is handled in scheduled function
        self.ScanAndTrade()


# AI Enhancement Integration Guide

## Overview
This guide shows how to integrate the AI models into your QuantConnect trading algorithm.

## AI Components Available

### 1. **Volatility Regime Predictor** (`ai_models.py`)
- **Purpose**: Predict market volatility regimes (LOW/MEDIUM/HIGH)
- **Technology**: Random Forest Classifier with rolling volatility features
- **Use Case**: Adjust position sizing and stop-losses based on predicted volatility

### 2. **Return Predictor** (`ai_models.py`)
- **Purpose**: Predict next-day returns for individual stocks
- **Technology**: Gradient Boosting Regressor with technical features
- **Use Case**: Stock ranking, entry signal generation, portfolio construction

### 3. **PCA Feature Reducer** (`ai_models.py`)
- **Purpose**: Reduce dimensionality of features for portfolio optimization
- **Technology**: Principal Component Analysis
- **Use Case**: Identify principal factors driving returns, reduce overfitting

### 4. **Portfolio Optimizer** (`ai_models.py`)
- **Purpose**: ML-based portfolio optimization using predicted returns
- **Technology**: Mean-variance optimization with ML predictions
- **Use Case**: Optimal position sizing across multiple stocks

### 5. **Sentiment Analyzer** (`ai_advanced.py`)
- **Purpose**: Analyze news sentiment for trading signals
- **Technology**: Keyword-based + extensible to LLM integration
- **Use Case**: Filter trades based on news sentiment, avoid negative sentiment stocks

### 6. **LLM Research Analyzer** (`ai_advanced.py`)
- **Purpose**: Framework for LLM-based fundamental analysis
- **Technology**: Template for OpenAI/Claude API integration
- **Use Case**: Deep fundamental analysis, stock screening, RAG applications

### 7. **Reinforcement Learning Agent** (`ai_advanced.py`)
- **Purpose**: Learn optimal position sizing through trial and error
- **Technology**: Q-Learning with market state discretization
- **Use Case**: Adaptive position sizing, hedging decisions

### 8. **Statistical Arbitrage Detector** (`ai_advanced.py`)
- **Purpose**: Find pairs trading opportunities
- **Technology**: Correlation analysis and mean reversion
- **Use Case**: Pairs trading, market-neutral strategies

### 9. **Adaptive Risk Manager** (`ai_advanced.py`)
- **Purpose**: Dynamically adjust risk parameters based on performance
- **Technology**: Performance-based parameter adaptation
- **Use Case**: Adaptive stop-losses, dynamic position sizing

## Integration Steps

### Step 1: Import AI Modules in main.py

```python
from AlgorithmImports import *
from ai_models import (
    VolatilityRegimePredictor,
    ReturnPredictor,
    PCAFeatureReducer,
    PortfolioOptimizer,
    RiskMetricsCalculator
)
from ai_advanced import (
    SentimentAnalyzer,
    ReinforcementLearningAgent,
    StatisticalArbitrageDetector,
    AdaptiveRiskManager
)
```

### Step 2: Initialize AI Components in `initialize()`

```python
def initialize(self):
    # ... existing initialization code ...
    
    # AI Components
    self.volatility_predictor = VolatilityRegimePredictor(self.logger)
    self.portfolio_optimizer = PortfolioOptimizer(self.logger)
    self.sentiment_analyzer = SentimentAnalyzer(self.logger)
    self.rl_agent = ReinforcementLearningAgent(self.logger)
    self.stat_arb_detector = StatisticalArbitrageDetector(self.logger)
    self.adaptive_risk = AdaptiveRiskManager(self.logger)
    self.risk_metrics = RiskMetricsCalculator(self.logger)
    
    # Return predictors for each symbol (created dynamically)
    self.return_predictors = {}
    
    # Schedule AI model training
    self.schedule.on(
        self.date_rules.week_start(),
        self.time_rules.at(9, 0),
        self._train_ai_models
    )
```

### Step 3: Update Data Feed to AI Models

```python
def on_data(self, data):
    """Update AI models with new market data"""
    
    # Update volatility predictor with SPY data
    if self.spy in data and data[self.spy] is not None:
        spy_price = float(data[self.spy].close)
        spy_volume = float(data[self.spy].volume)
        self.volatility_predictor.update(spy_price, spy_volume)
    
    # Update return predictors and stat arb detector
    for symbol in self._all_symbols:
        if symbol in data and data[symbol] is not None:
            price = float(data[symbol].close)
            volume = float(data[symbol].volume)
            
            # Create return predictor if doesn't exist
            if symbol not in self.return_predictors:
                self.return_predictors[symbol] = ReturnPredictor(self.logger, symbol)
                self.portfolio_optimizer.add_predictor(symbol, self.return_predictors[symbol])
            
            # Update models
            self.return_predictors[symbol].update(price, volume)
            self.stat_arb_detector.update_prices(symbol, price)
```

### Step 4: AI-Enhanced Regime Detection

Replace your existing `_detect_market_regime()` with AI-enhanced version:

```python
def _detect_market_regime(self):
    """AI-Enhanced market regime detection"""
    
    try:
        # Train volatility predictor if enough data
        if len(self.volatility_predictor.volatility_history) >= 126:
            if not self.volatility_predictor.trained:
                self.volatility_predictor.train()
        
        # Predict volatility regime
        volatility_regime = self.volatility_predictor.predict_regime()
        
        # Your existing EMA-based regime detection
        # ... existing code ...
        
        # Combine traditional and AI regime detection
        self.market_regime = self._combine_regimes(
            traditional_regime,  # Your EMA-based regime
            volatility_regime    # AI predicted volatility
        )
        
        self.logger.info(f"Market: {self.market_regime}, Volatility: {volatility_regime}")
        
    except Exception as e:
        self.logger.error(f"AI regime detection error: {e}")

def _combine_regimes(self, traditional, volatility):
    """Combine traditional and AI regime predictions"""
    
    # Conservative approach: downgrade to NEUTRAL if high volatility
    if volatility == "HIGH" and traditional == "BULL":
        return "NEUTRAL"
    elif volatility == "HIGH" and traditional == "NEUTRAL":
        return "BEAR"
    
    return traditional
```

### Step 5: AI-Enhanced Signal Evaluation

```python
def _evaluate_signals(self):
    """AI-Enhanced signal evaluation"""
    
    if self.is_warming_up:
        return
    
    # Train return predictors periodically
    for symbol in self._all_symbols:
        if symbol in self.return_predictors:
            predictor = self.return_predictors[symbol]
            if len(predictor.price_history) >= 60 and not predictor.trained:
                predictor.train()
    
    # Get AI predictions for all symbols
    predictions = {}
    for symbol in self._all_symbols:
        if symbol in self.return_predictors and self.return_predictors[symbol].trained:
            pred = self.return_predictors[symbol].predict_return()
            predictions[symbol] = pred
    
    # Rank symbols by predicted return
    ranked_symbols = sorted(
        predictions.items(),
        key=lambda x: x[1],
        reverse=True
    )
    
    # Get sentiment signals
    sentiment_filter = {}
    for symbol, _ in ranked_symbols[:20]:  # Top 20 by prediction
        sentiment = self.sentiment_analyzer.get_sentiment_signal(symbol)
        sentiment_filter[symbol] = sentiment
    
    # Apply traditional + AI filters for entry
    for symbol, predicted_return in ranked_symbols[:10]:
        
        # Skip if negative sentiment
        if sentiment_filter.get(symbol) == "BEARISH":
            self.debug(f"SKIP {symbol}: Negative sentiment")
            continue
        
        # Skip if negative predicted return
        if predicted_return < 0.001:  # At least 0.1% predicted return
            continue
        
        # Check traditional technical signals
        if not self._check_traditional_signals(symbol):
            continue
        
        # Calculate position size using RL agent
        state = self.rl_agent.get_state(
            self.market_regime,
            self.volatility_predictor.current_regime,
            self.portfolio.total_portfolio_value / self._starting_cash - 1,
            self._calculate_current_drawdown()
        )
        
        rl_position_size = self.rl_agent.get_optimal_position_size(state)
        
        # Get adaptive stop loss
        stop_loss = self.adaptive_risk.get_stop_loss(
            self.volatility_predictor.current_regime
        )
        
        # Execute trade with AI-enhanced parameters
        self._enter_position(symbol, predicted_return, rl_position_size, stop_loss)
```

### Step 6: AI-Enhanced Position Sizing

```python
def _calculate_position_size(self, symbol, current_price, predicted_return=0.0, rl_size=0.5):
    """AI-Enhanced position sizing"""
    
    try:
        available_cash = self.portfolio.cash
        
        # Get adaptive position size from adaptive risk manager
        confidence = abs(predicted_return) * 100  # Scale to 0-1
        adaptive_size = self.adaptive_risk.get_position_size(confidence)
        
        # Combine RL and adaptive sizing
        combined_size = (rl_size * 0.6 + adaptive_size * 0.4)
        
        # Apply to available cash
        target_value = available_cash * combined_size * 0.85
        
        if target_value < 4000:
            return 0
        
        qty = int(target_value / current_price)
        
        # Account for fees
        fee = self._estimate_order_fee(symbol, qty, current_price)
        total_cost = qty * current_price + fee
        
        if total_cost > target_value:
            qty = int((target_value - fee) / current_price)
        
        return qty if qty > 0 else 0
        
    except Exception as e:
        self.logger.error(f"AI position sizing error: {e}")
        return 0
```

### Step 7: Train AI Models Periodically

```python
def _train_ai_models(self):
    """Weekly training of AI models"""
    
    self.logger.info("=" * 60)
    self.logger.info("TRAINING AI MODELS")
    
    try:
        # Train volatility predictor
        if not self.volatility_predictor.trained:
            success = self.volatility_predictor.train()
            if success:
                self.logger.info("✓ Volatility predictor trained")
        
        # Train return predictors
        trained_count = 0
        for symbol, predictor in self.return_predictors.items():
            if not predictor.trained and len(predictor.price_history) >= 60:
                if predictor.train():
                    trained_count += 1
        
        if trained_count > 0:
            self.logger.info(f"✓ Trained {trained_count} return predictors")
        
        # Find pairs for statistical arbitrage
        pairs = self.stat_arb_detector.find_pairs(list(self._all_symbols))
        if pairs:
            self.logger.info(f"✓ Found {len(pairs)} stat arb pairs")
            for s1, s2, corr in pairs[:3]:
                self.logger.info(f"  {s1} <-> {s2}: {corr:.3f}")
        
    except Exception as e:
        self.logger.error(f"AI training error: {e}")
    
    self.logger.info("=" * 60)
```

### Step 8: Update Risk Metrics

```python
def on_end_of_day(self, symbol):
    """Update AI risk metrics daily"""
    
    # Calculate daily return
    current_equity = self.portfolio.total_portfolio_value
    daily_return = (current_equity - self._starting_cash) / self._starting_cash
    
    # Update risk metrics calculator
    self.risk_metrics.update(daily_return)
    
    # Calculate advanced metrics
    var_95 = self.risk_metrics.calculate_var(0.95)
    cvar_95 = self.risk_metrics.calculate_cvar(0.95)
    sharpe = self.risk_metrics.calculate_sharpe_ratio()
    max_dd = self.risk_metrics.calculate_max_drawdown()
    
    # Log metrics
    self.logger.info(f"Risk Metrics - VaR: {var_95:.3%}, CVaR: {cvar_95:.3%}, "
                    f"Sharpe: {sharpe:.2f}, MaxDD: {max_dd:.3%}")
```

## Performance Considerations

1. **Model Training Frequency**
   - Train weekly or bi-weekly to avoid overfitting
   - Use sufficient historical data (60-252 days minimum)

2. **Computation Limits**
   - QuantConnect has memory and CPU limits
   - Don't train too many models simultaneously
   - Use efficient NumPy operations

3. **Model Persistence**
   - Save trained models to Object Store for live trading
   - Reload models on algorithm restart

4. **Backtesting vs Live**
   - Some AI features work better in live (sentiment, news)
   - Backtest without lookahead bias
   - Use proper train/test splits

## Advanced Enhancements

### 1. LLM Integration (For Live Trading)

```python
# In initialize():
self.llm_analyzer = LLMResearchAnalyzer(self.logger, api_key="your-key")

# In stock evaluation:
fundamental_data = {
    'pe_ratio': self.securities[symbol].fundamentals.valuation_ratios.pe_ratio,
    'revenue_growth': self.securities[symbol].fundamentals.operation_ratios.revenue_growth.one_year,
    'profit_margin': self.securities[symbol].fundamentals.operation_ratios.net_margin
}

analysis = self.llm_analyzer.analyze_company(symbol, fundamental_data)
if analysis['score'] < 50:
    self.debug(f"SKIP {symbol}: Low LLM score {analysis['score']}")
    continue
```

### 2. News Sentiment Integration

```python
# Subscribe to news data
self.add_data(TradingEconomicsCalendar, "USA")

def on_data(self, data):
    # Process news
    if data.contains_key("TradingEconomicsCalendar"):
        news_item = data["TradingEconomicsCalendar"]
        
        # Analyze sentiment
        for symbol in self._core_symbols:
            if symbol.value in news_item.text:
                self.sentiment_analyzer.update_sentiment(
                    symbol,
                    [news_item.text]
                )
```

### 3. Reinforcement Learning Updates

```python
def on_order_event(self, order_event):
    """Update RL agent when trades complete"""
    
    if order_event.status != OrderStatus.FILLED:
        return
    
    symbol = order_event.symbol
    
    # When closing a position, calculate reward
    if not self.portfolio[symbol].invested:
        pnl = self._calculate_trade_pnl(symbol)
        
        # Update RL agent
        reward = self.rl_agent.calculate_reward(pnl)
        
        # Get current state
        current_state = self.rl_agent.get_state(
            self.market_regime,
            self.volatility_predictor.current_regime,
            self.portfolio.total_portfolio_value / self._starting_cash - 1,
            self._calculate_current_drawdown()
        )
        
        # Update Q-values
        last_state = self.entry_state.get(symbol, current_state)
        last_action = self.entry_action.get(symbol, 0.5)
        
        self.rl_agent.update_q_value(
            last_state,
            last_action,
            reward,
            current_state
        )
```

## Example: Complete AI-Enhanced Algorithm Workflow

```
1. Market Opens
   ↓
2. Update AI Models with New Data
   - Volatility Predictor
   - Return Predictors
   - Stat Arb Detector
   ↓
3. Detect Market Regime (Traditional + AI)
   - EMA crossovers
   - AI volatility prediction
   ↓
4. Generate Trading Signals
   - ML Return Predictions
   - Sentiment Analysis
   - Traditional Technical Indicators
   ↓
5. Rank & Filter Opportunities
   - Top predicted returns
   - Positive sentiment
   - Technical confirmation
   ↓
6. Calculate Position Sizes
   - RL Agent recommendation
   - Adaptive Risk Manager
   - Portfolio Optimizer
   ↓
7. Execute Trades
   - AI-enhanced stop losses
   - Dynamic position sizing
   ↓
8. Monitor & Manage
   - Update risk metrics
   - Train RL agent on outcomes
   - Adapt parameters
```

## Next Steps

1. **Start Simple**: Integrate one AI component at a time
2. **Backtest Thoroughly**: Validate each enhancement
3. **Monitor Performance**: Track AI vs traditional signals
4. **Iterate**: Refine based on results

## Recommended Integration Order

1. ✅ Volatility Regime Predictor (easiest, high impact)
2. ✅ Return Predictor (moderate complexity, high value)
3. ✅ Adaptive Risk Manager (simple, immediate benefit)
4. ✅ Sentiment Analyzer (requires news feed)
5. ✅ Portfolio Optimizer (for multiple positions)
6. ✅ RL Agent (complex, requires tuning)
7. ✅ Statistical Arbitrage (advanced strategy)
8. ✅ LLM Integration (requires API, best for live)

# Quick Reference Guide - AI Components

## 📋 Component Selection Guide

| Your Goal | Use This Component | Difficulty | Impact |
|-----------|-------------------|------------|--------|
| Better regime detection | `VolatilityRegimePredictor` | ⭐ Easy | 🔥 High |
| Predict stock returns | `ReturnPredictor` | ⭐⭐ Medium | 🔥🔥 Very High |
| Dynamic stop-losses | `AdaptiveRiskManager` | ⭐ Easy | 🔥 High |
| Optimal position sizing | `PortfolioOptimizer` | ⭐⭐ Medium | 🔥 High |
| News filtering | `SentimentAnalyzer` | ⭐ Easy | 🔥 Medium |
| Learn from outcomes | `ReinforcementLearningAgent` | ⭐⭐⭐ Hard | 🔥🔥 Very High |
| Find trading pairs | `StatisticalArbitrageDetector` | ⭐⭐ Medium | 🔥 Medium |
| Better risk metrics | `RiskMetricsCalculator` | ⭐ Easy | 🔥 Medium |
| Fundamental analysis | `LLMResearchAnalyzer` | ⭐⭐⭐ Hard | 🔥🔥 Very High |
| Reduce features | `PCAFeatureReducer` | ⭐⭐ Medium | 🔥 Low |

---

## 🚀 Quick Start Snippets

### 1. Volatility Regime Prediction (Easiest)

```python
# In initialize():
from ai_models import VolatilityRegimePredictor
self.vol_predictor = VolatilityRegimePredictor(self.logger)

# In on_data():
self.vol_predictor.update(spy_price, spy_volume)

# Weekly training:
if not self.vol_predictor.trained:
    self.vol_predictor.train()

# Get prediction:
vol_regime = self.vol_predictor.predict_regime()  # "LOW", "MEDIUM", "HIGH"

# Use in trading:
if vol_regime == "HIGH":
    max_positions = 2  # Reduce exposure
    stop_loss = 0.05  # Wider stops
else:
    max_positions = 5
    stop_loss = 0.03
```

---

### 2. Return Prediction (High Impact)

```python
# In initialize():
from ai_models import ReturnPredictor
self.predictors = {}

# Create predictor for each symbol:
for symbol in self.symbols:
    self.predictors[symbol] = ReturnPredictor(self.logger, symbol)

# In on_data():
self.predictors[symbol].update(price, volume)

# Train when enough data:
if len(self.predictors[symbol].price_history) >= 60:
    self.predictors[symbol].train()

# Get predictions:
predictions = {
    symbol: predictor.predict_return() 
    for symbol, predictor in self.predictors.items() 
    if predictor.trained
}

# Rank and trade:
ranked = sorted(predictions.items(), key=lambda x: x[1], reverse=True)
top_picks = ranked[:5]  # Top 5 predicted returns
```

---

### 3. Adaptive Risk Management (Quick Win)

```python
# In initialize():
from ai_advanced import AdaptiveRiskManager
self.adaptive_risk = AdaptiveRiskManager(self.logger)

# After each trade closes:
pnl_pct = (exit_price - entry_price) / entry_price
was_stop_loss = reason == "stop_loss"
self.adaptive_risk.update_trade_result(pnl_pct, was_stop_loss)

# Get dynamic parameters:
stop_loss = self.adaptive_risk.get_stop_loss(volatility_regime)
position_size = self.adaptive_risk.get_position_size(confidence=0.8)

# Use in exits:
if current_pnl <= -stop_loss:
    self.liquidate(symbol, "adaptive_stop")
```

---

### 4. Portfolio Optimization (Multiple Positions)

```python
# In initialize():
from ai_models import PortfolioOptimizer
self.optimizer = PortfolioOptimizer(self.logger)

# Add predictors:
for symbol in symbols:
    predictor = ReturnPredictor(self.logger, symbol)
    self.optimizer.add_predictor(symbol, predictor)

# Get optimal weights:
predictions = self.optimizer.predict_returns(symbols)
optimal_weights = self.optimizer.calculate_optimal_weights(
    symbols, 
    predictions, 
    risk_aversion=0.5  # 0 = aggressive, 1 = conservative
)

# Allocate capital:
for symbol, weight in optimal_weights.items():
    target_value = portfolio_value * weight
    qty = int(target_value / current_price)
    self.market_order(symbol, qty)
```

---

### 5. Sentiment Analysis (News Filtering)

```python
# In initialize():
from ai_advanced import SentimentAnalyzer
self.sentiment = SentimentAnalyzer(self.logger)

# Update with news (if you have news feed):
news_items = ["Stock surges on earnings beat", "CEO announces layoffs"]
self.sentiment.update_sentiment(symbol, news_items)

# Get sentiment:
sentiment_score = self.sentiment.get_sentiment_score(symbol)  # -1 to +1
sentiment_signal = self.sentiment.get_sentiment_signal(symbol)  # "BULLISH"/"BEARISH"/"NEUTRAL"

# Filter trades:
if sentiment_signal == "BEARISH":
    self.debug(f"Skip {symbol} - negative sentiment")
    continue
```

---

### 6. Reinforcement Learning (Advanced)

```python
# In initialize():
from ai_advanced import ReinforcementLearningAgent
self.rl_agent = ReinforcementLearningAgent(self.logger)

# Get state:
state = self.rl_agent.get_state(
    market_regime="BULL",
    volatility_regime="LOW", 
    portfolio_return=0.05,
    drawdown=0.02
)

# Select action (position size):
position_size = self.rl_agent.select_action(state)  # 0.0 to 1.0

# After trade closes, update Q-values:
reward = self.rl_agent.calculate_reward(trade_pnl)
self.rl_agent.update_q_value(entry_state, action, reward, current_state)
```

---

### 7. Statistical Arbitrage (Pairs Trading)

```python
# In initialize():
from ai_advanced import StatisticalArbitrageDetector
self.stat_arb = StatisticalArbitrageDetector(self.logger)

# Update prices:
self.stat_arb.update_prices(symbol, price)

# Find pairs:
pairs = self.stat_arb.find_pairs(symbols)  # [(sym1, sym2, correlation), ...]

# Get mean reversion signal:
for sym1, sym2, corr in pairs:
    signal = self.stat_arb.get_mean_reversion_signal(sym1, sym2)
    
    if signal == "LONG_1_SHORT_2":
        self.buy(sym1)
        self.short(sym2)
    elif signal == "SHORT_1_LONG_2":
        self.short(sym1)
        self.buy(sym2)
```

---

### 8. Risk Metrics (Monitoring)

```python
# In initialize():
from ai_models import RiskMetricsCalculator
self.risk_calc = RiskMetricsCalculator(self.logger)

# Update daily:
daily_return = (current_equity - prev_equity) / prev_equity
self.risk_calc.update(daily_return)

# Calculate metrics:
var_95 = self.risk_calc.calculate_var(0.95)
cvar_95 = self.risk_calc.calculate_cvar(0.95)
sharpe = self.risk_calc.calculate_sharpe_ratio()
max_dd = self.risk_calc.calculate_max_drawdown()

# Log or use in decisions:
self.logger.info(f"VaR: {var_95:.2%}, CVaR: {cvar_95:.2%}, Sharpe: {sharpe:.2f}")
```

---

## 🔧 Common Patterns

### Pattern 1: Train-Predict-Act

```python
# 1. Train (weekly or when enough data)
if not model.trained and len(model.history) >= threshold:
    model.train()

# 2. Predict (every evaluation)
if model.trained:
    prediction = model.predict()

# 3. Act (based on prediction)
if prediction > threshold:
    self.buy(symbol)
```

### Pattern 2: Update-Calculate-Adapt

```python
# 1. Update (on new data)
model.update(new_data)

# 2. Calculate (get current metrics)
metric = model.calculate_metric()

# 3. Adapt (change behavior)
if metric > threshold:
    self.adjust_strategy()
```

### Pattern 3: State-Action-Reward (RL)

```python
# 1. Get State
state = get_current_state()

# 2. Select Action
action = rl_agent.select_action(state)

# 3. Execute
self.execute(action)

# 4. Observe Reward
reward = calculate_reward(outcome)

# 5. Learn
next_state = get_current_state()
rl_agent.update_q_value(state, action, reward, next_state)
```

---

## ⚡ Performance Tips

### Minimize Training Frequency
```python
# ❌ Bad: Train every bar
if data:
    model.train()

# ✅ Good: Train weekly
self.schedule.on(self.date_rules.week_start(), ..., self._train_models)
```

### Use Efficient Data Structures
```python
# ❌ Bad: Python list
self.history = []

# ✅ Good: Fixed-size deque
self.history = deque(maxlen=252)
```

### Batch Operations
```python
# ❌ Bad: Loop over symbols
for symbol in symbols:
    prediction = model.predict(symbol)

# ✅ Good: Vectorize
predictions = model.predict_batch(symbols)
```

### Check Model Readiness
```python
# ❌ Bad: Always predict
prediction = model.predict()

# ✅ Good: Check if ready
if model.trained and len(model.history) >= min_data:
    prediction = model.predict()
```

---

## 🎯 Trading Strategy Templates

### Strategy 1: ML-Enhanced Momentum
```python
# Combine traditional momentum + ML prediction
def evaluate_signal(symbol):
    # Traditional
    macd_signal = check_macd(symbol)
    
    # AI
    predicted_return = predictor.predict_return()
    sentiment = sentiment_analyzer.get_sentiment_score(symbol)
    
    # Combine
    if macd_signal and predicted_return > 0.01 and sentiment > 0:
        return "BUY"
    return "HOLD"
```

### Strategy 2: Regime-Adaptive
```python
# Change strategy based on AI regime detection
regime = vol_predictor.predict_regime()

if regime == "LOW":
    # Aggressive in low vol
    max_positions = 10
    leverage = 2.0
    stop_loss = 0.02
elif regime == "MEDIUM":
    # Moderate
    max_positions = 5
    leverage = 1.0
    stop_loss = 0.03
else:  # HIGH
    # Conservative
    max_positions = 2
    leverage = 1.0
    stop_loss = 0.05
```

### Strategy 3: Portfolio Optimization
```python
# Daily rebalancing based on ML predictions
predictions = {sym: predictor.predict() for sym, predictor in predictors.items()}
weights = optimizer.calculate_optimal_weights(symbols, predictions)

for symbol, weight in weights.items():
    target_qty = int(portfolio_value * weight / price)
    current_qty = portfolio[symbol].quantity
    delta = target_qty - current_qty
    
    if abs(delta) > threshold:
        self.market_order(symbol, delta)
```

---

## 🐛 Debugging Checklist

### Model Not Training?
- [ ] Enough data? (Check `len(model.history)`)
- [ ] Data valid? (No NaN, inf values)
- [ ] Training frequency correct? (Not too frequent)
- [ ] Exceptions caught? (Check logs)

### Predictions Not Working?
- [ ] Model trained? (Check `model.trained`)
- [ ] Features extracted? (Check `model.extract_features()`)
- [ ] Scaler fitted? (Check after training)
- [ ] Input data same format as training?

### RL Not Learning?
- [ ] Reward signal correct?
- [ ] State discretization makes sense?
- [ ] Epsilon not too high? (Too much exploration)
- [ ] Updating Q-values after trades?

### Performance Slow?
- [ ] Training too frequently?
- [ ] Too many models?
- [ ] Data structures efficient?
- [ ] Unnecessary logging?

---

## 📊 Monitoring Dashboard

### Daily AI Health Check
```python
def log_ai_status(self):
    self.logger.info("=== AI COMPONENTS STATUS ===")
    
    # Models trained?
    self.logger.info(f"Vol Predictor: {'✓' if self.vol_predictor.trained else '✗'}")
    trained = sum(1 for p in self.predictors.values() if p.trained)
    self.logger.info(f"Return Predictors: {trained}/{len(self.predictors)}")
    
    # RL progress
    self.logger.info(f"RL States: {len(self.rl_agent.q_table)}")
    
    # Adaptive risk stats
    self.logger.info(f"Win Rate: {self.adaptive_risk.win_rate:.1%}")
    self.logger.info(f"Current SL: {self.adaptive_risk.current_stop_loss:.2%}")
```

---

## 🎓 Further Learning

### Understanding the Models

**Random Forest**: Ensemble of decision trees, robust and interpretable
**Gradient Boosting**: Sequential ensemble, higher accuracy
**SVM**: Kernel-based classification, good for non-linear patterns
**Q-Learning**: Model-free RL, learns from trial and error
**PCA**: Dimensionality reduction, finds principal components

### Hyperparameter Tuning

Key parameters to experiment with:
- `n_estimators`: More trees = better but slower (try 50-200)
- `max_depth`: Tree depth (3-10 for most tasks)
- `learning_rate`: RL/boosting learning speed (0.01-0.3)
- `epsilon`: RL exploration rate (0.1-0.3)
- `lookback`: History window (20-252 days)

### Resources

- **SKLearn Docs**: https://scikit-learn.org/
- **QuantConnect Docs**: https://www.quantconnect.com/docs
- **RL Tutorial**: Sutton & Barto - Reinforcement Learning
- **ML Finance**: Advances in Financial Machine Learning (de Prado)

---

## 🏁 Getting Started Checklist

- [ ] Read README.md for overview
- [ ] Review AI_INTEGRATION_GUIDE.md for details
- [ ] Study main_ai_example.py for full implementation
- [ ] Choose ONE component to start (recommend VolatilityRegimePredictor)
- [ ] Copy relevant code snippet from this quick reference
- [ ] Backtest with component
- [ ] Compare results to baseline
- [ ] Tune parameters
- [ ] Add next component
- [ ] Repeat until satisfied

---

**Remember**: Start simple, backtest thoroughly, iterate based on results! 🚀

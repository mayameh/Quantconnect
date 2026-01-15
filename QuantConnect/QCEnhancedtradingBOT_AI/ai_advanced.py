"""
Advanced AI Models Module - Part 2
LLM Integration, Sentiment Analysis, and Reinforcement Learning
"""

from AlgorithmImports import *
import numpy as np
from collections import deque
import json


class SentimentAnalyzer:
    """
    Perform sentiment analysis on news and alternative data
    Integrates with news feeds and provides sentiment scores
    """
    
    def __init__(self, logger):
        self.logger = logger
        self.sentiment_history = {}
        self.sentiment_window = 5  # Days to track
        
        # Simple keyword-based sentiment (can be replaced with LLM/transformer models)
        self.positive_keywords = {
            'bullish', 'surge', 'rally', 'gain', 'profit', 'growth', 
            'record', 'breakthrough', 'innovation', 'beat', 'upgrade',
            'outperform', 'strong', 'momentum', 'positive', 'revenue'
        }
        
        self.negative_keywords = {
            'bearish', 'decline', 'loss', 'crash', 'warning', 'downgrade',
            'miss', 'cut', 'layoff', 'investigation', 'lawsuit', 'weak',
            'underperform', 'concern', 'risk', 'negative', 'disappointing'
        }
    
    def analyze_text(self, text: str) -> float:
        """
        Analyze sentiment of text
        Returns score between -1 (very negative) and +1 (very positive)
        """
        if not text:
            return 0.0
            
        text_lower = text.lower()
        words = text_lower.split()
        
        positive_count = sum(1 for word in words if word in self.positive_keywords)
        negative_count = sum(1 for word in words if word in self.negative_keywords)
        
        total_sentiment_words = positive_count + negative_count
        
        if total_sentiment_words == 0:
            return 0.0
        
        sentiment_score = (positive_count - negative_count) / total_sentiment_words
        
        return sentiment_score
    
    def update_sentiment(self, symbol, news_items):
        """
        Update sentiment for a symbol based on news items
        
        Args:
            symbol: Symbol to update
            news_items: List of news text items
        """
        if symbol not in self.sentiment_history:
            self.sentiment_history[symbol] = deque(maxlen=self.sentiment_window)
        
        # Analyze all news items
        sentiments = [self.analyze_text(item) for item in news_items]
        
        if sentiments:
            avg_sentiment = float(np.mean(sentiments))
            self.sentiment_history[symbol].append(avg_sentiment)
    
    def get_sentiment_score(self, symbol) -> float:
        """
        Get current sentiment score for a symbol
        Returns average sentiment over recent window
        """
        if symbol not in self.sentiment_history or len(self.sentiment_history[symbol]) == 0:
            return 0.0
        
        return float(np.mean(list(self.sentiment_history[symbol])))
    
    def get_sentiment_signal(self, symbol, threshold=0.3) -> str:
        """
        Get trading signal based on sentiment
        
        Returns:
            "BULLISH", "BEARISH", or "NEUTRAL"
        """
        sentiment = self.get_sentiment_score(symbol)
        
        if sentiment > threshold:
            return "BULLISH"
        elif sentiment < -threshold:
            return "BEARISH"
        else:
            return "NEUTRAL"


class LLMResearchAnalyzer:
    """
    Framework for LLM-based stock research and RAG applications
    This is a template - replace with actual LLM API calls (OpenAI, Claude, etc.)
    """
    
    def __init__(self, logger, api_key=None):
        self.logger = logger
        self.api_key = api_key
        self.research_cache = {}
        
    def analyze_company(self, symbol: str, fundamental_data: dict) -> dict:
        """
        Analyze company using LLM with fundamental data
        
        Args:
            symbol: Stock symbol
            fundamental_data: Dict with financial metrics
        
        Returns:
            Dict with analysis results including score and insights
        """
        # Template for LLM integration
        # In production, this would call OpenAI/Claude API with prompts
        
        prompt = self._create_analysis_prompt(symbol, fundamental_data)
        
        # Placeholder analysis (replace with actual LLM call)
        analysis = {
            'symbol': symbol,
            'score': self._calculate_fundamental_score(fundamental_data),
            'recommendation': 'HOLD',
            'insights': 'Fundamental analysis placeholder',
            'risk_factors': []
        }
        
        self.research_cache[symbol] = analysis
        return analysis
    
    def _create_analysis_prompt(self, symbol: str, data: dict) -> str:
        """Create prompt for LLM analysis"""
        prompt = f"""
        Analyze the following stock fundamentals for {symbol}:
        
        PE Ratio: {data.get('pe_ratio', 'N/A')}
        Revenue Growth: {data.get('revenue_growth', 'N/A')}
        Profit Margin: {data.get('profit_margin', 'N/A')}
        Debt to Equity: {data.get('debt_to_equity', 'N/A')}
        
        Provide:
        1. Overall investment score (0-100)
        2. Key strengths and weaknesses
        3. Risk factors
        4. Recommendation (BUY/HOLD/SELL)
        """
        return prompt
    
    def _calculate_fundamental_score(self, data: dict) -> float:
        """
        Calculate fundamental quality score
        Returns score between 0 and 100
        """
        score = 50.0  # Base score
        
        # PE Ratio scoring
        pe_ratio = data.get('pe_ratio', None)
        if pe_ratio and 0 < pe_ratio < 20:
            score += 10
        elif pe_ratio and pe_ratio > 50:
            score -= 10
        
        # Revenue growth scoring
        revenue_growth = data.get('revenue_growth', None)
        if revenue_growth and revenue_growth > 0.15:
            score += 15
        elif revenue_growth and revenue_growth < 0:
            score -= 15
        
        # Profit margin scoring
        profit_margin = data.get('profit_margin', None)
        if profit_margin and profit_margin > 0.2:
            score += 10
        elif profit_margin and profit_margin < 0:
            score -= 20
        
        # Debt to equity scoring
        debt_to_equity = data.get('debt_to_equity', None)
        if debt_to_equity and debt_to_equity < 0.5:
            score += 10
        elif debt_to_equity and debt_to_equity > 2.0:
            score -= 10
        
        return max(0, min(100, score))


class ReinforcementLearningAgent:
    """
    Q-Learning based agent for position sizing and hedging decisions
    Learns optimal actions based on market state
    """
    
    def __init__(self, logger, learning_rate=0.1, discount_factor=0.95, epsilon=0.1):
        self.logger = logger
        self.lr = learning_rate
        self.gamma = discount_factor
        self.epsilon = epsilon
        
        # Q-table: state -> action -> Q-value
        self.q_table = {}
        
        # Actions: position sizes (as % of available capital)
        self.actions = [0.0, 0.25, 0.5, 0.75, 1.0]
        
        # State history
        self.state_history = deque(maxlen=1000)
        self.action_history = deque(maxlen=1000)
        self.reward_history = deque(maxlen=1000)
        
    def get_state(self, market_regime: str, volatility_regime: str, 
                  portfolio_return: float, drawdown: float) -> str:
        """
        Convert market conditions to discrete state
        
        Args:
            market_regime: BULL/NEUTRAL/BEAR
            volatility_regime: LOW/MEDIUM/HIGH
            portfolio_return: Current portfolio return
            drawdown: Current drawdown
        
        Returns:
            State string
        """
        # Discretize continuous variables
        ret_state = "PROFIT" if portfolio_return > 0.02 else "LOSS" if portfolio_return < -0.02 else "FLAT"
        dd_state = "HIGH_DD" if drawdown > 0.1 else "MED_DD" if drawdown > 0.05 else "LOW_DD"
        
        state = f"{market_regime}_{volatility_regime}_{ret_state}_{dd_state}"
        return state
    
    def select_action(self, state: str) -> float:
        """
        Select action using epsilon-greedy policy
        
        Returns:
            Position size as % of capital
        """
        # Initialize state in Q-table if new
        if state not in self.q_table:
            self.q_table[state] = {a: 0.0 for a in self.actions}
        
        # Epsilon-greedy selection
        if np.random.random() < self.epsilon:
            # Explore: random action
            action = np.random.choice(self.actions)
        else:
            # Exploit: best known action
            q_values = self.q_table[state]
            max_q = max(q_values.values())
            best_actions = [a for a, q in q_values.items() if q == max_q]
            action = np.random.choice(best_actions)
        
        return action
    
    def update_q_value(self, state: str, action: float, reward: float, next_state: str):
        """
        Update Q-value using Q-learning update rule
        
        Q(s,a) = Q(s,a) + α * (reward + γ * max(Q(s',a')) - Q(s,a))
        """
        # Initialize states if new
        if state not in self.q_table:
            self.q_table[state] = {a: 0.0 for a in self.actions}
        if next_state not in self.q_table:
            self.q_table[next_state] = {a: 0.0 for a in self.actions}
        
        # Current Q-value
        current_q = self.q_table[state][action]
        
        # Max Q-value for next state
        max_next_q = max(self.q_table[next_state].values())
        
        # Q-learning update
        new_q = current_q + self.lr * (reward + self.gamma * max_next_q - current_q)
        
        self.q_table[state][action] = new_q
        
        # Track history
        self.state_history.append(state)
        self.action_history.append(action)
        self.reward_history.append(reward)
    
    def calculate_reward(self, portfolio_return: float, risk_adjusted=True) -> float:
        """
        Calculate reward for reinforcement learning
        
        Args:
            portfolio_return: Return achieved
            risk_adjusted: Whether to adjust for risk
        
        Returns:
            Reward value
        """
        # Base reward is the return
        reward = portfolio_return * 100  # Scale to reasonable range
        
        # Penalize high drawdowns
        if len(self.reward_history) > 10:
            recent_returns = list(self.reward_history)[-10:]
            volatility = float(np.std(recent_returns))
            if risk_adjusted and volatility > 0:
                reward = reward / (1 + volatility)
        
        return reward
    
    def get_optimal_position_size(self, state: str) -> float:
        """
        Get optimal position size for current state
        
        Returns:
            Recommended position size (0.0 to 1.0)
        """
        if state not in self.q_table:
            return 0.5  # Default moderate position
        
        q_values = self.q_table[state]
        max_q = max(q_values.values())
        best_actions = [a for a, q in q_values.items() if q == max_q]
        
        return np.random.choice(best_actions)


class StatisticalArbitrageDetector:
    """
    Detect pairs trading opportunities using statistical methods
    Uses cointegration and correlation analysis
    """
    
    def __init__(self, logger):
        self.logger = logger
        self.price_history = {}
        self.correlation_threshold = 0.8
        self.lookback = 60
        
    def update_prices(self, symbol, price: float):
        """Update price history for a symbol"""
        if symbol not in self.price_history:
            self.price_history[symbol] = deque(maxlen=self.lookback)
        self.price_history[symbol].append(price)
    
    def find_pairs(self, symbols):
        """
        Find correlated pairs for statistical arbitrage
        
        Returns:
            List of (symbol1, symbol2, correlation) tuples
        """
        pairs = []
        
        # Need sufficient data
        valid_symbols = [s for s in symbols if s in self.price_history 
                        and len(self.price_history[s]) >= self.lookback * 0.8]
        
        if len(valid_symbols) < 2:
            return pairs
        
        try:
            # Calculate correlations between all pairs
            for i, sym1 in enumerate(valid_symbols):
                for sym2 in valid_symbols[i+1:]:
                    prices1 = np.array(list(self.price_history[sym1]))
                    prices2 = np.array(list(self.price_history[sym2]))
                    
                    # Ensure same length
                    min_len = min(len(prices1), len(prices2))
                    prices1 = prices1[-min_len:]
                    prices2 = prices2[-min_len:]
                    
                    if len(prices1) < 20:
                        continue
                    
                    # Calculate correlation
                    correlation = np.corrcoef(prices1, prices2)[0, 1]
                    
                    if abs(correlation) > self.correlation_threshold:
                        pairs.append((sym1, sym2, correlation))
            
            # Sort by correlation strength
            pairs.sort(key=lambda x: abs(x[2]), reverse=True)
            
            if pairs:
                self.logger.info(f"Found {len(pairs)} potential pairs for stat arb")
            
            return pairs[:5]  # Return top 5 pairs
            
        except Exception as e:
            self.logger.error(f"Pairs detection error: {e}")
            return []
    
    def calculate_spread(self, symbol1, symbol2):
        """Calculate normalized spread between two assets"""
        if symbol1 not in self.price_history or symbol2 not in self.price_history:
            return None
            
        prices1 = np.array(list(self.price_history[symbol1]))
        prices2 = np.array(list(self.price_history[symbol2]))
        
        min_len = min(len(prices1), len(prices2))
        if min_len < 20:
            return None
            
        prices1 = prices1[-min_len:]
        prices2 = prices2[-min_len:]
        
        # Normalize prices
        norm_prices1 = prices1 / prices1[0]
        norm_prices2 = prices2 / prices2[0]
        
        # Calculate spread
        spread = norm_prices1 - norm_prices2
        
        return spread
    
    def get_mean_reversion_signal(self, symbol1, symbol2, z_threshold=2.0):
        """
        Get mean reversion signal for a pair
        
        Returns:
            "LONG_1_SHORT_2", "SHORT_1_LONG_2", or "NEUTRAL"
        """
        spread = self.calculate_spread(symbol1, symbol2)
        
        if spread is None or len(spread) < 20:
            return "NEUTRAL"
        
        # Calculate z-score of current spread
        mean_spread = float(np.mean(spread))
        std_spread = float(np.std(spread))
        
        if std_spread == 0:
            return "NEUTRAL"
        
        current_spread = spread[-1]
        z_score = (current_spread - mean_spread) / std_spread
        
        if z_score > z_threshold:
            # Spread too high - short symbol1, long symbol2
            return "SHORT_1_LONG_2"
        elif z_score < -z_threshold:
            # Spread too low - long symbol1, short symbol2
            return "LONG_1_SHORT_2"
        else:
            return "NEUTRAL"


class AdaptiveRiskManager:
    """
    AI-based adaptive risk management
    Adjusts risk parameters based on market conditions using ML
    """
    
    def __init__(self, logger, base_stop_loss=0.03, base_position_size=0.2):
        self.logger = logger
        self.base_stop_loss = base_stop_loss
        self.base_position_size = base_position_size
        
        # Adaptive parameters
        self.current_stop_loss = base_stop_loss
        self.current_position_size = base_position_size
        
        # Performance tracking
        self.win_rate = 0.5
        self.avg_win = 0.0
        self.avg_loss = 0.0
        self.trade_history = deque(maxlen=50)
        
    def update_trade_result(self, pnl: float, was_stop_loss: bool):
        """Update based on trade result"""
        self.trade_history.append({
            'pnl': pnl,
            'was_stop_loss': was_stop_loss
        })
        
        # Recalculate statistics
        self._update_statistics()
        
        # Adapt parameters
        self._adapt_parameters()
    
    def _update_statistics(self):
        """Update win rate and average win/loss"""
        if len(self.trade_history) == 0:
            return
        
        wins = [t for t in self.trade_history if t['pnl'] > 0]
        losses = [t for t in self.trade_history if t['pnl'] <= 0]
        
        self.win_rate = len(wins) / len(self.trade_history)
        self.avg_win = float(np.mean([t['pnl'] for t in wins])) if wins else 0.0
        self.avg_loss = float(np.mean([abs(t['pnl']) for t in losses])) if losses else 0.0
    
    def _adapt_parameters(self):
        """Adapt risk parameters based on performance"""
        if len(self.trade_history) < 10:
            return
        
        # Adjust stop loss based on average loss
        if self.avg_loss > 0:
            # If average loss is less than stop loss, tighten it
            if self.avg_loss < self.base_stop_loss * 0.8:
                self.current_stop_loss = max(0.015, self.avg_loss * 1.2)
            else:
                self.current_stop_loss = min(0.05, self.avg_loss * 1.5)
        
        # Adjust position size based on win rate
        if self.win_rate > 0.6:
            # Increase position size when winning
            self.current_position_size = min(0.35, self.base_position_size * 1.3)
        elif self.win_rate < 0.4:
            # Decrease position size when losing
            self.current_position_size = max(0.1, self.base_position_size * 0.7)
        else:
            self.current_position_size = self.base_position_size
        
        self.logger.info(f"Adaptive Risk: SL={self.current_stop_loss:.3f}, "
                        f"Size={self.current_position_size:.2f}, WR={self.win_rate:.2%}")
    
    def get_stop_loss(self, volatility_regime: str = "MEDIUM"):
        """Get adaptive stop loss based on regime"""
        # Adjust for volatility
        if volatility_regime == "HIGH":
            return self.current_stop_loss * 1.5
        elif volatility_regime == "LOW":
            return self.current_stop_loss * 0.8
        else:
            return self.current_stop_loss
    
    def get_position_size(self, confidence: float = 0.5):
        """
        Get adaptive position size
        
        Args:
            confidence: ML model confidence (0-1)
        """
        # Scale position size by confidence
        adjusted_size = self.current_position_size * (0.5 + confidence * 0.5)
        return adjusted_size

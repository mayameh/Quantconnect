"""
AI Models Module for Enhanced Trading Algorithm
Implements ML/DL models for portfolio optimization, regime prediction, and risk management
"""

from AlgorithmImports import *
import numpy as np
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
from sklearn.ensemble import RandomForestClassifier, GradientBoostingRegressor
from sklearn.svm import SVR
from collections import deque
import warnings
warnings.filterwarnings('ignore')


class VolatilityRegimePredictor:
    """
    Predict market volatility regimes using machine learning
    Regimes: LOW, MEDIUM, HIGH volatility
    """
    
    def __init__(self, logger):
        self.logger = logger
        self.scaler = StandardScaler()
        self.model = RandomForestClassifier(
            n_estimators=100,
            max_depth=10,
            random_state=42
        )
        self.lookback = 252  # 1 year of daily data
        self.volatility_history = deque(maxlen=self.lookback)
        self.return_history = deque(maxlen=self.lookback)
        self.volume_history = deque(maxlen=self.lookback)
        self.trained = False
        self.current_regime = "MEDIUM"
        
    def update(self, price: float, volume: float):
        """Update historical data with new market data"""
        if len(self.return_history) > 0:
            daily_return = (price - self.return_history[-1]) / self.return_history[-1]
        else:
            daily_return = 0.0
            
        self.return_history.append(price)
        self.volume_history.append(volume)
        
        # Calculate rolling volatility
        if len(self.return_history) >= 20:
            returns = np.diff(list(self.return_history)[-20:]) / list(self.return_history)[-20:-1]
            volatility = float(np.std(returns) * np.sqrt(252))
            self.volatility_history.append(volatility)
    
    def extract_features(self):
        """Extract features for regime prediction"""
        if len(self.volatility_history) < 60:
            return None
            
        recent_vol = list(self.volatility_history)[-60:]
        recent_returns = list(self.return_history)[-60:]
        recent_volume = list(self.volume_history)[-60:]
        
        features = [
            np.mean(recent_vol[-5:]),      # 5-day avg volatility
            np.mean(recent_vol[-20:]),     # 20-day avg volatility
            np.std(recent_vol[-20:]),      # Volatility of volatility
            np.mean(recent_returns[-5:]),  # Recent returns
            np.mean(recent_volume[-20:]),  # Average volume
            max(recent_vol[-20:]),         # Max recent volatility
            min(recent_vol[-20:]),         # Min recent volatility
        ]
        return np.array(features).reshape(1, -1)
    
    def train(self):
        """Train the regime classifier"""
        if len(self.volatility_history) < self.lookback * 0.5:
            self.logger.warning("Insufficient data for regime training")
            return False
            
        try:
            X_train = []
            y_train = []
            
            vol_array = np.array(list(self.volatility_history))
            
            # Create labels based on percentiles
            low_threshold = float(np.percentile(vol_array, 33))
            high_threshold = float(np.percentile(vol_array, 67))
            
            for i in range(60, len(vol_array)):
                window_vol = vol_array[i-60:i]
                window_returns = list(self.return_history)[i-60:i]
                window_volume = list(self.volume_history)[i-60:i]
                
                features = [
                    np.mean(window_vol[-5:]),
                    np.mean(window_vol[-20:]),
                    np.std(window_vol[-20:]),
                    np.mean(window_returns[-5:]),
                    np.mean(window_volume[-20:]),
                    max(window_vol[-20:]),
                    min(window_vol[-20:]),
                ]
                
                # Label based on current volatility
                if vol_array[i] < low_threshold:
                    label = 0  # LOW
                elif vol_array[i] < high_threshold:
                    label = 1  # MEDIUM
                else:
                    label = 2  # HIGH
                    
                X_train.append(features)
                y_train.append(label)
            
            X_train = np.array(X_train)
            y_train = np.array(y_train)
            
            # Normalize features
            X_train = self.scaler.fit_transform(X_train)
            
            # Train model
            self.model.fit(X_train, y_train)
            self.trained = True
            
            self.logger.info(f"Volatility regime model trained on {len(y_train)} samples")
            return True
            
        except Exception as e:
            self.logger.error(f"Regime training error: {e}")
            return False
    
    def predict_regime(self):
        """Predict current volatility regime"""
        if not self.trained:
            return self.current_regime
            
        features = self.extract_features()
        if features is None:
            return self.current_regime
            
        try:
            features_scaled = self.scaler.transform(features)
            prediction = self.model.predict(features_scaled)[0]
            
            regime_map = {0: "LOW", 1: "MEDIUM", 2: "HIGH"}
            self.current_regime = regime_map[prediction]
            
            return self.current_regime
            
        except Exception as e:
            self.logger.error(f"Regime prediction error: {e}")
            return self.current_regime


class ReturnPredictor:
    """
    Predict next-day returns using ensemble of ML models
    Uses technical indicators, volume, and momentum features
    """
    
    def __init__(self, logger, symbol):
        self.logger = logger
        self.symbol = symbol
        self.scaler = StandardScaler()
        self.model = GradientBoostingRegressor(
            n_estimators=100,
            max_depth=5,
            learning_rate=0.1,
            random_state=42
        )
        self.price_history = deque(maxlen=100)
        self.volume_history = deque(maxlen=100)
        self.trained = False
        self.feature_importance = None
        
    def update(self, price: float, volume: float):
        """Update price and volume history"""
        self.price_history.append(price)
        self.volume_history.append(volume)
    
    def extract_features(self):
        """Extract technical features for prediction"""
        if len(self.price_history) < 50:
            return None
            
        prices = np.array(list(self.price_history))
        volumes = np.array(list(self.volume_history))
        
        # Calculate returns
        returns = np.diff(prices) / prices[:-1]
        
        # Technical features
        features = [
            # Momentum features
            float(returns[-1]) if len(returns) > 0 else 0.0,  # Last return
            float(np.mean(returns[-5:])) if len(returns) >= 5 else 0.0,  # 5-day avg return
            float(np.mean(returns[-20:])) if len(returns) >= 20 else 0.0,  # 20-day avg return
            
            # Volatility features
            float(np.std(returns[-20:])) if len(returns) >= 20 else 0.0,  # 20-day volatility
            float(np.std(returns[-5:])) if len(returns) >= 5 else 0.0,  # 5-day volatility
            
            # Price position features
            float((float(prices[-1]) - float(np.mean(prices[-20:]))) / float(np.std(prices[-20:]))) if len(prices) >= 20 and float(np.std(prices[-20:])) > 0 else 0.0,  # Z-score
            float((float(prices[-1]) - float(np.min(prices[-20:]))) / (float(np.max(prices[-20:])) - float(np.min(prices[-20:])))) if len(prices) >= 20 and float(np.max(prices[-20:])) > float(np.min(prices[-20:])) else 0.0,  # Position in range
            
            # Volume features
            float(float(volumes[-1]) / float(np.mean(volumes[-20:]))) if len(volumes) >= 20 and float(np.mean(volumes[-20:])) > 0 else 1.0,  # Relative volume
            float(float(np.mean(volumes[-5:])) / float(np.mean(volumes[-20:]))) if len(volumes) >= 20 and float(np.mean(volumes[-20:])) > 0 else 1.0,  # Volume trend
            
            # Trend features
            float((float(prices[-1]) - float(prices[-5])) / float(prices[-5])) if len(prices) >= 5 and float(prices[-5]) != 0 else 0.0,  # 5-day change
            float((float(prices[-1]) - float(prices[-20])) / float(prices[-20])) if len(prices) >= 20 and float(prices[-20]) != 0 else 0.0,  # 20-day change
        ]
        
        return np.array(features).reshape(1, -1)
    
    def train(self):
        """Train the return prediction model"""
        if len(self.price_history) < 60:
            return False
            
        try:
            X_train = []
            y_train = []
            
            prices = np.array(list(self.price_history))
            
            for i in range(50, len(prices) - 1):
                # Extract features from historical window
                window_prices = prices[:i+1]
                window_volumes = np.array(list(self.volume_history)[:i+1])
                
                returns = np.diff(window_prices) / window_prices[:-1]
                
                features = [
                    float(returns[-1]),
                    float(np.mean(returns[-5:])),
                    float(np.mean(returns[-20:])),
                    float(np.std(returns[-20:])),
                    float(np.std(returns[-5:])),
                    float((float(window_prices[-1]) - float(np.mean(window_prices[-20:]))) / float(np.std(window_prices[-20:]))) if float(np.std(window_prices[-20:])) > 0 else 0.0,
                    float((float(window_prices[-1]) - float(np.min(window_prices[-20:]))) / (float(np.max(window_prices[-20:])) - float(np.min(window_prices[-20:])))) if float(np.max(window_prices[-20:])) > float(np.min(window_prices[-20:])) else 0.0,
                    float(float(window_volumes[-1]) / float(np.mean(window_volumes[-20:]))) if float(np.mean(window_volumes[-20:])) > 0 else 1.0,
                    float(np.mean(window_volumes[-5:]) / np.mean(window_volumes[-20:])) if float(np.mean(window_volumes[-20:])) > 0 else 1.0,
                    float((float(window_prices[-1]) - float(window_prices[-5])) / float(window_prices[-5])) if float(window_prices[-5]) != 0 else 0.0,
                    float((float(window_prices[-1]) - float(window_prices[-20])) / float(window_prices[-20])) if float(window_prices[-20]) != 0 else 0.0,
                ]
                
                # Target: next day return
                next_return = float((float(prices[i+1]) - float(prices[i])) / float(prices[i]))
                
                X_train.append(features)
                y_train.append(next_return)
            
            X_train = np.array(X_train)
            y_train = np.array(y_train)
            
            # Normalize features
            X_train = self.scaler.fit_transform(X_train)
            
            # Train model
            self.model.fit(X_train, y_train)
            self.trained = True
            self.feature_importance = self.model.feature_importances_
            
            self.logger.info(f"Return predictor trained for {self.symbol} on {len(y_train)} samples")
            return True
            
        except Exception as e:
            self.logger.error(f"Return predictor training error for {self.symbol}: {e}")
            return False
    
    def predict_return(self):
        """Predict next-day return"""
        if not self.trained:
            return 0.0
            
        features = self.extract_features()
        if features is None:
            return 0.0
            
        try:
            features_scaled = self.scaler.transform(features)
            prediction = self.model.predict(features_scaled)[0]
            return prediction
            
        except Exception as e:
            self.logger.error(f"Return prediction error for {self.symbol}: {e}")
            return 0.0


class PCAFeatureReducer:
    """
    Use PCA to reduce feature dimensionality for portfolio optimization
    Helps identify principal components driving returns
    """
    
    def __init__(self, logger, n_components=5):
        self.logger = logger
        self.n_components = n_components
        self.pca = PCA(n_components=n_components)
        self.scaler = StandardScaler()
        self.fitted = False
        
    def fit_transform(self, features):
        """Fit PCA and transform features"""
        try:
            if features.shape[0] < self.n_components:
                self.logger.warning("Insufficient samples for PCA")
                return features
                
            # Normalize features
            features_scaled = self.scaler.fit_transform(features)
            
            # Apply PCA
            features_reduced = self.pca.fit_transform(features_scaled)
            
            self.fitted = True
            
            explained_var = sum(self.pca.explained_variance_ratio_)
            self.logger.info(f"PCA: {self.n_components} components explain {explained_var:.2%} of variance")
            
            return features_reduced
            
        except Exception as e:
            self.logger.error(f"PCA error: {e}")
            return features
    
    def transform(self, features):
        """Transform new features using fitted PCA"""
        if not self.fitted:
            return features
            
        try:
            features_scaled = self.scaler.transform(features)
            features_reduced = self.pca.transform(features_scaled)
            return features_reduced
        except Exception as e:
            self.logger.error(f"PCA transform error: {e}")
            return features
    
    def get_explained_variance(self):
        """Get explained variance ratio for each component"""
        if not self.fitted:
            return None
        return self.pca.explained_variance_ratio_


class PortfolioOptimizer:
    """
    ML-based portfolio optimization using predicted returns and risk
    Implements Markowitz-style optimization with ML predictions
    """
    
    def __init__(self, logger):
        self.logger = logger
        self.return_predictors = {}
        self.correlation_matrix = None
        
    def add_predictor(self, symbol, predictor):
        """Add a return predictor for a symbol"""
        self.return_predictors[symbol] = predictor
    
    def predict_returns(self, symbols):
        """Get predicted returns for all symbols"""
        predictions = {}
        for symbol in symbols:
            if symbol in self.return_predictors:
                pred = self.return_predictors[symbol].predict_return()
                predictions[symbol] = pred
            else:
                predictions[symbol] = 0.0
        return predictions
    
    def calculate_optimal_weights(self, symbols, predicted_returns, risk_aversion=1.0):
        """
        Calculate optimal portfolio weights using mean-variance optimization
        
        Args:
            symbols: List of symbols
            predicted_returns: Dict of predicted returns by symbol
            risk_aversion: Risk aversion parameter (higher = more conservative)
        
        Returns:
            Dict of optimal weights by symbol
        """
        try:
            n = len(symbols)
            if n == 0:
                return {}
            
            # Simple equal-weight with return tilt
            returns_array = np.array([predicted_returns.get(s, 0.0) for s in symbols])
            
            # Adjust weights based on predicted returns
            if np.max(np.abs(returns_array)) > 0:
                # Normalize returns to [0, 1]
                min_ret = np.min(returns_array)
                returns_normalized = returns_array - min_ret + 0.1  # Ensure positive
                
                # Weight by return prediction
                weights = returns_normalized / np.sum(returns_normalized)
                
                # Apply risk aversion (smooth toward equal weight)
                equal_weight = np.ones(n) / n
                weights = (1 - risk_aversion) * weights + risk_aversion * equal_weight
            else:
                weights = np.ones(n) / n
            
            # Create weight dictionary
            weight_dict = {symbol: weight for symbol, weight in zip(symbols, weights)}
            
            return weight_dict
            
        except Exception as e:
            self.logger.error(f"Portfolio optimization error: {e}")
            # Fallback to equal weights
            equal_weight = 1.0 / len(symbols) if len(symbols) > 0 else 0.0
            return {s: equal_weight for s in symbols}


class RiskMetricsCalculator:
    """
    Calculate advanced risk metrics using ML techniques
    """
    
    def __init__(self, logger):
        self.logger = logger
        self.return_history = deque(maxlen=252)
        
    def update(self, portfolio_return: float):
        """Update return history"""
        self.return_history.append(portfolio_return)
    
    def calculate_var(self, confidence=0.95):
        """Calculate Value at Risk"""
        if len(self.return_history) < 20:
            return 0.0
            
        returns = np.array(list(self.return_history))
        var = np.percentile(returns, (1 - confidence) * 100)
        return abs(var)
    
    def calculate_cvar(self, confidence=0.95):
        """Calculate Conditional Value at Risk (Expected Shortfall)"""
        if len(self.return_history) < 20:
            return 0.0
            
        returns = np.array(list(self.return_history))
        var = np.percentile(returns, (1 - confidence) * 100)
        cvar = np.mean(returns[returns <= var])
        return abs(cvar)
    
    def calculate_sharpe_ratio(self, risk_free_rate=0.02):
        """Calculate Sharpe ratio"""
        if len(self.return_history) < 20:
            return 0.0
            
        returns = np.array(list(self.return_history))
        excess_returns = returns - (risk_free_rate / 252)  # Daily risk-free rate
        
        if np.std(excess_returns) == 0:
            return 0.0
            
        sharpe = np.mean(excess_returns) / np.std(excess_returns) * np.sqrt(252)
        return sharpe
    
    def calculate_max_drawdown(self):
        """Calculate maximum drawdown"""
        if len(self.return_history) < 2:
            return 0.0
            
        returns = np.array(list(self.return_history))
        cumulative = np.cumprod(1 + returns)
        running_max = np.maximum.accumulate(cumulative)
        drawdown = (cumulative - running_max) / running_max
        max_dd = np.min(drawdown)
        return abs(max_dd)

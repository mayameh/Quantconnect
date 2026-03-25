"""
AI-powered trading enhancements: ML entry filtering and adaptive exit management.
Learns from completed trade outcomes to improve entry quality and exit timing.
"""

from AlgorithmImports import *
import numpy as np
from collections import deque

try:
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.preprocessing import StandardScaler
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False


class MLEntryFilter:
    """ML-based entry quality filter using GradientBoosting regression.

    Trains on completed trade outcomes: maps indicator features at entry time
    to actual trade P&L.  After enough samples, adjusts entry scores to
    filter out low-probability setups.
    """

    def __init__(self, algo, min_trades=20):
        self.algo = algo
        self.min_trades = min_trades
        self.trained = False
        self.completed_trades = deque(maxlen=300)  # (features, pnl_pct)
        self.pending = {}  # symbol -> features at entry time
        self._trades_since_retrain = 0

        if HAS_SKLEARN:
            self.model = GradientBoostingRegressor(
                n_estimators=50, max_depth=3, learning_rate=0.1, random_state=42
            )
            self.scaler = StandardScaler()

    # ── feature extraction ───────────────────────────────────────────

    def extract_features(self, symbol):
        """Build 8-feature vector from the symbol's live indicators."""
        indicators = self.algo._indicators.get(symbol)
        if not indicators:
            return None
        try:
            macd = indicators["macd"]
            rsi = indicators["rsi"]
            ema_50 = indicators["ema_50"]
            ema_9 = indicators.get("ema_9")
            ema_21 = indicators.get("ema_21")
            vol_sma = indicators.get("volume_sma")

            price = float(self.algo.securities[symbol].price)
            if price <= 0 or not ema_50.is_ready:
                return None

            e50 = ema_50.current.value
            e9 = ema_9.current.value if ema_9 and ema_9.is_ready else price
            e21 = ema_21.current.value if ema_21 and ema_21.is_ready else e50

            features = [
                (macd.current.value - macd.signal.current.value) * 100,  # MACD histogram
                rsi.current.value / 100.0,                                # RSI normalised
                (price - e50) / e50,                                      # price / EMA50
                (price - e9) / e9,                                        # price / EMA9
                (e9 - e21) / (e21 + 1e-10),                              # EMA9/21 alignment
                (e21 - e50) / (e50 + 1e-10),                             # EMA21/50 alignment
            ]

            # relative volume
            if vol_sma and vol_sma.is_ready:
                cur_vol = float(self.algo.securities[symbol].volume)
                avg_vol = vol_sma.current.value
                features.append(cur_vol / (avg_vol + 1e-10))
            else:
                features.append(1.0)

            # daily trend alignment
            d_ema20 = indicators.get('daily_ema_20')
            d_ema50 = indicators.get('daily_ema_50')
            if d_ema20 and d_ema50 and d_ema20.is_ready and d_ema50.is_ready:
                features.append((d_ema20.current.value - d_ema50.current.value) / (d_ema50.current.value + 1e-10))
            else:
                features.append(0.0)

            # market regime encoded
            regime_map = {"BULL": 1.0, "NEUTRAL": 0.0, "BEAR": -0.5, "EXTREME_BEAR": -1.0}
            features.append(regime_map.get(self.algo.market_regime, 0.0))

            return features
        except Exception:
            return None

    # ── trade lifecycle ──────────────────────────────────────────────

    def record_entry(self, symbol, features):
        """Store features captured at entry for matching with later exit."""
        if features is not None:
            self.pending[symbol] = list(features)

    def record_exit(self, symbol, pnl_pct):
        """Record completed trade and retrain when enough new data exists."""
        if symbol in self.pending:
            self.completed_trades.append((self.pending.pop(symbol), pnl_pct))
            self._trades_since_retrain += 1
            if (len(self.completed_trades) >= self.min_trades
                    and self._trades_since_retrain >= 10):
                self._train()

    # ── training ─────────────────────────────────────────────────────

    def _train(self):
        if not HAS_SKLEARN or len(self.completed_trades) < self.min_trades:
            return False
        try:
            X = np.array([t[0] for t in self.completed_trades])
            y = np.array([t[1] for t in self.completed_trades])
            X_scaled = self.scaler.fit_transform(X)
            self.model.fit(X_scaled, y)
            self.trained = True
            self._trades_since_retrain = 0
            avg_pred = float(np.mean(self.model.predict(X_scaled)))
            self.algo.debug(
                f"ML retrained on {len(y)} trades | avg_pred={avg_pred:.3%} "
                f"| actual_avg={float(np.mean(y)):.3%}"
            )
            return True
        except Exception as e:
            self.algo.debug(f"ML train error: {e}")
            return False

    # ── prediction ───────────────────────────────────────────────────

    def get_score_adjustment(self, features):
        """Return score adjustment in [-2, +2] based on predicted trade P&L.

        Mapping: predicted P&L ±4% → adjustment ±2.0, scaled by data confidence.
        """
        if not self.trained or not HAS_SKLEARN or features is None:
            return 0.0
        try:
            X = np.array(features).reshape(1, -1)
            X_scaled = self.scaler.transform(X)
            predicted_pnl = float(self.model.predict(X_scaled)[0])
            # confidence ramps from 0→1 as trades go from min_trades→50
            confidence = min(1.0, len(self.completed_trades) / 50)
            raw_adj = predicted_pnl * 50  # ±4% pnl → ±2.0
            return max(-2.0, min(2.0, raw_adj * confidence))
        except Exception:
            return 0.0


class AdaptiveExitManager:
    """Dynamically adjusts stop-loss and take-profit from trade statistics."""

    def __init__(self, base_stop_loss=0.03, base_take_profit=0.08):
        self.base_sl = base_stop_loss
        self.base_tp = base_take_profit
        self.current_sl = base_stop_loss
        self.current_tp = base_take_profit
        self.trade_results = deque(maxlen=50)
        self.win_rate = 0.5
        self.avg_win = 0.0
        self.avg_loss = 0.0

    def record_trade(self, pnl_pct, exit_reason):
        """Record outcome and re-adapt parameters."""
        self.trade_results.append({"pnl": pnl_pct, "reason": exit_reason})
        self._adapt()

    def _adapt(self):
        if len(self.trade_results) < 10:
            return
        results = list(self.trade_results)
        wins = [t for t in results if t["pnl"] > 0]
        losses = [t for t in results if t["pnl"] <= 0]

        self.win_rate = len(wins) / len(results)
        self.avg_win = float(np.mean([t["pnl"] for t in wins])) if wins else 0.0
        self.avg_loss = float(np.mean([abs(t["pnl"]) for t in losses])) if losses else self.base_sl

        # ── adapt stop loss ──
        recent = results[-20:] if len(results) >= 20 else results
        sl_exits = sum(1 for t in recent if t["reason"] == "stop_loss")
        sl_rate = sl_exits / len(recent)

        if sl_rate > 0.4:
            # stopped out too often → widen slightly
            self.current_sl = min(0.05, self.base_sl * 1.2)
        elif self.avg_loss > 0 and self.avg_loss < self.base_sl * 0.7:
            # losses are small → tighten
            self.current_sl = max(0.015, self.avg_loss * 1.3)
        else:
            self.current_sl = self.base_sl

        # ── adapt take profit ──
        tp_exits = sum(1 for t in recent if t["reason"] == "take_profit")
        tp_rate = tp_exits / len(recent)

        if tp_rate < 0.05 and self.avg_win > 0:
            # rarely hitting TP → lower closer to avg win
            self.current_tp = max(0.04, self.avg_win * 1.5)
        elif tp_rate > 0.3:
            # hitting TP often → raise to let winners run
            self.current_tp = min(0.15, self.base_tp * 1.3)
        else:
            self.current_tp = self.base_tp

    def get_stop_loss(self):
        return self.current_sl

    def get_take_profit(self):
        return self.current_tp

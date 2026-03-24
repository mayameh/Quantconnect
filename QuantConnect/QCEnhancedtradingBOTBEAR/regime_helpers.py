"""Market regime detection — extracted to reduce main.py size."""


def detect_market_regime(algo) -> None:
    """
    Robust market regime detection.
    Regime = structural trend, not short-term momentum.
    """

    try:
        # Skip on weekends/holidays
        if not algo._is_market_open():
            return
        # Guard: indicators must be ready
        if algo.spy_ema_20 is None or algo.spy_ema_50 is None or not (algo.spy_ema_20.is_ready and algo.spy_ema_50.is_ready):
            return

        spy_price = float(algo.securities[algo.spy].price)

        ema_20 = algo.spy_ema_20.current.value
        ema_50 = algo.spy_ema_50.current.value

        ema_20_prev = algo.spy_ema_20.previous.value
        ema_50_prev = algo.spy_ema_50.previous.value

        # RSI (optional confirmation only)
        rsi_ready = hasattr(algo, "spy_rsi") and algo.spy_rsi.is_ready
        rsi_val = algo.spy_rsi.current.value if rsi_ready else None

        previous_regime = algo.market_regime

        # Structural signals (regime)
        price_above_50 = spy_price > ema_50
        price_below_50 = spy_price < ema_50

        ema_structure_bull = ema_20 > ema_50
        ema_structure_bear = ema_20 < ema_50

        # Momentum signals (confirmation)
        ema_20_rising = ema_20 > ema_20_prev

        momentum_bear = (not ema_20_rising) and spy_price < ema_20

        # REGIME DECISION LOGIC (with hysteresis)

        # ---- BULL REGIME ----
        if (
            price_above_50
            and ema_structure_bull
        ):
            algo.market_regime = "BULL"

        # ---- EXTREME BEAR REGIME (deep selloff = discount buying opportunity) ----
        elif (
            price_below_50
            and ema_structure_bear
            and rsi_ready
            and rsi_val < algo.config.bear_dip_buy.spy_rsi_threshold
            and abs(ema_50) > 1e-9
            and ((ema_50 - spy_price) / ema_50) >= algo.config.bear_dip_buy.spy_discount_pct
        ):
            algo.market_regime = "EXTREME_BEAR"

        # ---- BEAR REGIME ----
        elif (
            price_below_50
            and ema_structure_bear
            and momentum_bear
        ):
            algo.market_regime = "BEAR"

        # ---- NEUTRAL (transition / chop) ----
        else:
            # Hysteresis: don't downgrade strong trends easily
            if previous_regime == "BULL" and price_above_50:
                algo.market_regime = "BULL"
            elif previous_regime == "BEAR" and price_below_50:
                algo.market_regime = "BEAR"
            elif previous_regime == "EXTREME_BEAR" and price_below_50 and rsi_ready and rsi_val < 40:
                algo.market_regime = "EXTREME_BEAR"
            else:
                algo.market_regime = "NEUTRAL"

        # LOGGING
        algo.logger.info(
            f"Regime Scan: {algo.market_regime} | "
            f"Price: {spy_price:.2f} | EMA20: {ema_20:.2f} | EMA50: {ema_50:.2f} | "
            f"Rising: {ema_20_rising}"
        )

        if previous_regime != algo.market_regime:
            algo.logger.critical(f"REGIME CHANGE: {previous_regime} -> {algo.market_regime} | SPY {spy_price:.2f} EMA20 {ema_20:.2f} EMA50 {ema_50:.2f}" + (f" RSI {rsi_val:.1f}" if rsi_ready else ""))

    except Exception as e:
        algo.logger.error(f"Regime detection error: {e}")

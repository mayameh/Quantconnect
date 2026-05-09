"""
Bot Configuration — Alpaca native version
Connects to Alpaca Markets REST API (paper or live).
"""
import os


class BOT_Config:
    """Configuration for the Alpaca trading bot."""

    # ============================================================
    # GENERAL
    # ============================================================
    class general:
        strategy_name = "Tech Universe Momentum"
        mode = "LIVE"              # "PAPER" or "LIVE"
        trading_style = os.environ.get("BOT_TRADING_STYLE", "SWING").strip().upper()
        # Fallback only. Live/paper trading replaces this with Alpaca account equity at startup.
        starting_capital = float(os.environ.get("BOT_STARTING_CAPITAL", "0"))

    # ============================================================
    # ALPACA CONNECTION
    # Paper:  https://paper-api.alpaca.markets
    # Live:   https://api.alpaca.markets
    # ============================================================
    class alpaca:
        api_key    = os.environ.get("APCA_API_KEY_ID", "")
        api_secret = os.environ.get("APCA_API_SECRET_KEY", "")
        # True  → paper-trading endpoint; False → live endpoint
        paper      = os.environ.get("APCA_API_PAPER", "true").lower() == "true"

    # ============================================================
    # RISK MANAGEMENT
    # ============================================================
    class risk:
        max_daily_loss        = 100     # $100 daily loss → circuit breaker
        max_drawdown_pct      = 0.05    # 5 % max drawdown

        intraday_max_daily_loss = 150
        swing_max_daily_loss    = 250

        max_position_size_pct = 0.15
        max_position_value    = 3500
        min_entry_notional    = 50
        target_position_value = 1.00

        symbol_max_loss          = 100
        max_consecutive_losses   = 3

    # ============================================================
    # TRADING PARAMETERS
    # ============================================================
    class trading:
        strategy_mode    = os.environ.get("BOT_STRATEGY_MODE", "TECH_MOMENTUM").strip().upper()
        max_positions    = 2
        min_hold_hours   = 24
        max_hold_days    = 14

        entry_signal_score_threshold  = 0.18
        intraday_entry_score_threshold = 0.40
        swing_entry_score_threshold    = 0.45
        bull_entry_score_threshold    = 0.45
        neutral_entry_score_threshold = 0.45
        bear_entry_enabled            = False
        bear_entry_score_threshold    = 0.35

        max_new_entries_per_day       = 3
        min_hours_between_symbol_entries = 4
        reentry_cooldown_after_exit_hours = 6

        stop_loss_pct             = 0.03    # 3 %
        take_profit_pct           = 0.08    # 8 %
        profit_lock_hours         = 30
        profit_lock_min_gain_pct  = 0.018   # 1.8 %

        trailing_stop_pct         = 0.03
        trailing_activation_pct   = 0.04

        max_weekly_trades         = 6
        min_days_between_trades   = 1
        symbol_cooldown_days      = 2

        momentum_rsi_min               = 55    # was 60 — include early momentum builds
        momentum_rsi_max               = 72    # was 78 — exclude overbought chasing
        mean_reversion_zscore_threshold = -1.3
        mean_reversion_rsi_max         = 48
        orb_min_volume_ratio           = 1.05
        sentiment_min_score            = 0.05
        order_imbalance_min            = 0.05

        intraday_benchmark_symbol       = "QQQ"
        backtest_intraday_interval      = "5m"
        intraday_min_relative_strength_pct = -0.0010
        intraday_max_vwap_distance_pct  = 0.012
        intraday_min_volume_ratio       = 0.95
        intraday_max_close_location     = 0.72
        intraday_require_vwap_component = True

        swing_benchmark_symbol          = "XLK"
        swing_min_relative_strength_20d = 0.010
        swing_min_relative_strength_60d = 0.000
        swing_max_ema20_extension_pct   = 0.060
        swing_min_volume_ratio          = 0.90

        bear_rsi_min                  = 25    # was 28
        bear_rsi_max                  = 55    # was 48 — catch more bear setups
        bear_orb_min_volume_ratio     = 1.05

        intraday_signal_interval_minutes = 15
        intraday_orb_minutes             = 30
        intraday_stop_loss_pct           = 0.006
        intraday_take_profit_pct         = 0.018
        intraday_profit_lock_hours       = 3
        intraday_profit_lock_min_gain_pct = 0.006
        intraday_trailing_stop_pct       = 0.006
        intraday_trailing_activation_pct = 0.008
        intraday_flatten_positions       = True

        swing_stop_loss_pct              = 0.03
        swing_take_profit_pct            = 0.08
        swing_profit_lock_hours          = 30
        swing_profit_lock_min_gain_pct   = 0.018
        swing_trailing_stop_pct          = 0.03
        swing_trailing_activation_pct    = 0.04
        swing_flatten_positions          = False

        tech_momentum_lookback_days      = 126
        tech_momentum_top_count          = 10
        tech_momentum_stop_loss_pct      = 0.05
        tech_momentum_min_dollar_volume  = 10_000_000
        tech_momentum_rebalance_weekday  = 0      # Monday
        tech_momentum_target_exposure    = 1.00

    # ============================================================
    # UNIVERSE
    # ============================================================
    class universe:
        core_symbols = ["NVDA", "AAPL", "MSFT", "AMZN", "META"]

        dynamic_enabled  = True
        refresh_days     = 14
        top_n_dynamic    = 10

        candidate_symbols = [
            "NVDA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "AVGO", "TSLA", "NFLX", "AMD",
            "ADBE", "CRM", "ORCL", "INTU", "QCOM", "TXN", "AMAT", "PANW", "MU", "LRCX",
            "ANET", "KLAC", "SHOP", "NOW", "CRWD", "PLTR", "SNOW", "MELI", "UBER", "ABNB",
        ]
        benchmark_symbols = ["SPY", "QQQ", "XLK"]
        exclude_symbols = ["SPY", "QQQ", "XLK"]

        min_price                = 50.0
        min_avg_dollar_volume    = 100_000_000
        avg_volume_window_days   = 20

        momentum_lookback_days   = 22
        momentum_weight          = 0.60
        revenue_growth_weight    = 0.40

        # External fundamentals (Alpaca) for live revenue growth
        fundamentals_external_enabled = True
        fundamentals_provider         = "alpaca"
        fundamentals_base_url         = os.environ.get(
            "BOT_FUNDAMENTALS_BASE_URL", "https://data.alpaca.markets"
        )
        fundamentals_path_template    = os.environ.get(
            "BOT_ALPACA_FINANCIALS_PATH", "/v1beta1/fundamentals/{symbol}"
        )
        # Reuse the same Alpaca credentials
        fundamentals_api_key          = os.environ.get("APCA_API_KEY_ID", "")
        fundamentals_api_secret       = os.environ.get("APCA_API_SECRET_KEY", "")
        fundamentals_timeout_seconds  = 8
        fundamentals_cache_hours      = 24
        fundamentals_fallback_to_static = True

        revenue_growth_1y = {
            "NVDA": 1.20,
            "META": 0.22,
            "AVGO": 0.34,
            "PLTR": 0.26,
            "CRWD": 0.33,
            "MELI": 0.27,
            "ABNB": 0.18,
            "TSLA": 0.05,
            "AAPL": 0.03,
            "MSFT": 0.15,
        }

        request_pause_seconds = 0.25

    # ============================================================
    # MONITORING
    # ============================================================
    class monitoring:
        log_dir   = os.environ.get(
            "BOT_LOG_DIR",
            os.path.expanduser("~/trading/alpaca-bot/logs"),
        )
        log_level = "INFO"

    # ============================================================
    # EMAIL
    # ============================================================
    class email:
        smtp_server     = "smtp.gmail.com"
        smtp_port       = 587
        sender_email    = os.environ.get("BOT_EMAIL_USER", "")
        sender_password = os.environ.get("BOT_EMAIL_PASS", "")
        recipient_email = os.environ.get("BOT_EMAIL_TO", "")

    # ============================================================
    # LIVE FEEDS  (Alpaca news + IEX quote stream, free tier)
    # ============================================================
    class feeds:
        # Re-uses the same Alpaca credentials.
        alpaca_api_key    = os.environ.get("APCA_API_KEY_ID", "")
        alpaca_api_secret = os.environ.get("APCA_API_SECRET_KEY", "")

        # Polling cadence for /v1beta1/news (5-min is comfortable on free tier)
        news_poll_interval_seconds: int = 300

        # Order-book imbalance: stale quotes older than this are ignored
        ob_max_age_seconds: int = 60

        # Sentiment: headlines older than this (minutes) receive zero weight
        sentiment_max_age_minutes: int = 120

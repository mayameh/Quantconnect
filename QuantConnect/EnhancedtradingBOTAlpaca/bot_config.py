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
        strategy_name = "AI-Enhanced Hybrid Universe Strategy"
        mode = "LIVE"              # "PAPER" or "LIVE"
        trading_style = os.environ.get("BOT_TRADING_STYLE", "SWING").strip().upper()
        starting_capital = 11000

    # ============================================================
    # ALPACA CONNECTION
    # Paper:  https://paper-api.alpaca.markets
    # Live:   https://api.alpaca.markets
    # ============================================================
    class alpaca:
        api_key    = os.environ.get("ALPACA_API_KEY", "")
        api_secret = os.environ.get("ALPACA_API_SECRET", "")
        # True  → paper-trading endpoint; False → live endpoint
        paper      = os.environ.get("ALPACA_PAPER", "true").lower() == "true"

    # ============================================================
    # RISK MANAGEMENT
    # ============================================================
    class risk:
        max_daily_loss        = 100     # $100 daily loss → circuit breaker
        max_drawdown_pct      = 0.05    # 5 % max drawdown

        intraday_max_daily_loss = 150
        swing_max_daily_loss    = 250

        max_position_size_pct = 0.25
        min_entry_notional    = 4000
        target_position_value = 0.85

        symbol_max_loss          = 100
        max_consecutive_losses   = 3

    # ============================================================
    # TRADING PARAMETERS
    # ============================================================
    class trading:
        max_positions    = 4
        min_hold_hours   = 24
        max_hold_days    = 14

        stop_loss_pct             = 0.03    # 3 %
        take_profit_pct           = 0.08    # 8 %
        profit_lock_hours         = 30
        profit_lock_min_gain_pct  = 0.018   # 1.8 %

        trailing_stop_pct         = 0.03
        trailing_activation_pct   = 0.04

        max_weekly_trades         = 2
        min_days_between_trades   = 3
        symbol_cooldown_days      = 10

        intraday_signal_interval_minutes = 15
        intraday_orb_minutes             = 30
        intraday_stop_loss_pct           = 0.01
        intraday_take_profit_pct         = 0.025
        intraday_profit_lock_hours       = 3
        intraday_profit_lock_min_gain_pct = 0.006
        intraday_trailing_stop_pct       = 0.008
        intraday_trailing_activation_pct = 0.012
        intraday_flatten_positions       = True

        swing_stop_loss_pct              = 0.03
        swing_take_profit_pct            = 0.08
        swing_profit_lock_hours          = 30
        swing_profit_lock_min_gain_pct   = 0.018
        swing_trailing_stop_pct          = 0.03
        swing_trailing_activation_pct    = 0.04
        swing_flatten_positions          = False

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
        exclude_symbols = ["SPY"]

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
        fundamentals_api_key          = os.environ.get("ALPACA_API_KEY", "")
        fundamentals_api_secret       = os.environ.get("ALPACA_API_SECRET", "")
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
        alpaca_api_key    = os.environ.get("ALPACA_API_KEY", "")
        alpaca_api_secret = os.environ.get("ALPACA_API_SECRET", "")

        # Polling cadence for /v1beta1/news (5-min is comfortable on free tier)
        news_poll_interval_seconds: int = 300

        # Order-book imbalance: stale quotes older than this are ignored
        ob_max_age_seconds: int = 60

        # Sentiment: headlines older than this (minutes) receive zero weight
        sentiment_max_age_minutes: int = 120

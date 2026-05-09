"""Configuration for the Alpaca-native Three-Sleeve Hybrid bot."""
from __future__ import annotations

import os


def _csv_env(name: str, default: str) -> list[str]:
    raw = os.environ.get(name, default)
    return [item.strip().upper() for item in raw.split(",") if item.strip()]


class BotConfig:
    class alpaca:
        api_key = os.environ.get("APCA_API_KEY_ID", "")
        api_secret = os.environ.get("APCA_API_SECRET_KEY", "")
        paper = os.environ.get("APCA_API_PAPER", "true").lower() == "true"

    class runtime:
        log_dir = os.environ.get(
            "BOT_LOG_DIR",
            os.path.expanduser("~/trading/three-sleeve-hybrid/logs"),
        )
        timezone = "US/Eastern"
        starting_capital_fallback = float(os.environ.get("BOT_STARTING_CAPITAL", "100000"))

    class email:
        smtp_server = os.environ.get("BOT_SMTP_SERVER", "smtp.gmail.com")
        smtp_port = int(os.environ.get("BOT_SMTP_PORT", "587"))
        sender_email = os.environ.get("BOT_EMAIL_USER", "")
        sender_password = os.environ.get("BOT_EMAIL_PASS", "")
        recipient_email = os.environ.get("BOT_EMAIL_TO", "")
        enabled = os.environ.get("BOT_EMAIL_ENABLED", "true").lower() == "true"

    class sleeves:
        s3_bull_budget = 0.80
        s1_bull_budget = 0.20
        s1_bull_spy_frac = 0.75
        s1_bull_gld_frac = 0.25
        ml_threshold = 0.65

    class instruments:
        live_spy_hedge = os.environ.get("BOT_S1_MARKET_PROXY", "BRK.B").upper()
        live_gld_hedge = os.environ.get("BOT_S1_GOLD_PROXY", "NEM").upper()
        spy_signal = "SPY"
        gld_signal = "GLD"
        hyg_signal = "HYG"
        lqd_signal = "LQD"
        rsp_signal = "RSP"
        ief_signal = "IEF"
        shy_signal = "SHY"
        vix_symbol = "VIX"
        vix3m_symbol = "VIX3M"

    class universe:
        blacklist = {"GME", "AMC"}
        s2_candidates = _csv_env(
            "BOT_S2_CANDIDATES",
            "AAPL,MSFT,GOOGL,AMZN,META,BRK.B,JPM,V,MA,UNH,HD,PG,COST,PEP,KO,"
            "MRK,ABBV,CSCO,ORCL,TXN,LOW,INTC,IBM,AMGN,CAT,DE,UPS,BLK,GS,MS",
        )
        s3_candidates = _csv_env(
            "BOT_S3_CANDIDATES",
            "NVDA,AAPL,MSFT,AMZN,META,GOOGL,AVGO,TSLA,LLY,JPM,V,MA,UNH,XOM,"
            "COST,HD,PG,NFLX,AMD,CRM,ORCL,ADBE,CSCO,ACN,QCOM,TXN,AMAT,INTU,"
            "NOW,IBM,GE,LIN,MS,GS,CAT,DE,UBER,ANET,PANW,PLTR",
        )

    class s2:
        max_position_weight = 0.20
        max_positions = 10
        momentum_lookback = 63
        momentum_min_return = 0.0

    class s3:
        lookbacks = [21, 63, 126, 189, 252]
        stock_count = 10
        band_len = 189
        hist_len = 126
        adx_limit = 35.0
        adx_period = 14
        rebal_threshold = 0.015
        bottom_levels = {0, 1, 2, 3, 4}
        max_position_weight = 0.20

    class risk:
        min_trade_notional = float(os.environ.get("BOT_MIN_TRADE_NOTIONAL", "25"))
        max_gross_exposure = float(os.environ.get("BOT_MAX_GROSS_EXPOSURE", "1.0"))
        fractional_qty_decimals = 6

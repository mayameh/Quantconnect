"""
live_feeds.py — Free real-time data feeds for the trading bot.

Three feed sources (all free-tier, no paid subscriptions required):

  1. AlpacaNewsPoller  – REST  /v1beta1/news  every N minutes.
                         Uses the same APCA key already configured for fundamentals.
                         Scores each headline with VADER and caches per-ticker with
                         exponential time-decay (30-min half-life).

  2. VaderScorer       – vaderSentiment lexicon scorer → compound score in [-1, 1].
                         Falls back gracefully to 0.0 if the package is not installed.

  3. AlpacaQuoteStream – WebSocket  wss://stream.data.alpaca.markets/v2/iex  (IEX, free).
                         Subscribes to real-time quotes for the active universe.
                         Computes bid/ask size imbalance → [-1, 1].
                         Positive = bid-heavy (bullish pressure).
                         Auto-reconnects on disconnect.

Usage (from main_ib.py):
    from new_live_feeds import FeedManager
    feeds = FeedManager(api_key, api_secret)
    feeds.update_symbols(["NVDA", "AAPL", ...])
    feeds.start()
    ...
    score = feeds.get_sentiment("NVDA")    # [-1, 1]
    imb   = feeds.get_ob_imbalance("NVDA") # [-1, 1]
    ...
    feeds.stop()
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import requests

logger = logging.getLogger("TradingBot.Feeds")

# ── Optional dependencies (graceful degradation) ────────────────────────────

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer  # type: ignore
    _VADER_OK = True
except ImportError:
    _VADER_OK = False
    logger.warning(
        "vaderSentiment not installed — sentiment feed disabled. "
        "Fix: pip install vaderSentiment"
    )

try:
    import websocket  # websocket-client package  # type: ignore
    _WS_OK = True
except ImportError:
    _WS_OK = False
    logger.warning(
        "websocket-client not installed — order-book feed disabled. "
        "Fix: pip install websocket-client"
    )


# ── Sentiment cache ──────────────────────────────────────────────────────────

class _SentimentEntry:
    """Single scored headline with a UTC timestamp."""
    __slots__ = ("score", "ts", "headline")

    def __init__(self, score: float, ts: datetime, headline: str) -> None:
        self.score = score
        self.ts = ts
        self.headline = headline


class SentimentCache:
    """
    Thread-safe per-ticker sentiment cache.

    Aggregates recent headlines via exponential time-decay so that older news
    has less influence.  Half-life = DECAY_HALF_LIFE_MINUTES.
    """

    DECAY_HALF_LIFE_MINUTES: float = 30.0

    def __init__(self, max_entries_per_ticker: int = 30) -> None:
        self._lock = threading.Lock()
        self._data: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=max_entries_per_ticker)
        )

    def add(
        self,
        ticker: str,
        score: float,
        headline: str,
        ts: Optional[datetime] = None,
    ) -> None:
        entry = _SentimentEntry(score, ts or datetime.utcnow(), headline)
        with self._lock:
            self._data[ticker.upper()].append(entry)

    def get(self, ticker: str, max_age_minutes: int = 120) -> float:
        """
        Returns a weighted-average sentiment score in [-1, 1].
        Returns 0.0 when no recent headlines exist.
        """
        now = datetime.utcnow()
        cutoff = now - timedelta(minutes=max_age_minutes)
        hl = self.DECAY_HALF_LIFE_MINUTES
        total_w = 0.0
        total_ws = 0.0
        with self._lock:
            entries = list(self._data.get(ticker.upper(), []))
        for e in entries:
            if e.ts < cutoff:
                continue
            age_min = max(0.0, (now - e.ts).total_seconds() / 60.0)
            w = 0.5 ** (age_min / hl)
            total_w += w
            total_ws += w * e.score
        return total_ws / total_w if total_w > 0 else 0.0


# ── VADER scorer ─────────────────────────────────────────────────────────────

class VaderScorer:
    """VADER compound scorer → float in [-1, 1].  0.0 if VADER unavailable."""

    def __init__(self) -> None:
        self._analyzer = SentimentIntensityAnalyzer() if _VADER_OK else None

    def score(self, text: str) -> float:
        if not self._analyzer or not text:
            return 0.0
        return float(self._analyzer.polarity_scores(text)["compound"])


# ── Alpaca News poller ────────────────────────────────────────────────────────

class AlpacaNewsPoller:
    """
    Polls Alpaca Markets News API (free tier) on a background thread.

    Endpoint : GET https://data.alpaca.markets/v1beta1/news
    Auth     : APCA-API-KEY-ID / APCA-API-SECRET-KEY headers
    Rate     : free tier allows reasonable polling; default 5-minute interval.
    """

    NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
    _MAX_SYMBOLS_PER_BATCH = 10   # Keep well within free-tier limits

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        sentiment_cache: SentimentCache,
        scorer: VaderScorer,
        poll_interval_seconds: int = 300,
    ) -> None:
        self._key = api_key
        self._secret = api_secret
        self._cache = sentiment_cache
        self._scorer = scorer
        self._interval = poll_interval_seconds
        self._symbols: List[str] = []
        self._seen_ids: set = set()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def update_symbols(self, symbols: List[str]) -> None:
        self._symbols = list(symbols)

    def start(self) -> None:
        if not _VADER_OK:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="alpaca-news-poller"
        )
        self._thread.start()
        logger.info("AlpacaNewsPoller started (interval=%ds).", self._interval)

    def stop(self) -> None:
        self._stop.set()

    # ── internals ────────────────────────────────────────────────────────────

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll()
            except Exception as exc:
                logger.debug("AlpacaNewsPoller poll error: %s", exc)
            self._stop.wait(self._interval)

    def _poll(self) -> None:
        if not self._symbols:
            return
        # Batch symbols to stay within free-tier limits
        syms = self._symbols[: self._MAX_SYMBOLS_PER_BATCH]
        headers = {
            "APCA-API-KEY-ID": self._key,
            "APCA-API-SECRET-KEY": self._secret,
        }
        params = {"symbols": ",".join(syms), "limit": 20, "sort": "desc"}
        resp = requests.get(
            self.NEWS_URL, headers=headers, params=params, timeout=10
        )
        if resp.status_code != 200:
            logger.debug("AlpacaNewsPoller HTTP %d: %s", resp.status_code, resp.text[:200])
            return

        articles = resp.json().get("news", [])
        new_count = 0
        for art in articles:
            art_id = art.get("id")
            if art_id in self._seen_ids:
                continue
            self._seen_ids.add(art_id)

            headline = art.get("headline", "")
            summary = art.get("summary", "")
            text = f"{headline}. {summary}".strip(". ")
            score = self._scorer.score(text)

            ts_str = art.get("created_at", "")
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).replace(
                    tzinfo=None
                )
            except Exception:
                ts = datetime.utcnow()

            # Map article to tickers it mentions (Alpaca returns symbols list)
            article_tickers = [t.upper() for t in art.get("symbols", [])]
            targets = (
                [t for t in article_tickers if t in self._symbols]
                or [t for t in self._symbols if t.upper() in article_tickers]
            )
            if not targets:
                # No specific ticker match; apply headline to all watched symbols
                # only if a ticker name appears in the text (avoids noise)
                targets = [t for t in self._symbols if t.upper() in text.upper()]

            for t in targets:
                self._cache.add(t, score, headline, ts)

            new_count += 1

        if new_count:
            logger.debug("AlpacaNewsPoller: %d new articles processed.", new_count)

        # Trim seen_ids to prevent unbounded growth
        if len(self._seen_ids) > 5000:
            self._seen_ids = set(list(self._seen_ids)[-2500:])


# ── Alpaca IEX WebSocket quote stream ─────────────────────────────────────────

class _QuoteState:
    __slots__ = ("bid_size", "ask_size", "updated")

    def __init__(self) -> None:
        self.bid_size = 0.0
        self.ask_size = 0.0
        self.updated = datetime.utcnow()


class AlpacaQuoteStream:
    """
    Streams real-time quotes from Alpaca IEX WebSocket (free tier).

    URL  : wss://stream.data.alpaca.markets/v2/iex
    Auth : {"action":"auth","key":"...","secret":"..."}
    Sub  : {"action":"subscribe","quotes":["NVDA","AAPL",...]}

    Each quote message (T="q") contains:
        bp  bid price     bs  bid size
        ap  ask price     as  ask size

    Order-book imbalance  = (bid_size − ask_size) / (bid_size + ask_size)
    Range [-1, 1];  >0 = bid-heavy (bullish);  <0 = offer-heavy (bearish).
    """

    WS_URL = "wss://stream.data.alpaca.markets/v2/iex"

    def __init__(self, api_key: str, api_secret: str) -> None:
        self._key = api_key
        self._secret = api_secret
        self._symbols: List[str] = []
        self._quotes: Dict[str, _QuoteState] = defaultdict(_QuoteState)
        self._lock = threading.Lock()
        self._ws: Optional[websocket.WebSocketApp] = None  # type: ignore[name-defined]
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._authenticated = False

    def update_symbols(self, symbols: List[str]) -> None:
        old = set(self._symbols)
        new = set(s.upper() for s in symbols)
        self._symbols = list(new)
        added = new - old
        if added and self._ws and self._authenticated:
            try:
                self._ws.send(
                    json.dumps({"action": "subscribe", "quotes": list(added)})
                )
            except Exception as exc:
                logger.debug("QuoteStream update_symbols send error: %s", exc)

    def get_imbalance(self, ticker: str, max_age_seconds: int = 60) -> float:
        """
        Returns bid/ask size imbalance in [-1, 1].
        Returns 0.0 when no recent quote is available.
        """
        with self._lock:
            state = self._quotes.get(ticker.upper())
        if not state:
            return 0.0
        age = (datetime.utcnow() - state.updated).total_seconds()
        if age > max_age_seconds:
            return 0.0
        total = state.bid_size + state.ask_size
        if total == 0:
            return 0.0
        return (state.bid_size - state.ask_size) / total

    def start(self) -> None:
        if not _WS_OK:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_ws, daemon=True, name="alpaca-quote-ws"
        )
        self._thread.start()
        logger.info("AlpacaQuoteStream started.")

    def stop(self) -> None:
        self._stop.set()
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass

    # ── WebSocket internals ───────────────────────────────────────────────────

    def _run_ws(self) -> None:
        retry_delay = 5
        while not self._stop.is_set():
            try:
                self._authenticated = False
                self._ws = websocket.WebSocketApp(  # type: ignore[attr-defined]
                    self.WS_URL,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                self._ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as exc:
                logger.debug("AlpacaQuoteStream WS fatal: %s", exc)
            if not self._stop.is_set():
                logger.debug(
                    "AlpacaQuoteStream reconnecting in %ds...", retry_delay
                )
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)

    def _on_open(self, ws) -> None:
        ws.send(
            json.dumps({"action": "auth", "key": self._key, "secret": self._secret})
        )

    def _on_message(self, ws, raw: str) -> None:
        try:
            msgs = json.loads(raw)
        except Exception:
            return
        if not isinstance(msgs, list):
            msgs = [msgs]
        for msg in msgs:
            msg_type = msg.get("T")
            if msg_type == "success" and msg.get("msg") == "authenticated":
                self._authenticated = True
                if self._symbols:
                    ws.send(
                        json.dumps({"action": "subscribe", "quotes": self._symbols})
                    )
                logger.info("AlpacaQuoteStream authenticated + subscribed.")
            elif msg_type == "q":
                sym = msg.get("S", "").upper()
                bs = float(msg.get("bs") or 0)
                as_ = float(msg.get("as") or 0)
                with self._lock:
                    state = self._quotes[sym]
                    state.bid_size = bs
                    state.ask_size = as_
                    state.updated = datetime.utcnow()

    def _on_error(self, ws, error) -> None:
        logger.debug("AlpacaQuoteStream WS error: %s", error)

    def _on_close(self, ws, close_status_code, close_msg) -> None:
        self._authenticated = False
        logger.debug(
            "AlpacaQuoteStream WS closed: status=%s msg=%s",
            close_status_code,
            close_msg,
        )


# ── Feed manager (public API) ────────────────────────────────────────────────

class FeedManager:
    """
    Aggregates AlpacaNewsPoller + AlpacaQuoteStream into a single interface.

    Requires:
        api_key    – APCA-API-KEY-ID  (same key used for fundamentals in bot_config)
        api_secret – APCA-API-SECRET-KEY

    If credentials are empty the manager starts in no-op mode: all getters
    return 0.0 so downstream signal logic is unaffected.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        news_poll_interval_seconds: int = 300,
    ) -> None:
        self._enabled = bool(api_key and api_secret)
        self._scorer = VaderScorer()
        self._sentiment_cache = SentimentCache()
        self._news_poller = AlpacaNewsPoller(
            api_key=api_key,
            api_secret=api_secret,
            sentiment_cache=self._sentiment_cache,
            scorer=self._scorer,
            poll_interval_seconds=news_poll_interval_seconds,
        )
        self._quote_stream = AlpacaQuoteStream(api_key=api_key, api_secret=api_secret)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def update_symbols(self, symbols: List[str]) -> None:
        """Call whenever the active universe changes."""
        self._news_poller.update_symbols(symbols)
        self._quote_stream.update_symbols(symbols)

    def start(self) -> None:
        if not self._enabled:
            logger.warning(
                "FeedManager: no Alpaca credentials found "
                "(BOT_ALPACA_API_KEY / BOT_ALPACA_API_SECRET) — feeds disabled."
            )
            return
        self._news_poller.start()
        self._quote_stream.start()
        logger.info("FeedManager: all feeds started.")

    def stop(self) -> None:
        self._news_poller.stop()
        self._quote_stream.stop()
        logger.info("FeedManager: feeds stopped.")

    # ── signal accessors ──────────────────────────────────────────────────────

    def get_sentiment(self, ticker: str) -> float:
        """
        Time-decayed news sentiment for *ticker*.
        Returns a float in [-1, 1].  0.0 = neutral or no recent news.
        """
        return self._sentiment_cache.get(ticker)

    def get_ob_imbalance(self, ticker: str) -> float:
        """
        Real-time bid/ask size imbalance from the IEX quote stream.
        Returns a float in [-1, 1].  0.0 = balanced or no recent data.
        """
        return self._quote_stream.get_imbalance(ticker)

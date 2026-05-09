"""
Three-Sleeve Hybrid Strategy — v1.3.9
══════════════════════════════════════
MODE LOGIC — binary switch:
  S3+S1 bull (strong bull): S3=80% momentum, S1=20% fixed hedge (75/25 BRK.B/NEM), S2=0%
  S1+S2  (not bull)       : S1=40-100% BRK.B/NEM, S2=0-40% equity, S3=0%

Strong bull gate — ALL five required:
  1. SPY > 200-day SMA    4. VIX < 80th-pct (300-bar window)
  2. SPY > 50-day SMA     5. VIX < 25 (hard ceiling)
  3. SPY 20-day return > 0

Regime (S1+S2 mode): R1/R4/R6 stress -> S2 off; R2/R3/R5 calm -> S2 on

Schedules (anchored to SPY/NYSE in both live and backtest):
  CheckSignal  : Daily      BMC-120  (~14:00 ET / 19:00 London)
  TrainModel   : MonthStart BMC-150
  RebalanceS2  : MonthStart BMC-90
  RebalanceS3  : MonthEnd   BMC-30
  DailySnapshot: Daily      BMC-1

Live instruments (UK — no PRIIPs issues, all US individual stocks):
  S1 hedge : BRK.B (Berkshire B — S&P proxy, ~0.95 correlation)
             NEM   (Newmont Mining — gold proxy, ~0.80 gold correlation)
  S2/S3    : US-listed equities (value+momentum / large-cap momentum)
  Signals  : SPY/GLD/HYG/LQD/IEF/SHY (read-only, never traded in live)

Log tags: [GATE] [SWITCH] [STATE] [S1] [S2] [S3] [SNAP] [INIT] [END]
"""

from AlgorithmImports import *
from collections import defaultdict, deque
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler

LABEL_HORIZON  = 21
SAFETY_BUFFER  = 10
TRAIN_VAL_GAP  = 126
MIN_TRAIN_ROWS = 100
ML_THRESHOLD   = 0.65
MIN_VIX_BARS   = 50
MIN_SPY_BARS   = 260
MIN_AUX_BARS   = 60
DIP_DEEP_THRESHOLD   = -0.08
DIP_SHALLOW_SPY_W    = 0.60
DIP_SHALLOW_SPY_W_ML = 0.75
DIP_DEEP_SPY_W       = 0.85
DIP_DEEP_SPY_W_ML    = 1.00
S3_BULL_BUDGET    = 0.80   # S3 allocation in strong bull mode
S1_BULL_BUDGET    = 0.20   # S1 macro hedge in strong bull mode
S1_BULL_SPY_FRAC  = 0.75   # fixed SPY fraction of S1 hedge in bull mode
S1_BULL_GLD_FRAC  = 0.25   # fixed GLD fraction of S1 hedge in bull mode


class ThreeSleeveHybrid(QCAlgorithm):

    def Initialize(self):
        self.SetStartDate(2016, 1, 1)
        self.SetEndDate(2021, 1, 1)
        self.SetCash(100_000)
        self.SetBrokerageModel(BrokerageName.InteractiveBrokersBrokerage, AccountType.Margin)

        if self.LiveMode:
            # UK PRIIPs: use US-listed individual stocks as S1 hedge — no restrictions:
            #   BRK.B (Berkshire B) — broad market proxy, ~0.95 S&P correlation
            #   NEM  (Newmont)  — largest gold miner, ~0.80 gold price correlation
            spy_ticker, gld_ticker = "BRK.B", "NEM"
            hyg_ticker, lqd_ticker = "HYG",  "LQD"
            ief_ticker, shy_ticker = "IEF",  "SHY"
            use_rsp = False
        else:
            spy_ticker, gld_ticker = "SPY",  "GLD"
            hyg_ticker, lqd_ticker = "HYG",  "LQD"
            ief_ticker, shy_ticker = "IEF",  "SHY"
            use_rsp = True

        self.spy = self.AddEquity(spy_ticker, Resolution.Daily).Symbol
        self.gld = self.AddEquity(gld_ticker, Resolution.Daily).Symbol
        self.vix   = self.AddData(CBOE, "VIX",   Resolution.Daily).Symbol
        self.vix3m = self.AddData(CBOE, "VIX3M", Resolution.Daily).Symbol
        self.hyg   = self.AddEquity(hyg_ticker, Resolution.Daily).Symbol
        self.lqd   = self.AddEquity(lqd_ticker, Resolution.Daily).Symbol
        self.rsp   = self.AddEquity("RSP", Resolution.Daily).Symbol if use_rsp else None
        self.ief   = self.AddEquity(ief_ticker, Resolution.Daily).Symbol
        self.shy   = self.AddEquity(shy_ticker, Resolution.Daily).Symbol

        if self.LiveMode:
            # Regime signals always use SPY/GLD history (read-only, never traded)
            self.spy_hist = self.AddEquity("SPY", Resolution.Daily).Symbol
            self.gld_hist = self.AddEquity("GLD", Resolution.Daily).Symbol
            self.hyg_hist = self.hyg
            self.lqd_hist = self.lqd
            self.ief_hist = self.ief
            self.shy_hist = self.shy
            # S1 hedge executes via BRK/B and NEM — fully automated, no PRIIPs issues
            self.spy_hedge = self.spy   # BRK/B
            self.gld_hedge = self.gld   # NEM
            self.Log("[INIT] Live S1 hedge: BRK.B (market proxy) + NEM (gold proxy)")
        else:
            self.spy_hist = self.spy;  self.gld_hist = self.gld
            self.hyg_hist = self.hyg;  self.lqd_hist = self.lqd
            self.ief_hist = self.ief;  self.shy_hist = self.shy
            self.spy_hedge = self.spy   # SPY in backtest
            self.gld_hedge = self.gld   # GLD in backtest

        self.SetBenchmark("SPY")   # SPY always available as benchmark read-only
        self.Log(f"[INIT] mode={'Live' if self.LiveMode else 'Backtest'} spy={spy_ticker}")

        self.model   = RandomForestClassifier(n_estimators=200, max_depth=6,
                                              min_samples_leaf=20, random_state=42)
        self.scaler  = StandardScaler()
        self.trained = False

        self.s1_spy_weight    = 0.0
        self.s1_gld_weight    = 0.0
        self._sleeves_active  = True
        self._s3_bull_market  = False
        self.s2_sleeve_budget = 0.0
        self._initial_deploy_done = False

        self.S2_MAX_POSITION_WEIGHT = 0.20
        self.S2_MAX_POSITIONS       = 10
        self.S2_MIN_HISTORY_DAYS    = 5
        self.S2_MOMENTUM_LOOKBACK   = 63
        self.S2_MOMENTUM_MIN_RETURN = 0.0
        self._s2_candidates: set  = set()
        self._s2_added_date: dict = {}
        self._s2_momentum:   dict = {}

        self._s3_candidates: set  = set()
        self.s3_lookbacks         = [21, 63, 126, 189, 252]
        self.s3_stock_count       = 10
        self.s3_band_len          = 189
        self.s3_hist_len          = 126
        self.s3_adx_limit         = 35
        self.s3_adx_period        = 14
        self.s3_rebal_threshold   = 0.015
        self.s3_symbols     = set()
        self.s3_ma          = {}
        self.s3_adx         = {}
        self.s3_close_win   = {}
        self.s3_stretch_ema = {}
        self.s3_band_hist   = {}
        self.s3_stretch_win = {}
        self.s3_band_idx    = {}
        self.s3_BOTTOM_LEVELS = {0, 1, 2, 3, 4}
        self.s3_allow         = True
        self.s3_was_risk_off  = False
        self.s3_risk_off_date = None
        self.s3_max_stress    = 0.0

        self._hwm        = 0.0
        self._prev_value = None
        self._daily_rets = deque(maxlen=252)

        self.UniverseSettings.Resolution            = Resolution.Daily
        self.UniverseSettings.DataNormalizationMode = DataNormalizationMode.Adjusted
        self.UniverseSettings.FillDataBeforeStart   = True
        self._universe_blacklist = {"GME", "AMC"}
        self.AddUniverse(self.MergedUniverseSelection)

        # Schedule anchor: spy_hist = SPY in both live and backtest.
        # SPY NYSE hours ensure CheckSignal fires during US market session.
        # In live: BMC-120 = ~14:00 ET = 19:00 London (US market open, orders fill same day).
        anchor = self.spy_hist
        self.Schedule.On(self.DateRules.MonthStart(anchor),
                         self.TimeRules.BeforeMarketClose(anchor, 150), self.TrainModel)
        self.Schedule.On(self.DateRules.EveryDay(anchor),
                         self.TimeRules.BeforeMarketClose(anchor, 120), self.CheckSignal)
        self.Schedule.On(self.DateRules.MonthStart(anchor),
                         self.TimeRules.BeforeMarketClose(anchor, 90), self.RebalanceSleeve2)
        self.Schedule.On(self.DateRules.MonthEnd(anchor),
                         self.TimeRules.BeforeMarketClose(anchor, 30), self.RebalanceSleeve3)
        self.Schedule.On(self.DateRules.EveryDay(anchor),
                         self.TimeRules.BeforeMarketClose(anchor, 1), self._DailySnapshot)
        self.Log(f"[INIT] Schedule anchor=SPY/NYSE BMC-120 (~14:00 ET)")
        self.SetWarmUp(300)

    # ── Logging helpers ───────────────────────────────────────────────────────

    def _log_gate(self, spy, sma50, sma200, ret20, vix, vix80, bull):
        def t(v): return "PASS" if v else "FAIL"
        self.Log(
            f"[GATE] {self.Time:%Y-%m-%d} "
            f"C1(>200MA):{t(spy>sma200)} C2(>50MA):{t(spy>sma50)} "
            f"C3(20d>0):{t(ret20>0)}({ret20:+.2%}) "
            f"C4(VIX<80pct):{t(vix<vix80)}({vix:.1f}<{vix80:.1f}) "
            f"C5(VIX<25):{t(vix<25)} => {'BULL' if bull else 'NOT_BULL'}"
        )

    def _log_state(self, tag):
        eq = self.Portfolio.TotalPortfolioValue
        if eq <= 0: return
        macro = {self.spy_hedge, self.gld_hedge}
        # Exclusive buckets: S1 first, then S2, then S3 for anything not already claimed.
        # Dual-listed stocks (in both _s2_candidates and s3_symbols) are counted once in S2.
        s1k = {kvp.Key for kvp in self.Portfolio if kvp.Value.Invested and kvp.Key in macro}
        s2k = {kvp.Key for kvp in self.Portfolio if kvp.Value.Invested and kvp.Key in self._s2_candidates}
        s3k = {kvp.Key for kvp in self.Portfolio if kvp.Value.Invested
               and kvp.Key in self.s3_symbols and kvp.Key not in s2k}
        s1v = sum(self.Portfolio[k].HoldingsValue for k in s1k)
        s2v = sum(self.Portfolio[k].HoldingsValue for k in s2k)
        s3v = sum(self.Portfolio[k].HoldingsValue for k in s3k)
        self.Log(
            f"[STATE] {tag} {self.Time:%Y-%m-%d} Eq={eq:,.0f} "
            f"S1={s1v/eq:.1%} S2={s2v/eq:.1%} S3={s3v/eq:.1%} Cash={self.Portfolio.Cash/eq:.1%}"
        )
        if s1k: self.Log(f"[STATE]  S1: {' '.join(f'{k.Value}({self.Portfolio[k].HoldingsValue/eq:.1%})' for k in s1k)}")
        if s2k: self.Log(f"[STATE]  S2: {' '.join(f'{k.Value}({self.Portfolio[k].HoldingsValue/eq:.1%})' for k in s2k)}")
        if s3k: self.Log(f"[STATE]  S3: {' '.join(f'{k.Value}({self.Portfolio[k].HoldingsValue/eq:.1%})' for k in s3k)}")

    def _log_budgets(self, tag):
        self.Log(
            f"[STATE] budgets/{tag} mode={'S3+S1' if self._s3_bull_market else 'S1+S2'} "
            f"spy={self.s1_spy_weight:.3f} gld={self.s1_gld_weight:.3f} "
            f"S2={self.s2_sleeve_budget:.3f} "
            f"S3={S3_BULL_BUDGET if self._s3_bull_market else 0.0:.3f} "
            f"S2active={self._sleeves_active}"
        )

    # ── Universe ──────────────────────────────────────────────────────────────

    def _uni_get_float(self, f, paths):
        for p in paths:
            try:
                obj = f
                for part in p.split('.'): obj = getattr(obj, part)
                if isinstance(obj, (float,int)) and np.isfinite(obj): return float(obj)
                if hasattr(obj,'Value'):
                    val = obj.Value
                    if isinstance(val,(float,int)) and np.isfinite(val): return float(val)
                val = float(obj)
                if np.isfinite(val): return val
            except: continue
        return float('nan')

    def _uni_is_finite(self, v):
        try: return v is not None and np.isfinite(float(v))
        except: return False

    def MergedUniverseSelection(self, fundamentals):
        s2_candidates = []
        s3_buckets    = defaultdict(list)
        for f in fundamentals:
            if not f.has_fundamental_data: continue
            if f.symbol.Value in self._universe_blacklist: continue
            exchange = f.company_reference.primary_exchange_id
            price    = f.price
            mktcap   = f.market_cap
            if (exchange in ("NYS","NAS","ASE") and price and price > 5
                    and mktcap and mktcap >= 5_000_000_000
                    and getattr(f,'DollarVolume',0) >= 50_000_000):   # $50M ADV floor
                sector = f.asset_classification.morningstar_sector_code
                if sector: s3_buckets[sector].append(f)
            if not price or price <= 5: continue
            if getattr(f,'DollarVolume',0) <= 10_000_000: continue
            pe  = self._uni_get_float(f,["ValuationRatios.PERatio","ValuationRatios.PriceEarningsRatio"])
            dte = self._uni_get_float(f,["OperationRatios.DebtToEquity","OperationRatios.TotalDebtEquityRatio"])
            dy  = self._uni_get_float(f,["ValuationRatios.TrailingDividendYield","ValuationRatios.ForwardDividendYield"])
            roi = self._uni_get_float(f,["OperationRatios.ROIC","ProfitabilityRatios.ROIC",
                                         "ProfitabilityRatios.ReturnOnInvestedCapital",
                                         "ProfitabilityRatios.ReturnOnInvestment"])
            if not all(self._uni_is_finite(v) for v in [pe,dte,dy,roi]): continue
            if pe<5 or pe>18 or dte>=1.0 or dy<=0.01 or roi<=0.12: continue
            s2_candidates.append((f.symbol, float(roi)))
        s2_sym = [x[0] for x in sorted(s2_candidates,key=lambda x:x[1],reverse=True)[:20]]
        s3_sym = []
        for _,stocks in s3_buckets.items():
            stocks.sort(key=lambda x:x.market_cap,reverse=True)
            s3_sym.extend(s.symbol for s in stocks[:100])
        self._s2_candidates = set(s2_sym)
        self._s3_candidates = set(s3_sym)
        self.Log(f"[STATE] Universe S2={len(s2_sym)} S3={len(s3_sym)} union={len(set(s2_sym)|set(s3_sym))}")
        return list(set(s2_sym)|set(s3_sym))

    def OnSecuritiesChanged(self, changes: SecurityChanges):
        macro = {self.spy,self.gld,self.hyg,self.lqd,self.ief,self.shy}
        if self.rsp: macro.add(self.rsp)
        added_s2,added_s3,rem_s2,rem_s3 = [],[],[],[]
        for sec in changes.RemovedSecurities:
            s = sec.Symbol
            if s in macro: continue
            self._s2_momentum.pop(s,None); self._s2_added_date.pop(s,None)
            if s in self._s2_candidates: rem_s2.append(s.Value)
            self.s3_symbols.discard(s)
            for d in [self.s3_ma,self.s3_adx,self.s3_stretch_ema,self.s3_close_win,
                      self.s3_band_hist,self.s3_band_idx,self.s3_stretch_win]: d.pop(s,None)
            if s in self._s3_candidates: rem_s3.append(s.Value)
        for sec in changes.AddedSecurities:
            s = sec.Symbol
            if s in macro: continue
            sec.SetFeeModel(InteractiveBrokersFeeModel())
            self._s2_added_date[s] = self.Time
            self._s2_momentum[s]   = self.ROC(s,self.S2_MOMENTUM_LOOKBACK,Resolution.Daily)
            if s in self._s2_candidates: added_s2.append(s.Value)
            if s in self._s3_candidates:
                self.s3_symbols.add(s)
                self.s3_ma[s]          = self.EMA(s,self.s3_band_len,Resolution.Daily)
                self.s3_adx[s]         = self.ADX(s,self.s3_adx_period,Resolution.Daily)
                self.s3_stretch_ema[s] = self.EMA(s,self.s3_band_len,Resolution.Daily)
                self.s3_close_win[s]   = RollingWindow[float](self.s3_band_len)
                self.s3_band_hist[s]   = RollingWindow[int](self.s3_hist_len)
                self.s3_stretch_win[s] = RollingWindow[float](self.s3_hist_len)
                added_s3.append(s.Value)
        if added_s2 or rem_s2:
            self.Log(f"[STATE] UniChange S2 +{len(added_s2)}/-{len(rem_s2)} pool={len(self._s2_candidates)}")
        if added_s3 or rem_s3:
            self.Log(f"[STATE] UniChange S3 +{len(added_s3)}/-{len(rem_s3)} active={len(self.s3_symbols)}")

    # ── OnData ────────────────────────────────────────────────────────────────

    def OnData(self, data: Slice):
        for s in list(self.s3_symbols):
            if not data.ContainsKey(s): continue
            bar = data[s]
            if bar is None: continue
            close = bar.Close
            self.s3_close_win[s].Add(close)
            if not self.s3_close_win[s].IsReady or not self.s3_ma[s].IsReady: continue
            dev = np.std(list(self.s3_close_win[s]))
            if dev <= 0: continue
            mid     = self.s3_ma[s].Current.Value
            stretch = abs(close - mid) / dev
            self.s3_stretch_ema[s].Update(self.Time, stretch)
            self.s3_stretch_win[s].Add(stretch)
            bands = [mid-dev*1.618,mid-dev*1.382,mid-dev,mid-dev*0.809,mid-dev*0.5,mid-dev*0.382,
                     mid,mid+dev*0.382,mid+dev*0.5,mid+dev*0.809,mid+dev,mid+dev*1.382,mid+dev*1.618]
            self.s3_band_idx[s] = self._s3_band_index(close, bands)

    def _s3_band_index(self, price, bands):
        for i in range(len(bands)-1):
            if bands[i] <= price < bands[i+1]: return i
        return len(bands)-2

    # ── Daily snapshot ────────────────────────────────────────────────────────

    def _DailySnapshot(self):
        if self.IsWarmingUp: return
        eq        = self.Portfolio.TotalPortfolioValue
        self._hwm = max(self._hwm, eq)
        dd        = (eq-self._hwm)/self._hwm if self._hwm>0 else 0.0
        dr        = (eq-self._prev_value)/self._prev_value if self._prev_value else 0.0
        self._prev_value = eq
        self._daily_rets.append(dr)
        sh = ""
        if len(self._daily_rets) >= 20:
            r = np.array(self._daily_rets)
            sig = np.std(r)*np.sqrt(252)
            sh = f" Sh={np.mean(r)*252/sig if sig>0 else 0:+.2f}"
        macro = {self.spy_hedge, self.gld_hedge}
        s1v = sum(kvp.Value.HoldingsValue for kvp in self.Portfolio if kvp.Value.Invested and kvp.Key in macro)
        s2v = sum(kvp.Value.HoldingsValue for kvp in self.Portfolio if kvp.Value.Invested and kvp.Key in self._s2_candidates)
        s3v = sum(kvp.Value.HoldingsValue for kvp in self.Portfolio if kvp.Value.Invested and kvp.Key in self.s3_symbols)
        mode = "S3+S1  " if self._s3_bull_market else "S1+S2  "
        self.Log(
            f"[SNAP] {self.Time:%Y-%m-%d} Eq={eq:,.0f} DD={dd:.2%} D={dr:+.2%}{sh} "
            f"[{mode}] S1={s1v/eq:.1%} S2={s2v/eq:.1%} S3={s3v/eq:.1%} Cash={self.Portfolio.Cash/eq:.1%}"
        )

    # ── History helpers ───────────────────────────────────────────────────────

    def _extract_closes(self, df, symbol):
        if df is None or df.empty: return None
        if isinstance(df.index, pd.MultiIndex):
            for key in (symbol, symbol.Value if hasattr(symbol,'Value') else None):
                if key is None: continue
                try:
                    c = df.xs(key,level=0)['close'].values
                    if len(c)>0: return c
                except: pass
        if 'close' in df.columns:
            c = df['close'].values
            if len(c)>0: return c
        return None

    def _get_closes(self, symbol, n_bars, is_custom=False):
        nm = symbol.Value if hasattr(symbol,'Value') else str(symbol)
        try:
            if is_custom:
                df = self.History(CBOE, symbol, self.Time-timedelta(days=n_bars*2), self.Time, Resolution.Daily)
                c  = self._extract_closes(df, symbol)
                if c is not None: return c
                self.Log(f"_get_closes CBOE [{nm}]: empty"); return None
            df = self.History([symbol], n_bars, Resolution.Daily)
            c  = self._extract_closes(df, symbol)
            if c is not None: return c
            df = self.History([symbol], self.Time-timedelta(days=n_bars*2), self.Time, Resolution.Daily)
            c  = self._extract_closes(df, symbol)
            if c is not None: return c
            self.Log(f"_get_closes [{nm}]: both attempts empty"); return None
        except Exception as e:
            self.Log(f"_get_closes error [{nm}]: {e}"); return None

    def _get_cboe_closes(self, symbol, days=4000, min_bars=1):
        nm = symbol.Value if hasattr(symbol,'Value') else str(symbol)
        for att, mult in enumerate((1,2), start=1):
            try:
                df = self.History(CBOE, symbol, self.Time-timedelta(days=days*mult), self.Time, Resolution.Daily)
                if df is None or df.empty: self.Log(f"_get_cboe [{nm}]: empty att={att}"); continue
                c = df['close'].values
                if len(c)>=min_bars: return c
            except Exception as e:
                self.Log(f"_get_cboe error [{nm}]: {e}"); return None
        self.Log(f"_get_cboe [{nm}]: failed"); return None

    # ── Sleeve 1 features & training ─────────────────────────────────────────

    def GetFeatures(self, vix_c, spy_c, vix3m_closes=None, hyg_closes=None,
                    lqd_closes=None, rsp_closes=None, ief_closes=None, shy_closes=None):
        if len(vix_c)<MIN_VIX_BARS or len(spy_c)<MIN_SPY_BARS: return None
        try:
            cv=vix_c[-1]; sc=spy_c[-1]
            vs20=np.mean(vix_c[-20:]); vs50=np.mean(vix_c[-50:]); vstd=np.std(vix_c[-20:])
            vz=(cv-vs20)/vstd if vstd>0 else 0.0; vpr=float(np.sum(vix_c<cv))/len(vix_c)
            ss50=np.mean(spy_c[-50:]); ss200=np.mean(spy_c[-200:])
            s5=spy_c[-1]/spy_c[-5]-1; s10=spy_c[-1]/spy_c[-10]-1; s20=spy_c[-1]/spy_c[-20]-1
            svol=np.std(np.diff(spy_c[-21:])/spy_c[-21:-1])
            s60=spy_c[-1]/spy_c[-60]-1; s120=spy_c[-1]/spy_c[-120]-1; s252=spy_c[-1]/spy_c[-252]-1
            vtr=vt5=0.0
            if vix3m_closes is not None and len(vix3m_closes)>=5 and vix3m_closes[-1]>0:
                vtr=cv/vix3m_closes[-1]; vt5=(cv/vix_c[-5])-(vix3m_closes[-1]/vix3m_closes[-5])
            cr=c5=c20=0.0
            if (hyg_closes is not None and lqd_closes is not None
                    and len(hyg_closes)>=MIN_AUX_BARS and len(lqd_closes)>=MIN_AUX_BARS and lqd_closes[-1]>0):
                cr=hyg_closes[-1]/lqd_closes[-1]
                c5=(hyg_closes[-1]/hyg_closes[-5])-(lqd_closes[-1]/lqd_closes[-5])
                c20=(hyg_closes[-1]/hyg_closes[-20])-(lqd_closes[-1]/lqd_closes[-20])
            br=b5=b20=0.0
            if rsp_closes is not None and len(rsp_closes)>=MIN_AUX_BARS and sc>0:
                br=rsp_closes[-1]/sc
                b5=(rsp_closes[-1]/rsp_closes[-5])-(spy_c[-1]/spy_c[-5])
                b20=(rsp_closes[-1]/rsp_closes[-20])-(spy_c[-1]/spy_c[-20])
            cu20=cu60=0.0
            if (ief_closes is not None and shy_closes is not None
                    and len(ief_closes)>=MIN_AUX_BARS and len(shy_closes)>=MIN_AUX_BARS):
                cu20=(ief_closes[-1]/ief_closes[-20])-(shy_closes[-1]/shy_closes[-20])
                cu60=(ief_closes[-1]/ief_closes[-60])-(shy_closes[-1]/shy_closes[-60])
            return [cv,vz,vpr,cv/vs20,cv/vs50,s5,s10,s20,sc/ss50,sc/ss200,
                    svol*np.sqrt(252),s60,s120,s252,vtr,vt5,cr,c5,c20,br,b5,b20,cu20,cu60]
        except Exception as e:
            self.Log(f"GetFeatures error: {e}"); return None

    def TrainModel(self):
        if self.IsWarmingUp: return
        try: self._TrainModelInner()
        except Exception as e: self.Log(f"[S1] TrainModel error: {e}")

    def _TrainModelInner(self):
        vix_c=self._get_cboe_closes(self.vix,4000,MIN_VIX_BARS)
        spy_c=self._get_closes(self.spy_hist,4000)
        if vix_c is None or spy_c is None: self.Log("[S1] TrainModel: missing history"); return
        self.Log(f"[S1] TrainModel SPY {spy_c[0]:.2f}->{spy_c[-1]:.2f} bars={len(spy_c)}")
        vix3m_c=self._get_cboe_closes(self.vix3m,4000,5)
        hyg_c=self._get_closes(self.hyg_hist,4000); lqd_c=self._get_closes(self.lqd_hist,4000)
        rsp_c=self._get_closes(self.rsp,4000) if self.rsp else None
        ief_c=self._get_closes(self.ief_hist,4000); shy_c=self._get_closes(self.shy_hist,4000)
        lc=len(spy_c)-LABEL_HORIZON-SAFETY_BUFFER
        if lc<MIN_SPY_BARS+MIN_TRAIN_ROWS: self.Log("[S1] TrainModel: insufficient data"); return
        te=lc-TRAIN_VAL_GAP
        if te-MIN_SPY_BARS<MIN_TRAIN_ROWS: self.Log("[S1] TrainModel: window too small"); return
        idx=list(range(MIN_SPY_BARS,lc))
        fr=[spy_c[i+LABEL_HORIZON]/spy_c[i]-1 for i in idx]; med=np.median(fr)
        Xa,ya=[],[]
        for ii,i in enumerate(idx):
            ft=self.GetFeatures(vix_c[:i],spy_c[:i],
                vix3m_closes=vix3m_c[:i] if vix3m_c is not None else None,
                hyg_closes=hyg_c[:i] if hyg_c is not None else None,
                lqd_closes=lqd_c[:i] if lqd_c is not None else None,
                rsp_closes=rsp_c[:i] if rsp_c is not None else None,
                ief_closes=ief_c[:i] if ief_c is not None else None,
                shy_closes=shy_c[:i] if shy_c is not None else None)
            if ft is not None: Xa.append(ft); ya.append(1 if fr[ii]>med else 0)
        if len(Xa)<MIN_TRAIN_ROWS+20: self.Log(f"[S1] TrainModel: too few samples ({len(Xa)})"); return
        Xa=np.array(Xa); ya=np.array(ya); r1=float(np.mean(ya))
        if r1>0.95 or r1<0.05: self.Log(f"[S1] TrainModel: degenerate ({r1:.3f})"); self.trained=False; return
        sp=te-MIN_SPY_BARS
        Xtr,ytr=Xa[:sp],ya[:sp]; Xva,yva=Xa[sp:],ya[sp:]
        if len(Xtr)<MIN_TRAIN_ROWS: self.Log("[S1] TrainModel: not enough rows"); return
        self.scaler.fit(Xtr); self.model.fit(self.scaler.transform(Xtr),ytr); self.trained=True
        if len(Xva)>0:
            acc=self.model.score(self.scaler.transform(Xva),yva)
            self.Log(f"[S1] TrainModel acc={acc:.3f} base={np.mean(yva):.3f} edge={acc-np.mean(yva):+.3f}")
        names=["vix_level","vix_zscore","vix_pct_rank","vix_vs_sma20","vix_vs_sma50",
               "spy_5d","spy_10d","spy_20d","spy_vs_sma50","spy_vs_sma200","spy_vol",
               "spy_60d","spy_120d","spy_252d","vix_term_ratio","vix_term_5d",
               "credit_ratio","credit_5d","credit_20d","breadth_ratio","breadth_5d",
               "breadth_20d","curve_20d","curve_60d"]
        top=sorted(zip(names,self.model.feature_importances_),key=lambda x:-x[1])[:5]
        self.Log("[S1] Features: "+" | ".join(f"{n}={v:.3f}" for n,v in top))

    # ── CheckSignal ───────────────────────────────────────────────────────────

    def CheckSignal(self):
        if self.IsWarmingUp: return
        try: self._CheckSignalInner()
        except Exception as e: self.Log(f"[S1] CheckSignal error: {e}")

    def _CheckSignalInner(self):
        spy_c=self._get_closes(self.spy_hist,270)
        vix_c=self._get_closes(self.vix,300,is_custom=True)
        if spy_c is None or vix_c is None: self.Log("[S1] CheckSignal: missing history"); return
        if len(vix_c)<MIN_VIX_BARS or len(spy_c)<MIN_SPY_BARS:
            self.Log(f"[S1] CheckSignal: bars vix={len(vix_c)} spy={len(spy_c)}"); return

        vix3m_c=self._get_closes(self.vix3m,10,is_custom=True)
        hyg_c=self._get_closes(self.hyg_hist,MIN_AUX_BARS)
        lqd_c=self._get_closes(self.lqd_hist,MIN_AUX_BARS)
        rsp_c=self._get_closes(self.rsp,MIN_AUX_BARS) if self.rsp else None
        ief_c=self._get_closes(self.ief_hist,MIN_AUX_BARS)
        shy_c=self._get_closes(self.shy_hist,MIN_AUX_BARS)

        cv=vix_c[-1]; vsma=np.mean(vix_c[-20:]); v80=np.percentile(vix_c,80)
        sc=spy_c[-1]; s50=np.mean(spy_c[-50:]); s200=np.mean(spy_c[-200:])
        r5=spy_c[-1]/spy_c[-5]-1; r10=spy_c[-1]/spy_c[-10]-1; r20=spy_c[-1]/spy_c[-20]-1

        ml=False
        if self.trained:
            ft=self.GetFeatures(vix_c,spy_c,vix3m_closes=vix3m_c,hyg_closes=hyg_c,
                                lqd_closes=lqd_c,rsp_closes=rsp_c,ief_closes=ief_c,shy_closes=shy_c)
            if ft is not None:
                try:
                    p=self.model.predict_proba(self.scaler.transform([ft]))[0]
                    ml=(p[1] if len(p)==2 else 0.5)>ML_THRESHOLD
                except Exception as e: self.Log(f"[S1] ML error: {e}")

        rs=rg=0.0; sa=True
        if cv>v80 and r5<-0.03:
            rs=(DIP_DEEP_SPY_W_ML if ml else DIP_DEEP_SPY_W) if r10<=DIP_DEEP_THRESHOLD else (DIP_SHALLOW_SPY_W_ML if ml else DIP_SHALLOW_SPY_W)
            rg=max(0.0,1.0-rs); sa=False; rn="R1-dip"
        elif cv<13 and sc>s50*1.05:
            rs=0.40; rg=0.20; rn="R2-lowvol"
        elif 20<cv<vsma:
            rs=0.85 if ml else 0.70; rg=0.10; rn="R3-recovery"
        elif cv>vsma*1.2:
            rs=0.30; rg=0.20; sa=False; rn="R4-stress"
        elif sc>s200:
            rs=0.70 if ml else 0.60; rg=0.15; rn="R5-trend"
        else:
            rs=0.30; rg=0.20; sa=False; rn="R6-below200"

        bull=(sc>s200 and sc>s50 and r20>0.0 and cv<v80 and cv<25)
        self._log_gate(sc,s50,s200,r20,cv,v80,bull)

        prev=self._s3_bull_market
        self._s3_bull_market=bull; self._sleeves_active=sa

        if bull and not prev:
            self.Log(f"[SWITCH] S1+S2->S3+S1(80/20) {self.Time:%Y-%m-%d} spy={sc:.2f} 50MA={s50:.2f} 200MA={s200:.2f} 20d={r20:+.2%} VIX={cv:.1f}")
            self._log_state("PRE-SWITCH->S3+S1")
        elif not bull and prev:
            reason=("VIX>25" if cv>=25 else "VIX>80pct" if cv>=v80
                    else "SPY<50MA" if sc<=s50 else "SPY<200MA" if sc<=s200 else "20d<0")
            self.Log(f"[SWITCH] S3+S1->S1+S2 {self.Time:%Y-%m-%d} reason={reason} spy={sc:.2f} VIX={cv:.1f}")
            self._log_state("PRE-SWITCH->S1+S2")

        if bull:
            # Bull mode: S3=80%, S1=20% (BRK.B 15% + NEM 5%), S2=0%
            self.s1_spy_weight    = S1_BULL_BUDGET * S1_BULL_SPY_FRAC   # 0.15 BRK.B
            self.s1_gld_weight    = S1_BULL_BUDGET * S1_BULL_GLD_FRAC   # 0.05 NEM
            self.s2_sleeve_budget = 0.0
            self._log_budgets("S3+S1-BULL")
            self._liquidate_sleeve2()
            self._safe_set_macro()   # buy BRK.B + NEM
            if bull and not prev:
                # Fresh bull entry — always force full rebalance
                self.Log("[S3] CheckSignal: fresh bull entry — forcing full S3 rebalance")
                self.RebalanceSleeve3()
                self._log_state("POST-DEPLOY-S3")
            elif self._sleeve3_is_empty():
                self.Log("[S3] CheckSignal: empty — seeding")
                self.RebalanceSleeve3()
                self._log_state("POST-DEPLOY-S3")
            else:
                # Check if S3 is meaningfully underdeployed (e.g. positions sold
                # externally, lot-size skips, or partial fills on entry).
                # Trigger a rebalance if more than 5% below budget.
                tv = self.Portfolio.TotalPortfolioValue
                s3_actual = (
                    sum(self.Portfolio[s].HoldingsValue / tv
                        for s in self.s3_symbols
                        if s in self.Portfolio and self.Portfolio[s].Invested)
                    if tv > 0 else 0.0
                )
                if s3_actual < S3_BULL_BUDGET - 0.05:
                    self.Log(
                        f"[S3] CheckSignal: underdeployed "
                        f"({s3_actual:.1%} vs {S3_BULL_BUDGET:.0%} target) — rebalancing"
                    )
                    self.RebalanceSleeve3()
                    self._log_state("POST-DEPLOY-S3")
                else:
                    self.Log(f"[S3] CheckSignal: invested ({s3_actual:.1%})")
                    self._log_state("S3 steady")
        else:
            self.s1_spy_weight=rs; self.s1_gld_weight=rg
            self.s2_sleeve_budget=max(0.0,1.0-rs-rg)
            self._log_budgets(f"S1+S2/{rn}")
            # Genuine S3->S1+S2 transition: force-close all S3 positions including
            # dual-listed stocks. Steady-state daily calls use default transition=False.
            if not bull and prev:
                self._liquidate_sleeve3(transition=True)
            else:
                self._liquidate_sleeve3()
            res=any(self.Portfolio[s].Invested for s in self.s3_symbols if s in self.Portfolio)
            self.Log(f"[SWITCH] S3 post-liq residual={res}")
            self._safe_set_macro()
            self.Log(f"[S1] {rn} vix={cv:.1f} v80={v80:.1f} spy_w={rs:.3f} gld_w={rg:.3f} S2={self.s2_sleeve_budget:.3f} ml={ml}")
            if not sa:
                self._liquidate_sleeve2(); self.Log(f"[S2] OFF ({rn})")
            else:
                if self._sleeve2_is_empty():
                    self.Log("[S2] empty — seeding"); self.RebalanceSleeve2()
                    self._log_state("POST-DEPLOY-S2")
                else:
                    self.Log(f"[S2] invested budget={self.s2_sleeve_budget:.3f}")
            if not bull and prev: self._log_state("POST-SWITCH-S3->S1+S2 final")

    # ── Macro helpers ─────────────────────────────────────────────────────────
    def _safe_set_macro(self):
        """S1 hedge: BRK.B (15%) + NEM (5%) in live. SPY/GLD in backtest.
        Skips execution outside market hours to prevent MOO order pile-up."""
        # Same market hours guard as S2/S3 — prevents MOO conversion and
        # cumulative pending order cash reservation issues from IB.
        if not self.Securities[self.spy_hist].Exchange.DateTimeIsOpen(self.Time):
            self.Log("[S1] Market closed — deferring S1 hedge to next session")
            return
        for sym, wt in [(self.spy_hedge, self.s1_spy_weight),
                        (self.gld_hedge, self.s1_gld_weight)]:
            if wt <= 0: continue
            try:
                # 95% buffer: accounts for GBP/USD FX conversion overhead
                buffered_wt = wt * 0.95 if self.LiveMode else wt
                qty = int(self.CalculateOrderQuantity(sym, buffered_wt))
                if qty == 0:
                    self.Log(f"[S1] {sym.Value} qty=0 at wt={wt:.3f} — skipping")
                    continue
                self.MarketOrder(sym, qty)
                self.Log(f"[S1] ORDER {sym.Value}={wt:.3f}(buf={buffered_wt:.3f}) qty={qty:+d} px={self.Securities[sym].Price:.2f}")
            except Exception as e:
                self.Log(f"[S1] {sym.Value} error: {e}")

    def _liquidate_sleeve1(self):
        liq=[]
        for sym in [self.spy_hedge, self.gld_hedge]:
            if sym in self.Portfolio and self.Portfolio[sym].Invested:
                qty = self.Portfolio[sym].Quantity
                if qty != 0:
                    self.MarketOrder(sym, -qty)
                    liq.append(f"{sym.Value}({qty} shares, ${self.Portfolio[sym].HoldingsValue:,.0f})")
        self.Log(f"[S1] LIQ: {' '.join(liq) if liq else 'nothing'}")

    # ── Sleeve 2 ──────────────────────────────────────────────────────────────

    def _sleeve2_is_empty(self):
        return not any(s in self.Portfolio and self.Portfolio[s].Invested for s in self._s2_candidates)

    def _liquidate_sleeve2(self):
        liq=[]
        for sym in list(self._s2_candidates):
            # In bull mode preserve positions that are also S3 candidates —
            # they belong to S3 and must not be swept by the S2 liquidation loop.
            if self._s3_bull_market and sym in self.s3_symbols:
                continue
            if sym in self.Securities and self.Portfolio[sym].Invested:
                liq.append(f"{sym.Value}(${self.Portfolio[sym].HoldingsValue:,.0f})")
                self.Liquidate(sym)
        self.Log(f"[S2] LIQ: {' '.join(liq) if liq else 'nothing'}")

    def RebalanceSleeve2(self):
        if self.IsWarmingUp: return
        # Skip execution outside market hours — daily resolution MarketOrders
        # get converted to MOO by QC, which IB then rejects at the next open.
        if not self.Securities[self.spy_hist].Exchange.DateTimeIsOpen(self.Time):
            self.Log("[S2] Market closed — deferring to next session"); return
        try: self._RebalanceSleeve2Inner()
        except Exception as e: self.Log(f"[S2] error: {e}")

    def _RebalanceSleeve2Inner(self):
        # S2 is OFF in bull mode (S1=20% BRK.B/NEM takes the hedge slot).
        # S2 runs only in S1+S2 mode at cash_sleeve_weight budget.
        if self._s3_bull_market:
            self._liquidate_sleeve2(); self.Log("[S2] BLOCKED — bull mode (S1 hedge active)"); return
        if not self._sleeves_active or not self._s2_candidates:
            self._liquidate_sleeve2()
            self.Log(f"[S2] OFF sa={self._sleeves_active} cand={len(self._s2_candidates)}"); return
        now=self.Time; cands=[]; sk={"ns":0,"np":0,"tn":0,"nr":0,"nm":0}
        for sym in self._s2_candidates:
            if sym not in self.Securities: sk["ns"]+=1; continue
            sec=self.Securities[sym]
            if not sec.HasData or sec.Price<=0 or not sec.IsTradable: sk["np"]+=1; continue
            ad=self._s2_added_date.get(sym)
            if ad and (now-ad).days<self.S2_MIN_HISTORY_DAYS: sk["tn"]+=1; continue
            roc=self._s2_momentum.get(sym)
            if roc is None or not roc.IsReady: sk["nr"]+=1; continue
            if float(roc.Current.Value)<self.S2_MOMENTUM_MIN_RETURN: sk["nm"]+=1; continue
            cands.append((sym,float(roc.Current.Value)))
        self.Log(f"[S2] Filter pool={len(self._s2_candidates)} qual={len(cands)} skip={sk}")
        cands=sorted(cands,key=lambda x:-x[1])[:self.S2_MAX_POSITIONS]
        if not cands: self._liquidate_sleeve2(); self.Log("[S2] No cands — liquidated"); return
        n=len(cands)
        pp=min(self.s2_sleeve_budget/n, self.S2_MAX_POSITION_WEIGHT*self.s2_sleeve_budget)
        self.Log(f"[S2] REBAL n={n} budget={self.s2_sleeve_budget:.3f} per_pos={pp:.3f}")
        self.Log(f"  {'Sym':<8} {'Wt%':>6} {'ROC63':>8}")
        for sym,rv in cands:
            qty = int(self.CalculateOrderQuantity(sym, pp))
            if qty == 0:
                self.Log(f"  {sym.Value:<8} SKIP (qty=0 at pp={pp:.3f})")
                continue
            self.Log(f"  {sym.Value:<8} {pp*100:>5.1f}% {rv*100:>+7.2f}% qty={qty:+d}")
            self.MarketOrder(sym, qty)
        tgt={sym for sym,_ in cands}
        for sym in self._s2_candidates:
            if sym not in tgt and sym in self.Portfolio and self.Portfolio[sym].Invested:
                self.Log(f"[S2] CLOSE stale {sym.Value}"); self.Liquidate(sym)
        eq=self.Portfolio.TotalPortfolioValue
        if eq>0:
            act=sum(self.Portfolio[s].HoldingsValue/eq for s in tgt if s in self.Portfolio and self.Portfolio[s].Invested)
            self.Log(f"[S2] Post-rebal target={self.s2_sleeve_budget:.3f} actual={act:.3f} d={act-self.s2_sleeve_budget:+.3f}")

    # ── Sleeve 3 ──────────────────────────────────────────────────────────────

    def _sleeve3_is_empty(self):
        return not any(s in self.Portfolio and self.Portfolio[s].Invested for s in self.s3_symbols)

    def _liquidate_sleeve3(self, transition=False):
        liq=[]
        for sym in list(self.s3_symbols):
            # Steady-state guard (transition=False): skip symbols that are also S2
            # candidates — they are legitimate S2 positions and S3 has no authority
            # over them while S1+S2 mode is active. Only a genuine S3->S1+S2
            # transition (transition=True) should force-close everything.
            if not transition and sym in self._s2_candidates:
                continue
            if sym in self.Securities and self.Portfolio[sym].Invested:
                liq.append(f"{sym.Value}(${self.Portfolio[sym].HoldingsValue:,.0f})")
                self.Liquidate(sym)
        self.Log(f"[S3] LIQ: {' '.join(liq) if liq else 'nothing'}")

    def RebalanceSleeve3(self):
        if self.IsWarmingUp: return
        # Skip execution outside market hours — daily resolution MarketOrders
        # get converted to MOO by QC, which IB then rejects at the next open.
        if not self.Securities[self.spy_hist].Exchange.DateTimeIsOpen(self.Time):
            self.Log("[S3] Market closed — deferring to next session"); return
        try: self._RebalanceSleeve3Inner()
        except Exception as e: self.Log(f"[S3] error: {e}")

    def _RebalanceSleeve3Inner(self):
        if not self._s3_bull_market:
            self._liquidate_sleeve3(transition=True); self.Log(f"[S3] BLOCKED bull={self._s3_bull_market}"); return
        ds=self.Time.strftime("%Y-%m-%d")
        idxs=list(self.s3_band_idx.values())
        if len(idxs)<50:
            if len(self.s3_symbols)>=50:
                # Symbols subscribed but OnData hasn't populated band indices yet
                # (first deploy after universe loads). Skip breadth, proceed to
                # momentum — breadth will be available on next rebalance.
                self.Log(
                    f"[S3] Band indices not yet populated ({len(idxs)} of "
                    f"{len(self.s3_symbols)}) — skipping breadth, running momentum only"
                )
                bf=0.0; self.s3_allow=True
            else:
                self.Log(f"[S3] Universe too small ({len(idxs)})"); return
        else:
            bf=sum(i in self.s3_BOTTOM_LEVELS for i in idxs)/len(idxs)
        self.s3_max_stress=max(self.s3_max_stress,bf)
        if bf>=0.40:
            if not self.s3_was_risk_off: self.s3_risk_off_date=self.Time; self.Log(f"[S3] RISK-OFF {ds} stress={bf:.1%}")
            self.s3_allow=False; self.s3_was_risk_off=True
        elif self.s3_was_risk_off:
            denom=max(self.s3_max_stress,0.10); imp=(self.s3_max_stress-bf)/denom
            doff=(self.Time-self.s3_risk_off_date).days if self.s3_risk_off_date else 0
            self.Log(f"[S3] RISK-OFF check {ds} stress={bf:.1%} imp={imp:.1%} days={doff}")
            if imp>=0.60 or bf<0.15 or doff>180:
                trig="60pct" if imp>=0.60 else "stress<15" if bf<0.15 else "180d"
                self.Log(f"[S3] RECOVERY {ds} trigger={trig}")
                for s in self.s3_symbols:
                    if s in self.s3_band_hist: self.s3_band_hist[s]=RollingWindow[int](self.s3_hist_len)
                self.s3_allow=True; self.s3_was_risk_off=False
                self.s3_max_stress=0.0; self.s3_risk_off_date=None
            else:
                self.Log(f"[S3] RISK-OFF {ds} stress={bf:.1%} imp={imp:.1%} days={doff}")
        else:
            self.s3_allow=True
        if not self.s3_allow: self._liquidate_sleeve3(); return

        a3=list(self.s3_symbols)
        if not a3: return
        hist=self.History(a3,max(self.s3_lookbacks)+1,Resolution.Daily)
        if hist.empty: self.Log("[S3] History empty"); return
        cl=hist["close"].unstack(0); mom={}; sk={"adx":0,"ema":0,"mc":0,"neg":0,"nh":0,"lot":0}
        tv=self.Portfolio.TotalPortfolioValue
        min_pos_val = (tv * S3_BULL_BUDGET / self.s3_stock_count) if tv > 0 else 0
        for s in a3:
            if s not in cl: sk["nh"]+=1; continue
            px=cl[s]
            if len(px)<max(self.s3_lookbacks)+1: sk["nh"]+=1; continue
            if not self.s3_adx[s].IsReady or self.s3_adx[s].Current.Value>self.s3_adx_limit: sk["adx"]+=1; continue
            adx_val = self.s3_adx[s].Current.Value
            mv=np.mean([px.iloc[-1]/px.iloc[-lb-1]-1 for lb in self.s3_lookbacks])
            if not self.s3_ma[s].IsReady: continue
            if self.Securities[s].Price<=self.s3_ma[s].Current.Value: sk["ema"]+=1; continue
            fn=self.Securities[s].Fundamentals
            if fn is None or fn.MarketCap<5_000_000_000: sk["mc"]+=1; continue
            if self.Securities[s].Price > min_pos_val: sk["lot"]+=1; continue
            if mv>0: mom[s]=mv*adx_val   # stronger trend → higher allocation weight
            else: sk["neg"]+=1
        self.Log(f"[S3] MOMENTUM {ds} uni={len(a3)} qual={len(mom)} skip={sk}")
        if not mom: self.Log("[S3] No momentum — liquidating"); self._liquidate_sleeve3(); return

        top=sorted(mom,key=mom.get,reverse=True)[:self.s3_stock_count]
        sc2={}; sm={}
        for s in top:
            if not self.s3_ma[s].IsReady or not self.s3_stretch_ema[s].IsReady: continue
            dev=np.std(list(self.s3_close_win[s]))
            if dev<=0: continue
            mid=self.s3_ma[s].Current.Value; lm=self.s3_stretch_ema[s].Current.Value
            lm2=lm/2.0; lm3=lm2*0.38196601; lm4=lm*1.38196601; lm5=lm*1.61803399; lm6=(lm+lm2)/2.0
            bands=[mid-dev*lm5,mid-dev*lm4,mid-dev*lm,mid-dev*lm6,mid-dev*lm2,mid-dev*lm3,mid,
                   mid+dev*lm3,mid+dev*lm2,mid+dev*lm6,mid+dev*lm,mid+dev*lm4,mid+dev*lm5]
            px2=self.Securities[s].Price; bi=self._s3_band_index(px2,bands)
            self.s3_band_hist[s].Add(bi)
            hi2=list(self.s3_band_hist[s]); hh=max(hi2) if hi2 else bi
            scale=1.0 if hh<=0 else (0.0 if bi>=hh else max(0.15,1.0-bi/hh))
            ex=False
            if self.s3_stretch_win[s].IsReady:
                sl=list(self.s3_stretch_win[s]); cs=sl[0]; ps=max(sl)
                if bi>=10 and ps>0 and cs<ps*0.80:
                    scale=min(scale,0.15); ex=True
                    self.Log(f"[S3] EXHAUST {s.Value} band={bi} str={cs:.2f}/pk={ps:.2f} sc->{scale:.2f}")
            sc2[s]=mom[s]*scale; sm[s]=(scale,bi,ex)
        if not sc2: self.Log("[S3] Band sizing zero"); self._liquidate_sleeve3(); return

        ts=sum(sc2.values()); rw={s:v/ts for s,v in sc2.items()}
        cw={s:min(0.20,w) for s,w in rw.items()}; cs=sum(cw.values())
        sw={s:(w/cs)*S3_BULL_BUDGET for s,w in cw.items()} if cs>0 else {}

        eq=self.Portfolio.TotalPortfolioValue
        self.Log(f"[S3] REBAL {ds} Eq={eq:,.0f} stress={bf:.1%} pos={len(sw)} budget={S3_BULL_BUDGET:.0%}")
        self.Log(f"  {'Sym':<8} {'Wt%':>6} {'Mom%':>7} {'Scale':>6} {'Band':>4}")
        for s,w in sorted(sw.items(),key=lambda x:-x[1]):
            sc3,bi2,ex2=sm.get(s,(1.0,0,False))
            self.Log(f"  {s.Value:<8} {w*100:>5.1f}% {mom[s]*100:>+6.2f}% {sc3:>6.3f} {bi2:>4}{'EXHAUST' if ex2 else ''}")

        tv=self.Portfolio.TotalPortfolioValue
        if tv<=0: return
        cw2={kvp.Key:kvp.Value.HoldingsValue/tv for kvp in self.Portfolio
             if kvp.Value.Invested and kvp.Key in self.s3_symbols}
        trades=[]
        # Build full trade list then sort: sells first (-qty) so cash is
        # freed before buys execute, avoiding insufficient settled cash rejections.
        trade_list=[]
        for s in set(list(cw2)+list(sw)):
            tg=sw.get(s,0.0); cu=cw2.get(s,0.0); dl=tg-cu
            if abs(dl)<=self.s3_rebal_threshold: continue
            qty=int(self.CalculateOrderQuantity(s, tg))
            if qty==0: continue
            rn2="BUY" if cu==0 and tg>0 else "CLOSE" if tg==0 else "ADD" if dl>0 else "TRIM"
            px3=self.Securities[s].Price if s in self.Securities else 0
            trade_list.append((s, qty, rn2, px3, cu, tg, dl))
        # Sells (negative qty) first, then buys
        trade_list.sort(key=lambda x: x[1])
        for s, qty, rn2, px3, cu, tg, dl in trade_list:
            self.Log(f"[S3] {rn2} {s.Value} {cu*100:.1f}%->{tg*100:.1f}% (d{dl*100:+.1f}%) px={px3:.2f} qty={qty:+d}")
            self.MarketOrder(s, qty); trades.append(s.Value)
        if not trades: self.Log(f"[S3] No trades needed {ds}")

        eq=self.Portfolio.TotalPortfolioValue
        if eq>0:
            s3a=sum(self.Portfolio[s].HoldingsValue/eq for s in sw if s in self.Portfolio and self.Portfolio[s].Invested)
            s1a=sum(self.Portfolio[sym].HoldingsValue/eq for sym in [self.spy_hedge,self.gld_hedge]
                    if sym in self.Portfolio and self.Portfolio[sym].Invested)
            expected_s1=self.s1_spy_weight+self.s1_gld_weight
            self.Log(f"[S3] Post-rebal S3={s3a:.1%} S1={s1a:.1%}(exp={expected_s1:.1%}) cash={self.Portfolio.Cash/eq:.1%}")
            if abs(s1a-expected_s1)>0.05:
                self.Log(f"[S3] WARNING S1 drift={s1a-expected_s1:+.1%} — reapplying macro")
                self._safe_set_macro()

    # ── Warmup / End ──────────────────────────────────────────────────────────

    def OnWarmupFinished(self):
        self.Log("[INIT] Warmup complete")
        self._log_state("PRE-INIT")

        # Guard: if universe hasn't populated yet defer to first CheckSignal
        if len(self.s3_symbols) == 0 and len(self._s2_candidates) == 0:
            self.Log("[INIT] Universe not yet populated — deferring to first CheckSignal")
            self._initial_deploy_done = True
            return

        # Log existing positions — do NOT liquidate anything here.
        # OnWarmupFinished fires before the universe is fully stable, so
        # valid S3 positions (e.g. recently bought) may appear unrecognised.
        # Let CheckSignal and the rebalances handle all position management.
        if self.Portfolio.TotalHoldingsValue != 0:
            macro = {self.spy_hedge, self.gld_hedge}
            managed = macro | self._s2_candidates | self.s3_symbols
            kept, unknown = [], []
            for kvp in self.Portfolio:
                if not kvp.Value.Invested: continue
                if kvp.Key in managed: kept.append(kvp.Key.Value)
                else: unknown.append(kvp.Key.Value)
            if kept:    self.Log(f"[INIT] Existing managed positions: {kept}")
            if unknown: self.Log(f"[INIT] Existing unclassified positions (keeping): {unknown}")

        self._initial_deploy_done = True
        self.CheckSignal()
        self._log_state("POST-INIT")

    def OnEndOfAlgorithm(self):
        eq=self.Portfolio.TotalPortfolioValue
        self.Log(f"[END] Eq={eq:,.2f} Ret={(eq/100_000-1)*100:+.2f}%")
        self._log_state("END"); self._log_budgets("END")


class CBOE(PythonData):
    def GetSource(self, config, date, isLive):
        urls={"VIX":"https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv",
              "VIX3M":"https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX3M_History.csv"}
        return SubscriptionDataSource(urls.get(config.Symbol.Value,urls["VIX"]),SubscriptionTransportMedium.RemoteFile)

    def Reader(self, config, line, date, isLive):
        if not (line.strip() and line[0].isdigit()): return None
        cols=line.split(',')
        try:
            obj=CBOE(); obj.Symbol=config.Symbol
            obj.Time=datetime.strptime(cols[0],"%m/%d/%Y"); obj.Value=float(cols[4])
            obj["close"]=float(cols[4]); obj["open"]=float(cols[1])
            obj["high"]=float(cols[2]);  obj["low"]=float(cols[3])
            return obj
        except: return None
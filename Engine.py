# -*- coding: utf-8 -*-
"""Engine — the conductor of the five-brain EUR/USD trading system.

The system has five brains, each independently testable:

    * **NewsMind**     — events, headlines, narratives → NewsContext
    * **MarketMind**   — DXY / RORO / yields / cross-asset macro
                         → MarketContext
    * **ChartMind**    — technical analysis (PA, ICT, candles, SMC)
                         → Analysis (with TradePlan and PositionHealth)
    * **GateMind**     — composes the three brain grades, checks
                         kill-switches, sizes the trade, routes to
                         broker, ledgers everything → CycleResult
    * **SmartNoteBook** — institutional memory: journal of every
                         closed trade, post/pre-mortem, pattern mining,
                         bias detection, lesson distillation, daily
                         briefing, and per-brain memory injection.

Engine wires them together with two precedence rules:

    1. **Halt-first**: if NewsMind says blackout, or MarketMind says
       halt, or GateMind's kill-switch fires, no trade is taken — even
       if ChartMind has the cleanest setup of the year. (Schwager's
       Market Wizards canon: refusing trades is the edge.)

    2. **Memory-first**: SmartNoteBook is consulted *before* the
       brains form their grades, not after. Each brain receives an
       injection block — committed lessons, recent bias flags,
       psychological warnings, optional pre-mortem — which the brain
       is expected to attend to in its prompt template. The journal's
       knowledge thereby compounds (Steenbarger's *Daily Trading
       Coach*: yesterday's evidence becomes today's discipline).

Lifecycle
---------
    Engine(config) -> instantiates all 5 brains and shared stores

    For each bar:
        decision = engine.step(bar, bundle=..., now_utc=...)
            1. SmartNoteBook.briefing()  (cached, ~10 min TTL)
            2. NewsContext = NewsMind.context_now(now)
            3. MarketContext = MarketMind.analyze(bundle)
            4. halt-check: news / market
            5. inject SmartNoteBook context into each brain's prompt
               (returned in decision.injection_blocks for the LLM
               wrappers to splice in)
            6. ChartAnalysis = ChartMind.analyze(bar)
            7. brain_grades = build_brain_grades(news, market, chart)
            8. GateMindContext built from grades + plan + market state
            9. CycleResult = GateMind.cycle(gctx)  [routes to broker]
           10. if a position opened: pre-mortem run and stashed for
               post-close ingestion

    On close (called by the monitor or broker fill loop):
        engine.record_close(trade_record)
            -> SmartNoteBook.record_trade(record)  [post-mortem +
                                                    journal append]

Thread safety
-------------
Engine is not thread-safe by itself. Callers should serialise step()
calls per bar; if you must run concurrently across pairs, instantiate
one Engine per pair (the SmartNoteBook journal supports concurrent
writes, but the GateMind ledger and portfolio do not).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# ----------------------------------------------------------------------
# Lazy imports — keep Engine import-cheap so callers can probe it
# without paying the full brain stack startup cost.
# ----------------------------------------------------------------------
def _lazy_chartmind():
    try:
        from ChartMind import ChartMind as _CM
        return _CM
    except ImportError:
        return None


def _lazy_marketmind():
    try:
        from MarketMind import MarketMind as _MM
        return _MM
    except ImportError:
        return None


def _lazy_newsmind():
    try:
        from NewsMind import NewsMind as _NM
        return _NM
    except ImportError:
        return None


def _lazy_gatemind():
    try:
        from GateMind import (
            GateMind as _GM,
            GateMindContext, BrainGrade,
            Portfolio, Ledger, PaperBroker,
        )
        return _GM, GateMindContext, BrainGrade, Portfolio, Ledger, PaperBroker
    except ImportError:
        return None


def _lazy_smartnotebook():
    try:
        from SmartNoteBook import (
            SmartNoteBook as _SNB,
            SmartNoteBookConfig,
            PreMortemContext,
        )
        return _SNB, SmartNoteBookConfig, PreMortemContext
    except ImportError:
        return None


def _lazy_llm_core():
    try:
        from LLMCore import (
            LLMClient, LLMConfig, GLOBAL_COST_TRACKER,
            run_brains_parallel,
        )
        return LLMClient, LLMConfig, GLOBAL_COST_TRACKER, run_brains_parallel
    except ImportError:
        return None


def _lazy_brain_llm_modules():
    """Import the per-brain LLM wrappers; return None if any are missing
    (e.g. when LLMCore itself isn't installed).
    """
    try:
        from ChartMind import chartmind_llm
        from MarketMind import marketmind_llm
        from NewsMind import newsmind_llm
        from GateMind import gatemind_llm
        from SmartNoteBook import llm_grader
        return {
            "chartmind": chartmind_llm,
            "marketmind": marketmind_llm,
            "newsmind":   newsmind_llm,
            "gatemind":   gatemind_llm,
            "grader":     llm_grader,
        }
    except ImportError:
        return None


# ----------------------------------------------------------------------
# Engine config.
# ----------------------------------------------------------------------
@dataclass
class EngineConfig:
    """All knobs the orchestrator exposes. Defaults are production-safe.

    Sub-brain configurations remain owned by the brains themselves;
    Engine only configures the wiring.
    """
    pair: str = "EUR/USD"
    state_dir: str = "/app/NewsMind/state"

    # Brain enables (turn off any brain and Engine falls back to the
    # remaining ones — useful for staged rollouts).
    enable_news: bool = True
    enable_market: bool = True
    enable_chart: bool = True
    enable_gate: bool = True
    enable_notebook: bool = True

    # GateMind plumbing — broker is paper unless the caller swaps it.
    use_paper_broker: bool = True
    paper_broker_starting_cash: float = 10_000.0

    # Brain-grade thresholds (confidence -> letter grade). Tunable so
    # different brains can use different scales without code changes.
    grade_a_plus_threshold: float = 0.80
    grade_a_threshold: float = 0.65
    grade_b_threshold: float = 0.50
    grade_c_threshold: float = 0.35

    # Memory-injection language for the LLM wrappers ("en" | "ar").
    injection_language: str = "en"

    # ---- LLM integration ------------------------------------------
    # When enable_llm is True and OPENAI_API_KEY is set in the env,
    # each brain runs through its `_llm.py` wrapper after its mechanical
    # analysis: the LLM verdict can downgrade confidence or veto the
    # trade, and a senior-reviewer LLM sits above the gate pass. After
    # a trade closes, an LLM grader rewrites the post-mortem narrative.
    # All LLM calls fall back gracefully to the mechanical answer if
    # the API is unreachable or returns malformed JSON.
    enable_llm: bool = False
    llm_model: str = "gpt-5"
    llm_temperature: float = 0.2
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 2
    llm_parallel_brains: bool = True   # fan out brain calls concurrently
    llm_grade_on_close: bool = True    # run grader after record_close
    llm_gate_review: bool = True       # run senior-reviewer over gate pass


# ----------------------------------------------------------------------
# Engine output.
# ----------------------------------------------------------------------
@dataclass
class Decision:
    """One Engine.step() result.

    `action` is the single-word verdict the caller acts on:
        "long" | "short" | "skip"

    All sub-results are exposed for logging / debugging; the caller
    does not need to consume them to act.
    """
    timestamp: datetime
    action: str
    reason: str

    # Sub-brain outputs (any may be None if the brain is disabled or
    # an upstream halt fired before this brain ran).
    news_context: Any = None
    market_context: Any = None
    chart_analysis: Any = None
    gate_result: Any = None        # GateMind.CycleResult
    pre_mortem: Any = None         # SmartNoteBook.PreMortemReport
    briefing: Any = None           # SmartNoteBook.DailyBriefing
    injection_blocks: dict = field(default_factory=dict)
    halt_sources: list = field(default_factory=list)

    # LLM-augmented verdicts (populated only when enable_llm=True and
    # OPENAI_API_KEY is set). Each is the per-brain LLM wrapper's
    # output dataclass; see ChartMind.chartmind_llm.LLMVerdict etc.
    chart_llm: Any = None
    market_llm: Any = None
    news_llm: Any = None
    gate_review_llm: Any = None
    llm_error: str = ""

    @classmethod
    def skip(cls, reason: str, *,
             halt_sources: Optional[list] = None,
             timestamp: Optional[datetime] = None,
             **rest) -> "Decision":
        return cls(
            timestamp=timestamp or datetime.now(timezone.utc),
            action="skip",
            reason=reason,
            halt_sources=halt_sources or [],
            **rest,
        )


# ----------------------------------------------------------------------
# The orchestrator.
# ----------------------------------------------------------------------
class Engine:
    """Composes the five brains into one coherent decision per bar.

    Construct once at process start; reuse across every poll cycle.
    """

    def __init__(self,
                 config: Optional[EngineConfig] = None,
                 *,
                 chartmind_kwargs: Optional[dict] = None,
                 newsmind_config_dir: Optional[Path] = None,
                 broker: Any = None,
                 ):
        self.config = config or EngineConfig()
        Path(self.config.state_dir).mkdir(parents=True, exist_ok=True)

        # ---- ChartMind ---------------------------------------------
        self.cm = None
        if self.config.enable_chart:
            CM = _lazy_chartmind()
            if CM is not None:
                self.cm = CM(**(chartmind_kwargs or {}))

        # ---- MarketMind --------------------------------------------
        self.mm = None
        if self.config.enable_market:
            MM = _lazy_marketmind()
            if MM is not None:
                self.mm = MM()

        # ---- NewsMind ----------------------------------------------
        self.nm = None
        if self.config.enable_news:
            NM = _lazy_newsmind()
            if NM is not None:
                self.nm = NM(config_dir=newsmind_config_dir)

        # ---- SmartNoteBook (initialised before GateMind so the gate
        # can be enriched with memory at decision time) -------------
        self.snb = None
        self.PreMortemContext = None
        if self.config.enable_notebook:
            snb_lazy = _lazy_smartnotebook()
            if snb_lazy is not None:
                SNB, SNBCfg, PMCtx = snb_lazy
                self.snb = SNB(SNBCfg(
                    state_dir=str(Path(self.config.state_dir) / "notebook"),
                    pair=self.config.pair,
                    injection_language=self.config.injection_language,
                ))
                self.PreMortemContext = PMCtx

        # ---- GateMind ----------------------------------------------
        self.gm = None
        self.BrainGrade = None
        self.GateMindContext = None
        if self.config.enable_gate:
            gm_lazy = _lazy_gatemind()
            if gm_lazy is not None:
                GM, GMCtx, BG, Portfolio, Ledger, PaperBroker = gm_lazy
                self.BrainGrade = BG
                self.GateMindContext = GMCtx
                gate_state = Path(self.config.state_dir) / "gate"
                gate_state.mkdir(parents=True, exist_ok=True)
                portfolio = Portfolio(
                    starting_equity=self.config.paper_broker_starting_cash,
                    path=str(gate_state / "portfolio.json"),
                )
                ledger = Ledger(directory=str(gate_state / "ledger"))
                br = broker if broker is not None else (
                    PaperBroker(equity=self.config.paper_broker_starting_cash)
                    if self.config.use_paper_broker else None
                )
                if br is not None:
                    self.gm = GM(
                        broker=br, portfolio=portfolio, ledger=ledger,
                    )

        # Cache of pre-mortems keyed by broker_order_id for later
        # post-mortem ingestion when the trade closes.
        self._pending_pre_mortems: dict[str, Any] = {}

        # ---- LLM (optional) ----------------------------------------
        self._llm_client = None
        self._llm_cfg = None
        self._llm_cost = None
        self._llm_run_parallel = None
        self._llm_brains = None
        if self.config.enable_llm:
            llm_lazy = _lazy_llm_core()
            brain_lazy = _lazy_brain_llm_modules()
            if llm_lazy is not None and brain_lazy is not None:
                LLMClient, LLMConfig, GLOBAL_COST, run_par = llm_lazy
                try:
                    self._llm_client = LLMClient()
                    self._llm_cfg = LLMConfig(
                        model=self.config.llm_model,
                        temperature=self.config.llm_temperature,
                        timeout_seconds=self.config.llm_timeout_seconds,
                        max_retries=self.config.llm_max_retries,
                    )
                    self._llm_cost = GLOBAL_COST
                    self._llm_run_parallel = run_par
                    self._llm_brains = brain_lazy
                except Exception:
                    # Missing API key, missing SDK, etc. — continue
                    # without LLM augmentation.
                    self._llm_client = None

    # ==================================================================
    # Per-bar entry point.
    # ==================================================================
    def step(self,
             bar: Any = None,
             bundle: Any = None,
             *,
             now_utc: Optional[datetime] = None) -> Decision:
        """One full decision cycle.

        Parameters
        ----------
        bar : ChartMind bar object (the latest M15 candle, typically a
            pandas Series or DataFrame). Optional — ChartMind is only
            invoked if a bar is supplied.
        bundle : MarketDataBundle for MarketMind. Optional — MarketMind
            is only invoked when a bundle is provided.
        now_utc : Override "now" (testing). Defaults to current UTC.

        Returns
        -------
        Decision with `action` in {"long", "short", "skip"} and all
        intermediate brain outputs attached for logging.
        """
        now = now_utc or datetime.now(timezone.utc)

        # ---- 1. SmartNoteBook briefing (cached) --------------------
        briefing = None
        injection_blocks: dict = {}
        if self.snb is not None:
            try:
                # Pull minutes-to-news from NewsMind if available
                # (cheap call — context_now returns cached if recent).
                mtn = float("inf")
                if self.nm is not None:
                    try:
                        nctx_preview = self.nm.context_now(now)
                        nxt = getattr(nctx_preview, "next_event", None)
                        if nxt is not None:
                            t_to = (nxt.scheduled_at - now).total_seconds() / 60.0
                            if t_to > 0:
                                mtn = t_to
                    except Exception:
                        pass
                briefing = self.snb.briefing(
                    minutes_to_next_high_impact_news=mtn,
                )
                injection_blocks = self.snb.injection_for_all()
            except Exception:
                briefing = None
                injection_blocks = {}

        # ---- 2. NewsMind -------------------------------------------
        news_ctx = None
        if self.nm is not None:
            try:
                news_ctx = self.nm.context_now(now)
            except Exception:
                news_ctx = None

        # ---- 3. MarketMind -----------------------------------------
        market_ctx = None
        if self.mm is not None and bundle is not None:
            try:
                market_ctx = self.mm.analyze(bundle)
            except Exception:
                market_ctx = None

        # ---- 4. Halt-first check (news, market) --------------------
        halt_sources: list[str] = []
        halt_reason: Optional[str] = None
        if news_ctx is not None:
            news_halt = (
                getattr(news_ctx, "do_not_trade", False)
                or getattr(getattr(news_ctx, "window_state", None),
                           "trading_halted", False)
            )
            if news_halt:
                halt_sources.append("news")
                halt_reason = (
                    getattr(news_ctx, "do_not_trade_reason", "")
                    or getattr(getattr(news_ctx, "window_state", None),
                               "window_reason", "")
                    or "news halt"
                )
        if market_ctx is not None and getattr(market_ctx, "halt_trading", False):
            halt_sources.append("market")
            if halt_reason is None:
                halt_reason = getattr(market_ctx, "halt_reason", "market halt")

        if halt_sources:
            return Decision.skip(
                reason=halt_reason or "halt",
                halt_sources=halt_sources,
                timestamp=now,
                news_context=news_ctx,
                market_context=market_ctx,
                briefing=briefing,
                injection_blocks=injection_blocks,
            )

        # ---- 5. ChartMind ------------------------------------------
        chart_analysis = None
        if self.cm is not None and bar is not None:
            try:
                chart_analysis = self.cm.analyze(bar)
            except (TypeError, AttributeError, ValueError):
                chart_analysis = None

        # Splice market + news factors into chart analysis, as the
        # legacy Engine did, so confluence_factors / conflicts /
        # devils_advocate stay populated for logging downstream.
        if chart_analysis is not None and market_ctx is not None:
            self._inject_market(chart_analysis, market_ctx)
        if chart_analysis is not None and news_ctx is not None:
            self._inject_news(chart_analysis, news_ctx, market_ctx)

        # ---- 6. Build BrainGrades for the gate ---------------------
        gate_result = None
        pre_mortem = None
        if (self.gm is not None
                and chart_analysis is not None
                and getattr(chart_analysis, "actionable", False)
                and self.BrainGrade is not None
                and self.GateMindContext is not None):
            grades = self._build_brain_grades(
                news_ctx=news_ctx,
                market_ctx=market_ctx,
                chart_analysis=chart_analysis,
                now=now,
            )
            plan = chart_analysis.plan

            # ---- 6b. Pre-mortem before submission ------------------
            if self.snb is not None and self.PreMortemContext is not None:
                try:
                    pre_mortem = self.snb.run_pre_mortem(
                        self.PreMortemContext(
                            pair=self.config.pair,
                            direction=plan.direction,
                            setup_type=plan.setup_type,
                            market_regime=_extract_regime(market_ctx),
                            news_state=_extract_news_state(news_ctx),
                            minutes_to_next_high_impact_news=_minutes_to_next_event(
                                news_ctx, now,
                            ),
                            spread_percentile_rank=_extract_spread_pct(market_ctx, news_ctx),
                            rr_planned=getattr(plan, "rr_ratio", 0.0),
                            plan_confidence=getattr(plan, "confidence", 0.0),
                            gate_combined_confidence=_avg_confidence(grades),
                            recent_drawdown_r=getattr(briefing, "current_drawdown_r", 0.0)
                                if briefing else 0.0,
                            recent_consecutive_losses=getattr(briefing, "consecutive_losses", 0)
                                if briefing else 0,
                            last_trade_was_loss=(
                                getattr(briefing, "consecutive_losses", 0) > 0
                                if briefing else False
                            ),
                        ),
                    )
                except Exception:
                    pre_mortem = None

            # ---- 6c. Build GateMindContext + run cycle -------------
            try:
                gctx = self.GateMindContext(
                    pair=self.config.pair,
                    grades=grades,
                    plan=plan,
                    current_price=_extract_current_price(bar, plan),
                    current_spread_pips=_extract_spread_pips(market_ctx, news_ctx),
                    spread_percentile_rank=_extract_spread_pct(market_ctx, news_ctx),
                    upcoming_news_events=_extract_upcoming_events(news_ctx),
                )
                gate_result = self.gm.cycle(gctx)

                # If a position opened, stash the pre-mortem keyed by
                # the broker_order_id so record_close() can pair them.
                if (gate_result is not None
                        and getattr(gate_result, "position", None) is not None
                        and pre_mortem is not None):
                    boid = getattr(gate_result.position, "broker_order_id", None)
                    if boid:
                        self._pending_pre_mortems[boid] = pre_mortem
            except Exception as e:
                gate_result = None

        # ---- 7. Compose final action -------------------------------
        action = self._derive_action(
            gate_result=gate_result,
            chart_analysis=chart_analysis,
            news_ctx=news_ctx,
        )

        decision = Decision(
            timestamp=now,
            action=action,
            reason="composed decision",
            news_context=news_ctx,
            market_context=market_ctx,
            chart_analysis=chart_analysis,
            gate_result=gate_result,
            pre_mortem=pre_mortem,
            briefing=briefing,
            injection_blocks=injection_blocks,
        )

        # ---- 8. LLM augmentation (optional) -----------------------
        if self._llm_client is not None:
            try:
                self._augment_with_llm(decision, now=now)
                # If LLM downgrades to skip / reject, propagate to action.
                if decision.action != "skip":
                    review = decision.gate_review_llm
                    if review is not None and getattr(
                        review, "final_action", "approve_as_is"
                    ) == "reject":
                        decision.action = "skip"
                        decision.reason = (
                            f"llm_review_reject: {review.rationale[:200]}"
                        )
            except Exception as e:
                # LLM failures never block trades — they only ever
                # downgrade. Silent fallback to mechanical action.
                decision.llm_error = f"{type(e).__name__}: {e}"

        return decision

    # ==================================================================
    # Closed-trade ingestion → SmartNoteBook.
    # ==================================================================
    def record_close(self, trade_record: Any) -> Any:
        """Hand a closed trade to SmartNoteBook for the post-mortem.

        `trade_record` should be a `SmartNoteBook.TradeRecord`. This
        method mainly exists so callers (the live monitor, the broker
        fill listener) can keep one Engine-shaped API instead of
        importing SmartNoteBook directly.

        Returns the PostMortemReport, or None if SmartNoteBook is
        disabled.
        """
        if self.snb is None:
            return None

        # If we have a stashed pre-mortem for this broker order, attach
        # it to the record before ingestion so calibration works.
        boid = getattr(trade_record, "broker_order_id", "")
        if boid in self._pending_pre_mortems:
            pm = self._pending_pre_mortems.pop(boid)
            if not getattr(trade_record, "pre_mortem_top_risk", ""):
                trade_record.pre_mortem_top_risk = pm.top_failure_mode
            if not getattr(trade_record, "pre_mortem_predicted_outcome", ""):
                trade_record.pre_mortem_predicted_outcome = pm.predicted_outcome

        post_mortem = self.snb.record_trade(trade_record)

        # ---- LLM grader (optional) --------------------------------
        if (post_mortem is not None
                and self.config.enable_llm
                and self.config.llm_grade_on_close
                and self._llm_client is not None
                and self._llm_brains is not None
                and "grader" in self._llm_brains):
            try:
                grader = self._llm_brains["grader"]
                review = grader.grade(
                    trade_record=trade_record,
                    post_mortem=post_mortem,
                    client=self._llm_client,
                    cfg=self._llm_cfg,
                )
                if review is not None and review.ok:
                    self.snb.attach_llm_review(
                        trade_record.trade_id,
                        decision_quality_grade=review.decision_quality_grade,
                        outcome_quality_grade=review.outcome_quality_grade,
                        what_went_right=review.what_went_right,
                        what_went_wrong=review.what_went_wrong,
                        what_id_change=review.what_id_change,
                        one_sentence_lesson=review.one_sentence_lesson,
                        tags=review.tags,
                    )
            except Exception:
                # Grader failures must never block ingestion; the
                # mechanical skeleton already lives on disk.
                pass

        return post_mortem

    # ==================================================================
    # Read-side conveniences.
    # ==================================================================
    def version_summary(self) -> dict:
        """Per-brain version map. Useful for /healthz endpoints."""
        out: dict[str, str] = {}
        for mod_name in ("ChartMind", "MarketMind", "NewsMind",
                         "GateMind", "SmartNoteBook"):
            try:
                mod = __import__(mod_name)
                out[mod_name.lower()] = getattr(mod, "__version__", "unknown")
            except ImportError:
                out[mod_name.lower()] = "not_installed"
        return out

    def briefing_console_string(self) -> str:
        """Console-friendly daily briefing — for the morning log line."""
        if self.snb is None:
            return "(SmartNoteBook disabled)"
        try:
            return self.snb.briefing().to_console_string()
        except Exception as e:
            return f"(briefing error: {e})"

    # ==================================================================
    # LLM augmentation.
    # ==================================================================
    def _augment_with_llm(self, decision: "Decision", *, now: datetime) -> None:
        """Run each brain's LLM wrapper in parallel and stash verdicts.

        Mutates `decision` in place: chart_llm / market_llm / news_llm /
        gate_review_llm get populated. The action itself is only
        downgraded by the senior-reviewer LLM when it returns
        `final_action="reject"`. Per-brain LLM verdicts that downgrade
        confidence are surfaced via the verdict dataclasses for the
        ledger but do not retroactively change the gate's pass.
        """
        brains = self._llm_brains
        if not brains:
            return

        client = self._llm_client
        cfg = self._llm_cfg
        ib = decision.injection_blocks or {}

        # ---- per-brain calls (parallel) ---------------------------
        callables: dict[str, Any] = {}

        if (decision.chart_analysis is not None
                and "chartmind" in brains):
            chart_inj = ib.get("chartmind")
            chart_inj_text = chart_inj.text if chart_inj is not None else ""
            news_summary = self._extract_summary(decision.news_context)
            market_summary = self._extract_summary(decision.market_context)
            pm_summary = self._pre_mortem_summary(decision.pre_mortem)
            callables["chartmind"] = lambda: brains["chartmind"].think(
                mechanical_analysis=decision.chart_analysis,
                injection_block_text=chart_inj_text,
                news_summary=news_summary,
                market_summary=market_summary,
                pre_mortem_summary=pm_summary,
                client=client, cfg=cfg, now=now,
            )

        if (decision.market_context is not None
                and "marketmind" in brains):
            mm_inj = ib.get("marketmind")
            mm_inj_text = mm_inj.text if mm_inj is not None else ""
            news_summary = self._extract_summary(decision.news_context)
            callables["marketmind"] = lambda: brains["marketmind"].think(
                mechanical_market_context=decision.market_context,
                injection_block_text=mm_inj_text,
                news_summary=news_summary,
                client=client, cfg=cfg, now=now,
            )

        if (decision.news_context is not None
                and "newsmind" in brains):
            nm_inj = ib.get("newsmind")
            nm_inj_text = nm_inj.text if nm_inj is not None else ""
            market_summary = self._extract_summary(decision.market_context)
            callables["newsmind"] = lambda: brains["newsmind"].think(
                mechanical_news_context=decision.news_context,
                injection_block_text=nm_inj_text,
                market_summary=market_summary,
                client=client, cfg=cfg, now=now,
            )

        if not callables:
            return

        if self.config.llm_parallel_brains and self._llm_run_parallel:
            results = self._llm_run_parallel(callables, max_workers=4)
        else:
            results = {name: fn() for name, fn in callables.items()}

        # Attach (Exception instances mean that brain's LLM failed —
        # we leave the field None and continue).
        for name, val in results.items():
            if isinstance(val, Exception):
                continue
            if name == "chartmind":
                decision.chart_llm = val
            elif name == "marketmind":
                decision.market_llm = val
            elif name == "newsmind":
                decision.news_llm = val

        # ---- senior-reviewer over the gate (sequential) ----------
        if (self.config.llm_gate_review
                and decision.gate_result is not None
                and "gatemind" in brains):
            gate = getattr(decision.gate_result, "gate", None)
            if gate is not None and getattr(gate, "pass_", False):
                kill = getattr(decision.gate_result, "kill_switch", None)
                grades = getattr(gate, "grades", None) or []
                # `grades` lives inside the gate decision; if not, fall
                # back to building a small list from the LLM verdicts.
                plan = (decision.chart_analysis.plan
                        if decision.chart_analysis is not None else None)
                sized = getattr(decision.gate_result, "sized", None)
                gm_inj = ib.get("gatemind")
                gm_inj_text = gm_inj.text if gm_inj is not None else ""
                briefing_summary = self._briefing_summary(decision.briefing)
                try:
                    decision.gate_review_llm = brains["gatemind"].think(
                        gate_decision=gate, kill_verdict=kill,
                        brain_grades=grades, plan=plan, sized_trade=sized,
                        briefing_summary=briefing_summary,
                        injection_block_text=gm_inj_text,
                        client=client, cfg=cfg, now=now,
                    )
                except Exception as e:
                    decision.llm_error = (
                        decision.llm_error
                        + f" | gate_review: {type(e).__name__}: {e}"
                    ).strip(" |")

    @staticmethod
    def _extract_summary(ctx) -> str:
        if ctx is None:
            return ""
        return (
            getattr(ctx, "summary_one_liner", "")
            or getattr(ctx, "narrative", "")[:300]
        )

    @staticmethod
    def _pre_mortem_summary(pm) -> str:
        if pm is None:
            return ""
        return (
            f"top_failure={getattr(pm, 'top_failure_mode', '')}; "
            f"predicted={getattr(pm, 'predicted_outcome', '')}; "
            f"warnings={list(getattr(pm, 'warnings_for_brain', []) or [])[:3]}"
        )

    @staticmethod
    def _briefing_summary(b) -> dict:
        if b is None:
            return {}
        return {
            "headline": getattr(b, "one_line_headline", ""),
            "current_drawdown_r": getattr(b, "current_drawdown_r", 0.0),
            "consecutive_losses": getattr(b, "consecutive_losses", 0),
            "consecutive_wins": getattr(b, "consecutive_wins", 0),
            "n_trades_lookback": getattr(b, "n_trades_lookback", 0),
            "psychological_warnings": list(
                getattr(b, "psychological_warnings", []) or []
            )[:5],
            "n_active_lessons": len(getattr(b, "active_lessons", []) or []),
            "n_bias_flags": len(getattr(b, "bias_flags", []) or []),
        }

    # ==================================================================
    # Internals.
    # ==================================================================
    def _inject_market(self, chart_analysis, market_ctx) -> None:
        try:
            from MarketMind.integration import (
                make_market_factor, make_market_conflict, make_market_challenge,
            )
        except ImportError:
            return
        f = make_market_factor(market_ctx)
        if f is not None and hasattr(chart_analysis, "confluence_factors"):
            chart_analysis.confluence_factors.append(f)
        mc = make_market_conflict(market_ctx, chart_analysis)
        if mc is not None and hasattr(chart_analysis, "conflicts"):
            chart_analysis.conflicts.append(mc)
        mch = make_market_challenge(market_ctx)
        if mch is not None and hasattr(chart_analysis, "devils_advocate"):
            da = chart_analysis.devils_advocate
            if da is not None and hasattr(da, "challenges"):
                da.challenges.append(mch)

    def _inject_news(self, chart_analysis, news_ctx, market_ctx) -> None:
        try:
            from NewsMind.integration import (
                make_news_factor, make_news_conflict, make_news_challenge,
            )
        except ImportError:
            return
        f = make_news_factor(news_ctx, market_ctx)
        if f is not None and hasattr(chart_analysis, "confluence_factors"):
            chart_analysis.confluence_factors.append(f)
        nc = make_news_conflict(news_ctx, chart_analysis, market_ctx)
        if nc is not None and hasattr(chart_analysis, "conflicts"):
            chart_analysis.conflicts.append(nc)
        nch = make_news_challenge(news_ctx)
        if nch is not None and hasattr(chart_analysis, "devils_advocate"):
            da = chart_analysis.devils_advocate
            if da is not None and hasattr(da, "challenges"):
                da.challenges.append(nch)

    def _build_brain_grades(self, *,
                            news_ctx,
                            market_ctx,
                            chart_analysis,
                            now: datetime) -> list:
        """Translate brain contexts into BrainGrade objects for the gate.

        Each brain produces a (direction, confidence, veto) triple by
        rule:

            * direction comes from `net_bias` / plan.direction
            * confidence comes from the brain's own confidence-like
              field
            * veto = True when the brain explicitly disabled trading

        The letter grade maps confidence to A+/A/B/C/F using the
        thresholds in EngineConfig.
        """
        grades = []

        # ---- ChartMind ---------------------------------------------
        plan = chart_analysis.plan
        cm_direction = getattr(plan, "direction", "neutral")
        cm_conf = float(getattr(plan, "confidence", 0.0))
        cm_veto = not getattr(plan, "is_actionable", False)
        cm_reason = getattr(plan, "reason_if_not", "") if cm_veto else ""
        grades.append(self.BrainGrade(
            name="ChartMind",
            direction=cm_direction,
            grade=self._grade_letter(cm_conf),
            confidence=cm_conf,
            veto=cm_veto,
            veto_reason=cm_reason,
            as_of=now,
            notes=getattr(plan, "rationale", "")[:200],
        ))

        # ---- NewsMind ----------------------------------------------
        if news_ctx is not None:
            nm_dir = getattr(news_ctx, "net_bias", "neutral")
            nm_conf = float(getattr(news_ctx, "confidence", 0.0))
            nm_veto = bool(getattr(news_ctx, "do_not_trade", False))
            nm_reason = getattr(news_ctx, "do_not_trade_reason", "")
            grades.append(self.BrainGrade(
                name="NewsMind",
                direction=nm_dir,
                grade=self._grade_letter(nm_conf),
                confidence=nm_conf,
                veto=nm_veto,
                veto_reason=nm_reason,
                as_of=now,
                notes=getattr(news_ctx, "summary_one_liner", "")[:200],
            ))

        # ---- MarketMind --------------------------------------------
        if market_ctx is not None:
            mm_dir = getattr(market_ctx, "net_bias", "neutral")
            mm_strength = float(getattr(market_ctx, "bias_strength", 0.0))
            mm_veto = bool(getattr(market_ctx, "halt_trading", False))
            mm_reason = getattr(market_ctx, "halt_reason", "")
            grades.append(self.BrainGrade(
                name="MarketMind",
                direction=mm_dir,
                grade=self._grade_letter(mm_strength),
                confidence=mm_strength,
                veto=mm_veto,
                veto_reason=mm_reason,
                as_of=now,
                notes=getattr(market_ctx, "summary_one_liner", "")[:200],
            ))

        return grades

    def _grade_letter(self, confidence: float) -> str:
        """Map [0..1] confidence to A+/A/B/C/F per EngineConfig."""
        c = self.config
        if confidence >= c.grade_a_plus_threshold:
            return "A+"
        if confidence >= c.grade_a_threshold:
            return "A"
        if confidence >= c.grade_b_threshold:
            return "B"
        if confidence >= c.grade_c_threshold:
            return "C"
        return "F"

    def _derive_action(self, *, gate_result, chart_analysis, news_ctx) -> str:
        """Single-word action derived from gate result, with fallbacks."""
        if gate_result is not None:
            gate = getattr(gate_result, "gate", None)
            if gate is not None and getattr(gate, "pass_", False):
                pos = getattr(gate_result, "position", None)
                if pos is not None:
                    return pos.direction
                return getattr(gate, "direction", "skip")
            return "skip"
        if (chart_analysis is not None
                and getattr(chart_analysis, "actionable", False)):
            return chart_analysis.plan.direction
        if news_ctx is not None and getattr(news_ctx, "bias_strength", 0.0) >= 0.25:
            nb = getattr(news_ctx, "net_bias", "neutral")
            return nb if nb in ("long", "short") else "skip"
        return "skip"


# ----------------------------------------------------------------------
# Plain helpers — defensive extractors over duck-typed brain contexts.
# ----------------------------------------------------------------------
def _extract_regime(market_ctx) -> str:
    if market_ctx is None:
        return "unknown"
    nb = getattr(market_ctx, "net_bias", "neutral")
    strength = float(getattr(market_ctx, "bias_strength", 0.0))
    if nb in ("long", "bullish") and strength >= 0.4:
        return "trend_up"
    if nb in ("short", "bearish") and strength >= 0.4:
        return "trend_down"
    if strength < 0.15:
        return "range"
    return "volatile"


def _extract_news_state(news_ctx) -> str:
    if news_ctx is None:
        return "calm"
    ws = getattr(news_ctx, "window_state", None)
    if ws is None:
        return "calm"
    if getattr(ws, "trading_halted", False):
        return "blackout"
    if getattr(ws, "in_pre_window", False):
        return "pre_event"
    if getattr(ws, "in_post_window", False):
        return "post_event"
    return "calm"


def _minutes_to_next_event(news_ctx, now: datetime) -> float:
    if news_ctx is None:
        return float("inf")
    nxt = getattr(news_ctx, "next_event", None)
    if nxt is None:
        return float("inf")
    sched = getattr(nxt, "scheduled_at", None)
    if sched is None:
        return float("inf")
    delta = (sched - now).total_seconds() / 60.0
    return max(0.0, delta)


def _extract_spread_pct(market_ctx, news_ctx) -> float:
    """Best-effort spread percentile rank in [0, 1]."""
    for src in (market_ctx, news_ctx):
        if src is None:
            continue
        for attr in ("spread_percentile_rank", "spread_pct"):
            v = getattr(src, attr, None)
            if isinstance(v, (int, float)):
                return float(v)
    return 0.5


def _extract_spread_pips(market_ctx, news_ctx) -> float:
    for src in (market_ctx, news_ctx):
        if src is None:
            continue
        v = getattr(src, "spread_pips", None)
        if isinstance(v, (int, float)):
            return float(v)
    return 0.5


def _extract_upcoming_events(news_ctx) -> list:
    if news_ctx is None:
        return []
    out = []
    nxt = getattr(news_ctx, "next_event", None)
    if nxt is not None:
        out.append({
            "event_id": getattr(nxt, "event_id", ""),
            "scheduled_at": getattr(nxt, "scheduled_at", None),
            "tier": getattr(nxt, "tier", "tier3"),
            "name": getattr(nxt, "name", ""),
        })
    return out


def _extract_current_price(bar, plan) -> float:
    if bar is not None:
        for attr in ("close", "Close"):
            v = getattr(bar, attr, None)
            if isinstance(v, (int, float)):
                return float(v)
        try:
            v = bar["close"]
            return float(v)
        except (TypeError, KeyError, IndexError):
            pass
    return float(getattr(plan, "entry_price", 0.0))


def _avg_confidence(grades) -> float:
    if not grades:
        return 0.0
    confs = [float(getattr(g, "confidence", 0.0)) for g in grades]
    return sum(confs) / len(confs) if confs else 0.0

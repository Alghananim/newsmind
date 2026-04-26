# -*- coding: utf-8 -*-
"""SmartNoteBook V3 — Certification Test (18 سيناريو من قائمة المستخدم)."""
from __future__ import annotations
import sys, tempfile, uuid
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

sys.path.insert(0, "/sessions/happy-zealous-volta/mnt/outputs")
from smartnotebook.v3 import (SmartNoteBookV3, TradeAuditEntry, DecisionEvent,
                               MindOutputs)


NOW = datetime(2026,4,26,13,30,0,tzinfo=timezone.utc)


def mk_mo(**kw):
    """Default 'all good' MindOutputs."""
    base = dict(news_grade="A+", news_perm="allow", news_market_bias="bullish",
                news_confidence=0.9, news_freshness="fresh", news_verified=True,
                market_grade="A+", market_perm="allow", market_direction="bullish",
                market_confidence=0.85, market_regime="trend", market_dollar_bias="weak",
                chart_grade="A+", chart_perm="allow", chart_trend_direction="bullish",
                chart_confidence=0.85, chart_structure="uptrend",
                chart_breakout_status="real", chart_retest_status="successful",
                chart_entry_quality="excellent", chart_rr=2.0,
                gate_decision="enter", gate_approved=True,
                gate_audit_id=str(uuid.uuid4()))
    base.update(kw)
    return MindOutputs(**base)


def mk_trade(trade_id, *, pnl, mo, **kw):
    base = dict(trade_id=trade_id, audit_id=str(uuid.uuid4()), pair="EUR/USD",
                system_mode="paper", direction="buy",
                entry_time=NOW, entry_price=1.10, stop_loss=1.099,
                take_profit=1.103, expected_rr=3.0,
                pnl=pnl, mind_outputs=mo)
    base.update(kw)
    return TradeAuditEntry(**base)


@dataclass
class Spec:
    name: str
    expect_classification: str
    expect_attribution_quality: str = ""
    expect_responsible: str = ""

CASES = []

# 1: logical_win (all aligned A+)
CASES.append((Spec("01_logical_win", "logical_win", "good"),
    lambda: mk_trade("t1", pnl=0.003, mo=mk_mo(), mfe=0.003)))

# 2: lucky_win — only one brain aligned
CASES.append((Spec("02_lucky_win_grade_mismatch", "lucky_win_grade_mismatch", "lucky"),
    lambda: mk_trade("t2", pnl=0.002,
        mo=mk_mo(news_grade="C", market_grade="C", chart_grade="A+",
                 news_market_bias="unclear", market_direction="unclear"),
        mfe=0.002)))

# 3: lucky_win_thin_margin — won barely
CASES.append((Spec("03_lucky_win_thin", "lucky_win_thin_margin", "good"),
    lambda: mk_trade("t3", pnl=0.0001,
        mo=mk_mo(),  mfe=0.0001)))

# 4: valid_loss — all aligned but lost
CASES.append((Spec("04_valid_loss", "valid_loss", "valid_loss", "none"),
    lambda: mk_trade("t4", pnl=-0.001, mo=mk_mo(), mae=-0.001)))

# 5: bad_loss_late_entry
CASES.append((Spec("05_bad_loss_late_entry", "bad_loss_late_entry", "bad", "chart"),
    lambda: mk_trade("t5", pnl=-0.001,
        mo=mk_mo(chart_late_entry=True), mae=-0.001)))

# 6: bad_loss_fake_breakout
CASES.append((Spec("06_bad_loss_fake_breakout", "bad_loss_fake_breakout", "bad", "chart"),
    lambda: mk_trade("t6", pnl=-0.001,
        mo=mk_mo(chart_fake_breakout=True), mae=-0.001)))

# 7: spread_loss
CASES.append((Spec("07_spread_loss", "spread_loss", "bad"),
    lambda: mk_trade("t7", pnl=-0.0005,
        mo=mk_mo(),
        spread_at_entry=0.5, slippage_estimate=0.5, actual_slippage=2.0,
        mae=-0.0005)))

# 8: bad_loss_choppy_market
CASES.append((Spec("08_bad_loss_choppy", "bad_loss_choppy_market", "bad", "market"),
    lambda: mk_trade("t8", pnl=-0.001,
        mo=mk_mo(market_regime="choppy"), mae=-0.001)))

# 9: bad_loss_misaligned
CASES.append((Spec("09_bad_loss_misaligned", "bad_loss_misaligned", "bad"),
    lambda: mk_trade("t9", pnl=-0.001,
        mo=mk_mo(news_market_bias="bearish",
                 market_direction="bearish",
                 chart_trend_direction="bearish"),
        direction="buy", mae=-0.001)))

# 10: breakeven
CASES.append((Spec("10_breakeven", "breakeven", "unclear"),
    lambda: mk_trade("t10", pnl=0.0, mo=mk_mo())))

# 11: system_bug
CASES.append((Spec("11_system_bug", "system_bug", "bad"),
    lambda: mk_trade("t11", pnl=-0.001,
        mo=mk_mo(gate_reason="bug:gate_calculation_error"),
        mae=-0.001)))

# 12: missing mind outputs
CASES.append((Spec("12_missing_mind_outputs", "missing_mind_outputs", "unclear"),
    lambda: mk_trade("t12", pnl=-0.001, mo=None)))


def run_main():
    print("=" * 100)
    print("SmartNoteBook V3 — Certification (18 سيناريو)")
    print("=" * 100)
    pass_n = 0
    fail_rows = []

    with tempfile.TemporaryDirectory() as tmp:
        nb = SmartNoteBookV3(tmp)

        for i, (spec, build) in enumerate(CASES, 1):
            t = build()
            t = nb.record_trade(t)

            ok_cls = t.classification == spec.expect_classification
            ok_q = (not spec.expect_attribution_quality) or (
                t.attribution and t.attribution.decision_quality == spec.expect_attribution_quality)
            ok_resp = (not spec.expect_responsible) or (
                t.attribution and t.attribution.responsible_mind == spec.expect_responsible)
            ok = ok_cls and ok_q and ok_resp
            if ok: pass_n += 1
            status = "PASS" if ok else "FAIL"

            print(f"\n┌─[{i:02d}] {spec.name}  {'✓ '+status if ok else '✗ '+status}")
            print(f"│ INPUT          : pnl={t.pnl}  mind_outputs={'present' if t.mind_outputs else 'NONE'}")
            print(f"│ classification : got={t.classification}  expect={spec.expect_classification}  {'✓' if ok_cls else '✗'}")
            if t.attribution:
                print(f"│ quality        : got={t.attribution.decision_quality}  expect={spec.expect_attribution_quality or 'any'}  {'✓' if ok_q else '✗'}")
                print(f"│ responsible    : got={t.attribution.responsible_mind}  expect={spec.expect_responsible or 'any'}  {'✓' if ok_resp else '✗'}")
                print(f"│ supporting     : {list(t.attribution.supporting_minds)}")
                print(f"│ contradicting  : {list(t.attribution.contradicting_minds)}")
            print(f"│ lesson         : {t.lesson}")
            print(f"│ stored?        : {t.trade_id in [tt.get('trade_id') for tt in nb.storage.query_trades(limit=200)]}")
            if not ok:
                why = []
                if not ok_cls: why.append(f"cls {t.classification}!={spec.expect_classification}")
                if not ok_q: why.append(f"quality !={spec.expect_attribution_quality}")
                if not ok_resp: why.append(f"resp !={spec.expect_responsible}")
                print(f"│ FIX_NEEDED     : {'; '.join(why)}")
                fail_rows.append((i, spec.name, "; ".join(why)))
            print(f"└─ correct? {status}")

        # Test storage queries
        print("\n--- Storage / search tests ---")
        # 13: rejected/wait events should be recorded
        e = DecisionEvent(event_id="e1", audit_id="ax1", timestamp=NOW,
                          event_type="block", pair="EUR/USD",
                          gate_decision="block",
                          rejected_reason="news_block_high_impact_event",
                          mind_outputs=mk_mo(news_perm="block", news_grade="C"))
        nb.record_decision(e)
        events = nb.storage.query_events(pair="EUR/USD", event_type="block")
        ok = any(ev.get("event_id") == "e1" for ev in events)
        print(f"[13] rejected_event_recorded: {'✓ PASS' if ok else '✗ FAIL'}")
        if ok: pass_n += 1
        else: fail_rows.append((13, "rejected_event_recorded", "event not stored"))

        # 14: bug recording
        b = nb.record_bug(affected_mind="market", bug_type="risk_check_inversion",
                         severity="high", example_event_id="e1",
                         impact="opened bad trades when correlation broken")
        bugs = nb.storage.all_bugs()
        ok = any(bb.get("bug_id") == b.bug_id for bb in bugs)
        print(f"[14] bug_recorded            : {'✓ PASS' if ok else '✗ FAIL'}")
        if ok: pass_n += 1
        else: fail_rows.append((14, "bug_recorded", "bug not stored"))

        # 15: daily summary
        s = nb.daily_report(date="2026-04-26", pair="EUR/USD")
        ok = s.n_trades >= 10 and s.total_pnl is not None
        print(f"[15] daily_summary           : {'✓ PASS' if ok else '✗ FAIL'} n_trades={s.n_trades} pnl={s.total_pnl}")
        if ok: pass_n += 1
        else: fail_rows.append((15, "daily_summary", "summary fields wrong"))

        # 16: search why_lose
        why = nb.why_lose(pair="EUR/USD")
        ok = why.get("count", 0) > 0 or "summary" in why
        print(f"[16] search_why_lose         : {'✓ PASS' if ok else '✗ FAIL'} {why.get('summary','')}")
        if ok: pass_n += 1
        else: fail_rows.append((16, "why_lose", "no result"))

        # 17: anti-overfitting (recommendation requires evidence)
        # We have 1-2 of each loss type, so most patterns won't hit MIN_EVIDENCE_FOR_SUGGESTION (3)
        lessons = nb.scan_lessons(pair="EUR/USD")
        # No lessons should be created with so few instances per pattern
        ok = len(lessons) == 0 or all(l.observed_count >= 3 for l in lessons)
        print(f"[17] anti_overfitting        : {'✓ PASS' if ok else '✗ FAIL'} lessons={len(lessons)}")
        if ok: pass_n += 1
        else: fail_rows.append((17, "anti_overfitting", "lesson with insufficient evidence"))

        # 18: duplicate trade_id rejected
        t_dup = mk_trade("t1", pnl=0.001, mo=mk_mo())  # same trade_id as t1
        nb.storage.warnings.clear()
        nb.record_trade(t_dup)
        ok = any("duplicate_trade_id" in w for w in nb.storage.warnings)
        print(f"[18] duplicate_trade_rejected: {'✓ PASS' if ok else '✗ FAIL'}")
        if ok: pass_n += 1
        else: fail_rows.append((18, "duplicate_trade_rejected", "no warning"))

    print("\n" + "=" * 100)
    print(f"FINAL: {pass_n}/18 PASSED")
    if fail_rows:
        print("FAILS:")
        for i, n, w in fail_rows: print(f"  [{i:02d}] {n} — {w}")
    print("=" * 100)
    return pass_n, 18


if __name__ == "__main__":
    p, n = run_main()
    sys.exit(0 if p == n else 1)

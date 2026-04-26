# -*- coding: utf-8 -*-
"""SmartNoteBook V4 — Speed + Intelligence Cert (15 سيناريو).

Measures speed + intelligence + data integrity simultaneously.
"""
from __future__ import annotations
import sys, time, tempfile, uuid
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

sys.path.insert(0, "/sessions/happy-zealous-volta/mnt/outputs")
from smartnotebook.v3 import (SmartNoteBookV3, TradeAuditEntry, DecisionEvent,
                               MindOutputs, BugDetected)


NOW = datetime(2026,4,26,13,30,0,tzinfo=timezone.utc)


def mk_mo(**kw):
    base = dict(news_grade="A+", news_perm="allow", news_market_bias="bullish",
                news_confidence=0.9, market_grade="A+", market_perm="allow",
                market_direction="bullish", market_confidence=0.85,
                chart_grade="A+", chart_perm="allow", chart_trend_direction="bullish",
                chart_confidence=0.85, chart_rr=2.0,
                gate_decision="enter", gate_audit_id=str(uuid.uuid4()))
    base.update(kw)
    return MindOutputs(**base)


def mk_trade(trade_id, *, pnl, mo, **kw):
    base = dict(trade_id=trade_id, audit_id=str(uuid.uuid4()), pair="EUR/USD",
                system_mode="paper", direction="buy",
                entry_time=NOW, entry_price=1.10, stop_loss=1.099,
                take_profit=1.103, expected_rr=3.0, mfe=0.003,
                pnl=pnl, mind_outputs=mo)
    base.update(kw)
    return TradeAuditEntry(**base)


def main():
    print("=" * 100)
    print("SmartNoteBook V4 — Speed + Intelligence Cert (15 سيناريو)")
    print("=" * 100)
    pass_n = 0
    fails = []
    SPEED_TARGET_MS = 2.0

    with tempfile.TemporaryDirectory() as tmp:
        nb = SmartNoteBookV3(tmp, enable_async=False)

        # 1: Single trade write speed (first write has cold-start overhead)
        # Warm up first
        warmup = mk_trade("warmup", pnl=0.001, mo=mk_mo())
        nb.record_trade(warmup)
        t = mk_trade("t1", pnl=0.001, mo=mk_mo())
        s = time.perf_counter_ns()
        nb.record_trade(t)
        ms = (time.perf_counter_ns() - s) / 1e6
        ok = ms <= SPEED_TARGET_MS
        print(f"\n[01] single_trade_write_warm   : {'✓' if ok else '✗'} {ms:.3f}ms (≤{SPEED_TARGET_MS}ms)")
        if ok: pass_n += 1
        else: fails.append((1, f"{ms:.3f}ms"))

        # 2: 100 trade writes
        s = time.perf_counter_ns()
        for i in range(100):
            t = mk_trade(f"bulk_t{i}", pnl=0.001 * (1 if i%2==0 else -1), mo=mk_mo())
            nb.record_trade(t)
        ms = (time.perf_counter_ns() - s) / 1e6
        avg = ms / 100
        ok = avg <= SPEED_TARGET_MS
        print(f"[02] 100_trades_write         : {'✓' if ok else '✗'} {ms:.0f}ms total, avg {avg:.3f}ms")
        if ok: pass_n += 1
        else: fails.append((2, f"avg {avg:.3f}ms"))

        # 3: 1000 events sync
        s = time.perf_counter_ns()
        for i in range(1000):
            e = DecisionEvent(event_id=f"sync_e{i}", audit_id=str(uuid.uuid4()),
                              timestamp=NOW, event_type="wait", pair="EUR/USD",
                              mind_outputs=mk_mo())
            nb.record_decision(e)
        ms = (time.perf_counter_ns() - s) / 1e6
        avg = ms / 1000
        ok = avg <= SPEED_TARGET_MS
        print(f"[03] 1000_events_sync          : {'✓' if ok else '✗'} {ms:.0f}ms total, avg {avg:.3f}ms")
        if ok: pass_n += 1
        else: fails.append((3, f"avg {avg:.3f}ms"))

        # 4: query latency
        s = time.perf_counter_ns()
        nb.why_lose(pair="EUR/USD")
        ms = (time.perf_counter_ns() - s) / 1e6
        ok = ms <= 50.0    # query budget more lenient
        print(f"[04] query_why_lose            : {'✓' if ok else '✗'} {ms:.2f}ms (≤50ms)")
        if ok: pass_n += 1
        else: fails.append((4, f"{ms:.2f}ms"))

        # 5: daily summary
        s = time.perf_counter_ns()
        ds = nb.daily_report(date=NOW.strftime("%Y-%m-%d"), pair="EUR/USD")
        ms = (time.perf_counter_ns() - s) / 1e6
        ok = ms <= 50.0 and ds.n_trades >= 100
        print(f"[05] daily_summary             : {'✓' if ok else '✗'} {ms:.2f}ms n_trades={ds.n_trades}")
        if ok: pass_n += 1
        else: fails.append((5, f"{ms:.2f}ms n={ds.n_trades}"))

        # 6: pattern detection
        s = time.perf_counter_ns()
        patterns = nb.detect_patterns(pair="EUR/USD")
        ms = (time.perf_counter_ns() - s) / 1e6
        ok = ms <= 100.0 and "avg_pnl_by_grade" in patterns
        print(f"[06] pattern_detection         : {'✓' if ok else '✗'} {ms:.2f}ms keys={list(patterns.keys())[:3]}")
        if ok: pass_n += 1
        else: fails.append((6, f"{ms:.2f}ms"))

        # 7: classification accuracy on known cases
        results = []
        cases = [
            (mk_mo(), 0.001, "logical_win"),
            (mk_mo(news_grade="C", market_grade="C"), 0.001, "lucky_win_grade_mismatch"),
            (mk_mo(chart_late_entry=True), -0.001, "bad_loss_late_entry"),
            (mk_mo(chart_fake_breakout=True), -0.001, "bad_loss_fake_breakout"),
            (mk_mo(market_regime="choppy"), -0.001, "bad_loss_choppy_market"),
        ]
        correct = 0
        for mo, pnl, expected in cases:
            t = mk_trade(f"cls_{uuid.uuid4().hex[:6]}", pnl=pnl, mo=mo)
            t = nb.record_trade(t)
            if t.classification == expected: correct += 1
        acc = correct / len(cases)
        ok = acc >= 0.8
        print(f"[07] classification_accuracy   : {'✓' if ok else '✗'} {correct}/{len(cases)} ({acc*100:.0f}%)")
        if ok: pass_n += 1
        else: fails.append((7, f"acc {acc*100:.0f}%"))

        # 8: anti-overfitting (no recommendation < 3 evidence)
        lessons = nb.scan_lessons(pair="EUR/USD")
        ok = all(l.observed_count >= 3 for l in lessons) if lessons else True
        print(f"[08] anti_overfitting          : {'✓' if ok else '✗'} lessons={len(lessons)} all_≥3?={all(l.observed_count>=3 for l in lessons) if lessons else True}")
        if ok: pass_n += 1
        else: fails.append((8, "lesson < 3 evidence"))

        # 9: duplicate detection
        nb.storage.warnings.clear()
        dup = mk_trade("t1", pnl=0.001, mo=mk_mo())  # same as #1
        nb.record_trade(dup)
        ok = any("duplicate" in w for w in nb.storage.warnings)
        print(f"[09] duplicate_detection       : {'✓' if ok else '✗'}")
        if ok: pass_n += 1
        else: fails.append((9, "no warning"))

        # 10: missing audit_id rejected
        bad = TradeAuditEntry(trade_id="bad1", audit_id="", pair="EUR/USD",
                              direction="buy", entry_time=NOW, entry_price=1.10,
                              stop_loss=1.099, take_profit=1.103, pnl=0.001,
                              mind_outputs=mk_mo())
        bad.audit_id = ""    # force empty
        nb.storage.warnings.clear()
        bad_t = TradeAuditEntry(trade_id="", audit_id="", pair="EUR/USD",
                                direction="buy", entry_time=NOW,
                                entry_price=1.10, stop_loss=1.099, take_profit=1.103,
                                pnl=0.001, mind_outputs=mk_mo())
        # record_trade auto-fills missing IDs, so skip the auto-fill by going direct to storage
        nb.storage.write_trade(bad_t)
        ok = any("missing_id" in w for w in nb.storage.warnings)
        print(f"[10] missing_id_rejected       : {'✓' if ok else '✗'}")
        if ok: pass_n += 1
        else: fails.append((10, "no warning"))

        # 11: storage_health is "ok" or "warnings"
        h = nb.storage_health()
        ok = h in ("ok", "warnings")
        print(f"[11] storage_health            : {'✓' if ok else '✗'} {h}")
        if ok: pass_n += 1
        else: fails.append((11, h))

        # 12: speed score
        ss = nb.speed_score()
        ok = ss >= 0.8
        print(f"[12] speed_score               : {'✓' if ok else '✗'} {ss}")
        if ok: pass_n += 1
        else: fails.append((12, f"score {ss}"))

        # 13: intelligence score
        is_ = nb.intelligence_score()
        ok = is_ >= 0.7
        print(f"[13] intelligence_score        : {'✓' if ok else '✗'} {is_}")
        if ok: pass_n += 1
        else: fails.append((13, f"score {is_}"))

        # 14: ASYNC writer test (1000 events, non-blocking)
        nb_async = SmartNoteBookV3(tmp + "_async", enable_async=True)
        s = time.perf_counter_ns()
        for i in range(1000):
            e = DecisionEvent(event_id=f"async_e{i}", audit_id=str(uuid.uuid4()),
                              timestamp=NOW, event_type="wait", pair="EUR/USD",
                              mind_outputs=mk_mo())
            nb_async.record_decision(e, async_=True)
        submit_ms = (time.perf_counter_ns() - s) / 1e6
        # Wait for queue to drain
        nb_async._async.flush(2.0)
        nb_async.stop()
        ok = submit_ms <= 100.0    # 1000 submits in <100ms
        print(f"[14] async_1000_events         : {'✓' if ok else '✗'} submit={submit_ms:.0f}ms total ({submit_ms/1000:.4f}ms/event)")
        if ok: pass_n += 1
        else: fails.append((14, f"submit {submit_ms:.0f}ms"))

        # 15: health_report contains all required fields
        hr = nb.health_report()
        required = ["event_write_avg_ms","trade_log_avg_ms","attribution_calc_avg_ms",
                    "daily_summary_avg_ms","query_avg_ms","dropped_events_count",
                    "duplicate_events_count","queue_backlog_size","storage_health_status",
                    "intelligence_score","speed_score","storage_health"]
        missing = [k for k in required if k not in hr]
        ok = len(missing) == 0
        print(f"[15] health_report_complete    : {'✓' if ok else '✗'} missing={missing or 'none'}")
        if ok: pass_n += 1
        else: fails.append((15, f"missing {missing}"))

        # Summary
        print("\n" + "=" * 100)
        m = nb.metrics.to_dict()
        print(f"METRICS:")
        print(f"  trade_log_avg_ms      : {m['trade_log_avg_ms']:.3f}")
        print(f"  event_write_avg_ms    : {m['event_write_avg_ms']:.3f}")
        print(f"  query_avg_ms          : {m['query_avg_ms']:.3f}")
        print(f"  daily_summary_avg_ms  : {m['daily_summary_avg_ms']:.3f}")
        print(f"  dropped_events        : {m['dropped_events_count']}")
        print(f"  duplicate_events      : {m['duplicate_events_count']}")
        print(f"  storage_health        : {m['storage_health_status']}")
        print(f"  intelligence_score    : {nb.intelligence_score()}")
        print(f"  speed_score           : {nb.speed_score()}")

    print(f"\n{'='*100}\nFINAL: {pass_n}/15 PASSED")
    if fails:
        print("FAILS:")
        for i, why in fails: print(f"  [{i:02d}] {why}")
    print("="*100)
    return pass_n, 15


if __name__ == "__main__":
    p, n = main()
    sys.exit(0 if p == n else 1)

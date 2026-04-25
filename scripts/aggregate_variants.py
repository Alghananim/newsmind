#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""aggregate_variants.py — collect all per-job JSONs, rank variants.

Reads <DIR>/<variant>__<pair>.json files produced by run_variant.py
and produces:
    <DIR>/all_results.json     — flat list of every (variant, pair)
    <DIR>/by_variant.json      — aggregate per variant across pairs
    <DIR>/leaderboard.txt      — human-readable ranking

Ranking logic
-------------
We score each variant by:
    score = sum_pairs(total_return_pct) + 50 * sum_pairs(sqn)
            - 100 * sum_pairs(halted_early ? 1 : 0)

Halt is heavily penalised; SQN heavily rewarded; raw return moderate.
This selects for systems that are profitable AND stable.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("VARIANT_DIR", "variant_output"))


def main() -> int:
    files = sorted(ROOT.glob("*.json"))
    files = [f for f in files if f.name not in ("all_results.json", "by_variant.json")]
    if not files:
        print("no per-job JSONs found")
        return 1

    rows = []
    for f in files:
        try:
            rows.append(json.loads(f.read_text()))
        except Exception as e:
            print(f"skip {f}: {e}")

    print(f"loaded {len(rows)} per-job summaries")

    # ----- aggregate per variant
    by_variant: dict[str, dict] = {}
    for r in rows:
        v = r["variant"]
        if v not in by_variant:
            by_variant[v] = {
                "variant": v, "pairs": [],
                "n_trades_total": 0, "wins_total": 0,
                "sum_return_pct": 0.0, "sum_sqn": 0.0,
                "max_dd_worst": 0.0, "halts": 0,
                "is_expectancy_avg": 0.0, "oos_expectancy_avg": 0.0,
            }
        b = by_variant[v]
        b["pairs"].append({
            "pair": r["pair"],
            "n": r["n_trades"], "wr": r["win_rate"],
            "e": r["expectancy_r"], "pf": r["profit_factor"],
            "sqn": r["sqn"], "dd": r["max_dd_pct"],
            "ret": r["total_return_pct"], "ann": r["annualised_return_pct"],
            "halted": r["halted_early"],
            "is_e": r["is_expectancy"], "oos_e": r["oos_expectancy"],
        })
        b["n_trades_total"] += r["n_trades"]
        b["wins_total"] += int(r["n_trades"] * r["win_rate"])
        b["sum_return_pct"] += r["total_return_pct"]
        b["sum_sqn"] += r["sqn"]
        b["max_dd_worst"] = max(b["max_dd_worst"], r["max_dd_pct"])
        b["halts"] += int(bool(r["halted_early"]))

    for v, b in by_variant.items():
        n = len(b["pairs"])
        b["avg_return_pct"] = b["sum_return_pct"] / n if n else 0
        b["avg_sqn"] = b["sum_sqn"] / n if n else 0
        b["overall_wr"] = b["wins_total"] / b["n_trades_total"] if b["n_trades_total"] else 0
        b["is_expectancy_avg"] = sum(p["is_e"] for p in b["pairs"]) / n if n else 0
        b["oos_expectancy_avg"] = sum(p["oos_e"] for p in b["pairs"]) / n if n else 0
        b["score"] = b["sum_return_pct"] + 50 * b["sum_sqn"] - 100 * b["halts"]

    # ----- write outputs
    (ROOT / "all_results.json").write_text(json.dumps(rows, indent=2, default=str))
    (ROOT / "by_variant.json").write_text(json.dumps(by_variant, indent=2, default=str))

    # ----- leaderboard
    ranked = sorted(by_variant.values(), key=lambda x: -x["score"])
    lines = []
    lines.append("=" * 100)
    lines.append(" VARIANT LEADERBOARD")
    lines.append("=" * 100)
    lines.append(f"{'Variant':22s} | {'Score':>8s} | {'AvgRet':>8s} | "
                 f"{'AvgSQN':>7s} | {'WR':>6s} | {'Trd':>6s} | {'Halts':>5s} | "
                 f"{'IS-E':>7s} | {'OOS-E':>7s}")
    lines.append("-" * 100)
    for b in ranked:
        lines.append(
            f"{b['variant']:22s} | "
            f"{b['score']:>+8.1f} | "
            f"{b['avg_return_pct']:>+7.2f}% | "
            f"{b['avg_sqn']:>+6.2f} | "
            f"{b['overall_wr']*100:>5.1f}% | "
            f"{b['n_trades_total']:>6d} | "
            f"{b['halts']:>5d} | "
            f"{b['is_expectancy_avg']:>+6.3f} | "
            f"{b['oos_expectancy_avg']:>+6.3f}"
        )

    lines.append("")
    lines.append(" PER-PAIR DETAIL  (best variant per pair)")
    lines.append("-" * 100)
    for pair in ["EUR/USD", "USD/JPY", "GBP/USD"]:
        pair_rows = [(v, p) for v in by_variant.values() for p in v["pairs"] if p["pair"] == pair]
        pair_rows.sort(key=lambda x: -x[1]["ret"])
        lines.append(f"\n  {pair}:")
        for v, p in pair_rows[:5]:
            halt = " HALT" if p["halted"] else ""
            lines.append(
                f"    {v['variant']:22s} | "
                f"trd={p['n']:>4d}  wr={p['wr']*100:>5.1f}%  "
                f"e={p['e']:>+6.3f}R  pf={p['pf']:>5.2f}  "
                f"sqn={p['sqn']:>+5.2f}  ret={p['ret']:>+7.2f}%  "
                f"dd={p['dd']:>5.2f}%{halt}"
            )

    lines.append("")
    lines.append(" CHAMPIONS")
    lines.append("-" * 100)
    if ranked:
        winner = ranked[0]
        lines.append(f"  Top variant by score: {winner['variant']}")
        lines.append(f"    avg return {winner['avg_return_pct']:+.2f}% / "
                     f"avg SQN {winner['avg_sqn']:+.2f} / "
                     f"halts {winner['halts']}/{len(winner['pairs'])}")
        target_hits = [v for v in ranked if v["sum_return_pct"] > 450]  # 150% × 3 pairs
        if target_hits:
            lines.append(f"  Variants exceeding 150% × 3-pair sum: "
                         f"{', '.join(v['variant'] for v in target_hits)}")
        else:
            lines.append("  No variant yet exceeds the 150% × 3-pair target.")

    text = "\n".join(lines)
    (ROOT / "leaderboard.txt").write_text(text)
    print(text)

    # Also write a Markdown summary for GitHub Step Summary
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write("## Variant matrix leaderboard\n\n")
            f.write("| Variant | Score | AvgRet | AvgSQN | WR | Trades | Halts | IS-E | OOS-E |\n")
            f.write("|---|---:|---:|---:|---:|---:|---:|---:|---:|\n")
            for b in ranked:
                f.write(
                    f"| **{b['variant']}** | {b['score']:+.1f} | "
                    f"{b['avg_return_pct']:+.2f}% | {b['avg_sqn']:+.2f} | "
                    f"{b['overall_wr']*100:.1f}% | {b['n_trades_total']} | "
                    f"{b['halts']} | {b['is_expectancy_avg']:+.3f} | "
                    f"{b['oos_expectancy_avg']:+.3f} |\n"
                )
            f.write("\n### Per-pair winners\n\n")
            for pair in ["EUR/USD", "USD/JPY", "GBP/USD"]:
                pair_rows = [(v, p) for v in by_variant.values() for p in v["pairs"] if p["pair"] == pair]
                pair_rows.sort(key=lambda x: -x[1]["ret"])
                f.write(f"\n**{pair}:**\n\n")
                f.write("| Variant | N | WR | E | PF | SQN | Return | DD | Halt |\n")
                f.write("|---|---:|---:|---:|---:|---:|---:|---:|:-:|\n")
                for v, p in pair_rows[:5]:
                    f.write(
                        f"| {v['variant']} | {p['n']} | {p['wr']*100:.1f}% | "
                        f"{p['e']:+.3f} | {p['pf']:.2f} | {p['sqn']:+.2f} | "
                        f"{p['ret']:+.2f}% | {p['dd']:.2f}% | "
                        f"{'⚠' if p['halted'] else ''} |\n"
                    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

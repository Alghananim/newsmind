#!/usr/bin/env python3
"""Aggregate walk-forward results across (variant, pair) into a robustness leaderboard."""
import json, os, sys
from pathlib import Path

ROOT = Path(os.environ.get("WF_DIR", "wf_output"))


def main():
    files = sorted(ROOT.glob("wf__*.json"))
    files = [f for f in files if f.name.startswith("wf__") and "/" not in str(f.name).removeprefix("wf__")]
    rows = [json.loads(f.read_text()) for f in files if f.is_file() and f.suffix == ".json"]
    rows = [r for r in rows if "n_quarters" in r]
    print(f"\nLoaded {len(rows)} walk-forward summaries\n")

    # leaderboard
    rows.sort(key=lambda r: (-r["profitable_quarters"], -r["mean_expectancy_r"]))
    lines = []
    lines.append("=" * 110)
    lines.append(" WALK-FORWARD ROBUSTNESS LEADERBOARD (real OANDA, 90-day rolling quarters)")
    lines.append("=" * 110)
    lines.append(f"{'Variant':24s} | {'Pair':9s} | {'Q':>3s} | {'Prof.Q':>6s} | "
                 f"{'MeanE':>7s} | {'Worst Q':>9s} | {'Robust':>7s}")
    lines.append("-" * 110)
    for r in rows:
        prof_pct = (r["profitable_quarters"]/r["n_quarters"]*100) if r["n_quarters"] else 0
        rob = "YES" if r["robust"] else "NO"
        lines.append(
            f"{r['variant']:24s} | {r['pair']:9s} | {r['n_quarters']:>3d} | "
            f"{r['profitable_quarters']:>2d}/{r['n_quarters']:<3d} | "
            f"{r['mean_expectancy_r']:>+6.3f} | "
            f"{r['worst_quarter_return_pct']:>+8.2f}% | "
            f"{rob:>7s}"
        )

    lines.append("")
    lines.append(" PER-QUARTER DETAIL")
    lines.append("-" * 110)
    for r in rows:
        lines.append(f"\n  {r['variant']} on {r['pair']}:")
        for q in r["quarters"]:
            halt_marker = f" h{q['halt_count']}" if q['halt_count'] else ""
            lines.append(
                f"    Q{q['quarter']} [{q['start']}..{q['end']}]: "
                f"n={q['n_trades']:>4d}  WR={q['win_rate']*100:>4.1f}%  "
                f"E={q['expectancy_r']:>+6.3f}R  PF={q['profit_factor']:>5.2f}  "
                f"DD={q['max_dd_pct']:>5.2f}%  ret={q['total_return_pct']:>+7.2f}%"
                f"{halt_marker}"
            )

    text = "\n".join(lines)
    (ROOT / "wf_leaderboard.txt").write_text(text)
    print(text)

    # Markdown summary for GitHub Step Summary
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write("## Walk-Forward Robustness Leaderboard\n\n")
            f.write("| Variant | Pair | Quarters | Profitable | MeanE | Worst Q | Robust |\n")
            f.write("|---|---|---:|---:|---:|---:|:-:|\n")
            for r in rows:
                rob = "✓" if r["robust"] else "✗"
                f.write(
                    f"| {r['variant']} | {r['pair']} | {r['n_quarters']} | "
                    f"{r['profitable_quarters']}/{r['n_quarters']} | "
                    f"{r['mean_expectancy_r']:+.3f} | "
                    f"{r['worst_quarter_return_pct']:+.2f}% | {rob} |\n"
                )
            f.write("\n### Per-quarter detail\n\n")
            for r in rows:
                f.write(f"\n**{r['variant']} on {r['pair']}:**\n\n")
                f.write("| Quarter | n | WR | E | PF | DD | Return | Halts |\n")
                f.write("|:-:|---:|---:|---:|---:|---:|---:|---:|\n")
                for q in r["quarters"]:
                    f.write(
                        f"| Q{q['quarter']} {q['start']}→{q['end']} | "
                        f"{q['n_trades']} | {q['win_rate']*100:.1f}% | "
                        f"{q['expectancy_r']:+.3f} | {q['profit_factor']:.2f} | "
                        f"{q['max_dd_pct']:.2f}% | "
                        f"{q['total_return_pct']:+.2f}% | "
                        f"{q['halt_count']} |\n"
                    )

if __name__ == "__main__":
    main()

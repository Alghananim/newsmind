# -*- coding: utf-8 -*-
"""Narrative Reasoning — upgrade #8.

Converts the structured outputs of ChartMind (reading, multi-TF,
confluence, calibrated proba, clarity) into a clear Arabic-language
explanation of what the system sees and why it wants to do (or not do)
what it wants. This is the layer that earns trust: traders trust what
they understand.

The narrative is deliberately:
  * Arabic — Mansur's language, not forced English.
  * Structured — the same order every time so eye-tracking stays
    consistent: state, context, setup, warnings, verdict, reasoning.
  * Honest — conflicts and anti-patterns are surfaced first, not
    buried. If the system wants to abstain, the narrative explains
    why clearly.
  * Compact — 6-15 lines, readable on Telegram in one screen.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# Localization dictionaries — all fixed strings live here.
# ---------------------------------------------------------------------------
_DIR_AR = {"up": "صاعد", "down": "هابط", "flat": "جانبي"}
_VERDICT_AR = {
    "long":    "شراء محتمل",
    "short":   "بيع محتمل",
    "neutral": "لا اتجاه واضح",
}
_VERDICT_EMOJI = {"long": "🟢", "short": "🔴", "neutral": "⚪"}
_CLARITY_AR = {
    "trade":   "الوضع واضح",
    "wait":    "تحذيرات بسيطه — انتظر",
    "abstain": "⚠️ توقف — الظروف ليست مناسبه",
}
_SESSION_AR = {
    "asian": "آسيا",
    "london": "لندن",
    "ny_am": "نيويورك صباحاً",
    "ny_pm": "نيويورك ظهراً",
    "overlap": "تداخل",
    "off": "سوق مغلق/منخفض",
}
_TRUST_AR = {
    "high": "عاليه",
    "medium": "متوسطه",
    "low": "ضعيفه",
    "none": "لا بيانات كافيه",
}
_REGIME_AR = {
    "low":    "منخفض",
    "normal": "طبيعي",
    "high":   "عالي",
}


@dataclass
class Narrative:
    """Final packaged narrative ready for user display."""
    headline: str
    sections: list           # list of (title, body) pairs
    verdict: str             # "long" | "short" | "neutral" | "abstain"
    confidence_pct: float    # 0-100, after calibration
    arabic_text: str         # full formatted string
    english_text: str        # English mirror for logs / audit

    def to_dict(self) -> dict:
        return {
            "headline": self.headline,
            "verdict": self.verdict,
            "confidence_pct": self.confidence_pct,
            "sections": [{"title": t, "body": b} for t, b in self.sections],
            "arabic_text": self.arabic_text,
            "english_text": self.english_text,
        }


# ---------------------------------------------------------------------------
# The generator.
# ---------------------------------------------------------------------------
class NarrativeGenerator:
    """Produce a structured Arabic narrative from ChartMind outputs.

    Usage:
        ng = NarrativeGenerator()
        narr = ng.generate(
            reading=r,
            mtf=mtf,
            confluence=conf,
            calibrated=cal,
            clarity=clar,
        )
        send_to_telegram(narr.arabic_text)
    """

    def generate(
        self,
        *,
        reading,
        mtf=None,
        confluence=None,
        calibrated=None,
        clarity=None,
        plan=None,
        entry=None,
    ) -> Narrative:
        sections: list[tuple[str, str]] = []

        # --- Headline ---------------------------------------------
        if clarity is not None and clarity.verdict == "abstain":
            verdict_label = "abstain"
            emoji = "🛑"
            main_line = "توقف — الظروف ليست ملائمه للتداول"
        elif confluence is not None:
            verdict_label = confluence.verdict
            emoji = _VERDICT_EMOJI.get(verdict_label, "⚪")
            main_line = _VERDICT_AR.get(verdict_label, "لا اتجاه واضح")
        else:
            verdict_label = "neutral"
            emoji = "⚪"
            main_line = "قراءه عامه"

        conf_pct = self._confidence_pct(confluence, calibrated, clarity)
        headline = (
            f"{emoji} {reading.pair} @ {reading.price:.5f}  •  "
            f"{main_line} ({conf_pct:.0f}%)"
        )

        # --- Section 1: State -------------------------------------
        sess = _SESSION_AR.get(reading.session, reading.session or "—")
        kz = f" 🎯 Killzone: {reading.killzone}" if reading.killzone else ""
        state = (
            f"الوقت: {reading.timestamp.strftime('%H:%M UTC')}  "
            f"|  الجلسه: {sess}{kz}\n"
            f"الاتجاه: {_DIR_AR.get(reading.trend_direction, reading.trend_direction)}"
            f" (ADX {reading.adx:.0f}, قوه {reading.trend_strength:.2f})\n"
            f"التقلّب: {_REGIME_AR.get(reading.volatility_regime, reading.volatility_regime)}"
            f" (ATR rank {reading.atr_pct_rank:.2f})"
        )
        sections.append(("الحاله", state))

        # --- Section 2: Multi-TF context --------------------------
        if mtf is not None:
            tf_lines = []
            for tf in ("H4", "H1", "M30", "M15", "M5", "D"):
                if tf in mtf.per_tf:
                    rd = mtf.per_tf[tf]
                    arrow = {"up": "↑", "down": "↓", "flat": "→"}.get(
                        rd.trend_direction, "·"
                    )
                    tf_lines.append(
                        f"  {tf}: {arrow} {_DIR_AR.get(rd.trend_direction, '')}"
                    )
            mtf_body = (
                f"الإطارات: محاذاه {mtf.alignment:+.2f}  "
                f"(التوجه المسيطر من {mtf.dominant_tf})\n"
                + "\n".join(tf_lines)
            )
            if mtf.conflicts:
                mtf_body += "\n⚠️  تضاربات: " + "؛ ".join(mtf.conflicts)
            sections.append(("السياق متعدد الإطارات", mtf_body))

        # --- Section 3: Setup -------------------------------------
        setup_parts: list[str] = []
        if reading.candle_patterns:
            pat = reading.candle_patterns[-1]
            setup_parts.append(
                f"شمعه: {pat.name} ({pat.direction}, قوه {pat.strength:.2f})"
            )
        if reading.key_support:
            nearest_sup = min(
                reading.key_support,
                key=lambda lv: abs(reading.price - lv.price),
            )
            setup_parts.append(
                f"دعم قريب: {nearest_sup.price:.5f} (×{nearest_sup.touches})"
            )
        if reading.key_resistance:
            nearest_res = min(
                reading.key_resistance,
                key=lambda lv: abs(lv.price - reading.price),
            )
            setup_parts.append(
                f"مقاومه قريبه: {nearest_res.price:.5f} (×{nearest_res.touches})"
            )
        unmit_ob = [ob for ob in reading.order_blocks if not ob.mitigated]
        if unmit_ob:
            recent = unmit_ob[-1]
            setup_parts.append(
                f"Order Block {recent.side}: "
                f"{recent.low:.5f}–{recent.high:.5f}"
            )
        unfilled_fvg = [f for f in reading.fair_value_gaps if not f.filled]
        if unfilled_fvg:
            recent = unfilled_fvg[-1]
            setup_parts.append(
                f"FVG {recent.side}: {recent.bottom:.5f}–{recent.top:.5f}"
            )
        if reading.micro:
            m = reading.micro
            micro_str = f"Delta {m.delta_estimate:+.2f}, Wick {m.wick_pressure:+.2f}"
            if m.absorption_score > 0.4:
                micro_str += f", Absorption {m.absorption_score:.2f}"
            if m.range_regime != "normal":
                micro_str += f", نطاق {m.range_regime}"
            setup_parts.append("Order Flow: " + micro_str)
        # New phases: traps / wyckoff / price_action / chart_patterns
        if reading.traps:
            # Only show the three strongest + most recent
            trap_cut = (reading.timestamp - __import__('pandas').Timedelta(minutes=15*15))
            recent_traps = sorted(
                [t for t in reading.traps if t.ts >= trap_cut],
                key=lambda t: -t.strength,
            )[:3]
            if recent_traps:
                dir_ar = {"bullish": "صعودي", "bearish": "هبوطي"}
                trap_lines = [
                    f"  • {t.name} {dir_ar.get(t.direction, t.direction)} "
                    f"(قوه {t.strength:.2f})"
                    for t in recent_traps
                ]
                setup_parts.append("فخاخ حديثه:\n" + "\n".join(trap_lines))
        if reading.wyckoff is not None and reading.wyckoff.phase != "unknown":
            phase_ar = {
                "accumulation": "تجميع",
                "markup": "صعود",
                "distribution": "توزيع",
                "markdown": "هبوط",
            }
            setup_parts.append(
                f"Wyckoff: {phase_ar.get(reading.wyckoff.phase, reading.wyckoff.phase)} "
                f"({reading.wyckoff.sub_phase}، ثقه {reading.wyckoff.confidence:.2f})"
            )
        if reading.pa_context is not None and reading.pa_context.best_setup:
            setup_parts.append(
                f"Brooks price-action: {reading.pa_context.best_setup}"
            )
        if reading.chart_patterns:
            best_pat = max(reading.chart_patterns, key=lambda p: p.confidence)
            dir_ar = {"bullish": "صعودي", "bearish": "هبوطي"}
            setup_parts.append(
                f"نمط كلاسيكي: {best_pat.name} "
                f"{dir_ar.get(best_pat.direction, best_pat.direction)} "
                f"(ثقه {best_pat.confidence:.2f})"
            )

        if setup_parts:
            sections.append(("الإعداد", "\n".join(setup_parts)))

        # --- Section 3.5: Algorithm awareness (Phase G) ---------
        aw = getattr(reading, "algo_awareness", None)
        if aw is not None:
            aw_parts: list[str] = []
            if aw.vwap is not None:
                regime_ar = {
                    "above_2s": "فوق +٢σ",
                    "above_1s": "فوق +١σ",
                    "inside": "داخل الحزم",
                    "below_1s": "تحت -١σ",
                    "below_2s": "تحت -٢σ",
                }.get(aw.vwap.regime, aw.vwap.regime)
                aw_parts.append(
                    f"VWAP: {aw.vwap.vwap:.5f} ({regime_ar}، "
                    f"المسافه {aw.vwap.distance_pips:+.1f} pip)"
                )
            if aw.nearby_round_numbers:
                closest = aw.nearby_round_numbers[0]
                aw_parts.append(
                    f"أقرب رقم مستدير: {closest.price:.5f} "
                    f"({closest.strength}، {closest.distance_pips:.1f} pip، "
                    f"خطر stop-hunt {closest.stop_hunt_risk:.2f})"
                )
            if aw.footprint.burst_detected:
                aw_parts.append(
                    f"⚡ burst بعد تجانس — {aw.footprint.interpretation[:70]}"
                )
            elif aw.footprint.uniform_candles:
                aw_parts.append(
                    f"خوارزمي: تجانس شموع (CV={aw.footprint.range_cv:.2f})"
                )
            if aw_parts:
                sections.append(("الخوارزميات و VWAP", "\n".join(aw_parts)))

        # --- Section 4: Confluence + contributions ---------------
        if confluence is not None:
            conf_body = (
                f"قرار: {_VERDICT_AR.get(confluence.verdict, confluence.verdict)}  "
                f"(قوه {confluence.verdict_strength:.2f})\n"
                f"شراء={confluence.long_conviction:.2f}  "
                f"بيع={confluence.short_conviction:.2f}\n"
            )
            top_factors = sorted(
                confluence.factors, key=lambda f: -abs(f.contribution),
            )[:4]
            factor_lines = []
            for f in top_factors:
                if abs(f.contribution) < 0.01:
                    continue
                arrow = "↑" if f.contribution > 0 else "↓"
                factor_lines.append(
                    f"  {arrow} {f.name}: {f.contribution:+.2f}"
                )
            if factor_lines:
                conf_body += "أهم العوامل:\n" + "\n".join(factor_lines)
            sections.append(("حُكم الـ Confluence", conf_body))

        # --- Section 5: Calibrated probability --------------------
        if calibrated is not None:
            trust = _TRUST_AR.get(calibrated.trust, calibrated.trust)
            cal_body = (
                f"الاحتماليه الخام: {calibrated.raw:.2%}\n"
                f"الاحتماليه المعايَره (من التاريخ): {calibrated.calibrated:.2%}\n"
                f"فتره الثقه ٩٥٪: "
                f"[{calibrated.ci_low:.2%}, {calibrated.ci_high:.2%}]\n"
                f"درجه الموثوقيه: {trust}  "
                f"(بيانات مرجعيه: {calibrated.n_reference})"
            )
            sections.append(("الاحتماليه المعايَره", cal_body))

        # --- Section 6: Warnings (clarity) ----------------------
        if clarity is not None:
            clarity_body_parts = [
                f"الحاله: {_CLARITY_AR.get(clarity.verdict, clarity.verdict)}",
                f"شده الإنذار: {clarity.net_severity:.2f}",
            ]
            if clarity.conflicts:
                clarity_body_parts.append(
                    f"التعارضات ({len(clarity.conflicts)}):"
                )
                for c in sorted(clarity.conflicts,
                                key=lambda x: -x.severity)[:3]:
                    clarity_body_parts.append(
                        f"  • {c.detail}"
                    )
            if clarity.anti_patterns:
                clarity_body_parts.append(
                    f"المواقف المعاديه ({len(clarity.anti_patterns)}):"
                )
                for a in sorted(clarity.anti_patterns,
                                key=lambda x: -x.severity)[:3]:
                    clarity_body_parts.append(
                        f"  • {a.detail}"
                    )
            if not clarity.conflicts and not clarity.anti_patterns:
                clarity_body_parts.append("لا تعارضات، لا مواقف معاديه.")
            sections.append(("التحذيرات", "\n".join(clarity_body_parts)))

        # --- Section 6.5: Trade plan (Phase E) -------------------
        if plan is not None:
            dir_ar = {"long": "شراء", "short": "بيع", "neutral": "محايد"}
            if plan.is_actionable:
                risk_pips = abs(plan.entry_price - plan.stop_price) / 0.0001
                reward_pips = abs(plan.target_price - plan.entry_price) / 0.0001
                plan_body = (
                    f"الإعداد: {plan.setup_type} — {dir_ar.get(plan.direction, plan.direction)}\n"
                    f"دخول: {plan.entry_price:.5f}\n"
                    f"وقف: {plan.stop_price:.5f}  ({risk_pips:.1f} pip مخاطره)\n"
                    f"هدف: {plan.target_price:.5f}  ({reward_pips:.1f} pip ربح)\n"
                    f"R:R = {plan.rr_ratio:.2f}  |  ميزانيه الوقت = {plan.time_budget_bars} شمعه\n"
                    f"ثقه الخطه: {plan.confidence:.0%}"
                )
                if plan.contingencies:
                    plan_body += "\nسيناريوهات الإبطال:\n" + "\n".join(
                        f"  • {c}" for c in plan.contingencies[:3]
                    )
                sections.append(("خطه التداول", plan_body))
            else:
                sections.append((
                    "خطه التداول",
                    f"لا خطه قابله للتنفيذ. السبب: {plan.reason_if_not}"
                ))

        # --- Section 6.7: Entry decision (Phase F) ---------------
        if entry is not None:
            order_ar = {
                "market": "أمر سوق الآن",
                "limit":  "أمر حد (Limit) منتظر",
                "stop":   "أمر إيقاف (Stop) منتظر",
                "wait":   "انتظار — لا أمر",
            }
            if entry.is_actionable:
                entry_body = (
                    f"نوع الأمر: {order_ar.get(entry.order_type, entry.order_type)}\n"
                    f"سعر التنفيذ: {entry.entry_price:.5f}\n"
                    f"المرساه: {entry.anchor}\n"
                    f"ميزانيه الانزلاق: {entry.slippage_budget_pips:.1f} pip\n"
                    f"الانزلاق المتوقع: {entry.expected_slippage_pips:.1f} pip"
                )
                if entry.order_type == "limit":
                    entry_body += f"\nصلاحيه الحد: {entry.limit_valid_for_bars} شمعه"
            else:
                entry_body = (
                    f"{order_ar.get(entry.order_type, entry.order_type)}\n"
                    f"السبب: {entry.reason_if_not}"
                )
            sections.append(("قرار الدخول", entry_body))

        # --- Section 7: Reasoning + final recommendation ---------
        reasoning = self._build_reasoning(
            reading=reading, mtf=mtf, confluence=confluence,
            calibrated=calibrated, clarity=clarity,
        )
        sections.append(("الخلاصه", reasoning))

        # --- Assemble final Arabic text ---------------------------
        arabic_parts: list[str] = [headline, ""]
        for title, body in sections:
            arabic_parts.append(f"◆ {title}")
            arabic_parts.append(body)
            arabic_parts.append("")
        arabic_text = "\n".join(arabic_parts).rstrip()

        # --- English mirror (for audit/logs only) ---------------
        english_text = self._build_english_mirror(
            reading, mtf, confluence, calibrated, clarity,
            verdict_label, conf_pct,
        )

        return Narrative(
            headline=headline,
            sections=sections,
            verdict=verdict_label,
            confidence_pct=conf_pct,
            arabic_text=arabic_text,
            english_text=english_text,
        )

    # ------------------------------------------------------------------
    # Confidence aggregation.
    # ------------------------------------------------------------------
    @staticmethod
    def _confidence_pct(confluence, calibrated, clarity) -> float:
        # If calibrated is available use it; else raw confluence.
        if clarity is not None and clarity.verdict == "abstain":
            return 0.0
        if calibrated is not None:
            return 100.0 * calibrated.calibrated
        if confluence is not None:
            return 100.0 * confluence.verdict_strength
        return 0.0

    # ------------------------------------------------------------------
    # Reasoning paragraph.
    # ------------------------------------------------------------------
    @staticmethod
    def _build_reasoning(*, reading, mtf, confluence,
                         calibrated, clarity) -> str:
        # If abstaining, skip the directional reasoning.
        if clarity is not None and clarity.verdict == "abstain":
            top_issue = None
            if clarity.anti_patterns:
                top_issue = max(clarity.anti_patterns,
                                key=lambda a: a.severity)
            elif clarity.conflicts:
                top_issue = max(clarity.conflicts, key=lambda c: c.severity)
            if top_issue is not None:
                return (
                    f"النضام يوصي بالابتعاد الآن. السبب الأهم: {top_issue.detail}"
                )
            return "النضام يوصي بالابتعاد — مؤشرات متعارضه."

        if confluence is None:
            return "لا حُكم كافي لتحديد اتجاه. قراءه عامه فقط."

        if confluence.verdict == "neutral":
            return (
                "لا توجد محاذاه كافيه بين العوامل. الانتظار هو الموقف المتّزن."
            )

        dir_ar = "شراء" if confluence.verdict == "long" else "بيع"
        reasons: list[str] = []
        if mtf is not None and abs(mtf.alignment) > 0.3:
            reasons.append(
                f"الإطارات العليا توافق ({mtf.alignment:+.2f})"
            )
        if reading.trend_direction in ("up", "down"):
            reasons.append(
                f"الاتجاه المحلي {_DIR_AR[reading.trend_direction]}"
            )
        top_factor = max(confluence.factors, key=lambda f: abs(f.contribution))
        if abs(top_factor.contribution) > 0.1:
            reasons.append(f"أقوى عامل: {top_factor.name}")

        reason_str = "، ".join(reasons) if reasons else "العوامل المتاحه"
        cal_txt = ""
        if calibrated is not None:
            cal_txt = f" الاحتماليه المعايَره {calibrated.calibrated:.0%}."
        return (
            f"النضام يوصي بـ {dir_ar}.{cal_txt} الأساس: {reason_str}."
        )

    # ------------------------------------------------------------------
    # Short English mirror for audit logs.
    # ------------------------------------------------------------------
    @staticmethod
    def _build_english_mirror(
        reading, mtf, confluence, calibrated, clarity,
        verdict_label, conf_pct,
    ) -> str:
        lines: list[str] = []
        lines.append(
            f"{reading.pair} @ {reading.price:.5f}  "
            f"verdict={verdict_label}  conf={conf_pct:.1f}%"
        )
        if clarity is not None and clarity.verdict == "abstain":
            issues = [a.name for a in clarity.anti_patterns] \
                   + [c.kind for c in clarity.conflicts]
            lines.append("ABSTAIN reasons: " + ", ".join(issues))
        if mtf is not None:
            lines.append(
                f"MTF alignment={mtf.alignment:+.2f} "
                f"dominant={mtf.dominant_tf}:{mtf.dominant_trend}"
            )
        if confluence is not None:
            lines.append(
                f"Confluence long={confluence.long_conviction:.2f} "
                f"short={confluence.short_conviction:.2f}"
            )
        if calibrated is not None:
            lines.append(
                f"Calibrated raw={calibrated.raw:.2f} "
                f"adj={calibrated.calibrated:.2f} "
                f"trust={calibrated.trust}"
            )
        return "  |  ".join(lines)

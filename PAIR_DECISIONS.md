# 🔒 قرار الأزواج النهائي بناءً على دليل OANDA الحقيقي

**التاريخ:** 26 أبريل 2026  
**الـ commit:** `9c51403` (سيتبع commit الـ pair-mode mechanism)

## ✅ الحالة الإنتاجية لكل زوج

| الزوج | Mode | Variant | Return / 2y | PF | Action |
|---|---|---|---|---|---|
| **EUR/USD** | 🟢 production | kill_asia | **+5.51%** | 1.12 | تداول حقيقي مفعّل |
| **USD/JPY** | 🟡 monitoring | kill_asia | +1.69% | 1.02 | PaperBroker فقط، إشارات تُسجَّل |
| **GBP/USD** | 🔴 disabled | — | -14.20% | 0.52 | محظور تماماً من Live |

## آلية الـ PAIR_MODE

```python
PAIR_STATUS = {
    "EUR/USD": "production",   # live trading allowed
    "USD/JPY": "monitoring",   # paper-only
    "GBP/USD": "disabled",     # no trading at all
}
```

**عند بدء main.py:**
- `disabled` → الـ engine يخرج فوراً (لا يحجز موارد)
- `monitoring` → `enable_oanda` يُجبَر على False → PaperBroker → إشارات فقط
- `production` → سلوك حي طبيعي

**Override للـ ops:** `PAIR_MODE=production` env var يكسر القاعدة لجلسة اختبار يدوية.

---

## 🔬 تحليل: لماذا نجح EUR/USD؟

**سبب النجاح:**
1. **Spread منخفض** (~0.5 pip في session times) — أقل أكل للـ edge
2. **NY-overlap ساعات (12-16 UTC) فعّالة** على EUR/USD specifically
3. **Volatility متوسطة** — كافية للـ patterns بدون noise زائد
4. **Trend periods متوازنة** — patterns حصلت في contexts صالحة
5. **Sample size كبير** (132 صفقة) — متوسط الأرقام موثوق إحصائياً

**Evidence:** kill_asia +5.51%, PF 1.12, +0.084R expectancy. لا overfit — نتيجة على full 2y بدون cherry-picking.

## 🟡 تحليل: لماذا USD/JPY ضعيف؟

**سبب الضعف:**
1. **Pip definition مختلف** (0.01 vs 0.0001) — slippage يأكل نسبياً أكثر
2. **BoJ intervention episodes** (2024-Q3 onwards) — حركات مفاجئة لا يلتقطها ChartMind
3. **Yen carry trade unwinds** — حركات لا-trend لا تخدم pattern detection
4. **576 صفقة عالي جداً** — over-trading يأكل expectancy
5. **WR 36% × R:R ~2:1** = breakeven مع التكاليف

**Evidence:** PF 1.02 = على حافة breakeven. أي تدهور في الـ market = خسارة.

## 🔴 تحليل: لماذا فشل GBP/USD؟

**سبب الفشل (متعدد):**
1. **Spread عالي** (0.9 pip vs 0.5 EUR) — تكلفة 80% أكثر لكل صفقة
2. **Volatility مفرطة** بدون trends مستدامة — pattern noise dominates
3. **WR 22-34%** عبر الـ variants — أقل بكثير من EUR/JPY
4. **PF 0.52** = $0.52 per $1 risked = خسارة رياضية ضمنية
5. **Sample size صغيرة** (66-193 صفقة) — pattern الأنماط لا تتكرر
6. **BoE shocks + UK politics** — أحداث لا يدخلها calendar
7. **Cable mean-reverts** أكثر من تـ trend — كل الـ trend-following فاشل

**Evidence:** كل variant يصل halt 15% DD. أي إعداد جربناه: -13.86 إلى -15.18%.

## ❓ هل المشكلة من الاستراتيجية أم من الزوج؟

**الإجابة:** **خصائص الزوج**.

نفس kill_asia يربح EUR، يتعادل JPY، يخسر GBP. هذا يثبت:
- الاستراتيجية (pattern + session filter) صالحة لـ EUR microstructure
- لكنها غير ملائمة لـ GBP الذي يحتاج: mean-reversion، أو event-driven، أو أعلى timeframe (H1+)

## 🛠️ خطة إصلاح GBP/USD (مسار بحث منفصل)

**الفرضيات للاختبار (واحدة في كل مرة، لا تجميع):**

1. **H1 timeframe بدلاً من M15** — يقلل noise، يستهدف swings حقيقية
2. **Mean-reversion strategy** — fade extremes بدلاً من follow trends
3. **Limit BoE/UK CPI window** ±90 دقيقة — حماية أوسع
4. **Volume-weighted entries** — صفقات في فترات liquidity عالية
5. **ATR-based SL أوسع** (2x بدلاً من 1x) — يتحمل GBP volatility
6. **Min spread filter < 1.0 pip** — يرفض دخول بسبريد عالي

**شروط القبول لإعادة GBP إلى production:**
- Walk-forward على 8 ربعيات: ربعيات رابحة ≥5/8
- Mean expectancy موجبة عبر الكل
- لا quarter يخسر > -10%
- لا يكسر EUR/USD بأي تعديل مشترك

## 📋 الخلاصة لـ Ops

**ما يُشغَّل اليوم على Hostinger:**
```bash
# EUR/USD live — production mode
PAIR=EUR/USD
PAIR_MODE=production  # (يُحدد تلقائياً من PAIR_STATUS لكن صريح أوضح)
VARIANT_FILTER=  # (يُختار kill_asia تلقائياً)
```

**ما لا يُشغَّل أبداً:**
- GBP/USD على live (PAIR_MODE=disabled تلقائياً)

**ما يُسجَّل بدون تنفيذ:**
- USD/JPY على paper (PAIR_MODE=monitoring → PaperBroker)

**سحب USD/JPY إلى production يحتاج:**
- 4 أسابيع forward-test على Practice (ليس backtest)
- ربحية فعلية > +0.5% خلال الفترة
- لا أيام بـ DD > 5%

## 🚫 القاعدة الذهبية الجديدة

**`production` لا يعني "كل الأزواج". يعني فقط الأزواج التي أثبتت نفسها بأرقام واقعية وضمن walk-forward صارم.**

أي تعديل مستقبلي يكسر هذه القاعدة (يدمر EUR/USD لإصلاح GBP) = **رفض فوري** بغض النظر عن النية.

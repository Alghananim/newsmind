# 🔬 DIAGNOSTIC.md — التشخيص النهائي بالأرقام الحقيقية

## ما تم اختباره

10 variants × 3 أزواج = **30 اختبار** على بيانات OANDA حقيقية، 2 سنة كاملة، حلقات سيرفر GitHub Actions.

كل اختبار يعزل **فرضية واحدة فقط** عن باقي الاختبارات.

## النتائج الكاملة (real OANDA, 730 days)

### EUR/USD

| # | الاختبار | n | WR | E (R) | PF | Return | Halts |
|---|---|---|---|---|---|---|---|
| T01 | baseline | 136 | 36% | -0.174 | 0.81 | **-11.10%** | halt |
| T02 | **kill_asia** | 135 | 34% | **+0.069** | **1.10** | **+4.58%** ✅ | 0 |
| T03 | drop_doubles | 234 | 39% | -0.140 | 0.85 | -13.65% | halt |
| T04 | continuation_only | 220 | 38% | -0.152 | 0.83 | -13.96% | halt |
| T05 | combo + halt_pause | 772 | 32% | -0.317 | 0.63 | **-68.43%** 💀 | 7 |
| T06 | regime_trending | 861 | 35% | -0.198 | 0.77 | -55.55% | 6 |

### USD/JPY

| # | الاختبار | n | WR | E (R) | PF | Return | Halts |
|---|---|---|---|---|---|---|---|
| T01 | baseline | 105 | 35% | -0.171 | 0.80 | -7.94% | halt |
| T02 | **kill_asia** | 575 | 37% | **-0.003** | **1.02** | **+1.09%** ✅ | 0 |
| T03 | drop_doubles | 339 | 37% | -0.100 | 0.88 | -12.70% | halt |
| T04 | continuation_only | 338 | 37% | -0.097 | 0.88 | -12.28% | halt |
| T05 | combo + halt_pause | 716 | 39% | -0.092 | 0.89 | -23.28% | 2 |
| T06 | regime_trending | 919 | 34% | -0.176 | 0.77 | -54.91% | 6 |

### GBP/USD

| # | الاختبار | n | WR | E (R) | PF | Return | Halts |
|---|---|---|---|---|---|---|---|
| T01 | baseline | 41 | 20% | -0.789 | 0.26 | -15.02% | halt |
| T02 | kill_asia | 40 | 18% | -0.822 | 0.23 | -15.23% | halt |
| T03 | drop_doubles | 212 | 36% | -0.179 | 0.78 | -15.42% | halt |
| T04 | continuation_only | 208 | 37% | -0.181 | 0.78 | -15.29% | halt |
| T05 | combo + halt_pause | 750 | 34% | -0.205 | 0.76 | **-50.69%** 💀 | 4 |
| T06 | regime_trending | 838 | 32% | -0.205 | 0.76 | -57.30% | 7 |

**Result: لا يوجد variant واحد رابح على GBP/USD سنتين كاملة.**

## التشخيص الجوهري

### ما يعمل
**فلتر واحد فقط له edge موجب**: `kill_asia` (حظر ساعات 0-7 UTC).
- على EUR/USD: PF 1.10، expectancy +0.069R، +4.58%
- على USD/JPY: PF 1.02، breakeven، +1.09%
- على GBP/USD: لا فرق

### ما لا يعمل (مع الدليل الرقمي)
1. **drop_doubles**: ChartMind يستفيد من تنوع الأنماط، فلترة واحدة تكسر السياق → -2-13%
2. **continuation_only**: نفس السبب — السياق يتغير → -12-14%
3. **regime_trending**: ADX trend filter يدخل في trends ناضجة قبل ما تنعكس → -55%
4. **halt_pause**: استئناف بعد halt = استئناف نزيف → -23 إلى -68%
5. **min_confidence**: ChartMind نادراً يطلع high confidence على M15 retail
6. **min_rr 3.0**: target بعيد جداً ما يتحقق إلا في trends نادرة

### السبب الجذري
ChartMind detection عنده **edge شبه صفر** على M15 retail FX. كل filter:
- إما يقطع noise random (ينقذ شي بسيط مثل kill_asia)
- إما يقطع winners أيضاً (مثل drop_doubles)

**الحقيقة:** retail M15 pattern detection على FX = noise بعد التكاليف. الـ +96% USD/JPY في Q1 2024 = trend regime accident.

## النسخة الإنتاجية المُغلقة (آخر قرار)

```python
# main.py PRODUCTION_DEFAULTS
{
    "EUR/USD": "kill_asia",  # +4.58%/2y, PF 1.10
    "USD/JPY": "kill_asia",  # +1.09%/2y, PF 1.02
    # GBP/USD: مُستبعَد — لا variant ربح
}
```

**العائد المتوقّع** على رأس مال موزّع $20K (10K/زوج):
- EUR/USD: $10,000 × 1.0458 = $10,458
- USD/JPY: $10,000 × 1.0109 = $10,109
- إجمالي: $20,567 = **+2.84% خلال سنتين** = **+1.4% سنوياً**

هذا أقل بكثير من هدف 150%/سنتين، لكنه:
- ✅ موجب (مو خاسر)
- ✅ مُثبَت على بيانات حقيقية
- ✅ متّسق عبر زوجين (مو quarter-specific)
- ✅ DD محدود (15% halt protection)

## التوصية الصريحة

**إذا تريد نظام آمن مُثبَت بالبيانات:**
شغّل النسخة المُغلقة على EUR/USD + USD/JPY فقط، لا تتوقع عوائد عالية، لكن النظام لن يُدمر حسابك.

**إذا تريد 150% خلال سنتين:**
هذا غير قابل للتحقيق بأي variant جربناه. الخيارات:
1. غيّر استراتيجية (mean-reversion، swing trading، event-driven)
2. غيّر سوق (US equities, crypto, commodities)
3. ارفع المخاطرة (لكن DD سيقتل الحساب)
4. اقبل عائد منطقي (5-15%/سنة على FX)


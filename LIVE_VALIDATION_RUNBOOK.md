# 🛡️ Live Validation Runbook — Engine V3

**هدف هذه المرحلة:** إثبات أن النظام يعمل صح في live بدون أخطاء.
**ليس** مرحلة تعظيم أرباح.

**المخاطرة:** 0.25% لكل صفقة (مع cap absolute 0.5%، 10% **مستحيل** بنياً).

---

## ⚠️ قبل أي شيء — Credential Safety (إلزامي)

اقرأ `CREDENTIAL_SAFETY_NOTICE.md` أولاً.
**لا تكمل** قبل:
1. ✅ revoke OANDA API key السابق + توليد جديد
2. ✅ revoke OpenAI key السابق + توليد جديد
3. ✅ revoke Telegram token + توليد جديد
4. ✅ وضع الجدد في `.env` على VPS فقط (chmod 600)

---

## الخطوة 1: نشر الكود على VPS

```bash
# على VPS (Hostinger):
cd /opt
git clone <your_repo> newsmind && cd newsmind
git checkout main
# (أو scp الملفات يدوياً لو الـ repo private)

# اتصل بالـ python:
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt   # oandapyV20, requests, python-dotenv
```

---

## الخطوة 2: إنشاء `.env` (على VPS فقط)

```bash
cp engine/v3/.env.example /opt/newsmind/.env
nano /opt/newsmind/.env
```

ضع القيم:
```
OANDA_API_KEY=<NEW_KEY>
OANDA_ACCOUNT_ID=<ID>
OANDA_ENV=practice                # ← يجب practice أولاً، NOT live
RISK_PCT_PER_TRADE=0.25
MAX_RISK_PCT_PER_TRADE=0.5
DAILY_LOSS_LIMIT_PCT=2.0
CONSECUTIVE_LOSSES_LIMIT=2
DAILY_TRADE_LIMIT=5
MAX_SPREAD_PIPS_EURUSD=1.5
MAX_SLIPPAGE_PIPS=2.0
SMARTNOTEBOOK_DIR=/opt/newsmind/notebook
```

ثم:
```bash
chmod 600 /opt/newsmind/.env
```

---

## الخطوة 3: تشغيل cert_pre_live (إلزامي)

```bash
cd /opt/newsmind
source venv/bin/activate
export $(grep -v '^#' .env | xargs)
python3 engine/v3/cert_pre_live.py
```

**النتيجة المطلوبة:** `FINAL: 15/15 PASSED` + `ALL PRE-LIVE CHECKS PASSED`.

**إذا فشل أي اختبار** → **لا تتقدم**. أصلح أولاً.

---

## الخطوة 4: تشغيل dry-run على Practice (24 ساعة)

```bash
# Practice account first
sed -i 's/^OANDA_ENV=.*/OANDA_ENV=practice/' /opt/newsmind/.env

# Run as systemd service
cp engine/v3/main_validation.py /opt/newsmind/
sudo systemctl create newsmind.service ... # template أدناه
```

`/etc/systemd/system/newsmind.service`:
```ini
[Unit]
Description=NewsMind Engine V3 Live Validation
After=network.target

[Service]
Type=simple
User=newsmind
WorkingDirectory=/opt/newsmind
EnvironmentFile=/opt/newsmind/.env
ExecStart=/opt/newsmind/venv/bin/python /opt/newsmind/main_validation.py
Restart=on-failure
RestartSec=10
StandardOutput=append:/var/log/newsmind/engine.log
StandardError=append:/var/log/newsmind/engine.err

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now newsmind.service
sudo journalctl -u newsmind -f
```

---

## الخطوة 5: مراقبة 24 ساعة على Practice

كل ساعة افحص:
```bash
# عدد القرارات
sqlite3 /opt/newsmind/notebook/notebook.db \
    "SELECT event_type, COUNT(*) FROM decision_events GROUP BY event_type;"

# الصفقات
sqlite3 /opt/newsmind/notebook/notebook.db \
    "SELECT trade_id, pair, direction, pnl, classification FROM trade_audit;"

# الأخطاء
sqlite3 /opt/newsmind/notebook/notebook.db "SELECT * FROM bugs;"
```

**علامات النجاح (24h Practice):**
- ✅ كل قرار له audit_id
- ✅ كل صفقة لها mind_outputs كامل
- ✅ المخاطرة الفعلية = 0.25% (تأكد من `risk_pct_actual` في الـ logs)
- ✅ لا صفقة على GBP/USD (disabled)
- ✅ لا صفقة على USD/JPY في live mode (paper-only)
- ✅ لا صفقة خارج NY hours (03-05 / 08-12)
- ✅ لا 2 صفقات بنفس audit_id (duplicate detection)
- ✅ daily_loss_pct لم يتجاوز 2%
- ✅ بعد خسارتين متتاليتين → cooldown مفعّل

**علامات الفشل (أوقف فوراً):**
- ✗ صفقة بمخاطرة > 0.25%
- ✗ صفقة على disabled pair
- ✗ صفقة خارج الوقت
- ✗ صفقة بدون stop_loss
- ✗ صفقة بدون take_profit
- ✗ smartnotebook لم يسجل صفقة
- ✗ broker rejection بسبب bad params
- ✗ duplicate orders
- ✗ broken position sizing

---

## الخطوة 6: تقرير Live Validation

في نهاية الـ 24h، شغّل:
```bash
python3 -c "
from smartnotebook.v3 import SmartNoteBookV3
nb = SmartNoteBookV3('/opt/newsmind/notebook')
print('=== Daily Summary ===')
for pair in ['EUR/USD','USD/JPY']:
    s = nb.daily_report(date='2026-04-XX', pair=pair)
    print(f'{pair}: trades={s.n_trades} blocked={s.n_blocked} wait={s.n_waited} pnl={s.total_pnl}')

print()
print('=== Why did we lose? ===')
for pair in ['EUR/USD','USD/JPY']:
    print(pair, nb.why_lose(pair=pair))

print()
print('=== Health ===')
print(nb.health_report())
"
```

شارك معي الـ output. سنقرر:
- متابعة على Practice أسبوع آخر (لو فيه أخطاء صغيرة)
- التقدم إلى Live بـ 0.25% (لو 0 أخطاء حرجة)
- إصلاح + إعادة validation (لو فيه bugs)

---

## الخطوة 7: Live (فقط بعد Practice ناجح)

```bash
# تأكد من Practice أولاً نجح. ثم:
sed -i 's/^OANDA_ENV=.*/OANDA_ENV=live/' /opt/newsmind/.env
sudo systemctl restart newsmind

# راقب أول 4 ساعات بشكل مكثف
sudo journalctl -u newsmind -f
```

**القاعدة:** أول live trade = أصغر حجم ممكن (units > 1). راقب filling/spread/slippage.

---

## الخطوة 8: متى نرفع المخاطرة؟

لا ترفع 0.25% → 0.5% إلا بعد:
- ✅ 5 أيام تداول كاملة بدون أخطاء حرجة
- ✅ ≥10 صفقات منفذة بنجاح
- ✅ classification يطابق المتوقع (logical_win > lucky_win)
- ✅ attribution لا ينسب الخسائر للعقل الخطأ
- ✅ 0 bugs في bug_log
- ✅ daily_loss_pct لم يصل أبداً 2%

ثم:
```bash
sed -i 's/^RISK_PCT_PER_TRADE=.*/RISK_PCT_PER_TRADE=0.5/' /opt/newsmind/.env
sudo systemctl restart newsmind
```

**لا 1% أبداً قبل أسبوعين تداول كاملين بـ 0.5%.**

---

## القواعد الذهبية

1. ❌ **ممنوع 10% نهائياً** — مرفوض بنياً (`SystemExit` على init)
2. ❌ **ممنوع تجاوز 0.5% في validation** — مرفوض بنياً
3. ❌ **ممنوع live على pair monitoring** — مرفوض بنياً
4. ❌ **ممنوع trade على disabled pair** — مرفوض بنياً
5. ❌ **ممنوع trade بدون stop_loss** — position_sizer يرفض
6. ❌ **ممنوع trade بدون SmartNoteBook** — safety_rails يرفض
7. ❌ **ممنوع trade خارج NY hours** — GateMind session_check يرفض
8. ✅ **كل صفقة لها audit_id** — uuid للتتبع
9. ✅ **كل قرار مُسجَّل** — حتى الـ blocked
10. ✅ **stop المخاطرة = 0.25%** — مُحَسَب من balance حالي

---

**التوقيع التشغيلي:** ✅ Engine V3 جاهز للـ Live Validation. 15/15 pre-live cert. Hard caps لا يمكن تجاوزها (10% مرفوض بنياً). الانتقال إلى Practice ثم Live آمن بشروط هذا الـ runbook.

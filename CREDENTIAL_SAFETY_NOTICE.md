# 🚨 إشعار أمني عاجل — Credentials مكشوفة

**التاريخ:** 26 أبريل 2026
**الخطورة:** 🔴 حرجة

---

## ما حدث

في الجلسة، شاركت 4 credentials حية في chat:
1. OANDA API key (مالي مباشر)
2. OANDA Account ID
3. OpenAI API key (خصم رصيد)
4. Telegram Bot token

**هذه ظهرت في سجل المحادثة وقد تكون قابلة للاستعادة من logs.**

---

## ⚡ خطوات إلزامية فوراً (قبل أي شيء آخر)

### 1. اقطع OANDA API key
- افتح: https://www.oanda.com/account/api
- ابحث عن المفتاح القديم → **Revoke**
- أنشئ جديد → **انسخه إلى `.env` فقط**

### 2. اقطع OpenAI key
- افتح: https://platform.openai.com/api-keys
- ابحث عن sk-proj-xYNja... → **Revoke**
- أنشئ جديد → **`.env` فقط**

### 3. revoke Telegram Bot
- افتح Telegram → @BotFather
- أرسل: `/revoke`
- اختر البوت → confirm
- أنشئ جديد عبر `/newbot` أو احتفظ بنفس البوت بـ token جديد

### 4. (اختياري لكن موصى به) راجع OANDA log
- افتح: My Account → Account Activity
- ابحث عن أي login/order غير مألوف منذ الآن

---

## القواعد الذهبية للـ credentials

| ❌ ممنوع | ✅ مطلوب |
|---|---|
| لصق key في chat | حفظه في `.env` على VPS |
| إرسال key عبر email | استعمال secret manager (1Password/Bitwarden) |
| commit `.env` في git | `.env` في `.gitignore` |
| key في source code | `os.getenv("OANDA_API_KEY")` |
| نفس key لـ paper و live | mfasul keys منفصلة لكل بيئة |

---

## كيف يستعمل النظام الـ credentials بأمان

في كود Engine V3:
```python
import os

OANDA_API_KEY = os.environ.get("OANDA_API_KEY")
OANDA_ACCOUNT_ID = os.environ.get("OANDA_ACCOUNT_ID")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")

if not (OANDA_API_KEY and OANDA_ACCOUNT_ID):
    raise SystemExit("FATAL: missing OANDA credentials in env. Refuse to start.")
```

و `.env` على VPS فقط:
```bash
# على VPS:
cat > /opt/newsmind/.env << 'EOF'
OANDA_API_KEY=<NEW_KEY_AFTER_REVOKE>
OANDA_ACCOUNT_ID=<ID>
OANDA_ENV=practice    # دائماً practice أولاً
OPENAI_API_KEY=<NEW>
TELEGRAM_TOKEN=<NEW>
EOF
chmod 600 /opt/newsmind/.env   # owner read/write فقط
```

و `.gitignore`:
```
.env
.env.*
*.key
secrets/
```

---

**لا تستمر في live validation حتى تكمل الـ 4 خطوات أعلاه.** الكود الذي سأبنيه في الخطوات التالية يفترض أن credentials جديدة في env vars، وأنا لن ألمس الـ credentials المسربة.

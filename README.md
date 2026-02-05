# Telegram Escrow Auditor V3

نظام إدارة وتحويل حسابات تيليجرام بشكل آمن مع دعم وضعين للتحويل.

## 🚀 المميزات

- ✅ جلب Recovery Email ديناميكياً من Telegram (لا يُخزّن)
- ✅ تشفير الهاش للإيميلات (S+TelegramID → HMAC-SHA256)
- ✅ وضعين للتحويل (bot_only / user_keeps_session)
- ✅ فحص Session صحيح باتصال حقيقي
- ✅ استخراج الكود من Subject و Body
- ✅ Modular Architecture (auth, sessions, admin, delivery)
- ✅ Log Bot للإشعارات على Telegram
- ✅ Migration تلقائي للداتابيز

## 📦 التثبيت

### المتطلبات
- Python 3.10+
- Git

### الخطوات

```bash
# استنساخ المشروع
git clone https://github.com/ssamy2/acc.git
cd acc

# إنشاء virtual environment
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# أو
venv\Scripts\activate  # Windows

# تثبيت المتطلبات
pip install -r requirements.txt

# إعداد متغيرات البيئة (اختياري)
cp .env.example .env
nano .env
```

### إعداد `.env`
```env
HASH_SECRET_KEY=your_super_secret_key_here
API_ID=28907635
API_HASH=fa6c3335de68283781976ae20f813f73
```

## 🏃 التشغيل

### محلياً
```bash
python run_v2.py
```

السيرفر سيعمل على: http://localhost:8001

### على VPS
راجع ملف `VPS_DEPLOYMENT.md` للتفاصيل الكاملة.

## 📚 الروابط

| الصفحة | الرابط |
|--------|--------|
| الرئيسية (إضافة حساب) | `/` |
| استلام الحسابات | `/receive` |
| API Documentation | `/docs` |
| Health Check | `/health` |

## 🔧 الـ API

### Authentication
```http
POST /api/v1/auth/init
POST /api/v1/auth/verify
```

### Account Management
```http
GET  /api/v1/account/audit/{phone}
POST /api/v1/account/finalize/{phone}
```

### Email Management
```http
GET  /api/v1/email/target/{phone}
GET  /api/v1/email/code/{phone}
POST /api/v1/email/confirm/{phone}
```

### Sessions
```http
GET  /api/v1/sessions/health/{phone}
POST /api/v1/sessions/regenerate/{phone}
```

### Delivery
```http
GET  /api/v1/accounts/ready
POST /api/v1/delivery/request-code/{phone}
POST /api/v1/delivery/confirm/{phone}
```

### Webhook
```http
POST /api3/webhook
GET  /api3/webhook/health
```

## 📖 التوثيق الكامل

- [API V3 Documentation](docs/API_V3_DOCUMENTATION.md)
- [VPS Deployment Guide](VPS_DEPLOYMENT.md)

## 🔐 الأمان

- تشفير الهاش باستخدام HMAC-SHA256
- حفظ الـ credentials في ملفات مشفرة
- CORS محمي
- Session validation

## 🛠️ الهيكلة

```
acc/
├── backend/
│   ├── api/
│   │   ├── routes.py          # Legacy API (v1)
│   │   ├── auth.py            # Authentication endpoints
│   │   ├── sessions.py        # Session management + dynamic recovery email
│   │   ├── admin.py           # Admin dashboard endpoints
│   │   ├── delivery.py        # Delivery flow endpoints
│   │   └── webhook_routes.py  # Email webhook (subject + body extraction)
│   ├── core_engine/
│   │   ├── pyrogram_client.py # Pyrogram session manager
│   │   ├── telethon_client.py # Telethon session manager
│   │   └── credentials_logger.py
│   ├── models/
│   │   └── database.py        # SQLAlchemy models
│   ├── services/
│   │   └── security_audit.py
│   └── log_bot.py             # Telegram notification bot
├── frontend/
│   ├── index_main.html        # الصفحة الرئيسية
│   ├── receive.html           # صفحة الاستلام (+ live recovery email)
│   └── style_v3.css
├── run_v2.py                  # Entry point
└── requirements.txt
```

## 🔄 Recovery Email Flow

```
1. Frontend يعرض الحساب
2. API يجلب recovery email مباشرة من Telegram
3. لا يتم تخزين الإيميل في قاعدة البيانات
4. يتم عرض الإيميل الحالي مع حالته (confirmed/pending/none)
```

## 📧 Email Hash Logic

```
Hash = HMAC-SHA256("S" + telegram_id, secret_key)
Email = email-for-{hash}@channelsseller.site
```

## 🐛 حل المشاكل

### خطأ في الاتصال بالـ API
```bash
# تأكد من تشغيل السيرفر
ps aux | grep python
```

### خطأ في الداتابيز
```bash
# تشغيل Migration يدوياً
python migrate_all_columns.py
```

### CORS Error
تأكد من أن الدومين مضاف في `backend/main_v2.py`:
```python
allow_origins=[
    "https://yourdomain.com",
    "*"
]
```

## 📝 الترخيص

هذا المشروع خاص.

## 👨‍💻 المطور

[@ssamy2](https://github.com/ssamy2)

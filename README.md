# Telegram Escrow Auditor V3

نظام إدارة وتحويل حسابات تيليجرام بشكل آمن مع دعم وضعين للتحويل.

## 🚀 المميزات

- ✅ تشفير الهاش للإيميلات (HMAC-SHA256)
- ✅ وضعين للتحويل (bot_only / user_keeps_session)
- ✅ إدارة الإيميل والتحقق من الكود
- ✅ مراقبة صحة الجلسات
- ✅ نظام التسليم مع fallback تلقائي
- ✅ Migration تلقائي للداتابيز
- ✅ صفحة استلام للمشتري

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
│   │   ├── routes_v3.py       # API الرئيسي
│   │   └── webhook_routes.py  # Email webhook
│   ├── core_engine/
│   │   ├── pyrogram_client.py
│   │   ├── telethon_client.py
│   │   └── credentials_logger.py
│   ├── models/
│   │   └── database.py
│   └── services/
│       ├── security_audit.py
│       └── delivery_service.py
├── frontend/
│   ├── index_v3.html          # الصفحة الرئيسية
│   ├── receive.html           # صفحة الاستلام
│   ├── app_v3.js
│   └── style_v3.css
├── migrate_all_columns.py     # Migration تلقائي
├── run_v2.py                  # نقطة التشغيل
└── requirements.txt
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

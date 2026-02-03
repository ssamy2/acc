# دليل تشغيل المشروع على VPS

## المتطلبات الأساسية
- Ubuntu 20.04+ أو أي Linux distribution
- Python 3.10+
- Nginx
- SSL Certificate (Let's Encrypt)

---

## الخطوة 1: تحضير السيرفر

```bash
# تحديث النظام
sudo apt update && sudo apt upgrade -y

# تثبيت Python والأدوات المطلوبة
sudo apt install python3 python3-pip python3-venv git screen nginx -y
```

---

## الخطوة 2: استنساخ المشروع

```bash
cd /home
git clone https://github.com/ssamy2/acc.git
cd acc
```

---

## الخطوة 3: إعداد البيئة الافتراضية

```bash
# إنشاء virtual environment
python3 -m venv venv

# تفعيلها
source venv/bin/activate

# تثبيت المتطلبات
pip install -r requirements.txt
```

---

## الخطوة 4: إعداد TDLib (اختياري)

```bash
# تحميل TDLib للـ Linux
mkdir -p tdlib/linux
cd tdlib/linux

# تحميل من الموقع الرسمي أو بناء من المصدر
# https://core.telegram.org/tdlib/docs/

cd /home/acc
```

---

## الخطوة 5: إعداد متغيرات البيئة

```bash
# إنشاء ملف .env
nano .env
```

أضف المحتوى التالي:
```env
HASH_SECRET_KEY=your_super_secret_key_here_change_it
API_ID=28907635
API_HASH=fa6c3335de68283781976ae20f813f73
```

---

## الخطوة 6: إعداد Nginx

```bash
sudo nano /etc/nginx/sites-available/acc
```

أضف الإعدادات التالية:

```nginx
server {
    server_name acctest.channelsseller.site;

    # API الرئيسي (V3) - بورت 8001
    location /api/v1 {
        proxy_pass http://localhost:8001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Email Webhook - بورت 8001 (نفس السيرفر)
    location /api3 {
        proxy_pass http://localhost:8001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Frontend والصفحات الثابتة
    location / {
        proxy_pass http://localhost:8001;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # SSL - سيتم إضافته بواسطة Certbot
    listen 80;
}
```

```bash
# تفعيل الموقع
sudo ln -s /etc/nginx/sites-available/acc /etc/nginx/sites-enabled/

# اختبار الإعدادات
sudo nginx -t

# إعادة تشغيل Nginx
sudo systemctl restart nginx
```

---

## الخطوة 7: إعداد SSL (Let's Encrypt)

```bash
# تثبيت Certbot
sudo apt install certbot python3-certbot-nginx -y

# الحصول على شهادة SSL
sudo certbot --nginx -d acctest.channelsseller.site

# التجديد التلقائي (يتم تلقائياً)
sudo certbot renew --dry-run
```

---

## الخطوة 8: تشغيل التطبيق

### استخدام Screen (موصى به)

```bash
# إنشاء screen جديد للتطبيق
screen -S escrow

# تفعيل البيئة الافتراضية
cd /home/acc
source venv/bin/activate

# تشغيل السيرفر
python run_v2.py

# للخروج من Screen مع إبقائه يعمل: Ctrl+A ثم D
# للعودة إليه: screen -r escrow
```

### أو استخدام systemd (للإنتاج)

```bash
sudo nano /etc/systemd/system/escrow.service
```

```ini
[Unit]
Description=Telegram Escrow Auditor
After=network.target

[Service]
User=root
WorkingDirectory=/home/acc
Environment="PATH=/home/acc/venv/bin"
ExecStart=/home/acc/venv/bin/python run_v2.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
# تفعيل وتشغيل الخدمة
sudo systemctl daemon-reload
sudo systemctl enable escrow
sudo systemctl start escrow

# مراقبة اللوجات
sudo journalctl -u escrow -f
```

---

## عدد الـ Screens المطلوبة

### الحد الأدنى: **Screen واحد فقط** ✅

```bash
screen -S escrow    # التطبيق الرئيسي (يشمل API + Webhook + Frontend)
```

السيرفر الواحد (`run_v2.py`) يشغل كل شيء:
- `/api/v1/*` - API الرئيسي
- `/api3/*` - Email Webhook
- `/` - Frontend

---

## الملفات المهمة

| الملف | الوظيفة |
|-------|---------|
| `run_v2.py` | نقطة الدخول الرئيسية |
| `backend/main_v2.py` | تطبيق FastAPI |
| `backend/api/routes_v3.py` | API Endpoints |
| `backend/api/webhook_routes.py` | Email Webhook |
| `frontend/index_v3.html` | الواجهة الأمامية |

---

## الأوامر المفيدة

```bash
# مراقبة اللوجات
tail -f logs/app_*.log

# التحقق من حالة Nginx
sudo systemctl status nginx

# التحقق من البورتات
sudo netstat -tlnp | grep -E '8001|80|443'

# إعادة تشغيل التطبيق
screen -r escrow
# Ctrl+C لإيقافه
python run_v2.py
```

---

## إعداد Cloudflare Email Worker

للـ Webhook، تحتاج إعداد Cloudflare Email Worker ليرسل الإيميلات إلى:
```
https://acctest.channelsseller.site/api3/webhook
```

### مثال Worker:
```javascript
export default {
  async email(message, env) {
    const to = message.to;
    const from = message.from;
    const subject = message.headers.get("subject") || "";
    
    // قراءة محتوى الإيميل
    const body = await new Response(message.raw).text();
    
    // استخراج الـ hash من الإيميل
    const hashMatch = to.match(/email-for-([^@]+)@/);
    const hash = hashMatch ? hashMatch[1] : "";
    
    // إرسال للـ webhook
    await fetch("https://acctest.channelsseller.site/api3/webhook", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        from: from,
        to: to,
        hash: hash,
        subject: subject,
        body: body
      })
    });
  }
}
```

---

## التحقق من عمل النظام

```bash
# اختبار الـ API
curl https://acctest.channelsseller.site/api/v1/docs/internal

# اختبار الـ Webhook
curl https://acctest.channelsseller.site/api3/webhook/health

# اختبار الـ Frontend
curl https://acctest.channelsseller.site/
```

---

## حل المشاكل الشائعة

### 1. خطأ في الاتصال بالـ API
```bash
# تأكد من أن التطبيق يعمل
ps aux | grep python

# تأكد من البورت
sudo netstat -tlnp | grep 8001
```

### 2. خطأ SSL
```bash
# تجديد الشهادة
sudo certbot renew
```

### 3. خطأ في Nginx
```bash
# فحص الأخطاء
sudo nginx -t
sudo tail -f /var/log/nginx/error.log
```

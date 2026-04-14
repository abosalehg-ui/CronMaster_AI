# 🤖 CronMaster_AI

مدير ومراقب ذكي لـ **OpenClaw Cron Jobs** مع إصلاح تلقائي.

---

## ✨ المميزات

| الميزة | الوصف |
|--------|-------|
| 🔍 **مراقبة OpenClaw** | يقرأ حالة جميع الـ cron jobs مباشرة |
| 🔧 **إصلاح تلقائي** | يزيد timeout تلقائياً عند الفشل |
| 🔄 **إعادة تشغيل** | يعيد تشغيل المهمة بعد الإصلاح |
| 📢 **تنبيهات Telegram** | يرسل تنبيه فوري عند الفشل |
| 📊 **تقارير** | تقارير Markdown و JSON |

---

## 🚀 التثبيت

```bash
# لا يحتاج تثبيت — يعمل مباشرة مع OpenClaw
git clone https://github.com/abosalehg-ui/CronMaster_AI.git
cd CronMaster_AI
```

### المتطلبات
- Python 3.8+
- OpenClaw CLI مثبت ومُعد

---

## 📖 الاستخدام

### حالة سريعة
```bash
python3 CronMaster_AI.py status
```
```
========================================
📊 حالة OpenClaw Cron Jobs
========================================
إجمالي المهام:   8
ناجحة ✅:        7
فاشلة ❌:        1
حرجة ⚠️:         0
نسبة النجاح:     87.5%
========================================
```

### مراقبة وإصلاح تلقائي
```bash
# مراقبة + إصلاح + تنبيه + إعادة تشغيل
python3 CronMaster_AI.py monitor

# مراقبة بدون إصلاح
python3 CronMaster_AI.py monitor --no-fix

# بدون تنبيهات
python3 CronMaster_AI.py monitor --no-alert

# بدون إعادة تشغيل
python3 CronMaster_AI.py monitor --no-retry
```

### توليد تقرير
```bash
# Markdown
python3 CronMaster_AI.py report

# JSON
python3 CronMaster_AI.py report --format json
```

### قائمة المهام
```bash
python3 CronMaster_AI.py list
```

### إصلاح مهمة محددة
```bash
python3 CronMaster_AI.py fix <job_id>
```

---

## 🔧 الإصلاح التلقائي

عند فشل مهمة بسبب **timeout**:

1. ✅ يكتشف الخطأ تلقائياً
2. ✅ يزيد timeout بـ 60 ثانية (حد أقصى 300s)
3. ✅ يعيد تشغيل المهمة
4. ✅ يرسل تنبيه Telegram بالإصلاح

### أنواع الأخطاء المدعومة

| النوع | قابل للإصلاح التلقائي |
|-------|----------------------|
| `timeout` | ✅ نعم |
| `permission_denied` | ❌ لا |
| `not_found` | ❌ لا |
| `dependency_error` | ❌ لا |
| `network_error` | ❌ لا |
| `api_error` | ❌ لا |

---

## ⏰ الجدولة مع OpenClaw

```bash
# مراقبة كل 12 ساعة
openclaw cron add \
  --cron "0 */12 * * *" \
  --tz "Asia/Riyadh" \
  --name "CronMaster - مراقبة" \
  --message "python3 /path/to/CronMaster_AI.py monitor" \
  --channel telegram \
  --to YOUR_CHAT_ID \
  --announce
```

---

## 📁 هيكل الملفات

```
~/.cronmaster/
├── cronmaster.log    # سجل العمليات
├── state.json        # حالة الإصلاحات
├── backups/          # النسخ الاحتياطية
└── reports/          # التقارير المولدة
```

---

## ⚙️ الإعدادات

في `CronMaster_AI.py` > `class Config`:

```python
ALERT_THRESHOLD = 2      # فشل متتالي قبل التنبيه
TIMEOUT_INCREMENT = 60   # زيادة timeout (ثواني)
MAX_TIMEOUT = 300        # حد أقصى timeout
AUTO_RETRY = True        # إعادة تشغيل بعد الإصلاح
TELEGRAM_CHAT_ID = "..." # Chat ID للتنبيهات
```

---

## 📜 الترخيص

MIT License

---

**المطور:** Pipbot 🤖  
**لـ:** عبدالكريم  
**آخر تحديث:** 2026-04-14

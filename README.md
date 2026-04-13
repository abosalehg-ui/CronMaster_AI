# 🤖 CronMaster_AI

مدير ومراقب ذكي للمهام المجدولة (Cron Jobs) مع قدرات تحليل وإصلاح ذاتي.

---

## 📋 المتطلبات

```bash
# Python 3.8+
python3 --version

# المكتبات المطلوبة (اختيارية للميزات المتقدمة)
pip install requests
```

---

## 🚀 التثبيت والتشغيل

### 1. الصلاحيات

```bash
# جعل السكربت قابل للتنفيذ
chmod +x CronMaster_AI.py

# للوصول لسجلات النظام، تحتاج صلاحيات قراءة
# إما تشغيل كـ root أو إضافة المستخدم لمجموعة adm
sudo usermod -a -G adm $USER
# ثم أعد تسجيل الدخول
```

### 2. متغيرات البيئة (اختياري - للتنبيهات)

```bash
# Telegram
export TELEGRAM_BOT_TOKEN="your_bot_token"
export TELEGRAM_CHAT_ID="6803381"

# Slack
export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."

# Discord
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."

# Brave Search (للبحث عن حلول)
export BRAVE_API_KEY="your_api_key"
```

---

## 📖 الاستخدام

### مراقبة التنفيذات

```bash
# مراقبة آخر 24 ساعة
python3 CronMaster_AI.py monitor

# مراقبة آخر 48 ساعة مع تفاصيل
python3 CronMaster_AI.py monitor --hours 48 --verbose
```

### تحليل فشل مهمة

```bash
# تحليل آخر فشل
python3 CronMaster_AI.py analyze

# تحليل مهمة محددة (بالبحث في الاسم)
python3 CronMaster_AI.py analyze --command "backup"

# بدون بحث ويب
python3 CronMaster_AI.py analyze --no-web
```

### توليد التقارير

```bash
# تقرير Markdown
python3 CronMaster_AI.py report

# تقرير JSON
python3 CronMaster_AI.py report --format json
```

### النسخ الاحتياطي والاستعادة

```bash
# عرض النسخ الاحتياطية
python3 CronMaster_AI.py backups

# استعادة نسخة
python3 CronMaster_AI.py restore ~/.cronmaster/backups/crontab_20260413_120000.bak
```

### اختبار تجريبي (Dry-Run)

```bash
# اختبار أمر قبل جدولته
python3 CronMaster_AI.py test "python3 /path/to/script.py"

# مع مهلة مخصصة
python3 CronMaster_AI.py test "long_running_script.sh" --timeout 120
```

### مقارنة الملفات

```bash
# عرض الفروق بين نسختين
python3 CronMaster_AI.py diff old_script.py new_script.py
```

### حالة النظام

```bash
python3 CronMaster_AI.py status
```

---

## 📁 هيكل الملفات

```
~/.cronmaster/
├── backups/           # النسخ الاحتياطية
├── reports/           # التقارير المولدة
├── state.json         # حالة المهام
└── cronmaster.log     # سجل النظام
```

---

## 🔄 الجدولة التلقائية

### مراقبة كل ساعة

```bash
# أضف للـ crontab
crontab -e

# أضف السطر:
0 * * * * /usr/bin/python3 /path/to/CronMaster_AI.py monitor >> ~/.cronmaster/hourly.log 2>&1
```

### تقرير أسبوعي كل جمعة

```bash
# الساعة 9 مساءً كل جمعة
0 21 * * 5 /usr/bin/python3 /path/to/CronMaster_AI.py report >> ~/.cronmaster/weekly.log 2>&1
```

---

## 🔔 التنبيهات

النظام يرسل تنبيه تلقائي عندما:
- مهمة تفشل **3 مرات متتالية** أو أكثر
- يتم اكتشاف خطأ حرج

### إعداد Telegram Bot

1. تحدث مع [@BotFather](https://t.me/BotFather)
2. أنشئ بوت جديد واحصل على Token
3. اعرف Chat ID الخاص بك
4. أضف المتغيرات للبيئة

---

## 🧠 أنواع الأخطاء المدعومة

| النوع | الوصف | مثال |
|-------|-------|------|
| `permission_denied` | نقص صلاحيات | `EACCES` |
| `timeout` | انتهاء المهلة | `deadline exceeded` |
| `not_found` | ملف/أمر غير موجود | `command not found` |
| `dependency_error` | مكتبة ناقصة | `ModuleNotFoundError` |
| `syntax_error` | خطأ نحوي | `SyntaxError` |
| `network_error` | مشكلة شبكة | `Connection refused` |
| `disk_full` | قرص ممتلئ | `No space left` |
| `memory_error` | نفاد الذاكرة | `MemoryError` |

---

## 📊 مثال على تقرير

```markdown
# 📊 تقرير CronMaster الأسبوعي

## 📈 الإحصائيات العامة

| المقياس | القيمة |
|---------|--------|
| إجمالي المهام | 15 |
| إجمالي التنفيذات | 168 |
| نسبة النجاح | 97.6% |
| المهام الحرجة | 1 |

## ❌ المهام الفاشلة (4)

### 1. `backup_database.sh`
- **نوع الخطأ:** permission_denied
- **التحليل:** نقص في الصلاحيات
- **الحل المقترح:** تحقق من صلاحيات الملف
```

---

## 🛠️ التطوير

### إضافة نوع خطأ جديد

```python
# في ERROR_DATABASE
ErrorSignature(
    pattern=r"your_error_pattern",
    error_type=ErrorType.YOUR_TYPE,
    description_ar="وصف بالعربي",
    suggested_fix="الحل المقترح"
)
```

### إضافة قناة تنبيه جديدة

```python
# في AlertManager
def _send_custom(self, message: str) -> bool:
    # تنفيذك هنا
    pass
```

---

## 📝 ملاحظات

- السكربت يعمل مع سجلات syslog/cron القياسية
- للأنظمة التي تستخدم journald، قد تحتاج تعديل `LogParser`
- النسخ الاحتياطي يحفظ آخر 50 نسخة افتراضياً (عدّل حسب الحاجة)

---

## 📜 الترخيص

MIT License - استخدم كما تشاء!

---

**المطور:** Pipbot 🤖
**لـ:** عبدالكريم
**التاريخ:** 2026-04-13

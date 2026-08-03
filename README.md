# 🤖 CronMaster_AI

مدير ومراقب ذكي **للمهام المجدولة** مع إصلاح تلقائي وسجل تاريخي.

الواجهة الافتراضية هي **OpenClaw Cron**، ويدعم أيضاً **crontab النظام**.

---

## ✨ ما الجديد في الإصدار 3

| الجديد | الوصف |
|--------|-------|
| 📈 **سجل تاريخي** | قاعدة SQLite في `~/.cronmaster/history.db` تحفظ كل دورة مراقبة، ومنها أمر `stats` وتقرير HTML |
| ⏱️ **تحذير مبكر من التباطؤ** | ينبّهك حين تتضاعف مدة المهمة **قبل** أن تتحول إلى انتهاء مهلة |
| 🛑 **حارس الإصلاح غير المجدي** | يتوقف عن رفع المهلة بعد `max_timeout_fixes` ويصعّد بدل أن يؤجل المشكلة |
| 🔒 **قفل تنفيذ** | دورتا مراقبة متزامنتان لا تتسابقان على الحالة ولا تُكرران الإصلاح |
| ♻️ **`restore`** | النسخ الاحتياطية صارت قابلة للاستخدام فعلاً |
| 🩺 **`doctor`** | فحص ذاتي شامل قبل أن تعتمد على الأداة |
| ⛔ **قاطع الدائرة والتراجع وإعادة الجدولة** | إصلاحات أوسع، كلٌّ منها خلف إعداد مستقل ومعطّل افتراضياً |
| 📢 **قنوات تنبيه متعددة** | Telegram وWebhook وSlack وDiscord، مع **فترة هدوء** وملخص مؤجَّل |
| 🌐 **تقرير HTML** | صفحة واحدة مكتفية بذاتها، RTL، رسوم SVG يدوية، تدعم الوضع الداكن |
| 📊 **تكامل Prometheus وhealthchecks** | ملف نصي ذرّي ونبضة نجاح/فشل |
| 🤖 **مصنّف أخطاء اختياري** | يشرح الأخطاء التي لا يعرفها أي نمط، عبر Anthropic SDK (اختياري تماماً) |
| 🧩 **بنية حزمة** | الكود صار حزمة `cronmaster/`، و`CronMaster_AI.py` غلاف توافق رفيع |
| 🌍 **`--lang en`** | مخرجات إنجليزية اختيارية؛ العربية تبقى الافتراضية بلا أي تغيير |

> **التوافق:** كل أمر وعلم ومفتاح إعدادات ومتغير بيئة من الإصدار السابق يعمل كما هو.
> `state.json` القديم يُقرأ ويُرقّى تلقائياً بلا فقدان بيانات. النواة ما زالت
> **بلا أي اعتمادية خارجية**.

---

## 🚀 التثبيت

```bash
git clone https://github.com/abosalehg-ui/CronMaster_AI.git
cd CronMaster_AI

# يعمل مباشرة بلا تثبيت (مكتبة Python القياسية فقط)
python3 CronMaster_AI.py status
```

أو تثبيته كحزمة ليصبح الأمر `cronmaster` متاحاً:

```bash
pip install .
cronmaster status
```

### المتطلبات
- Python 3.8+
- واجهة خلفية: OpenClaw CLI (افتراضي) أو `crontab`

### الإضافة الاختيارية `[ai]`

```bash
pip install '.[ai]'          # يثبّت anthropic
export ANTHROPIC_API_KEY=... # لا يُكتب أبداً في أي ملف تُنتجه الأداة
```

ثم فعّله في `~/.cronmaster/config.json`:

```json
{ "llm_enabled": true }
```

بدون هذه الإضافة (أو بدون مفتاح) تعمل الأداة كما هي تماماً، مع سطر INFO واحد
يوضّح أن التصنيف سيبقى نمطياً.

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
صامتة 😴:        0
نسبة النجاح:     87.5%
========================================
```

> إذا تعذّر التواصل مع الواجهة الخلفية نفسها، تفشل الأداة **بصوت عالٍ** (رسالة خطأ وكود خروج 1) — لن ترى أبداً "نجاح 100%" زائفاً.

### مراقبة وإصلاح تلقائي
```bash
python3 CronMaster_AI.py monitor                       # مراقبة + إصلاح + تنبيه + إعادة تشغيل
python3 CronMaster_AI.py monitor --dry-run             # عرض ما سيحدث دون أي تنفيذ
python3 CronMaster_AI.py monitor --no-fix              # مراقبة بدون إصلاح
python3 CronMaster_AI.py monitor --no-alert            # بدون تنبيهات
python3 CronMaster_AI.py monitor --no-retry            # بدون إعادة تشغيل
python3 CronMaster_AI.py monitor --prometheus-textfile /var/lib/node_exporter/cronmaster.prom
```

### إحصائيات من السجل التاريخي
```bash
python3 CronMaster_AI.py stats
python3 CronMaster_AI.py stats --job <job_id> --days 14
```

### التقارير
```bash
python3 CronMaster_AI.py report              # Markdown
python3 CronMaster_AI.py report -f json      # JSON
python3 CronMaster_AI.py report -f html      # صفحة HTML مكتفية بذاتها
```

### استعادة مهمة من نسخة احتياطية
```bash
python3 CronMaster_AI.py restore <job_id> --list          # عرض النسخ المتاحة
python3 CronMaster_AI.py restore <job_id>                 # الأحدث، مع سؤال تأكيد
python3 CronMaster_AI.py restore <job_id> --backup <file> --yes
```

### فحص ذاتي
```bash
python3 CronMaster_AI.py doctor    # كود خروج غير صفري عند أي فشل جوهري
```

### بقية الأوامر
```bash
python3 CronMaster_AI.py list                  # قائمة المهام
python3 CronMaster_AI.py fix <job_id>          # إصلاح مهمة محددة
python3 CronMaster_AI.py history --limit 20    # سجل الإصلاحات والتنبيهات
python3 CronMaster_AI.py --version
python3 CronMaster_AI.py --lang en status      # مخرجات إنجليزية
```

---

## 🔢 أكواد الخروج

| الأمر | الكود | المعنى |
|-------|:-----:|--------|
| `monitor` | `0` | كل شيء سليم |
| `monitor` | `1` | فشل المراقب نفسه (الواجهة الخلفية لا تستجيب) |
| `monitor` | `2` | مهام فاشلة، لكن المراقب سليم |
| `monitor` | `3` | طُبِّقت إصلاحات في هذه الدورة |
| `doctor` | `0` / `1` | كل الفحوص الجوهرية سليمة / فشل فحص جوهري |
| البقية | `0` / `1` | نجاح / فشل التواصل مع الواجهة الخلفية |

> **مهم للجدولة:** `monitor` يعيد `2` و`3` في حالات ليست أخطاءً. إن كان مشغّلك
> يعتبر أي كود غير صفري فشلاً، استخدم `|| true` أو عالج الأكواد صراحة.

عند وجود دورة مراقبة أخرى قيد التنفيذ، تخرج النسخة الثانية بكود `0` مع سطر واضح
في السجل — التداخل ليس خطأً.

---

## 🔌 اختيار الواجهة الخلفية

| الواجهة | القدرات | ملاحظات |
|---------|---------|---------|
| `openclaw` (افتراضي) | كل شيء: مهلة، تشغيل، تفعيل، جدولة، حالة آخر تشغيل، مدة | السلوك التاريخي دون تغيير |
| `crontab` | قراءة، تشغيل، تفعيل/تعطيل، جدولة | **لا** مهلة لكل مهمة و**لا** حالة آخر تشغيل — يتدرّج المنسّق بلطف ويسجّل "غير مدعوم" |

```bash
python3 CronMaster_AI.py --backend crontab list
```
أو `{"backend": "crontab"}` في الإعدادات، أو `CRONMASTER_BACKEND=crontab`.

---

## 🔧 الإصلاح التلقائي

عند فشل مهمة بسبب **timeout**:

1. ✅ يكتشف الخطأ تلقائياً
2. 💾 يحفظ نسخة احتياطية من تعريف المهمة في `~/.cronmaster/backups/` (بأذونات `0600`)
3. ✅ يزيد timeout بـ 120 ثانية (حد أقصى 900s)
4. ✅ يعيد تشغيل المهمة
5. ✅ يرسل تنبيهاً بالإصلاح

### أنواع الأخطاء المدعومة

| النوع | قابل للإصلاح التلقائي | آلية الإصلاح |
|-------|:---:|--------------|
| `timeout` | ✅ نعم | زيادة المهلة تدريجياً (مع نسخة احتياطية) ثم إعادة التشغيل |
| `network_error` | ✅ نعم | إعادة محاولة **فورية مسقوفة** (خطأ عابر غالباً) |
| `api_error` (429) | ✅ نعم | إعادة محاولة مسقوفة **بعد فترة تهدئة** (احتراماً لحد الطلبات) |
| `permission_denied` | ❌ لا | يحتاج تدخلاً — تصحيح الصلاحيات |
| `not_found` | ❌ لا | يحتاج تدخلاً — تصحيح المسار/الأمر |
| `dependency_error` | ❌ لا | يحتاج تدخلاً — تثبيت المكتبة الناقصة |
| `syntax_error` | ❌ لا | يحتاج تدخلاً — تعديل الكود |
| `memory_error` | ❌ لا | يحتاج تدخلاً — تقليل الحمل/زيادة الذاكرة |
| `disk_full` | ❌ لا | يحتاج تدخلاً — تفريغ مساحة |

> **لماذا لا تُصلَح البقية تلقائياً؟** لأن إصلاحها يتطلب إما تخميناً غير آمن (المسار الصحيح، اسم الحزمة)، أو تعديل كود المستخدم، أو تغيير بيئة النظام — وهذه قرارات بشرية. المبدأ: الأداة تُصلح **إعدادات المهمة** لا **كود المستخدم** ولا **بيئة النظام**.

> أخطاء الشبكة التي تحتوي كلمة "timed out" (مثل `Connection timed out`) تُصنَّف **شبكة** لا مهلة، فتُعاد المحاولة بدل رفع timeout المهمة بلا جدوى.

### 🔁 إعادة المحاولة للأخطاء العابرة (network / api)

1. **حد أقصى للمحاولات** (`max_retries`، افتراضياً 3): بعد استنفادها تتوقف الأداة وتصعّد.
2. **عدّاد لكل مهمة** يُخزَّن في `state.json` ويُصفَّر عند **تعافي** المهمة.
3. **فرق بين النوعين:** `network_error` فوري؛ `api_error` ينتظر `retry_backoff_hours`.

### 🛑 حارس الإصلاح غير المجدي

رفع المهلة مرة بعد مرة بلا نتيجة ليس إصلاحاً بل تأجيلاً. بعد `max_timeout_fixes`
(افتراضياً 3) رفعات على مهمة ما تزال تفشل بانتهاء المهلة، تتوقف الأداة عن الرفع
وتصعّد تنبيهاً صريحاً بأن المشكلة تحتاج إنساناً لا مزيداً من الثواني.

### ⏱️ التحذير المبكر من التباطؤ

يقارن السجل التاريخي متوسط المدة الأخيرة بخط الأساس؛ إذا تجاوزها بمعامل
`duration_regression_factor` (افتراضياً 2.0) مع عينات كافية، يصلك تحذير **قبل**
أن يتحول التباطؤ إلى فشل.

### ⛔ الإصلاحات الموسّعة (معطّلة افتراضياً)

| الإصلاح | الإعداد | ما يفعله |
|---------|---------|----------|
| **قاطع الدائرة** | `circuit_breaker_enabled` + `circuit_breaker_threshold` (10) | بعد فشل متتالٍ طويل بخطأ غير قابل للإصلاح: نسخة احتياطية ثم تعطيل المهمة وتنبيه حرج يشرح كيفية إعادة التفعيل |
| **التراجع** | `rollback_timeout_enabled` + `rollback_after_cycles` (2) | إن لم يُجدِ رفع المهلة، يعيدها إلى قيمتها السابقة ويصعّد |
| **إعادة الجدولة** | `auto_reschedule` + `reschedule_shift_minutes` (17) | عند تكرار أخطاء 429، يزيح دقيقة التشغيل بعيداً عن نافذة الازدحام (يقترح فقط ما لم يُفعَّل) |

كلها تظهر في `history` وفي التقارير وفي نص التنبيه، وكلها تحترم `--dry-run`.

---

## 📢 التنبيهات

- يُرسل التنبيه عند بلوغ `alert_threshold` (افتراضياً 2)، أو فور تطبيق إصلاح تلقائي.
- لا يتكرر نفس التنبيه (نفس المهمة ونفس نوع الخطأ) قبل `alert_cooldown_hours`.
- عند تعافي مهمة يصلك إشعار تعافٍ ويُصفَّر سجل تنبيهاتها وعدّاداتها.

### القنوات

```json
{
  "notifiers": [
    { "type": "telegram", "chat_id": "123456" },
    { "type": "slack",    "url": "https://hooks.slack.com/services/..." },
    { "type": "discord",  "url": "https://discord.com/api/webhooks/..." },
    { "type": "webhook",  "url": "https://example.com/hook", "field": "text" }
  ]
}
```

`telegram_chat_id` وحده ما زال اختصاراً صالحاً لقناة Telegram واحدة.
فشل قناة لا يمنع البقية، والتنبيه يُعتبر ناجحاً إذا نجحت **قناة واحدة على الأقل**.

### فترة الهدوء

```json
{ "quiet_hours": { "from": "22:00", "to": "07:00", "tz": "Asia/Riyadh" } }
```

خلالها تُؤجَّل التنبيهات غير الحرجة وتُرسَل **ملخصاً واحداً** في أول دورة بعدها.
التنبيهات الحرجة (فشل المراقب نفسه، قاطع الدائرة) تخرج فوراً دائماً.

---

## 🤖 المصنّف الاختياري بالذكاء الاصطناعي

الأخطاء التي لا يعرفها أي نمط كانت تنتهي إلى `unknown` ورسالة "راجع السجلات".
عند تفعيل `llm_enabled` تُرسَل تلك الحالات **فقط** إلى Anthropic للتشخيص.

**الضمانات:**

- المصنّف النمطي يعمل **أولاً ودائماً**؛ النموذج لا يُستشار إلا عند `unknown`.
- استيراد كسول: غياب الحزمة أو المفتاح = سطر INFO واحد وسقوط إلى نتيجة regex.
- **ذاكرة مؤقتة** بمفتاح `sha256` لنص الخطأ بعد التطبيع: الخطأ المتكرر يُدفع ثمنه مرة واحدة (`llm_cache_days`، افتراضياً 30 يوماً).
- **لا صلاحية للإصلاح:** أقصى ما يمنحه النموذج هو تشخيص وحلّ مقترح وتلميح "خطأ عابر". فتح مسار إعادة المحاولة المسقوف يتطلب `confidence >= llm_min_confidence` (0.8)، و`max_retries` يبقى سارياً. **تعديل المهلة يبقى محصوراً بالتصنيف النمطي**، ولا يمكن للنموذج أن يأذن بأي إجراء مدمّر.
- `--dry-run` لا يُجري أي نداء.
- `ANTHROPIC_API_KEY` يُقرأ من البيئة فقط، ولا يُكتب في إعدادات ولا حالة ولا نسخ احتياطية ولا تقارير ولا تنبيهات.

---

## 📊 التكامل مع المراقبة

```bash
python3 CronMaster_AI.py monitor --prometheus-textfile /var/lib/node_exporter/cronmaster.prom
```

المقاييس: `cronmaster_jobs_total`، `_failed`، `_silent`، `_critical`،
`cronmaster_job_consecutive_errors{job,job_id}`، `cronmaster_fixes_applied_total`،
`cronmaster_last_run_timestamp_seconds`، `cronmaster_monitor_success`.
الكتابة **ذرّية** فلا يقرأ node_exporter ملفاً نصفياً.

```json
{ "healthcheck_ping_url": "https://hc-ping.com/UUID" }
```
نبضة إلى العنوان عند نجاح الدورة، وإلى `<url>/fail` عند فشلها، بمهلة قصيرة
وبلا أي استثناء يتسرب.

---

## ⏰ الجدولة مع OpenClaw

```bash
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
~/.cronmaster/                 # المجلد بأذونات 0700
├── config.json                # إعداداتك (اختياري)          0600
├── cronmaster.log             # سجل العمليات (مع تدوير)     0600
├── cronmaster.lock            # قفل التنفيذ                 0600
├── state.json                 # حالة الإصلاحات والتنبيهات   0600
├── history.db                 # السجل التاريخي (SQLite)     0600
├── backups/                   # نسخ احتياطية قبل أي تعديل   0600
└── reports/                   # التقارير المولدة
```

```
cronmaster/                    # الحزمة
├── __init__.py                # الإصدار والأسماء العامة
├── config.py                  # الإعدادات والتسجيل
├── i18n.py                    # كتالوج الرسائل (ar/en)
├── models.py                  # Job / ErrorType / FailureAnalysis
├── backends/                  # base + openclaw + crontab
├── analysis/                  # قاعدة الأنماط + المحلل + المصنّف الذكي + المرشحات
├── fixers.py                  # الإصلاح والنسخ الاحتياطي والاستعادة
├── storage.py                 # StateManager + HistoryStore
├── lock.py                    # قفل التنفيذ
├── notifiers/                 # telegram / webhook / slack / discord / null
├── reporting/                 # markdown + json + html
├── metrics.py                 # Prometheus + healthchecks
├── core.py                    # المنسّق
└── cli.py                     # واجهة سطر الأوامر
CronMaster_AI.py               # غلاف توافق رفيع
```

---

## ⚙️ الإعدادات

كل إعداد متاح بثلاث طرق، بالأولوية: **القيمة الافتراضية ← `config.json` ← `CRONMASTER_<KEY>`**.
المفاتيح المجهولة يُحذَّر منها في السجل وتظهر في `doctor`.

| المفتاح (`config.json`) | متغير البيئة | الافتراضي | الوصف |
|---|---|---|---|
| `backend` | `CRONMASTER_BACKEND` | `openclaw` | الواجهة الخلفية (`openclaw` / `crontab`) |
| `lang` | `CRONMASTER_LANG` | `ar` | لغة المخرجات (`ar` / `en`) |
| `telegram_chat_id` | `CRONMASTER_TELEGRAM_CHAT_ID` | (فارغ) | اختصار لقناة Telegram واحدة |
| `notifiers` | `CRONMASTER_NOTIFIERS` (JSON) | `[]` | قائمة قنوات التنبيه |
| `quiet_hours` | `CRONMASTER_QUIET_HOURS` (JSON) | `{}` | فترة تأجيل التنبيهات غير الحرجة |
| `alert_threshold` | `CRONMASTER_ALERT_THRESHOLD` | `2` | فشل متتالي قبل التنبيه |
| `alert_cooldown_hours` | `CRONMASTER_ALERT_COOLDOWN_HOURS` | `24` | تهدئة تكرار التنبيه |
| `auto_fix_timeout` | `CRONMASTER_AUTO_FIX_TIMEOUT` | `true` | رفع المهلة تلقائياً |
| `timeout_increment` | `CRONMASTER_TIMEOUT_INCREMENT` | `120` | مقدار الزيادة (ثوانٍ) |
| `max_timeout` | `CRONMASTER_MAX_TIMEOUT` | `900` | حد أقصى للمهلة (ثوانٍ) |
| `max_timeout_fixes` | `CRONMASTER_MAX_TIMEOUT_FIXES` | `3` | عدد الرفعات قبل التصعيد |
| `auto_retry` | `CRONMASTER_AUTO_RETRY` | `true` | إعادة تشغيل بعد إصلاح المهلة |
| `auto_retry_transient` | `CRONMASTER_AUTO_RETRY_TRANSIENT` | `true` | إعادة محاولة الأخطاء العابرة |
| `max_retries` | `CRONMASTER_MAX_RETRIES` | `3` | حد إعادات المحاولة قبل التصعيد |
| `retry_backoff_hours` | `CRONMASTER_RETRY_BACKOFF_HOURS` | `1` | تهدئة إعادة محاولة أخطاء الـ API |
| `silent_grace_hours` | `CRONMASTER_SILENT_GRACE_HOURS` | `6` | سماحية كشف المهام الصامتة |
| `history_enabled` | `CRONMASTER_HISTORY_ENABLED` | `true` | تفعيل السجل التاريخي |
| `history_retention_days` | `CRONMASTER_HISTORY_RETENTION_DAYS` | `90` | مدة الاحتفاظ بالسجل |
| `duration_regression_factor` | `CRONMASTER_DURATION_REGRESSION_FACTOR` | `2.0` | معامل التباطؤ الذي يُطلق التحذير |
| `duration_regression_min_samples` | `CRONMASTER_DURATION_REGRESSION_MIN_SAMPLES` | `5` | أقل عدد عينات قبل التحذير |
| `circuit_breaker_enabled` | `CRONMASTER_CIRCUIT_BREAKER_ENABLED` | `false` | تفعيل قاطع الدائرة |
| `circuit_breaker_threshold` | `CRONMASTER_CIRCUIT_BREAKER_THRESHOLD` | `10` | فشل متتالٍ قبل تعطيل المهمة |
| `rollback_timeout_enabled` | `CRONMASTER_ROLLBACK_TIMEOUT_ENABLED` | `false` | تفعيل التراجع عن رفع المهلة |
| `rollback_after_cycles` | `CRONMASTER_ROLLBACK_AFTER_CYCLES` | `2` | دورات فشل قبل التراجع |
| `auto_reschedule` | `CRONMASTER_AUTO_RESCHEDULE` | `false` | تطبيق إزاحة الجدولة لا اقتراحها فقط |
| `reschedule_shift_minutes` | `CRONMASTER_RESCHEDULE_SHIFT_MINUTES` | `17` | مقدار الإزاحة بالدقائق |
| `reschedule_after_errors` | `CRONMASTER_RESCHEDULE_AFTER_ERRORS` | `3` | أخطاء 429 قبل اقتراح الإزاحة |
| `llm_enabled` | `CRONMASTER_LLM_ENABLED` | `false` | تفعيل المصنّف الذكي |
| `llm_model` | `CRONMASTER_LLM_MODEL` | `claude-opus-5` | الموديل المستخدم |
| `llm_min_confidence` | `CRONMASTER_LLM_MIN_CONFIDENCE` | `0.8` | أقل ثقة تُعتمد |
| `llm_cache_days` | `CRONMASTER_LLM_CACHE_DAYS` | `30` | صلاحية ذاكرة الأحكام |
| `healthcheck_ping_url` | `CRONMASTER_HEALTHCHECK_PING_URL` | (فارغ) | عنوان نبضة healthchecks.io |
| `prometheus_textfile` | `CRONMASTER_PROMETHEUS_TEXTFILE` | (فارغ) | مسار ملف Prometheus الافتراضي |

> `ANTHROPIC_API_KEY` **ليس** مفتاح إعدادات: يُقرأ من البيئة فقط ولا يُكتب في أي ملف.

مثال `~/.cronmaster/config.json`:

```json
{
  "telegram_chat_id": "YOUR_CHAT_ID",
  "alert_threshold": 2,
  "max_timeout": 900,
  "history_retention_days": 90,
  "quiet_hours": { "from": "22:00", "to": "07:00", "tz": "Asia/Riyadh" },
  "circuit_breaker_enabled": true,
  "llm_enabled": false
}
```

---

## 🧪 التطوير والاختبارات

```bash
pip install pytest ruff
ruff check .
pytest -v
```

الاختبارات لا تلمس الشبكة ولا عملية فرعية حقيقية ولا مجلد المستخدم: كل شيء
يعمل فوق `tmp_path` وواجهة خلفية مزيفة و`anthropic` مزيف.

تعمل الاختبارات وفحص الجودة تلقائياً عبر GitHub Actions على كل push وpull request.

---

## 📜 الترخيص

[MIT License](LICENSE)

---

**المطور:** Pipbot 🤖
**لـ:** عبدالكريم

# -*- coding: utf-8 -*-
"""كتالوج الرسائل (عربي/إنجليزي).

العربية هي اللغة الافتراضية، وقيم ``ar`` هنا مطابقة حرفياً للنصوص التي كانت
مكتوبة داخل الكود سابقاً — فمخرجات الأداة بدون ``--lang`` تبقى كما هي بايتاً ببايت.
لا اعتماديات خارجية ولا gettext: قاموسان بسيطان ودالة ``t``.
"""

import logging
from typing import Any, Dict

DEFAULT_LANG = "ar"
SUPPORTED_LANGS = ("ar", "en")

_current_lang = DEFAULT_LANG


MESSAGES: Dict[str, Dict[str, str]] = {
    "ar": {
        # ---- حالة سريعة (status) ----
        "status.title": "📊 حالة OpenClaw Cron Jobs",
        "status.total": "إجمالي المهام:",
        "status.ok": "ناجحة ✅:",
        "status.error": "فاشلة ❌:",
        "status.critical": "حرجة ⚠️:",
        "status.silent": "صامتة 😴:",
        "status.rate": "نسبة النجاح:",
        # ---- التقارير ----
        "report.title": "📊 تقرير CronMaster",
        "report.date": "التاريخ",
        "report.stats": "📈 الإحصائيات",
        "report.metric": "المقياس",
        "report.value": "القيمة",
        "report.total_jobs": "إجمالي المهام",
        "report.ok_jobs": "ناجحة ✅",
        "report.failed_jobs": "فاشلة ❌",
        "report.other_jobs": "أخرى ⏸️",
        "report.failed_section": "❌ المهام الفاشلة",
        "report.no_failures": "لا توجد مهام فاشلة 🎉",
        "report.ok_section": "✅ المهام الناجحة",
        "report.error": "الخطأ",
        "report.analysis": "التحليل",
        "report.fix": "الحل",
        "report.state": "الحالة",
        "report.fix_details": "تفاصيل الإصلاح",
        "report.fixed": "✅ تم الإصلاح",
        "report.needs_review": "⏳ يحتاج مراجعة",
        "report.footer": "تم التوليد بواسطة CronMaster_AI",
        "report.and_more": "و {count} مهمة أخرى",
        "report.generated": "✅ التقرير: {path}",
        # ---- تقرير HTML ----
        "html.title": "تقرير CronMaster",
        "html.summary": "الملخص الحالي",
        "html.jobs_table": "المهام",
        "html.col_job": "المهمة",
        "html.col_status": "الحالة",
        "html.col_schedule": "الجدولة",
        "html.col_success": "نسبة النجاح",
        "html.col_flaky": "التذبذب",
        "html.col_runs": "عدد التشغيلات",
        "html.col_duration": "متوسط المدة",
        "html.trend": "اتجاه نسبة النجاح",
        "html.top_failing": "أكثر المهام فشلاً",
        "html.history": "سجل الإصلاحات والتنبيهات",
        "html.col_time": "الوقت",
        "html.col_kind": "النوع",
        "html.col_details": "التفاصيل",
        "html.no_data": "لا توجد بيانات كافية بعد",
        "html.generated_at": "وُلِّد في {ts}",
        # ---- الإحصائيات (stats) ----
        "stats.title": "📈 إحصائيات المهام",
        "stats.window": "النافذة الزمنية: آخر {days} يوماً",
        "stats.job": "المهمة",
        "stats.success_rate": "نسبة النجاح",
        "stats.flakiness": "التذبذب",
        "stats.avg_duration": "متوسط المدة",
        "stats.trend": "الاتجاه",
        "stats.mtbf": "متوسط الزمن بين الأعطال",
        "stats.samples": "عينات",
        "stats.no_history": "لا يوجد سجل تاريخي بعد — شغّل monitor أولاً",
        "stats.disabled": "السجل التاريخي معطّل (history_enabled=false)",
        "stats.trend_up": "تدهور ↑",
        "stats.trend_flat": "مستقر →",
        "stats.trend_down": "تحسّن ↓",
        # ---- التشخيص (doctor) ----
        "doctor.title": "🩺 فحص CronMaster",
        "doctor.backend": "الواجهة الخلفية ({name})",
        "doctor.config": "ملف الإعدادات",
        "doctor.unknown_keys": "مفاتيح إعدادات غير معروفة",
        "doctor.coerced_keys": "مفاتيح صُحِّحت قيمتها",
        "doctor.workdir": "صلاحيات مجلد العمل",
        "doctor.database": "سلامة قاعدة البيانات",
        "doctor.notifiers": "قنوات التنبيه",
        "doctor.llm": "مصنّف الأخطاء بالذكاء الاصطناعي",
        "doctor.ok": "سليم",
        "doctor.warn": "تحذير",
        "doctor.fail": "فشل",
        "doctor.passed": "✅ كل الفحوص الأساسية سليمة",
        "doctor.failed": "❌ فشل فحص أو أكثر — راجع التفاصيل أعلاه",
        # ---- الاستعادة (restore) ----
        "restore.no_backups": "لا توجد نسخ احتياطية للمهمة {job_id}",
        "restore.available": "النسخ الاحتياطية المتاحة للمهمة {job_id}:",
        "restore.confirm": "استعادة المهمة {job_id} من {backup}؟ [y/N]: ",
        "restore.cancelled": "أُلغيت الاستعادة",
        "restore.done": "✅ استُعيدت المهمة {job_id} من {backup}",
        "restore.failed": "❌ تعذرت الاستعادة: {reason}",
        "restore.nothing": "لا يوجد في النسخة الاحتياطية أي حقل قابل للاستعادة",
        "restore.unsupported": "العملية غير مدعومة على هذه الواجهة الخلفية: {op}",
        "restore.outside_dir": "❌ ملف النسخة الاحتياطية يجب أن يكون داخل {dir} — استخدم --list لعرض المتاح",
        # ---- القفل ----
        "lock.busy": "نسخة أخرى من CronMaster تعمل الآن — تم التخطي",
        "lock.stale": "قفل قديم من عملية منتهية (pid={pid}) — أُعيد استخدامه",
        # ---- التنبيهات ----
        "alert.header": "🚨 تنبيه CronMaster",
        "alert.recovered": "💚 مهام تعافت:",
        "alert.silent": "😴 مهام مفعّلة فات موعد تشغيلها (تحقق من المجدول):",
        "alert.missed_at": "الموعد الفائت",
        "alert.error_type": "نوع الخطأ",
        "alert.analysis": "التحليل",
        "alert.fix": "🔧 الإصلاح",
        "alert.suggestion": "💡 الحل",
        "alert.digest": "📬 ملخص تنبيهات فترة الهدوء ({count})",
        "alert.regression": (
            "📈 تباطؤ ملحوظ: {name} — متوسط المدة الأخير {recent:.0f}s مقابل خط أساس "
            "{baseline:.0f}s (×{factor:.1f}). قد تنتهي المهلة قريباً."
        ),
        "alert.futile_fix": (
            "🛑 رفع المهلة لم يعد مجدياً لـ {name}: طُبّق {count} مرة والمهمة ما تزال "
            "تفشل بانتهاء المهلة. المطلوب تدخل بشري لا مزيد من الثواني."
        ),
        "alert.circuit_open": (
            "⛔ قاطع الدائرة: عُطّلت المهمة {name} بعد {count} فشلاً متتالياً. "
            "لإعادة تفعيلها: cronmaster restore {job_id} أو تفعيلها يدوياً من الواجهة الخلفية."
        ),
        "alert.rollback": "↩️ أُعيدت مهلة {name} إلى {seconds}s لأن رفعها لم يُصلح الفشل — يحتاج مراجعة.",
        "alert.reschedule": "🕒 نُقلت جدولة {name} من «{old}» إلى «{new}» تفادياً لازدحام حد الطلبات.",
        "alert.reschedule_hint": "🕒 يُقترح نقل جدولة {name} من «{old}» إلى «{new}» (auto_reschedule معطّل).",
        # ---- تصنيفات الأخطاء (قاعدة الأنماط) ----
        "error.permission_denied.desc": "نقص في الصلاحيات",
        "error.permission_denied.fix": "تحقق من صلاحيات الملف أو شغّل بـ sudo",
        "error.dependency_error.desc": "مكتبة أو موديول ناقص",
        "error.dependency_error.fix": "ثبّت المكتبة باستخدام pip install",
        "error.not_found.desc": "ملف أو أمر غير موجود",
        "error.not_found.fix": "تحقق من المسار الكامل",
        "error.syntax_error.desc": "خطأ في صياغة الكود",
        "error.syntax_error.fix": "راجع السطر المذكور في رسالة الخطأ",
        "error.memory_error.desc": "نفاد الذاكرة",
        "error.memory_error.fix": "قلّل حجم المعالجة أو زد ذاكرة الجهاز",
        "error.disk_full.desc": "القرص ممتلئ",
        "error.disk_full.fix": "احذف ملفات غير ضرورية",
        "error.api_error.desc": "تجاوز حد الطلبات (Rate Limit)",
        "error.api_error.fix": "إعادة المحاولة تلقائياً بعد فترة تهدئة",
        "error.network_error.desc": "مشكلة في الاتصال بالشبكة",
        "error.network_error.fix": "إعادة المحاولة تلقائياً (خطأ عابر غالباً)",
        "error.timeout.desc": "انتهاء المهلة الزمنية - العملية أخذت وقت أطول من المسموح",
        "error.timeout.fix": "زيادة timeout",
        "error.unknown.desc": "خطأ غير مصنف",
        "error.unknown.fix": "راجع السجلات",
        # ---- رسائل الإصلاح ----
        "fix.timeout_raised": "تم زيادة timeout من {old}s إلى {new}s",
        "fix.timeout_max": "الـ timeout وصل الحد الأقصى ({max}s)",
        "fix.timeout_failed": "فشل تحديث timeout: {error}",
        "fix.retry_done": "أُعيد التشغيل تلقائياً (محاولة {count}/{max})",
        "fix.retry_failed": "فشلت إعادة التشغيل التلقائية",
        "fix.retry_exhausted": "استُنفدت محاولات إعادة التشغيل التلقائية ({max}) — يحتاج تدخلاً يدوياً",
        "fix.api_cooldown": "[api] بانتظار انقضاء فترة التهدئة ({hours}h) قبل إعادة المحاولة",
        "fix.restarted": "🔄 أعيد تشغيل: {name}",
        # ---- رسائل وضع العرض فقط ----
        "dry.timeout_raise": "[dry-run] سيُرفع timeout من {old}s إلى {new}s",
        "dry.timeout_max": "[dry-run] الـ timeout بلغ الحد الأقصى ({max}s)",
        "dry.retry": "[dry-run] ستُعاد المحاولة تلقائياً ({count}/{max})",
        "dry.rollback": "[dry-run] قد يُتراجع عن رفع مهلة {name}",
        "dry.alert_skipped": "[dry-run] تنبيه لن يُرسل:\n{message}",
        # ---- المخرجات البشرية لسطر الأوامر ----
        "human.monitor_title": "🔍 نتيجة دورة المراقبة",
        "human.dry_run_note": "وضع العرض فقط — لم يُنفَّذ أي تعديل ولم يُرسل أي تنبيه",
        "human.total": "إجمالي المهام:",
        "human.failed": "مهام فاشلة:",
        "human.fixes": "إصلاحات مطبّقة:",
        "human.retries": "إعادات تشغيل:",
        "human.recovered": "مهام تعافت:",
        "human.silent": "مهام صامتة:",
        "human.alert_sent": "أُرسل تنبيه:",
        "human.yes": "نعم",
        "human.no": "لا",
        "human.notices": "📌 ملاحظات:",
        "human.monitor_error": "❌ فشل المراقب: {error}",
        "human.jobs_title": "📋 المهام",
        "human.no_jobs": "لا توجد مهام",
        "human.col_id": "المعرّف",
        "human.history_title": "🗂️ سجل الإصلاحات والتنبيهات",
        "human.no_history": "لا يوجد سجل بعد",
        "human.last_run": "آخر دورة:",
        "human.fix_title": "🔧 نتيجة الإصلاح",
        "human.hint_json": "(للحصول على JSON: أضف --json)",
        # ---- عامة ----
        "cli.backend_error": "❌ تعذر التواصل مع الواجهة الخلفية: {error}",
        "cli.unknown_job": "المهمة غير موجودة",
        "cli.unexpected_error": "❌ خطأ غير متوقع: {error}\n   التفاصيل الكاملة في: {log}",
        "monitor.started": "بدء المراقبة...",
        "monitor.dry_run_suffix": " [dry-run]",
        "monitor.progress": "[{index}/{total}] تحليل: {name}",
        "monitor.finished": "انتهت المراقبة: {failed} فشل، {fixes} إصلاح، {retries} إعادة تشغيل، {recovered} تعافٍ",
    },
    "en": {
        "status.title": "📊 Scheduled job status",
        "status.total": "Total jobs:",
        "status.ok": "Healthy ✅:",
        "status.error": "Failing ❌:",
        "status.critical": "Critical ⚠️:",
        "status.silent": "Silent 😴:",
        "status.rate": "Success rate:",
        "report.title": "📊 CronMaster report",
        "report.date": "Date",
        "report.stats": "📈 Statistics",
        "report.metric": "Metric",
        "report.value": "Value",
        "report.total_jobs": "Total jobs",
        "report.ok_jobs": "Healthy ✅",
        "report.failed_jobs": "Failing ❌",
        "report.other_jobs": "Other ⏸️",
        "report.failed_section": "❌ Failing jobs",
        "report.no_failures": "No failing jobs 🎉",
        "report.ok_section": "✅ Healthy jobs",
        "report.error": "Error",
        "report.analysis": "Diagnosis",
        "report.fix": "Suggested fix",
        "report.state": "State",
        "report.fix_details": "Fix details",
        "report.fixed": "✅ Fixed",
        "report.needs_review": "⏳ Needs review",
        "report.footer": "Generated by CronMaster_AI",
        "report.and_more": "and {count} more job(s)",
        "report.generated": "✅ Report: {path}",
        "html.title": "CronMaster report",
        "html.summary": "Current summary",
        "html.jobs_table": "Jobs",
        "html.col_job": "Job",
        "html.col_status": "Status",
        "html.col_schedule": "Schedule",
        "html.col_success": "Success rate",
        "html.col_flaky": "Flakiness",
        "html.col_runs": "Runs",
        "html.col_duration": "Avg duration",
        "html.trend": "Success-rate trend",
        "html.top_failing": "Top failing jobs",
        "html.history": "Fix & alert history",
        "html.col_time": "Time",
        "html.col_kind": "Kind",
        "html.col_details": "Details",
        "html.no_data": "Not enough data yet",
        "html.generated_at": "Generated at {ts}",
        "stats.title": "📈 Job statistics",
        "stats.window": "Window: last {days} day(s)",
        "stats.job": "Job",
        "stats.success_rate": "Success rate",
        "stats.flakiness": "Flakiness",
        "stats.avg_duration": "Avg duration",
        "stats.trend": "Trend",
        "stats.mtbf": "Mean time between failures",
        "stats.samples": "samples",
        "stats.no_history": "No history yet — run monitor first",
        "stats.disabled": "History store is disabled (history_enabled=false)",
        "stats.trend_up": "regressing ↑",
        "stats.trend_flat": "stable →",
        "stats.trend_down": "improving ↓",
        "doctor.title": "🩺 CronMaster health check",
        "doctor.backend": "Backend ({name})",
        "doctor.config": "Config file",
        "doctor.unknown_keys": "Unknown config keys",
        "doctor.coerced_keys": "Coerced config values",
        "doctor.workdir": "Work-dir permissions",
        "doctor.database": "Database integrity",
        "doctor.notifiers": "Notification channels",
        "doctor.llm": "AI error classifier",
        "doctor.ok": "ok",
        "doctor.warn": "warning",
        "doctor.fail": "failed",
        "doctor.passed": "✅ All hard checks passed",
        "doctor.failed": "❌ One or more checks failed — see details above",
        "restore.no_backups": "No backups found for job {job_id}",
        "restore.available": "Available backups for job {job_id}:",
        "restore.confirm": "Restore job {job_id} from {backup}? [y/N]: ",
        "restore.cancelled": "Restore cancelled",
        "restore.done": "✅ Restored job {job_id} from {backup}",
        "restore.failed": "❌ Restore failed: {reason}",
        "restore.nothing": "The backup contains no restorable field",
        "restore.unsupported": "Operation unsupported on this backend: {op}",
        "restore.outside_dir": "❌ The backup file must live inside {dir} — use --list to see what is available",
        "lock.busy": "Another CronMaster instance is running — skipping",
        "lock.stale": "Stale lock from a dead process (pid={pid}) — reclaimed",
        "alert.header": "🚨 CronMaster alert",
        "alert.recovered": "💚 Recovered jobs:",
        "alert.silent": "😴 Enabled jobs that missed their schedule (check the scheduler):",
        "alert.missed_at": "missed at",
        "alert.error_type": "Error type",
        "alert.analysis": "Diagnosis",
        "alert.fix": "🔧 Fix",
        "alert.suggestion": "💡 Suggestion",
        "alert.digest": "📬 Quiet-hours alert digest ({count})",
        "alert.regression": (
            "📈 Slowdown detected: {name} — recent mean duration {recent:.0f}s vs baseline "
            "{baseline:.0f}s (×{factor:.1f}). A timeout is likely soon."
        ),
        "alert.futile_fix": (
            "🛑 Raising the timeout is no longer helping {name}: applied {count} times and the job "
            "still times out. This needs a human, not more seconds."
        ),
        "alert.circuit_open": (
            "⛔ Circuit breaker: job {name} was disabled after {count} consecutive failures. "
            "To re-enable: cronmaster restore {job_id}, or enable it manually in the backend."
        ),
        "alert.rollback": "↩️ Timeout for {name} rolled back to {seconds}s — raising it did not help; needs review.",
        "alert.reschedule": "🕒 Rescheduled {name} from “{old}” to “{new}” to dodge the rate-limit window.",
        "alert.reschedule_hint": "🕒 Suggest moving {name} from “{old}” to “{new}” (auto_reschedule is off).",
        "error.permission_denied.desc": "Insufficient permissions",
        "error.permission_denied.fix": "Check file permissions, or run with sudo",
        "error.dependency_error.desc": "Missing library or module",
        "error.dependency_error.fix": "Install the package with pip install",
        "error.not_found.desc": "File or command not found",
        "error.not_found.fix": "Verify the full path",
        "error.syntax_error.desc": "Syntax error in the code",
        "error.syntax_error.fix": "Review the line named in the error message",
        "error.memory_error.desc": "Out of memory",
        "error.memory_error.fix": "Reduce the workload or add memory",
        "error.disk_full.desc": "Disk is full",
        "error.disk_full.fix": "Delete unneeded files",
        "error.api_error.desc": "Rate limit exceeded",
        "error.api_error.fix": "Retry automatically after a cooldown",
        "error.network_error.desc": "Network connectivity problem",
        "error.network_error.fix": "Retry automatically (usually transient)",
        "error.timeout.desc": "Timed out — the run took longer than allowed",
        "error.timeout.fix": "Raise the timeout",
        "error.unknown.desc": "Unclassified error",
        "error.unknown.fix": "Check the logs",
        "fix.timeout_raised": "Timeout raised from {old}s to {new}s",
        "fix.timeout_max": "Timeout is already at the maximum ({max}s)",
        "fix.timeout_failed": "Could not update the timeout: {error}",
        "fix.retry_done": "Restarted automatically (attempt {count}/{max})",
        "fix.retry_failed": "Automatic restart failed",
        "fix.retry_exhausted": "Automatic retries exhausted ({max}) — needs manual intervention",
        "fix.api_cooldown": "[api] waiting out the cooldown ({hours}h) before retrying",
        "fix.restarted": "🔄 restarted: {name}",
        "dry.timeout_raise": "[dry-run] would raise the timeout from {old}s to {new}s",
        "dry.timeout_max": "[dry-run] the timeout is already at the maximum ({max}s)",
        "dry.retry": "[dry-run] would retry automatically ({count}/{max})",
        "dry.rollback": "[dry-run] the timeout raise for {name} may be rolled back",
        "dry.alert_skipped": "[dry-run] alert not sent:\n{message}",
        "human.monitor_title": "🔍 Monitor cycle result",
        "human.dry_run_note": "Dry run — nothing was changed and no alert was sent",
        "human.total": "Total jobs:",
        "human.failed": "Failing jobs:",
        "human.fixes": "Fixes applied:",
        "human.retries": "Retries:",
        "human.recovered": "Recovered jobs:",
        "human.silent": "Silent jobs:",
        "human.alert_sent": "Alert sent:",
        "human.yes": "yes",
        "human.no": "no",
        "human.notices": "📌 Notices:",
        "human.monitor_error": "❌ Monitor failed: {error}",
        "human.jobs_title": "📋 Jobs",
        "human.no_jobs": "No jobs",
        "human.col_id": "ID",
        "human.history_title": "🗂️ Fix & alert history",
        "human.no_history": "No history yet",
        "human.last_run": "Last cycle:",
        "human.fix_title": "🔧 Fix result",
        "human.hint_json": "(for JSON output, add --json)",
        "cli.backend_error": "❌ Cannot reach the backend: {error}",
        "cli.unknown_job": "Job not found",
        "cli.unexpected_error": "❌ Unexpected error: {error}\n   Full details in: {log}",
        "monitor.progress": "[{index}/{total}] analysing: {name}",
        "monitor.started": "Monitoring started...",
        "monitor.dry_run_suffix": " [dry-run]",
        "monitor.finished": (
            "Monitoring done: {failed} failing, {fixes} fixes, {retries} retries, {recovered} recovered"
        ),
    },
}


def set_lang(lang: str) -> None:
    """ضبط لغة المخرجات. أي قيمة غير مدعومة تُتجاهل مع تحذير."""
    global _current_lang
    if not lang:
        return
    lang = lang.strip().lower()
    if lang not in SUPPORTED_LANGS:
        logging.getLogger(__name__).warning("لغة غير مدعومة: %s — سيُستخدم %s", lang, DEFAULT_LANG)
        return
    _current_lang = lang


def get_lang() -> str:
    return _current_lang


def t(key: str, **kwargs: Any) -> str:
    """جلب نص من الكتالوج مع تعبئة المتغيرات.

    السقوط الآمن: اللغة الحالية ← العربية ← المفتاح نفسه، حتى لا يفشل الإخراج أبداً.
    """
    table = MESSAGES.get(_current_lang, MESSAGES[DEFAULT_LANG])
    text = table.get(key) or MESSAGES[DEFAULT_LANG].get(key) or key
    if not kwargs:
        return text
    try:
        return text.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return text

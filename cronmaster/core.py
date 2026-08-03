# -*- coding: utf-8 -*-
"""المحرك الرئيسي: تنسيق المراقبة والتحليل والإصلاح والتنبيه والتقارير."""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .analysis import ErrorAnalyzer, filter_critical, filter_failed, filter_silent
from .analysis.llm import LLMClassifier
from .backends import get_backend
from .backends.base import BackendError, Capability, CronBackend
from .config import Config
from .fixers import AutoFixer, shift_cron_expression
from .i18n import t
from .metrics import ping_healthcheck, write_prometheus_textfile
from .models import ErrorType, FailureAnalysis, Job
from .notifiers import AlertManager, build_notifiers
from .reporting import ReportGenerator
from .storage import HistoryStore, StateManager

# أكواد خروج أمر monitor
EXIT_OK = 0
EXIT_MONITOR_FAILURE = 1
EXIT_JOBS_FAILING = 2
EXIT_FIXES_APPLIED = 3


class CronMaster:
    """المحرك الرئيسي"""

    def __init__(self, backend: Optional[CronBackend] = None, dry_run: bool = False):
        Config.init_dirs()

        self.backend = backend or get_backend()
        # اسم مهجور محفوظ للتوافق مع الكود القديم
        self.parser = self.backend
        self.state = StateManager()
        self.history_store = HistoryStore()
        self.analyzer = ErrorAnalyzer(
            llm_classifier=LLMClassifier(cache=self.history_store, dry_run=dry_run) if Config.LLM_ENABLED else None
        )
        self.fixer = AutoFixer(backend=self.backend)
        self.alerter = AlertManager(state=self.state)
        self.reporter = ReportGenerator()
        self.logger = logging.getLogger(__name__)
        # رسائل إضافية تُضم إلى تنبيه الدورة (تحذيرات مبكرة، تعطيل، تراجع...)
        self._extras: List[str] = []
        self._critical: List[str] = []

    # ------------------------------------------------------------
    # الإصلاح التلقائي (منطق موحّد يستخدمه monitor و fix)
    # ------------------------------------------------------------

    def _apply_auto_fix(
        self,
        job: Job,
        analysis: FailureAnalysis,
        retry: bool = True,
        dry_run: bool = False,
    ) -> Tuple[int, int]:
        """يطبّق الإصلاح التلقائي المناسب حسب نوع الخطأ.

        - TIMEOUT: زيادة المهلة (مع نسخة احتياطية) ثم إعادة تشغيل اختيارية.
        - NETWORK_ERROR / API_ERROR: إعادة محاولة مسقوفة بـ MAX_RETRIES؛
          أخطاء الـ API تحترم فترة تهدئة منسوبة لآخر تشغيل فعلي.

        يُحدّث ``analysis`` في مكانه ويعيد (عدد الإصلاحات، عدد إعادات التشغيل).
        """
        et = analysis.error_type

        if et == ErrorType.TIMEOUT and Config.AUTO_FIX_TIMEOUT:
            return self._fix_timeout_flow(job, analysis, retry, dry_run)

        if et in (ErrorType.NETWORK_ERROR, ErrorType.API_ERROR) and Config.AUTO_RETRY_TRANSIENT:
            return self._retry_transient_flow(job, analysis, dry_run)

        return 0, 0

    def _fix_timeout_flow(
        self, job: Job, analysis: FailureAnalysis, retry: bool, dry_run: bool
    ) -> Tuple[int, int]:
        """إصلاح خطأ المهلة بزيادتها ثم إعادة التشغيل"""
        # حارس الإصلاح غير المجدي: رفع المهلة مراراً بلا نتيجة ليس إصلاحاً، بل تأجيل
        applied_before = self.state.get_timeout_fixes(job.id)
        if applied_before >= Config.MAX_TIMEOUT_FIXES:
            analysis.fix_details = t("alert.futile_fix", name=job.name, count=applied_before)
            self._escalate(analysis.fix_details)
            return 0, 0

        if dry_run:
            current, new = AutoFixer.compute_new_timeout(job.timeout_seconds)
            analysis.fix_details = (
                f"[dry-run] سيُرفع timeout من {current}s إلى {new}s"
                if new != current
                else f"[dry-run] الـ timeout بلغ الحد الأقصى ({Config.MAX_TIMEOUT}s)"
            )
            return 0, 0

        previous_timeout = job.timeout_seconds
        self.fixer.fix(analysis)  # يُعدّل analysis في مكانه
        if not analysis.fix_applied:
            return 0, 0

        self.state.record_timeout_fix(job.id)
        # أول رفعة فقط تُسجّل نقطة التراجع: نعود إلى آخر مهلة معروفة الصلاح،
        # لا إلى الرفعة السابقة التي لم تُجدِ هي الأخرى
        if self.state.get_pending_rollback(job.id) is None:
            self.state.set_pending_rollback(job.id, previous_timeout)
        self.state.record_fix(job.id, analysis.error_type.value, analysis.fix_details or "")

        retries = 0
        if retry and Config.AUTO_RETRY and self.fixer.retry_job(job.id):
            retries = 1
            self.logger.info(f"🔄 أعيد تشغيل: {job.name}")
        return 1, retries

    @staticmethod
    def _api_backoff_elapsed(job: Job) -> bool:
        """هل مضى ما يكفي منذ آخر تشغيل فعلي لإعادة المحاولة على خطأ rate-limit؟"""
        if not job.last_run_at:
            return True
        return datetime.now() - job.last_run_at >= timedelta(hours=Config.RETRY_BACKOFF_HOURS)

    def _retry_transient_flow(
        self, job: Job, analysis: FailureAnalysis, dry_run: bool
    ) -> Tuple[int, int]:
        """إعادة محاولة مسقوفة للأخطاء العابرة (شبكة / rate-limit)"""
        count = self.state.get_retry_count(job.id)

        if count >= Config.MAX_RETRIES:
            analysis.fix_details = (
                f"استُنفدت محاولات إعادة التشغيل التلقائية ({Config.MAX_RETRIES}) — يحتاج تدخلاً يدوياً"
            )
            return 0, 0

        # أخطاء الـ API (429): لا نعيد المحاولة قبل انقضاء فترة التهدئة حتى لا نصطدم بالحد نفسه
        if analysis.error_type == ErrorType.API_ERROR and not self._api_backoff_elapsed(job):
            analysis.fix_details = (
                f"[api] بانتظار انقضاء فترة التهدئة ({Config.RETRY_BACKOFF_HOURS}h) قبل إعادة المحاولة"
            )
            return 0, 0

        if dry_run:
            analysis.fix_details = f"[dry-run] ستُعاد المحاولة تلقائياً ({count + 1}/{Config.MAX_RETRIES})"
            return 0, 0

        if not self.fixer.retry_job(job.id):
            analysis.fix_details = "فشلت إعادة التشغيل التلقائية"
            return 0, 0

        self.state.record_retry(job.id)
        analysis.fix_applied = True
        analysis.fix_details = f"أُعيد التشغيل تلقائياً (محاولة {count + 1}/{Config.MAX_RETRIES})"
        self.logger.info(f"🔄 {job.name}: {analysis.fix_details}")
        self.state.record_fix(job.id, analysis.error_type.value, analysis.fix_details)
        return 1, 1

    # ------------------------------------------------------------
    # إجراءات الإصلاح الموسّعة (كلها مُقيَّدة بإعداد ومحترمة لـ dry-run)
    # ------------------------------------------------------------

    def _escalate(self, message: str, critical: bool = False):
        """إضافة سطر إلى تنبيه الدورة"""
        self.logger.warning(message)
        if critical:
            self._critical.append(message)
        else:
            self._extras.append(message)

    def _check_duration_regression(self, job: Job, dry_run: bool):
        """تحذير مبكر: المدة الأخيرة تجاوزت خط الأساس بمعامل مقلق قبل أن تصبح timeout"""
        if not self.history_store.available:
            return
        trend = self.history_store.duration_trend(job.id)
        if trend["samples"] < Config.DURATION_REGRESSION_MIN_SAMPLES:
            return
        ratio = trend["ratio"]
        if not ratio or ratio < Config.DURATION_REGRESSION_FACTOR:
            return
        if not dry_run and not self.state.should_alert(job.id, "duration_regression"):
            return

        message = t(
            "alert.regression",
            name=job.name,
            recent=trend["recent_mean"],
            baseline=trend["baseline_mean"],
            factor=ratio,
        )
        self._escalate(message)
        if not dry_run:
            self.state.record_alert(job.id, "duration_regression")

    def _check_rollback(self, job: Job, analysis: FailureAnalysis, dry_run: bool) -> bool:
        """التراجع عن رفع المهلة إذا استمر الفشل بنفس السبب بعد عدة دورات"""
        if not Config.ROLLBACK_TIMEOUT_ENABLED or analysis.error_type != ErrorType.TIMEOUT:
            return False
        pending = self.state.get_pending_rollback(job.id)
        if not pending:
            return False

        if dry_run:
            self._escalate(f"[dry-run] قد يُتراجع عن رفع مهلة {job.name}")
            return False

        cycles = self.state.bump_rollback_cycle(job.id)
        if cycles < Config.ROLLBACK_AFTER_CYCLES:
            return False

        previous = pending.get("previous")
        if previous is None:
            self.state.clear_pending_rollback(job.id)
            return False

        if not self.fixer.set_timeout(job, int(previous)):
            return False

        self.state.clear_pending_rollback(job.id)
        message = t("alert.rollback", name=job.name, seconds=previous)
        analysis.fix_applied = True
        analysis.fix_details = message
        self._escalate(message)
        self.state.record_fix(job.id, "timeout_rollback", message)
        return True

    def _check_circuit_breaker(self, job: Job, analysis: FailureAnalysis, dry_run: bool) -> bool:
        """تعطيل مهمة تفشل بلا انقطاع بخطأ لا يمكن إصلاحه تلقائياً"""
        if not Config.CIRCUIT_BREAKER_ENABLED or analysis.auto_fixable:
            return False
        if job.consecutive_errors < Config.CIRCUIT_BREAKER_THRESHOLD:
            return False
        if self.state.is_circuit_open(job.id):
            return False
        if not self.backend.supports(Capability.SET_ENABLED):
            self.logger.warning(t("restore.unsupported", op=Capability.SET_ENABLED))
            return False

        message = t("alert.circuit_open", name=job.name, count=job.consecutive_errors, job_id=job.id)

        if dry_run:
            self._escalate(f"[dry-run] {message}")
            return False

        if not self.fixer.disable_job(job):
            return False

        self.state.open_circuit(job.id, job.consecutive_errors)
        self.state.record_fix(job.id, "circuit_breaker", message)
        analysis.fix_applied = True
        analysis.fix_details = message
        self._escalate(message, critical=True)
        return True

    def _check_reschedule(self, job: Job, dry_run: bool) -> bool:
        """اقتراح/تطبيق إزاحة جدولة عند تكرار أخطاء حد الطلبات"""
        if job.consecutive_errors < Config.RESCHEDULE_AFTER_ERRORS or self.state.has_reschedule(job.id):
            return False

        new_expr = shift_cron_expression(job.schedule, Config.RESCHEDULE_SHIFT_MINUTES)
        if not new_expr:
            self.logger.info("جدولة %s غير قابلة للإزاحة تلقائياً (%s)", job.name, job.schedule)
            return False

        if not Config.AUTO_RESCHEDULE:
            message = t("alert.reschedule_hint", name=job.name, old=job.schedule, new=new_expr)
            self._escalate(message)
            if not dry_run:
                self.state.record_reschedule(job.id, job.schedule, new_expr, applied=False)
            return False

        if dry_run:
            self._escalate(f"[dry-run] {t('alert.reschedule', name=job.name, old=job.schedule, new=new_expr)}")
            return False

        if not self.fixer.reschedule(job, new_expr):
            return False

        self.state.record_reschedule(job.id, job.schedule, new_expr, applied=True)
        message = t("alert.reschedule", name=job.name, old=job.schedule, new=new_expr)
        self.state.record_fix(job.id, "reschedule", message)
        self._escalate(message)
        return True

    # ------------------------------------------------------------
    # المراقبة
    # ------------------------------------------------------------

    def monitor(
        self,
        auto_fix: bool = True,
        alert: bool = True,
        retry: bool = True,
        dry_run: bool = False,
        prometheus_textfile: Optional[str] = None,
    ) -> Dict[str, Any]:
        """مراقبة وإصلاح المهام.

        dry_run: يعرض ما سيُفعل (إصلاحات وتنبيهات) دون تنفيذ أي تعديل أو إرسال.
        """
        self.logger.info(t("monitor.started") + (t("monitor.dry_run_suffix") if dry_run else ""))
        self._extras = []
        self._critical = []

        try:
            all_jobs = self.backend.list_jobs()
        except BackendError as e:
            # فشل المراقب نفسه حدث حرج — يُبلَّغ عنه صراحة ولا يُقدَّم كنجاح
            self.logger.error(f"تعذر جلب المهام: {e}")
            if alert and not dry_run:
                self.alerter.send(f"🚨 CronMaster لا يستطيع قراءة مهام OpenClaw:\n{e}", critical=True)
            result = {"timestamp": datetime.now().isoformat(), "error": str(e)}
            self._publish_metrics(result, [], prometheus_textfile)
            return result

        failed_jobs = filter_failed(all_jobs)
        silent_jobs = filter_silent(all_jobs)

        analyses: List[FailureAnalysis] = []
        fixes_applied = 0
        retries = 0

        for job in failed_jobs:
            analysis = self.analyzer.analyze(job)

            handled = self._check_circuit_breaker(job, analysis, dry_run)
            if handled:
                fixes_applied += 1
            elif self._check_rollback(job, analysis, dry_run):
                fixes_applied += 1
            elif auto_fix and analysis.auto_fixable:
                f, r = self._apply_auto_fix(job, analysis, retry=retry, dry_run=dry_run)
                fixes_applied += f
                retries += r

            if analysis.error_type == ErrorType.API_ERROR:
                self._check_reschedule(job, dry_run)

            analyses.append(analysis)

        # تحذير مبكر قبل أن يتحول التباطؤ إلى انتهاء مهلة
        for job in all_jobs:
            if job.enabled and not job.is_failed:
                self._check_duration_regression(job, dry_run)

        # كشف التعافي: مهام كانت فاشلة في الدورة السابقة وأصبحت ناجحة
        prev_failing = set(self.state.get_failing())
        now_failing = {j.id for j in failed_jobs}
        recovered = [j for j in all_jobs if j.id in prev_failing - now_failing and j.last_status == "ok"]

        # اختيار ما يستحق التنبيه: إصلاح مطبق، أو فشل بلغ العتبة ولم يُنبَّه عنه مؤخراً
        alert_analyses = [
            a
            for a in analyses
            if a.fix_applied
            or (
                a.job.consecutive_errors >= Config.ALERT_THRESHOLD
                and self.state.should_alert(a.job.id, a.error_type.value)
            )
        ]
        alert_silent = [j for j in silent_jobs if self.state.should_alert(j.id, "silent")]

        alert_sent = False
        if alert and (alert_analyses or recovered or alert_silent or self._extras or self._critical):
            message = self.alerter.format_alert(
                alert_analyses, recovered, alert_silent, extra=self._extras + self._critical
            )
            if dry_run:
                self.logger.info(f"[dry-run] تنبيه لن يُرسل:\n{message}")
            elif self.alerter.send(message, critical=bool(self._critical)):
                alert_sent = True
                for a in alert_analyses:
                    self.state.record_alert(a.job.id, a.error_type.value)
                for j in alert_silent:
                    self.state.record_alert(j.id, "silent")

        if not dry_run:
            error_types = {a.job.id: a.error_type.value for a in analyses}
            self.history_store.record_cycle(all_jobs, error_types)
            self._maybe_prune()
            if alert and self.alerter.flush_digest():
                alert_sent = True
            for j in recovered:
                self.state.clear_alerts_for(j.id)
                self.state.clear_retries_for(j.id)
                self.state.clear_timeout_fixes(j.id)
                self.state.clear_pending_rollback(j.id)
                self.state.close_circuit(j.id)
            self.state.prune_retries(list(now_failing))
            self.state.set_failing(list(now_failing))
            self.state.save()

        result = {
            "timestamp": datetime.now().isoformat(),
            "dry_run": dry_run,
            "total_jobs": len(all_jobs),
            "failed_jobs": len(failed_jobs),
            "critical_jobs": len(filter_critical(all_jobs)),
            "silent_jobs": [j.name for j in silent_jobs],
            "recovered_jobs": [j.name for j in recovered],
            "fixes_applied": fixes_applied,
            "retries": retries,
            "alert_sent": alert_sent,
            "notices": list(self._extras + self._critical),
            "analyses": [a.to_dict() for a in analyses],
        }

        self.logger.info(
            t(
                "monitor.finished",
                failed=len(failed_jobs),
                fixes=fixes_applied,
                retries=retries,
                recovered=len(recovered),
            )
        )
        self._publish_metrics(result, all_jobs, prometheus_textfile, dry_run=dry_run)
        return result

    def _maybe_prune(self):
        """تنظيف السجل التاريخي مرة يومياً كحد أقصى"""
        if not self.history_store.available or not self.state.should_prune():
            return
        removed = self.history_store.prune()
        self.history_store.prune_llm_cache()
        self.state.mark_pruned()
        if removed:
            self.logger.info("نُظّف السجل التاريخي: %s صفاً محذوفاً", removed)

    def _publish_metrics(
        self,
        result: Dict[str, Any],
        jobs: List[Job],
        prometheus_textfile: Optional[str],
        dry_run: bool = False,
    ):
        """كتابة مقاييس Prometheus وإرسال نبضة healthcheck"""
        if dry_run:
            return
        path = prometheus_textfile or Config.PROMETHEUS_TEXTFILE
        if path:
            write_prometheus_textfile(path, result, jobs)
        if Config.HEALTHCHECK_PING_URL:
            ping_healthcheck(Config.HEALTHCHECK_PING_URL, success="error" not in result)

    @staticmethod
    def exit_code(result: Dict[str, Any]) -> int:
        """كود الخروج المشتق من نتيجة المراقبة"""
        if result.get("error"):
            return EXIT_MONITOR_FAILURE
        if result.get("fixes_applied"):
            return EXIT_FIXES_APPLIED
        if result.get("failed_jobs"):
            return EXIT_JOBS_FAILING
        return EXIT_OK

    # ------------------------------------------------------------
    # بقية الأوامر
    # ------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        """حالة سريعة. ترفع BackendError عند تعذر الجلب."""
        jobs = self.backend.list_jobs()

        # نسبة النجاح تُحسب على المهام المفعّلة التي نُفِّذت مرة على الأقل
        measurable = [j for j in jobs if j.enabled and j.last_status]
        ok = len([j for j in measurable if j.last_status == "ok"])
        error = len(filter_failed(jobs))
        critical = len(filter_critical(jobs))
        silent = len(filter_silent(jobs))

        return {
            "total_jobs": len(jobs),
            "ok": ok,
            "error": error,
            "critical": critical,
            "silent": silent,
            "success_rate": (ok / len(measurable) * 100) if measurable else 100.0,
        }

    def report(self, fmt: str = "markdown") -> Path:
        """توليد تقرير. يرفع BackendError عند تعذر الجلب."""
        jobs = self.backend.list_jobs()
        failed = filter_failed(jobs)
        analyses = [self.analyzer.analyze(j, use_llm=False) for j in failed]

        stats: List[Dict[str, Any]] = []
        trend: List[Dict[str, Any]] = []
        history: Dict[str, Any] = {}
        if fmt == "html":
            if self.history_store.available:
                stats = [self.history_store.job_summary(j.id, Config.HISTORY_RETENTION_DAYS) for j in jobs]
                trend = self.history_store.daily_success_rates(days=Config.HISTORY_RETENTION_DAYS)
            history = self.state.get_history(limit=40)

        return self.reporter.generate_report(jobs, analyses, fmt, stats=stats, trend=trend, history=history)

    def list_jobs(self) -> List[Dict]:
        """قائمة المهام. ترفع BackendError عند تعذر الجلب."""
        return [j.to_dict() for j in self.backend.list_jobs()]

    def fix_job(self, job_id: str, retry: bool = True) -> Dict[str, Any]:
        """إصلاح مهمة محددة. ترفع BackendError عند تعذر الجلب."""
        jobs = self.backend.list_jobs()
        job = next((j for j in jobs if j.id == job_id), None)

        if not job:
            return {"error": t("cli.unknown_job")}

        analysis = self.analyzer.analyze(job)

        if analysis.auto_fixable:
            self._apply_auto_fix(job, analysis, retry=retry, dry_run=False)
            self.state.save()

        return analysis.to_dict()

    def history(self, limit: int = 20) -> Dict[str, Any]:
        """سجل الإصلاحات والتنبيهات"""
        return self.state.get_history(limit)

    def stats(self, job_id: Optional[str] = None, days: Optional[int] = None) -> Dict[str, Any]:
        """إحصائيات مشتقة من السجل التاريخي"""
        if not Config.HISTORY_ENABLED:
            return {"available": False, "reason": t("stats.disabled"), "jobs": []}
        if not self.history_store.available:
            return {"available": False, "reason": t("stats.no_history"), "jobs": []}

        window = days if days is not None else Config.HISTORY_RETENTION_DAYS
        ids = [job_id] if job_id else self.history_store.job_ids()
        return {
            "available": True,
            "days": window,
            "jobs": [self.history_store.job_summary(jid, window) for jid in ids],
        }

    # ------------------------------------------------------------
    # الاستعادة
    # ------------------------------------------------------------

    def list_backups(self, job_id: str) -> List[Path]:
        return AutoFixer.list_backups(job_id)

    def restore(self, job_id: str, backup: Optional[Path] = None) -> Dict[str, Any]:
        """استعادة مهمة من نسخة احتياطية (الأحدث افتراضياً)"""
        backups = self.list_backups(job_id)
        if not backups:
            return {"error": t("restore.no_backups", job_id=job_id)}

        path = Path(backup) if backup else backups[0]
        if not path.exists():
            return {"error": t("restore.no_backups", job_id=job_id)}

        try:
            outcome = self.fixer.restore(job_id, path)
        except ValueError as e:
            return {"error": t("restore.failed", reason=str(e))}

        self.state.close_circuit(job_id)
        self.state.clear_pending_rollback(job_id)
        self.state.clear_timeout_fixes(job_id)
        self.state.record_fix(job_id, "restore", f"{path.name}: {outcome['restored']}")
        return {"backup": str(path), **outcome}

    # ------------------------------------------------------------
    # التشخيص
    # ------------------------------------------------------------

    def doctor(self) -> Dict[str, Any]:
        """فحص ذاتي شامل قبل الاعتماد على الأداة"""
        checks: List[Dict[str, Any]] = []

        def add(name: str, ok: bool, detail: str = "", hard: bool = True):
            checks.append({"name": name, "ok": ok, "detail": detail, "hard": hard})

        # 1) الواجهة الخلفية
        try:
            jobs = self.backend.list_jobs()
            add(t("doctor.backend", name=self.backend.name), True, f"{len(jobs)} مهمة")
        except BackendError as e:
            add(t("doctor.backend", name=self.backend.name), False, str(e))

        # 2) ملف الإعدادات
        if Config.CONFIG_FILE.exists():
            try:
                import json

                json.loads(Config.CONFIG_FILE.read_text(encoding="utf-8"))
                add(t("doctor.config"), True, str(Config.CONFIG_FILE))
            except (OSError, ValueError) as e:
                add(t("doctor.config"), False, str(e))
        else:
            add(t("doctor.config"), True, "غير موجود — ستُستخدم القيم الافتراضية", hard=False)

        # 3) مفاتيح غير معروفة
        add(
            t("doctor.unknown_keys"),
            not Config.unknown_keys,
            ", ".join(Config.unknown_keys) or "—",
            hard=False,
        )

        # 4) صلاحيات مجلد العمل
        import os

        writable = os.access(str(Config.WORK_DIR), os.W_OK)
        try:
            mode = oct(Config.WORK_DIR.stat().st_mode & 0o777)
        except OSError:
            mode = "?"
        add(t("doctor.workdir"), writable, f"{Config.WORK_DIR} ({mode})")

        # 5) قاعدة البيانات
        if Config.HISTORY_ENABLED:
            add(t("doctor.database"), self.history_store.integrity_check(), str(Config.history_db()))
        else:
            add(t("doctor.database"), True, t("stats.disabled"), hard=False)

        # 6) قنوات التنبيه — تحقق بلا إرسال
        notifiers = build_notifiers()
        if notifiers:
            invalid = [n.name for n in notifiers if not n.validate()]
            add(
                t("doctor.notifiers"),
                not invalid,
                ", ".join(n.name for n in notifiers) + (f" (غير مضبوطة: {', '.join(invalid)})" if invalid else ""),
                hard=False,
            )
        else:
            add(t("doctor.notifiers"), True, "لا توجد قنوات مضبوطة", hard=False)

        # 7) المصنّف الذكي
        if Config.LLM_ENABLED:
            classifier = LLMClassifier(cache=self.history_store)
            ready = classifier._get_client() is not None  # noqa: SLF001 — فحص جاهزية مقصود
            add(t("doctor.llm"), ready, Config.LLM_MODEL if ready else "غير جاهز (حزمة أو مفتاح ناقص)", hard=False)
        else:
            add(t("doctor.llm"), True, "معطّل", hard=False)

        healthy = all(c["ok"] for c in checks if c["hard"])
        return {"ok": healthy, "checks": checks}

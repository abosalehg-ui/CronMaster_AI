# -*- coding: utf-8 -*-
"""المحرك الرئيسي: تنسيق المراقبة والتحليل والإصلاح والتنبيه والتقارير."""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .analysis import ErrorAnalyzer, filter_critical, filter_failed, filter_silent
from .analysis.llm import LLMClassifier
from .backends import get_backend
from .backends.base import BackendError, CronBackend
from .config import Config
from .fixers import AutoFixer
from .i18n import t
from .metrics import ping_healthcheck, write_prometheus_textfile
from .models import FailureAnalysis, Job
from .notifiers import AlertManager, build_notifiers
from .policies import PolicyEngine
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
        self.policies = PolicyEngine(
            state=self.state,
            fixer=self.fixer,
            backend=self.backend,
            history_store=self.history_store,
            logger=self.logger,
        )

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
        self.policies.reset()

        try:
            all_jobs = self.backend.list_jobs()
        except BackendError as e:
            # فشل المراقب نفسه حدث حرج — يُبلَّغ عنه صراحة ولا يُقدَّم كنجاح
            self.logger.error("تعذر جلب المهام: %s", e)
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

        for index, job in enumerate(failed_jobs, start=1):
            self.logger.info(t("monitor.progress", index=index, total=len(failed_jobs), name=job.name))
            analysis = self.analyzer.analyze(job)
            fixes, job_retries = self.policies.handle_failure(
                job, analysis, auto_fix=auto_fix, retry=retry, dry_run=dry_run
            )
            fixes_applied += fixes
            retries += job_retries
            analyses.append(analysis)

        # تحذير مبكر قبل أن يتحول التباطؤ إلى انتهاء مهلة
        for job in all_jobs:
            if job.enabled and not job.is_failed:
                self.policies.check_duration_regression(job, dry_run)

        # كشف التعافي: مهام كانت فاشلة في الدورة السابقة وأصبحت ناجحة
        prev_failing = set(self.state.get_failing())
        now_failing = {j.id for j in failed_jobs}
        recovered = [j for j in all_jobs if j.id in prev_failing - now_failing and j.last_status == "ok"]

        notices, critical = self.policies.notices, self.policies.critical
        alert_sent = self._maybe_alert(analyses, recovered, silent_jobs, notices, critical, alert, dry_run)

        if not dry_run:
            self._persist_cycle(all_jobs, analyses, recovered, now_failing)
            if alert and self.alerter.flush_digest():
                alert_sent = True

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
            "notices": list(notices + critical),
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

    def _maybe_alert(
        self,
        analyses: List[FailureAnalysis],
        recovered: List[Job],
        silent_jobs: List[Job],
        notices: List[str],
        critical: List[str],
        alert: bool,
        dry_run: bool,
    ) -> bool:
        """اختيار ما يستحق التنبيه ثم إرساله. يعيد هل أُرسل فعلاً.

        يستحق التنبيه: إصلاح مطبَّق، أو فشل بلغ العتبة ولم يُنبَّه عنه مؤخراً.
        """
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

        if not alert or not (alert_analyses or recovered or alert_silent or notices or critical):
            return False

        message = self.alerter.format_alert(alert_analyses, recovered, alert_silent, extra=notices + critical)
        if dry_run:
            self.logger.info(t("dry.alert_skipped", message=message))
            return False

        if not self.alerter.send(message, critical=bool(critical)):
            return False

        for a in alert_analyses:
            self.state.record_alert(a.job.id, a.error_type.value)
        for j in alert_silent:
            self.state.record_alert(j.id, "silent")
        return True

    def _persist_cycle(
        self,
        all_jobs: List[Job],
        analyses: List[FailureAnalysis],
        recovered: List[Job],
        now_failing: Set[str],
    ) -> None:
        """تثبيت نتيجة الدورة: السجل التاريخي، تصفير عدّادات المتعافية، ثم حفظ واحد"""
        error_types = {a.job.id: a.error_type.value for a in analyses}
        self.history_store.record_cycle(all_jobs, error_types)
        self._maybe_prune()

        for j in recovered:
            self.state.clear_alerts_for(j.id)
            self.state.clear_retries_for(j.id)
            self.state.clear_timeout_fixes(j.id)
            self.state.clear_pending_rollback(j.id)
            self.state.close_circuit(j.id)

        self.state.prune_retries(list(now_failing))
        self.state.set_failing(list(now_failing))
        self.state.save()

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
                stats = self.history_store.job_summaries(
                    [j.id for j in jobs], Config.HISTORY_RETENTION_DAYS
                )
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
            self.policies.apply_auto_fix(job, analysis, retry=retry, dry_run=False)
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
            "jobs": self.history_store.job_summaries(ids, window),
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
        self.state.save()  # مسار مستقل عن دورة المراقبة، فيحفظ حالته بنفسه
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

        # 3ب) مفاتيح صُحِّحت قيمتها: القيمة الفعلية ليست ما كتبه المستخدم
        add(
            t("doctor.coerced_keys"),
            not Config.coerced_keys,
            ", ".join(Config.coerced_keys) or "—",
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

# -*- coding: utf-8 -*-
"""سياسات الإصلاح التلقائي.

كل سياسة هنا قرار مستقل: هل نرفع المهلة؟ هل نعيد المحاولة؟ هل نتراجع؟ هل نعطّل
المهمة؟ هل نزيح جدولتها؟ كانت هذي القرارات الخمسة تعيش داخل ``CronMaster`` فتضخّم
المنسّق وصعّب اختبار كل سياسة على حدة، والآن يبقى ``monitor`` حلقة تنسيق قصيرة.

قواعد مشتركة تلتزم بها كل سياسة:
- محكومة بإعداد صريح، وأغلبها معطّل افتراضياً.
- تحترم ``dry_run`` فلا تُعدّل شيئاً ولا ترسل شيئاً.
- تسأل الواجهة الخلفية عن قدرتها قبل أي عملية.
- تسجّل ما تريد قوله في ``notices`` / ``critical`` بدل الإرسال مباشرة.
"""

import logging
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from .backends.base import Capability, CronBackend
from .config import Config
from .fixers import AutoFixer, shift_cron_expression
from .i18n import t
from .models import ErrorType, FailureAnalysis, Job
from .storage import HistoryStore, StateManager


class PolicyEngine:
    """تطبيق سياسات الإصلاح على مهمة فاشلة، وجمع ما يستحق التصعيد."""

    def __init__(
        self,
        state: StateManager,
        fixer: AutoFixer,
        backend: CronBackend,
        history_store: HistoryStore,
        logger: Optional[logging.Logger] = None,
    ):
        self.state = state
        self.fixer = fixer
        self.backend = backend
        self.history_store = history_store
        self.logger = logger or logging.getLogger(__name__)
        # رسائل تُضم إلى تنبيه الدورة (تحذيرات مبكرة، تعطيل، تراجع...)
        self.notices: List[str] = []
        self.critical: List[str] = []

    def reset(self) -> None:
        """تفريغ الرسائل قبل دورة جديدة"""
        self.notices = []
        self.critical = []

    def escalate(self, message: str, critical: bool = False) -> None:
        """إضافة سطر إلى تنبيه الدورة"""
        self.logger.warning(message)
        if critical:
            self.critical.append(message)
        else:
            self.notices.append(message)

    # ------------------------------------------------------------
    # نقطة الدخول الموحّدة
    # ------------------------------------------------------------

    def handle_failure(
        self,
        job: Job,
        analysis: FailureAnalysis,
        auto_fix: bool = True,
        retry: bool = True,
        dry_run: bool = False,
    ) -> Tuple[int, int]:
        """تطبيق السياسات بالترتيب الصحيح على مهمة فاشلة واحدة.

        الترتيب مقصود: قاطع الدائرة أولاً (المهمة تفشل بخطأ لا يُصلَح تلقائياً)،
        ثم التراجع (رفع المهلة لم يُجدِ)، ثم الإصلاح التلقائي. إزاحة الجدولة
        مستقلة عن ذلك لأنها تعالج ازدحام حد الطلبات لا الفشل نفسه.
        """
        fixes = retries = 0

        if self.check_circuit_breaker(job, analysis, dry_run):
            fixes = 1
        elif self.check_rollback(job, analysis, dry_run):
            fixes = 1
        elif auto_fix and analysis.auto_fixable:
            fixes, retries = self.apply_auto_fix(job, analysis, retry=retry, dry_run=dry_run)

        if analysis.error_type == ErrorType.API_ERROR:
            self.check_reschedule(job, dry_run)

        return fixes, retries

    # ------------------------------------------------------------
    # الإصلاح التلقائي (منطق موحّد يستخدمه monitor و fix)
    # ------------------------------------------------------------

    def apply_auto_fix(
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
            return self._timeout_flow(job, analysis, retry, dry_run)

        if et in (ErrorType.NETWORK_ERROR, ErrorType.API_ERROR) and Config.AUTO_RETRY_TRANSIENT:
            return self._transient_flow(job, analysis, dry_run)

        return 0, 0

    def _timeout_flow(
        self, job: Job, analysis: FailureAnalysis, retry: bool, dry_run: bool
    ) -> Tuple[int, int]:
        """إصلاح خطأ المهلة بزيادتها ثم إعادة التشغيل"""
        # حارس الإصلاح غير المجدي: رفع المهلة مراراً بلا نتيجة ليس إصلاحاً، بل تأجيل
        applied_before = self.state.get_timeout_fixes(job.id)
        if applied_before >= Config.MAX_TIMEOUT_FIXES:
            analysis.fix_details = t("alert.futile_fix", name=job.name, count=applied_before)
            # التصعيد يمر على التهدئة كبقية المسارات: الرسالة نفسها في كل دورة ليست
            # تنبيهاً بل إغراقاً يدفع المستخدم لكتم القناة، فيضيع التنبيه المهم القادم.
            # عند التعافي تُمسح تنبيهات المهمة فيعود التصعيد فوراً إن عادت المشكلة.
            if dry_run or self.state.should_alert(job.id, "futile_fix"):
                self.escalate(analysis.fix_details)
                if not dry_run:
                    self.state.record_alert(job.id, "futile_fix")
            return 0, 0

        if dry_run:
            current, new = AutoFixer.compute_new_timeout(job.timeout_seconds)
            analysis.fix_details = (
                t("dry.timeout_raise", old=current, new=new)
                if new != current
                else t("dry.timeout_max", max=Config.MAX_TIMEOUT)
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
            self.logger.info(t("fix.restarted", name=job.name))
        return 1, retries

    @staticmethod
    def _api_backoff_elapsed(job: Job) -> bool:
        """هل مضى ما يكفي منذ آخر تشغيل فعلي لإعادة المحاولة على خطأ rate-limit؟"""
        if not job.last_run_at:
            return True
        return datetime.now() - job.last_run_at >= timedelta(hours=Config.RETRY_BACKOFF_HOURS)

    def _transient_flow(
        self, job: Job, analysis: FailureAnalysis, dry_run: bool
    ) -> Tuple[int, int]:
        """إعادة محاولة مسقوفة للأخطاء العابرة (شبكة / rate-limit)"""
        count = self.state.get_retry_count(job.id)

        if count >= Config.MAX_RETRIES:
            analysis.fix_details = t("fix.retry_exhausted", max=Config.MAX_RETRIES)
            return 0, 0

        # أخطاء الـ API (429): لا نعيد المحاولة قبل انقضاء فترة التهدئة حتى لا نصطدم بالحد نفسه
        if analysis.error_type == ErrorType.API_ERROR and not self._api_backoff_elapsed(job):
            analysis.fix_details = t("fix.api_cooldown", hours=Config.RETRY_BACKOFF_HOURS)
            return 0, 0

        if dry_run:
            analysis.fix_details = t("dry.retry", count=count + 1, max=Config.MAX_RETRIES)
            return 0, 0

        if not self.fixer.retry_job(job.id):
            analysis.fix_details = t("fix.retry_failed")
            return 0, 0

        self.state.record_retry(job.id)
        analysis.fix_applied = True
        analysis.fix_details = t("fix.retry_done", count=count + 1, max=Config.MAX_RETRIES)
        self.logger.info("🔄 %s: %s", job.name, analysis.fix_details)
        self.state.record_fix(job.id, analysis.error_type.value, analysis.fix_details)
        return 1, 1

    def check_duration_regression(self, job: Job, dry_run: bool):
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
        self.escalate(message)
        if not dry_run:
            self.state.record_alert(job.id, "duration_regression")

    def check_rollback(self, job: Job, analysis: FailureAnalysis, dry_run: bool) -> bool:
        """التراجع عن رفع المهلة إذا استمر الفشل بنفس السبب بعد عدة دورات"""
        if not Config.ROLLBACK_TIMEOUT_ENABLED or analysis.error_type != ErrorType.TIMEOUT:
            return False
        pending = self.state.get_pending_rollback(job.id)
        if not pending:
            return False

        if dry_run:
            self.escalate(t("dry.rollback", name=job.name))
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
        self.escalate(message)
        self.state.record_fix(job.id, "timeout_rollback", message)
        return True

    def check_circuit_breaker(self, job: Job, analysis: FailureAnalysis, dry_run: bool) -> bool:
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
            self.escalate(f"[dry-run] {message}")
            return False

        if not self.fixer.disable_job(job):
            return False

        self.state.open_circuit(job.id, job.consecutive_errors)
        self.state.record_fix(job.id, "circuit_breaker", message)
        analysis.fix_applied = True
        analysis.fix_details = message
        self.escalate(message, critical=True)
        return True

    def check_reschedule(self, job: Job, dry_run: bool) -> bool:
        """اقتراح/تطبيق إزاحة جدولة عند تكرار أخطاء حد الطلبات"""
        if job.consecutive_errors < Config.RESCHEDULE_AFTER_ERRORS or self.state.has_reschedule(job.id):
            return False

        new_expr = shift_cron_expression(job.schedule, Config.RESCHEDULE_SHIFT_MINUTES)
        if not new_expr:
            self.logger.info("جدولة %s غير قابلة للإزاحة تلقائياً (%s)", job.name, job.schedule)
            return False

        if not Config.AUTO_RESCHEDULE:
            message = t("alert.reschedule_hint", name=job.name, old=job.schedule, new=new_expr)
            self.escalate(message)
            if not dry_run:
                self.state.record_reschedule(job.id, job.schedule, new_expr, applied=False)
            return False

        if dry_run:
            self.escalate(f"[dry-run] {t('alert.reschedule', name=job.name, old=job.schedule, new=new_expr)}")
            return False

        if not self.fixer.reschedule(job, new_expr):
            return False

        self.state.record_reschedule(job.id, job.schedule, new_expr, applied=True)
        message = t("alert.reschedule", name=job.name, old=job.schedule, new=new_expr)
        self.state.record_fix(job.id, "reschedule", message)
        self.escalate(message)
        return True

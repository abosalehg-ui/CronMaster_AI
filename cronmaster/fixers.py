# -*- coding: utf-8 -*-
"""الإصلاح التلقائي: النسخ الاحتياطي، رفع المهلة، التعطيل، إعادة الجدولة، والاستعادة."""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from .backends import get_backend
from .backends.base import BackendError, Capability, CronBackend
from .config import Config, write_secure
from .i18n import t
from .models import ErrorType, FailureAnalysis, Job


class AutoFixer:
    """إصلاح الأخطاء تلقائياً عبر الواجهة الخلفية.

    كل عملية تسأل عن قدرة الواجهة أولاً؛ إن كانت غير مدعومة تُسجَّل رسالة
    صريحة وتُعاد نتيجة فاشلة بدل رفع استثناء أو تخمين سلوك بديل.
    """

    def __init__(self, backend: Optional[CronBackend] = None):
        self.logger = logging.getLogger(__name__)
        self.backend = backend or get_backend()

    # ------------------------------------------------------------
    # حساب المهلة
    # ------------------------------------------------------------

    @staticmethod
    def compute_new_timeout(current: Optional[int]) -> Tuple[int, int]:
        """حساب المهلة الجديدة: (الحالية، الجديدة بعد الزيادة والسقف)"""
        current = current or 30
        return current, min(current + Config.TIMEOUT_INCREMENT, Config.MAX_TIMEOUT)

    # ------------------------------------------------------------
    # النسخ الاحتياطي والاستعادة
    # ------------------------------------------------------------

    def _backup_job(self, job: Job) -> Optional[Path]:
        """نسخة احتياطية من تعريف المهمة الخام قبل أي تعديل عليها"""
        try:
            Config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            backup_file = Config.BACKUP_DIR / f"{job.id}_{datetime.now():%Y%m%d_%H%M%S}.json"
            write_secure(backup_file, json.dumps(job.raw, indent=2, ensure_ascii=False))
            self.logger.info("💾 نسخة احتياطية: %s", backup_file)
            return backup_file
        except OSError as e:
            self.logger.warning("تعذر حفظ النسخة الاحتياطية لـ %s: %s", job.name, e)
            return None

    @staticmethod
    def list_backups(job_id: str) -> List[Path]:
        """النسخ الاحتياطية المتاحة لمهمة، من الأحدث إلى الأقدم"""
        if not Config.BACKUP_DIR.exists():
            return []
        return sorted(Config.BACKUP_DIR.glob(f"{job_id}_*.json"), reverse=True)

    @staticmethod
    def read_backup(path: Path) -> dict:
        """قراءة نسخة احتياطية. ترفع ValueError عند التلف."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise ValueError(f"نسخة احتياطية غير قابلة للقراءة: {e}") from e
        if not isinstance(data, dict):
            raise ValueError("محتوى النسخة الاحتياطية ليس تعريف مهمة")
        return data

    @staticmethod
    def extract_restorable(raw: dict) -> dict:
        """الحقول القابلة للاستعادة من تعريف خام"""
        fields = {}
        payload = raw.get("payload")
        if isinstance(payload, dict) and payload.get("timeoutSeconds") is not None:
            fields["timeout_seconds"] = payload["timeoutSeconds"]
        schedule = raw.get("schedule")
        if isinstance(schedule, dict) and schedule.get("expr"):
            fields["schedule"] = schedule["expr"]
        elif isinstance(raw.get("expr"), str):
            fields["schedule"] = raw["expr"]
        if "enabled" in raw:
            fields["enabled"] = bool(raw["enabled"])
        return fields

    def restore(self, job_id: str, backup_path: Path) -> dict:
        """استعادة الحقول القابلة للعكس من نسخة احتياطية.

        يعيد قاموساً: ``{"restored": {...}, "skipped": {...}}``.
        """
        raw = self.read_backup(backup_path)
        fields = self.extract_restorable(raw)
        restored, skipped = {}, {}

        if not fields:
            raise ValueError(t("restore.nothing"))

        capability_map = {
            "timeout_seconds": Capability.SET_TIMEOUT,
            "schedule": Capability.SET_SCHEDULE,
            "enabled": Capability.SET_ENABLED,
        }

        for field, value in fields.items():
            capability = capability_map[field]
            if not self.backend.supports(capability):
                skipped[field] = t("restore.unsupported", op=capability)
                self.logger.warning(skipped[field])
                continue
            try:
                if field == "timeout_seconds":
                    self.backend.set_timeout(job_id, int(value))
                elif field == "schedule":
                    self.backend.set_schedule(job_id, str(value))
                else:
                    self.backend.set_enabled(job_id, bool(value))
                restored[field] = value
            except (BackendError, ValueError, TypeError) as e:
                skipped[field] = str(e)
                self.logger.error("تعذرت استعادة %s: %s", field, e)

        return {"restored": restored, "skipped": skipped}

    # ------------------------------------------------------------
    # الإصلاحات
    # ------------------------------------------------------------

    def fix(self, analysis: FailureAnalysis) -> FailureAnalysis:
        """محاولة إصلاح الخطأ تلقائياً"""
        if not analysis.auto_fixable:
            return analysis

        if analysis.error_type == ErrorType.TIMEOUT:
            return self._fix_timeout(analysis)

        return analysis

    def _fix_timeout(self, analysis: FailureAnalysis) -> FailureAnalysis:
        """إصلاح مشكلة Timeout بزيادة المهلة"""
        job = analysis.job
        current_timeout, new_timeout = self.compute_new_timeout(job.timeout_seconds)

        if new_timeout == current_timeout:
            analysis.fix_details = t("fix.timeout_max", max=Config.MAX_TIMEOUT)
            return analysis

        if not self.backend.supports(Capability.SET_TIMEOUT):
            analysis.fix_details = t("restore.unsupported", op=Capability.SET_TIMEOUT)
            self.logger.warning(analysis.fix_details)
            return analysis

        self._backup_job(job)

        try:
            self.backend.set_timeout(job.id, new_timeout)
        except BackendError as e:
            analysis.fix_details = t("fix.timeout_failed", error=e)
            self.logger.error(analysis.fix_details)
            return analysis

        analysis.fix_applied = True
        analysis.fix_details = t("fix.timeout_raised", old=current_timeout, new=new_timeout)
        self.logger.info("✅ %s: %s", job.name, analysis.fix_details)
        return analysis

    def retry_job(self, job_id: str) -> bool:
        """إعادة تشغيل مهمة"""
        if not self.backend.supports(Capability.RUN):
            self.logger.warning(t("restore.unsupported", op=Capability.RUN))
            return False
        try:
            return bool(self.backend.run_job(job_id))
        except BackendError as e:
            self.logger.error("فشل إعادة التشغيل: %s", e)
            return False

    def set_timeout(self, job: Job, seconds: int, backup: bool = True) -> bool:
        """ضبط مهلة مباشرة (يُستخدم في التراجع)"""
        if not self.backend.supports(Capability.SET_TIMEOUT):
            self.logger.warning(t("restore.unsupported", op=Capability.SET_TIMEOUT))
            return False
        if backup:
            self._backup_job(job)
        try:
            self.backend.set_timeout(job.id, int(seconds))
        except (BackendError, ValueError, TypeError) as e:
            self.logger.error("تعذر ضبط المهلة: %s", e)
            return False
        return True

    def disable_job(self, job: Job) -> bool:
        """تعطيل مهمة (قاطع الدائرة) — مع نسخة احتياطية قبل التعديل"""
        if not self.backend.supports(Capability.SET_ENABLED):
            self.logger.warning(t("restore.unsupported", op=Capability.SET_ENABLED))
            return False
        self._backup_job(job)
        try:
            self.backend.set_enabled(job.id, False)
        except BackendError as e:
            self.logger.error("تعذر تعطيل المهمة %s: %s", job.name, e)
            return False
        return True

    def reschedule(self, job: Job, expr: str) -> bool:
        """تغيير جدولة مهمة — مع نسخة احتياطية قبل التعديل"""
        if not self.backend.supports(Capability.SET_SCHEDULE):
            self.logger.warning(t("restore.unsupported", op=Capability.SET_SCHEDULE))
            return False
        self._backup_job(job)
        try:
            self.backend.set_schedule(job.id, expr)
        except BackendError as e:
            self.logger.error("تعذرت إعادة جدولة %s: %s", job.name, e)
            return False
        return True


# ============================================================
# إزاحة الجدولة
# ============================================================


def shift_cron_expression(expr: str, minutes: int) -> Optional[str]:
    """إزاحة حقل الدقائق في تعبير cron بمقدار ``minutes``.

    يعيد None إذا كان التعبير غير قياسي (مثل ``@daily``) أو حقل الدقائق
    ليس رقماً أو قائمة أرقام — لا نخمّن في جدولة المستخدم.
    """
    if not expr or expr.startswith("@"):
        return None
    parts = expr.split()
    if len(parts) < 5:
        return None

    minute_field = parts[0]
    if minute_field == "*":
        return None

    shifted = []
    for token in minute_field.split(","):
        if not token.isdigit():
            return None
        shifted.append(str((int(token) + minutes) % 60))

    parts[0] = ",".join(shifted)
    return " ".join(parts)

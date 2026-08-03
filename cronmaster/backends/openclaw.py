# -*- coding: utf-8 -*-
"""الواجهة الخلفية الافتراضية: OpenClaw CLI."""

import json
import subprocess
from typing import Any, List, Optional

from ..models import Job, ms_to_datetime
from .base import BackendError, Capability, CronBackend


class OpenClawError(BackendError):
    """فشل التواصل مع openclaw CLI نفسه (غير مثبت، مهلة، خرج بخطأ...)"""


def run_openclaw(*args: str, timeout: int = 30) -> "subprocess.CompletedProcess[str]":
    """تنفيذ أمر openclaw بمهلة موحدة.

    يرفع OpenClawError إذا تعذر تنفيذ الأمر نفسه — فشل المراقب يجب أن
    يكون حدثاً صريحاً، لا قائمة فارغة تُفسَّر على أنها "كل شيء سليم".
    """
    try:
        return subprocess.run(
            ["openclaw", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as e:
        raise OpenClawError("أمر openclaw غير موجود في PATH — هل هو مثبت؟") from e
    except subprocess.TimeoutExpired as e:
        raise OpenClawError(f"انتهت مهلة تنفيذ: openclaw {' '.join(args)}") from e


def _duration_seconds(state: dict) -> Optional[float]:
    """مدة آخر تشغيل بالثواني إن أعلنتها الواجهة"""
    for key, divisor in (("lastDurationMs", 1000.0), ("lastDurationSeconds", 1.0), ("durationMs", 1000.0)):
        value = state.get(key)
        if isinstance(value, (int, float)) and value >= 0:
            return float(value) / divisor
    return None


class OpenClawBackend(CronBackend):
    """قراءة وتعديل OpenClaw cron jobs عبر الـ CLI."""

    name = "openclaw"
    capabilities = {
        Capability.LIST,
        Capability.SET_TIMEOUT,
        Capability.RUN,
        Capability.SET_ENABLED,
        Capability.SET_SCHEDULE,
        Capability.LAST_STATUS,
        Capability.DURATION,
    }
    error_type = OpenClawError

    # ------------------------------------------------------------
    # القراءة
    # ------------------------------------------------------------

    def list_jobs(self) -> List[Job]:
        """جلب جميع المهام. يرفع OpenClawError عند تعذر الجلب —
        القائمة الفارغة تعني حرفياً "لا توجد مهام"، لا "فشل الجلب"."""
        result = run_openclaw("cron", "list", "--json")

        if result.returncode != 0:
            raise OpenClawError(f"openclaw cron list فشل: {result.stderr.strip() or 'بدون تفاصيل'}")

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise OpenClawError(f"مخرجات openclaw ليست JSON صالحاً: {e}") from e

        # OpenClaw يرجع {"jobs": [...]} أو [...]
        jobs_data = data.get("jobs", data) if isinstance(data, dict) else data
        jobs = []

        for job in jobs_data:
            jobs.append(self._parse_job(job))

        return jobs

    @staticmethod
    def _parse_job(job: Any) -> Job:
        """تحويل تعريف خام إلى Job"""
        # استخراج timeout من payload
        timeout = None
        payload = job.get("payload", {})
        if isinstance(payload, dict):
            timeout = payload.get("timeoutSeconds")

        schedule = job.get("schedule", {})
        state = job.get("state", {})

        return Job(
            id=job.get("id", ""),
            name=job.get("name", "Unknown"),
            enabled=job.get("enabled", False),
            schedule=schedule.get("expr", "unknown"),
            last_status=state.get("lastStatus"),
            last_error=state.get("lastError"),
            last_error_reason=state.get("lastErrorReason"),
            consecutive_errors=state.get("consecutiveErrors", 0),
            timeout_seconds=timeout,
            last_run_at=ms_to_datetime(state.get("lastRunAtMs")),
            next_run_at=ms_to_datetime(state.get("nextRunAtMs")),
            last_duration_seconds=_duration_seconds(state),
            raw=job,
        )

    # ------------------------------------------------------------
    # التعديل
    # ------------------------------------------------------------

    def set_timeout(self, job_id: str, seconds: int) -> bool:
        result = run_openclaw("cron", "edit", job_id, "--timeout-seconds", str(seconds))
        if result.returncode != 0:
            raise OpenClawError(result.stderr.strip() or "فشل تحديث timeout")
        return True

    def run_job(self, job_id: str) -> bool:
        result = run_openclaw("cron", "run", job_id)
        return result.returncode == 0

    def set_enabled(self, job_id: str, enabled: bool) -> bool:
        action = "enable" if enabled else "disable"
        result = run_openclaw("cron", action, job_id)
        if result.returncode != 0:
            raise OpenClawError(result.stderr.strip() or f"فشل {action} للمهمة")
        return True

    def set_schedule(self, job_id: str, expr: str) -> bool:
        result = run_openclaw("cron", "edit", job_id, "--cron", expr)
        if result.returncode != 0:
            raise OpenClawError(result.stderr.strip() or "فشل تحديث الجدولة")
        return True

# اسم مهجور محفوظ للتوافق مع الكود القديم
OpenClawCronParser = OpenClawBackend

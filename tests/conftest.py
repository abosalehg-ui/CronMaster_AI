# -*- coding: utf-8 -*-
"""أدوات مشتركة للاختبارات: عزل المسارات وواجهة خلفية مزيفة.

لا اختبار هنا يلمس الشبكة ولا عملية فرعية حقيقية ولا مجلد المستخدم الحقيقي.
"""

import sys
import types
from datetime import datetime, timedelta

import pytest

from cronmaster.backends.base import BackendError, Capability, CronBackend
from cronmaster.config import Config
from cronmaster.models import Job


# ============================================================
# عزل البيئة
# ============================================================


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """عزل كل مسارات Config وإعداداتها القابلة للتعديل عن جهاز المطور"""
    monkeypatch.setattr(Config, "WORK_DIR", tmp_path)
    monkeypatch.setattr(Config, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(Config, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(Config, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(Config, "CONFIG_FILE", tmp_path / "config.json")

    # إعدادات قد تُعدّلها الاختبارات — نُعيدها إلى قيمها الافتراضية بعد كل اختبار
    for attr in (
        "NOTIFIERS", "QUIET_HOURS", "TELEGRAM_CHAT_ID", "HEALTHCHECK_PING_URL",
        "PROMETHEUS_TEXTFILE", "LLM_ENABLED", "LLM_MIN_CONFIDENCE", "LLM_CACHE_DAYS",
        "HISTORY_ENABLED", "CIRCUIT_BREAKER_ENABLED", "CIRCUIT_BREAKER_THRESHOLD",
        "ROLLBACK_TIMEOUT_ENABLED", "ROLLBACK_AFTER_CYCLES", "AUTO_RESCHEDULE",
        "MAX_TIMEOUT_FIXES", "DURATION_REGRESSION_FACTOR", "DURATION_REGRESSION_MIN_SAMPLES",
        "BACKEND", "LANG", "ALERT_THRESHOLD",
    ):
        monkeypatch.setattr(Config, attr, getattr(Config, attr))

    monkeypatch.setattr(Config, "NOTIFIERS", [])
    monkeypatch.setattr(Config, "QUIET_HOURS", {})
    monkeypatch.setattr(Config, "TELEGRAM_CHAT_ID", "")
    monkeypatch.setattr(Config, "HEALTHCHECK_PING_URL", "")
    monkeypatch.setattr(Config, "PROMETHEUS_TEXTFILE", "")
    monkeypatch.setattr(Config, "LLM_ENABLED", False)
    return tmp_path


# ============================================================
# بناء المهام
# ============================================================


def make_job(**overrides) -> Job:
    defaults = dict(id="j1", name="Test Job", enabled=True, schedule="0 * * * *")
    defaults.update(overrides)
    return Job(**defaults)


def failing_job(error: str = "Request timed out", **overrides) -> Job:
    defaults = dict(
        id="j1",
        name="Failing Job",
        enabled=True,
        schedule="0 * * * *",
        last_status="error",
        last_error=error,
        consecutive_errors=3,
        timeout_seconds=60,
        last_run_at=datetime.now() - timedelta(hours=2),
        raw={"id": "j1", "name": "Failing Job", "payload": {"timeoutSeconds": 60},
             "schedule": {"expr": "0 * * * *"}, "enabled": True},
    )
    defaults.update(overrides)
    return Job(**defaults)


# ============================================================
# واجهة خلفية مزيفة
# ============================================================


class FakeBackend(CronBackend):
    """واجهة خلفية في الذاكرة تسجّل كل عملية تُطلب منها."""

    name = "fake"
    error_type = BackendError

    def __init__(self, jobs=None, capabilities=None, fail_list=False, fail_ops=False):
        super().__init__()
        self.jobs = list(jobs or [])
        self.calls = []
        self.fail_list = fail_list
        self.fail_ops = fail_ops
        self.capabilities = set(
            capabilities
            if capabilities is not None
            else {
                Capability.LIST, Capability.SET_TIMEOUT, Capability.RUN,
                Capability.SET_ENABLED, Capability.SET_SCHEDULE,
                Capability.LAST_STATUS, Capability.DURATION,
            }
        )

    # ---- القراءة ----

    def list_jobs(self):
        self.calls.append(("list",))
        if self.fail_list:
            raise BackendError("الواجهة الخلفية لا تستجيب")
        return list(self.jobs)

    def _find(self, job_id):
        return next((j for j in self.jobs if j.id == job_id), None)

    # ---- التعديل ----

    def set_timeout(self, job_id, seconds):
        self.calls.append(("set_timeout", job_id, seconds))
        if self.fail_ops:
            raise BackendError("رفض التعديل")
        job = self._find(job_id)
        if job:
            job.timeout_seconds = seconds
        return True

    def run_job(self, job_id):
        self.calls.append(("run", job_id))
        return not self.fail_ops

    def set_enabled(self, job_id, enabled):
        self.calls.append(("set_enabled", job_id, enabled))
        if self.fail_ops:
            raise BackendError("رفض التعديل")
        job = self._find(job_id)
        if job:
            job.enabled = enabled
        return True

    def set_schedule(self, job_id, expr):
        self.calls.append(("set_schedule", job_id, expr))
        if self.fail_ops:
            raise BackendError("رفض التعديل")
        job = self._find(job_id)
        if job:
            job.schedule = expr
        return True

    # ---- مساعدات للاختبارات ----

    def mutations(self):
        """كل العمليات التي تُعدّل حالة النظام (لفحص dry-run)"""
        return [c for c in self.calls if c[0] != "list"]


# ============================================================
# SDK مزيف لـ anthropic (لا شبكة، لا حزمة حقيقية مطلوبة)
# ============================================================


def build_fake_anthropic():
    """وحدة ``anthropic`` مزيفة تحمل نفس أسماء الاستثناءات التي يلتقطها الكود"""
    mod = types.ModuleType("anthropic")

    class APIError(Exception):
        pass

    class APIConnectionError(APIError):
        pass

    class APIStatusError(APIError):
        def __init__(self, message="", status_code=500):
            super().__init__(message)
            self.status_code = status_code

    class AuthenticationError(APIStatusError):
        pass

    class RateLimitError(APIStatusError):
        pass

    class Anthropic:  # pragma: no cover — لا يُبنى إلا عند غياب عميل محقون
        def __init__(self, *a, **k):
            raise AssertionError("يجب ألا يُبنى عميل حقيقي داخل الاختبارات")

    mod.APIError = APIError
    mod.APIConnectionError = APIConnectionError
    mod.APIStatusError = APIStatusError
    mod.AuthenticationError = AuthenticationError
    mod.RateLimitError = RateLimitError
    mod.Anthropic = Anthropic
    return mod


@pytest.fixture
def fake_anthropic(monkeypatch):
    mod = build_fake_anthropic()
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    return mod

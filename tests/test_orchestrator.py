# -*- coding: utf-8 -*-
"""اختبارات المنسّق فوق واجهة خلفية مزيفة: الإصلاحات الموسّعة والاستعادة."""

import json
from datetime import datetime, timedelta

import pytest

from cronmaster.config import Config
from cronmaster.core import (
    EXIT_FIXES_APPLIED,
    EXIT_JOBS_FAILING,
    EXIT_MONITOR_FAILURE,
    EXIT_OK,
    CronMaster,
)

from conftest import FakeBackend, failing_job, make_job


def build(sandbox, jobs, **config):
    """محرك فوق واجهة مزيفة مع إعدادات محددة"""
    for key, value in config.items():
        setattr(Config, key, value)
    backend = FakeBackend(jobs=jobs)
    master = CronMaster(backend=backend)
    return master, backend


# ============================================================
# حارس الإصلاح غير المجدي
# ============================================================


def test_timeout_fix_counter_increments(sandbox):
    job = failing_job(error="Request timed out")
    master, backend = build(sandbox, [job])

    master.monitor(alert=False)

    assert master.state.get_timeout_fixes(job.id) == 1
    assert ("set_timeout", "j1", 180) in backend.calls


def test_futile_timeout_fix_stops_and_escalates(sandbox):
    job = failing_job(error="Request timed out")
    master, backend = build(sandbox, [job], MAX_TIMEOUT_FIXES=3)
    master.state.state["timeout_fixes"][job.id] = 3

    result = master.monitor(alert=False)

    assert result["fixes_applied"] == 0
    assert not any(c[0] == "set_timeout" for c in backend.calls)
    assert any("لم يعد مجدياً" in notice for notice in result["notices"])


def test_recovery_clears_the_futile_fix_counter(sandbox):
    job = failing_job(error="Request timed out")
    master, backend = build(sandbox, [job])
    master.monitor(alert=False)
    assert master.state.get_timeout_fixes(job.id) == 1

    backend.jobs = [make_job(id="j1", name="Failing Job", last_status="ok")]
    master.monitor(alert=False)

    assert master.state.get_timeout_fixes("j1") == 0
    assert master.state.get_pending_rollback("j1") is None


# ============================================================
# التراجع عن رفع المهلة
# ============================================================


def test_rollback_restores_previous_timeout(sandbox):
    job = failing_job(error="Request timed out", timeout_seconds=60)
    master, backend = build(sandbox, [job], ROLLBACK_TIMEOUT_ENABLED=True, ROLLBACK_AFTER_CYCLES=1)

    master.monitor(alert=False)  # الدورة الأولى ترفع المهلة إلى 180
    assert job.timeout_seconds == 180
    assert master.state.get_pending_rollback("j1")["previous"] == 60

    result = master.monitor(alert=False)  # ما زالت تفشل بنفس السبب → تراجع

    assert ("set_timeout", "j1", 60) in backend.calls
    assert master.state.get_pending_rollback("j1") is None
    assert any("أُعيدت مهلة" in notice for notice in result["notices"])


def test_rollback_disabled_by_default(sandbox):
    job = failing_job(error="Request timed out", timeout_seconds=60)
    master, backend = build(sandbox, [job], ROLLBACK_TIMEOUT_ENABLED=False)

    master.monitor(alert=False)
    master.monitor(alert=False)

    assert ("set_timeout", "j1", 60) not in backend.calls


def test_rollback_dry_run_mutates_nothing(sandbox):
    job = failing_job(error="Request timed out", timeout_seconds=60)
    master, backend = build(sandbox, [job], ROLLBACK_TIMEOUT_ENABLED=True, ROLLBACK_AFTER_CYCLES=1)
    master.state.set_pending_rollback("j1", 60)

    result = master.monitor(alert=False, dry_run=True)

    assert backend.mutations() == []
    assert result["fixes_applied"] == 0


# ============================================================
# قاطع الدائرة
# ============================================================


def stubborn_job(errors=12):
    return failing_job(error="permission denied", consecutive_errors=errors, name="Stubborn")


def test_circuit_breaker_disables_after_threshold(sandbox):
    job = stubborn_job()
    master, backend = build(sandbox, [job], CIRCUIT_BREAKER_ENABLED=True, CIRCUIT_BREAKER_THRESHOLD=10)

    result = master.monitor(alert=False)

    assert ("set_enabled", "j1", False) in backend.calls
    assert master.state.is_circuit_open("j1") is True
    assert result["fixes_applied"] == 1
    assert any("قاطع الدائرة" in notice for notice in result["notices"])
    # الإجراء مسجَّل في سجل الإصلاحات ليظهر في history وفي التقرير
    assert any(f["fix_type"] == "circuit_breaker" for f in master.state.state["fixes_applied"])


def test_circuit_breaker_backs_up_before_disabling(sandbox):
    job = stubborn_job()
    master, _ = build(sandbox, [job], CIRCUIT_BREAKER_ENABLED=True, CIRCUIT_BREAKER_THRESHOLD=10)

    master.monitor(alert=False)

    backups = list(Config.BACKUP_DIR.glob("j1_*.json"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8"))["id"] == "j1"


def test_circuit_breaker_disabled_by_default(sandbox):
    job = stubborn_job()
    master, backend = build(sandbox, [job])
    master.monitor(alert=False)
    assert not any(c[0] == "set_enabled" for c in backend.calls)


def test_circuit_breaker_ignores_auto_fixable_errors(sandbox):
    job = failing_job(error="Connection refused", consecutive_errors=50)
    master, backend = build(sandbox, [job], CIRCUIT_BREAKER_ENABLED=True, CIRCUIT_BREAKER_THRESHOLD=10)

    master.monitor(alert=False)

    assert not any(c[0] == "set_enabled" for c in backend.calls)


def test_circuit_breaker_opens_once(sandbox):
    job = stubborn_job()
    master, backend = build(sandbox, [job], CIRCUIT_BREAKER_ENABLED=True, CIRCUIT_BREAKER_THRESHOLD=10)

    master.monitor(alert=False)
    master.monitor(alert=False)

    assert len([c for c in backend.calls if c[0] == "set_enabled"]) == 1


def test_circuit_breaker_dry_run_mutates_nothing(sandbox):
    job = stubborn_job()
    master, backend = build(sandbox, [job], CIRCUIT_BREAKER_ENABLED=True, CIRCUIT_BREAKER_THRESHOLD=10)

    result = master.monitor(alert=False, dry_run=True)

    assert backend.mutations() == []
    assert master.state.is_circuit_open("j1") is False
    assert any("[dry-run]" in notice for notice in result["notices"])


def test_circuit_breaker_needs_backend_support(sandbox):
    from cronmaster.backends.base import Capability

    job = stubborn_job()
    Config.CIRCUIT_BREAKER_ENABLED = True
    Config.CIRCUIT_BREAKER_THRESHOLD = 10
    backend = FakeBackend(jobs=[job], capabilities={Capability.LIST, Capability.RUN})
    master = CronMaster(backend=backend)

    master.monitor(alert=False)

    assert backend.mutations() == []
    assert master.state.is_circuit_open("j1") is False


# ============================================================
# إعادة الجدولة عند أخطاء حد الطلبات
# ============================================================


def rate_limited_job(**overrides):
    defaults = dict(
        error="HTTP 429 too many requests",
        consecutive_errors=4,
        schedule="0 3 * * *",
        last_run_at=datetime.now() - timedelta(hours=5),
    )
    defaults.update(overrides)
    return failing_job(**defaults)


def test_reschedule_applied_when_enabled(sandbox):
    job = rate_limited_job()
    master, backend = build(sandbox, [job], AUTO_RESCHEDULE=True, RESCHEDULE_SHIFT_MINUTES=17)

    result = master.monitor(alert=False)

    assert ("set_schedule", "j1", "17 3 * * *") in backend.calls
    assert master.state.has_reschedule("j1")
    assert any("نُقلت جدولة" in notice for notice in result["notices"])


def test_reschedule_only_proposed_when_disabled(sandbox):
    job = rate_limited_job()
    master, backend = build(sandbox, [job], AUTO_RESCHEDULE=False)

    result = master.monitor(alert=False)

    assert not any(c[0] == "set_schedule" for c in backend.calls)
    assert any("يُقترح نقل جدولة" in notice for notice in result["notices"])
    assert master.state.state["reschedules"]["j1"]["applied"] is False


def test_reschedule_dry_run_mutates_nothing(sandbox):
    job = rate_limited_job()
    master, backend = build(sandbox, [job], AUTO_RESCHEDULE=True)

    master.monitor(alert=False, dry_run=True)

    assert backend.mutations() == []
    assert not master.state.has_reschedule("j1")


def test_reschedule_skips_unshiftable_expressions(sandbox):
    job = rate_limited_job(schedule="@daily")
    master, backend = build(sandbox, [job], AUTO_RESCHEDULE=True)

    master.monitor(alert=False)

    assert not any(c[0] == "set_schedule" for c in backend.calls)


def test_reschedule_happens_once(sandbox):
    job = rate_limited_job()
    master, backend = build(sandbox, [job], AUTO_RESCHEDULE=True)

    master.monitor(alert=False)
    master.monitor(alert=False)

    assert len([c for c in backend.calls if c[0] == "set_schedule"]) == 1


# ============================================================
# التحذير المبكر من تدهور المدة
# ============================================================


def test_duration_regression_warns_before_timeout(sandbox):
    healthy = make_job(id="slow", name="Slow Job", last_status="ok", last_run_at=datetime.now())
    master, _ = build(sandbox, [healthy], DURATION_REGRESSION_FACTOR=2.0, DURATION_REGRESSION_MIN_SAMPLES=4)

    base = datetime(2026, 5, 1)
    for i, seconds in enumerate([10, 10, 10, 10, 40, 40, 40, 40]):
        master.history_store.record_observation(
            make_job(id="slow", name="Slow Job", last_status="ok",
                     last_run_at=base + timedelta(hours=i), last_duration_seconds=seconds)
        )

    result = master.monitor(alert=False)

    assert any("تباطؤ ملحوظ" in notice for notice in result["notices"])


def test_duration_regression_respects_min_samples(sandbox):
    healthy = make_job(id="slow", name="Slow Job", last_status="ok", last_run_at=datetime.now())
    master, _ = build(sandbox, [healthy], DURATION_REGRESSION_MIN_SAMPLES=20)

    base = datetime(2026, 5, 1)
    for i, seconds in enumerate([10, 10, 40, 40]):
        master.history_store.record_observation(
            make_job(id="slow", last_status="ok", last_run_at=base + timedelta(hours=i),
                     last_duration_seconds=seconds)
        )

    result = master.monitor(alert=False)
    assert result["notices"] == []


# ============================================================
# السجل التاريخي داخل دورة المراقبة
# ============================================================


def test_monitor_records_history(sandbox):
    job = failing_job(error="Request timed out")
    master, _ = build(sandbox, [job])

    master.monitor(alert=False)

    assert master.history_store.run_count("j1") == 1
    assert master.history_store.success_rate("j1") == 0.0


def test_dry_run_records_no_history(sandbox):
    job = failing_job(error="Request timed out")
    master, _ = build(sandbox, [job])

    master.monitor(alert=False, dry_run=True)

    assert master.history_store.run_count("j1") == 0


def test_history_pruning_runs_at_most_daily(sandbox):
    job = make_job(id="a", last_status="ok", last_run_at=datetime.now())
    master, _ = build(sandbox, [job])

    master.monitor(alert=False)
    first = master.state.state["last_prune"]
    assert first is not None

    master.monitor(alert=False)
    assert master.state.state["last_prune"] == first  # لم يُنظَّف مجدداً في نفس اليوم


# ============================================================
# الاستعادة
# ============================================================


def make_backup(job_id="j1", timeout=45, schedule="30 2 * * *", enabled=True):
    Config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    path = Config.BACKUP_DIR / f"{job_id}_20260101_010101.json"
    path.write_text(
        json.dumps(
            {"id": job_id, "payload": {"timeoutSeconds": timeout},
             "schedule": {"expr": schedule}, "enabled": enabled},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_restore_without_backups_reports_error(sandbox):
    master, _ = build(sandbox, [failing_job()])
    result = master.restore("j1")
    assert "error" in result


def test_restore_applies_reversible_fields(sandbox):
    job = failing_job(timeout_seconds=600, schedule="0 * * * *", enabled=False)
    master, backend = build(sandbox, [job])
    path = make_backup(timeout=45, schedule="30 2 * * *", enabled=True)

    result = master.restore("j1", path)

    assert result["restored"] == {"timeout_seconds": 45, "schedule": "30 2 * * *", "enabled": True}
    assert ("set_timeout", "j1", 45) in backend.calls
    assert ("set_schedule", "j1", "30 2 * * *") in backend.calls
    assert ("set_enabled", "j1", True) in backend.calls


def test_restore_closes_the_circuit(sandbox):
    master, _ = build(sandbox, [failing_job()])
    master.state.open_circuit("j1", 12)
    path = make_backup()

    master.restore("j1", path)

    assert master.state.is_circuit_open("j1") is False
    assert master.state.get_timeout_fixes("j1") == 0


def test_restore_skips_unsupported_operations(sandbox):
    from cronmaster.backends.base import Capability

    backend = FakeBackend(jobs=[failing_job()], capabilities={Capability.LIST, Capability.SET_ENABLED})
    master = CronMaster(backend=backend)
    path = make_backup()

    result = master.restore("j1", path)

    assert "enabled" in result["restored"]
    assert "timeout_seconds" in result["skipped"]
    assert "schedule" in result["skipped"]


def test_restore_rejects_corrupt_backup(sandbox):
    master, _ = build(sandbox, [failing_job()])
    Config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    path = Config.BACKUP_DIR / "j1_20260101_010101.json"
    path.write_text("{ not json", encoding="utf-8")

    result = master.restore("j1", path)
    assert "error" in result


def test_list_backups_newest_first(sandbox):
    master, _ = build(sandbox, [failing_job()])
    Config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    for stamp in ("20260101_010101", "20260202_020202"):
        (Config.BACKUP_DIR / f"j1_{stamp}.json").write_text("{}", encoding="utf-8")

    names = [p.name for p in master.list_backups("j1")]
    assert names == ["j1_20260202_020202.json", "j1_20260101_010101.json"]


# ============================================================
# أكواد الخروج
# ============================================================


@pytest.mark.parametrize(
    "result,expected",
    [
        ({"error": "boom"}, EXIT_MONITOR_FAILURE),
        ({"failed_jobs": 0, "fixes_applied": 0}, EXIT_OK),
        ({"failed_jobs": 2, "fixes_applied": 0}, EXIT_JOBS_FAILING),
        ({"failed_jobs": 2, "fixes_applied": 1}, EXIT_FIXES_APPLIED),
    ],
)
def test_exit_codes(result, expected):
    assert CronMaster.exit_code(result) == expected


def test_monitor_result_carries_critical_count(sandbox):
    jobs = [failing_job(error="permission denied", consecutive_errors=5)]
    master, _ = build(sandbox, jobs, ALERT_THRESHOLD=2)
    result = master.monitor(alert=False)
    assert result["critical_jobs"] == 1


def test_backend_failure_short_circuits(sandbox):
    backend = FakeBackend(fail_list=True)
    master = CronMaster(backend=backend)

    result = master.monitor(alert=False)

    assert "error" in result
    assert "total_jobs" not in result
    assert CronMaster.exit_code(result) == EXIT_MONITOR_FAILURE

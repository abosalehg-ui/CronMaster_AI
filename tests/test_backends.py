# -*- coding: utf-8 -*-
"""اختبارات طبقة الواجهات الخلفية: الاختيار، crontab، والقدرات."""

import subprocess

import pytest

import cronmaster.backends.crontab as crontab_module
from cronmaster.backends import get_backend
from cronmaster.backends.base import BackendError, Capability, CronBackend
from cronmaster.backends.crontab import CrontabError, SystemCrontabBackend
from cronmaster.backends.openclaw import OpenClawBackend, OpenClawCronParser, OpenClawError
from cronmaster.config import Config
from cronmaster.fixers import shift_cron_expression


# ============================================================
# اختيار الواجهة
# ============================================================


def test_default_backend_is_openclaw(sandbox):
    assert isinstance(get_backend(), OpenClawBackend)


def test_backend_selected_by_name(sandbox):
    assert isinstance(get_backend("crontab"), SystemCrontabBackend)


def test_backend_selected_by_config(sandbox, monkeypatch):
    monkeypatch.setattr(Config, "BACKEND", "crontab")
    assert isinstance(get_backend(), SystemCrontabBackend)


def test_unknown_backend_falls_back_to_default(sandbox):
    assert isinstance(get_backend("quantum-cron"), OpenClawBackend)


def test_deprecated_alias_points_at_the_backend():
    assert OpenClawCronParser is OpenClawBackend


def test_openclaw_error_is_a_backend_error():
    assert issubclass(OpenClawError, BackendError)
    assert issubclass(CrontabError, BackendError)


# ============================================================
# القدرات
# ============================================================


def test_openclaw_declares_full_capabilities():
    backend = OpenClawBackend()
    for capability in (
        Capability.LIST, Capability.SET_TIMEOUT, Capability.RUN,
        Capability.SET_ENABLED, Capability.SET_SCHEDULE, Capability.LAST_STATUS,
    ):
        assert backend.supports(capability)


def test_crontab_declares_reduced_capabilities():
    backend = SystemCrontabBackend()
    assert backend.supports(Capability.LIST)
    assert backend.supports(Capability.SET_SCHEDULE)
    assert backend.supports(Capability.SET_ENABLED)
    # crontab لا يعرف مهلة لكل مهمة ولا حالة آخر تشغيل
    assert not backend.supports(Capability.SET_TIMEOUT)
    assert not backend.supports(Capability.LAST_STATUS)
    assert not backend.supports(Capability.DURATION)


def test_base_backend_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        CronBackend().list_jobs()


# ============================================================
# crontab: القراءة
# ============================================================

SAMPLE_CRONTAB = """\
# a comment
PATH=/usr/bin:/bin

0 3 * * * /usr/local/bin/backup.sh
*/15 * * * * python3 /opt/sync.py --quiet
@daily /usr/local/bin/rotate-logs
# CRONMASTER-DISABLED 30 4 * * * /usr/local/bin/broken.sh
"""


def fake_crontab(stdout="", returncode=0, stderr=""):
    def runner(*args, stdin=None, timeout=30):
        runner.calls.append((args, stdin))
        return subprocess.CompletedProcess(args=["crontab", *args], returncode=returncode,
                                           stdout=stdout, stderr=stderr)

    runner.calls = []
    return runner


def test_crontab_parses_jobs(monkeypatch):
    monkeypatch.setattr(crontab_module, "run_crontab", fake_crontab(stdout=SAMPLE_CRONTAB))
    jobs = SystemCrontabBackend().list_jobs()

    assert [j.schedule for j in jobs] == ["0 3 * * *", "*/15 * * * *", "@daily", "30 4 * * *"]
    assert jobs[0].name == "/usr/local/bin/backup.sh"
    assert jobs[0].enabled is True
    assert jobs[3].enabled is False  # السطر المعطَّل بعلامتنا
    # لا حالة مختلقة: crontab ببساطة لا يعرفها
    assert all(j.last_status is None for j in jobs)


def test_crontab_ignores_comments_and_env_lines(monkeypatch):
    monkeypatch.setattr(crontab_module, "run_crontab", fake_crontab(stdout=SAMPLE_CRONTAB))
    jobs = SystemCrontabBackend().list_jobs()
    assert not any("PATH" in j.name for j in jobs)
    assert not any(j.name.startswith("a comment") for j in jobs)


def test_crontab_job_ids_are_stable(monkeypatch):
    monkeypatch.setattr(crontab_module, "run_crontab", fake_crontab(stdout=SAMPLE_CRONTAB))
    first = [j.id for j in SystemCrontabBackend().list_jobs()]
    second = [j.id for j in SystemCrontabBackend().list_jobs()]
    assert first == second
    assert len(set(first)) == len(first)


def test_empty_crontab_is_not_an_error(monkeypatch):
    monkeypatch.setattr(
        crontab_module, "run_crontab", fake_crontab(returncode=1, stderr="no crontab for user")
    )
    assert SystemCrontabBackend().list_jobs() == []


def test_crontab_read_failure_raises(monkeypatch):
    monkeypatch.setattr(crontab_module, "run_crontab", fake_crontab(returncode=1, stderr="permission denied"))
    with pytest.raises(CrontabError):
        SystemCrontabBackend().list_jobs()


def test_missing_crontab_binary_raises(monkeypatch):
    def raise_fnf(*a, **k):
        raise FileNotFoundError("crontab")

    monkeypatch.setattr(crontab_module.subprocess, "run", raise_fnf)
    with pytest.raises(CrontabError):
        crontab_module.run_crontab("-l")


# ============================================================
# crontab: التعديل
# ============================================================


def test_crontab_set_timeout_is_unsupported(monkeypatch):
    monkeypatch.setattr(crontab_module, "run_crontab", fake_crontab(stdout=SAMPLE_CRONTAB))
    backend = SystemCrontabBackend()
    job = backend.list_jobs()[0]
    with pytest.raises(CrontabError):
        backend.set_timeout(job.id, 300)


def test_crontab_disable_comments_the_line(monkeypatch):
    runner = fake_crontab(stdout=SAMPLE_CRONTAB)
    monkeypatch.setattr(crontab_module, "run_crontab", runner)
    backend = SystemCrontabBackend()
    job = backend.list_jobs()[0]

    assert backend.set_enabled(job.id, False) is True
    written = runner.calls[-1][1]
    assert "# CRONMASTER-DISABLED 0 3 * * * /usr/local/bin/backup.sh" in written


def test_crontab_enable_uncomments_the_line(monkeypatch):
    runner = fake_crontab(stdout=SAMPLE_CRONTAB)
    monkeypatch.setattr(crontab_module, "run_crontab", runner)
    backend = SystemCrontabBackend()
    disabled = backend.list_jobs()[3]

    assert backend.set_enabled(disabled.id, True) is True
    written = runner.calls[-1][1]
    assert "\n30 4 * * * /usr/local/bin/broken.sh" in written


def test_crontab_set_schedule_rewrites_expression(monkeypatch):
    runner = fake_crontab(stdout=SAMPLE_CRONTAB)
    monkeypatch.setattr(crontab_module, "run_crontab", runner)
    backend = SystemCrontabBackend()
    job = backend.list_jobs()[0]

    assert backend.set_schedule(job.id, "17 3 * * *") is True
    assert "17 3 * * * /usr/local/bin/backup.sh" in runner.calls[-1][1]


def test_crontab_edit_of_unknown_job_is_a_noop(monkeypatch):
    runner = fake_crontab(stdout=SAMPLE_CRONTAB)
    monkeypatch.setattr(crontab_module, "run_crontab", runner)
    assert SystemCrontabBackend().set_schedule("does-not-exist", "0 0 * * *") is False
    assert all(call[0][0] == "-l" for call in runner.calls)  # لم تُكتب crontab


# ============================================================
# إزاحة تعبير cron
# ============================================================


@pytest.mark.parametrize(
    "expr,minutes,expected",
    [
        ("0 3 * * *", 17, "17 3 * * *"),
        ("50 3 * * *", 17, "7 3 * * *"),
        ("0,30 * * * *", 5, "5,35 * * * *"),
        ("*/15 * * * *", 17, None),
        ("* * * * *", 17, None),
        ("@daily", 17, None),
        ("bad", 17, None),
        ("", 17, None),
    ],
)
def test_shift_cron_expression(expr, minutes, expected):
    assert shift_cron_expression(expr, minutes) == expected

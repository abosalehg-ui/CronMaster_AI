# -*- coding: utf-8 -*-
"""اختبارات التكامل مع أنظمة المراقبة (بلا أي نداء شبكة حقيقي)."""

import cronmaster.metrics as metrics
from cronmaster.metrics import build_prometheus_text, ping_healthcheck, write_prometheus_textfile

from conftest import make_job

HEALTHY = {
    "total_jobs": 5,
    "failed_jobs": 2,
    "critical_jobs": 1,
    "silent_jobs": ["A"],
    "fixes_applied": 3,
}


# ============================================================
# نص Prometheus
# ============================================================


def test_all_expected_metrics_present():
    jobs = [make_job(id="a", name="Alpha", consecutive_errors=4)]
    text = build_prometheus_text(HEALTHY, jobs)

    for name in (
        "cronmaster_jobs_total",
        "cronmaster_jobs_failed",
        "cronmaster_jobs_silent",
        "cronmaster_jobs_critical",
        "cronmaster_job_consecutive_errors",
        "cronmaster_fixes_applied_total",
        "cronmaster_last_run_timestamp_seconds",
        "cronmaster_monitor_success",
    ):
        assert f"# TYPE {name} gauge" in text

    assert "cronmaster_jobs_total 5" in text
    assert "cronmaster_jobs_failed 2" in text
    assert "cronmaster_jobs_silent 1" in text
    assert "cronmaster_jobs_critical 1" in text
    assert "cronmaster_fixes_applied_total 3" in text
    assert "cronmaster_monitor_success 1" in text
    assert 'cronmaster_job_consecutive_errors{job="Alpha",job_id="a"} 4' in text


def test_failed_monitor_reports_zero_success_and_no_job_metrics():
    text = build_prometheus_text({"error": "backend down"}, [])
    assert "cronmaster_monitor_success 0" in text
    assert "cronmaster_jobs_total" not in text


def test_disabled_jobs_are_not_exported():
    jobs = [make_job(id="a", enabled=True), make_job(id="b", enabled=False)]
    text = build_prometheus_text(HEALTHY, jobs)
    assert 'job_id="a"' in text
    assert 'job_id="b"' not in text


def test_label_values_are_escaped():
    jobs = [make_job(id="a", name='Weird "quoted"\\job')]
    text = build_prometheus_text(HEALTHY, jobs)
    assert r'job="Weird \"quoted\"\\job"' in text


def test_textfile_is_written_atomically(tmp_path):
    target = tmp_path / "sub" / "cronmaster.prom"
    assert write_prometheus_textfile(str(target), HEALTHY, []) is True
    assert target.exists()
    assert not target.with_name(target.name + ".tmp").exists()
    assert "cronmaster_jobs_total 5" in target.read_text(encoding="utf-8")


def test_textfile_failure_is_swallowed(tmp_path):
    # المسار يشير إلى مجلد: الكتابة تفشل ولا تُرفع
    directory = tmp_path / "dir"
    directory.mkdir()
    assert write_prometheus_textfile(str(directory), HEALTHY, []) is False


# ============================================================
# نبضة healthcheck
# ============================================================


class FakeResponse:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_ping_success_uses_plain_url(monkeypatch):
    seen = []
    monkeypatch.setattr(
        metrics.urllib.request, "urlopen",
        lambda url, timeout=None: (seen.append(url), FakeResponse())[1],
    )
    assert ping_healthcheck("https://hc.example/abc", success=True) is True
    assert seen == ["https://hc.example/abc"]


def test_ping_failure_appends_fail_suffix(monkeypatch):
    seen = []
    monkeypatch.setattr(
        metrics.urllib.request, "urlopen",
        lambda url, timeout=None: (seen.append(url), FakeResponse())[1],
    )
    ping_healthcheck("https://hc.example/abc/", success=False)
    assert seen == ["https://hc.example/abc/fail"]


def test_ping_network_error_is_swallowed(monkeypatch):
    def boom(url, timeout=None):
        raise OSError("لا شبكة")

    monkeypatch.setattr(metrics.urllib.request, "urlopen", boom)
    assert ping_healthcheck("https://hc.example/abc", success=True) is False


def test_ping_skips_empty_or_invalid_url(monkeypatch):
    def fail(*a, **k):
        raise AssertionError("يجب ألا يُفتح أي اتصال")

    monkeypatch.setattr(metrics.urllib.request, "urlopen", fail)
    assert ping_healthcheck("", success=True) is False
    assert ping_healthcheck("not-a-url", success=True) is False

# -*- coding: utf-8 -*-
"""اختبارات وحدة لـ CronMaster_AI"""

import json
import subprocess
from datetime import datetime, timedelta

import pytest

import CronMaster_AI as cm


def make_completed(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=["openclaw"], returncode=returncode, stdout=stdout, stderr=stderr)


def make_job(**overrides):
    defaults = dict(id="j1", name="Test Job", enabled=True, schedule="0 * * * *")
    defaults.update(overrides)
    return cm.OpenClawJob(**defaults)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """عزل كل مسارات Config عن جهاز المطور"""
    monkeypatch.setattr(cm.Config, "WORK_DIR", tmp_path)
    monkeypatch.setattr(cm.Config, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(cm.Config, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(cm.Config, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(cm.Config, "CONFIG_FILE", tmp_path / "config.json")
    return tmp_path


# ============================================================
# تصنيف الأخطاء
# ============================================================


@pytest.mark.parametrize(
    "text,expected,fixable",
    [
        ("Request timed out after 30s", cm.ErrorType.TIMEOUT, True),
        ("deadline exceeded", cm.ErrorType.TIMEOUT, True),
        # أخطاء الشبكة التي تحتوي كلمة timeout تُصنَّف شبكة (لا مهلة) لكنها قابلة لإعادة المحاولة
        ("Connection timed out", cm.ErrorType.NETWORK_ERROR, True),
        ("connect ETIMEDOUT 1.2.3.4:443", cm.ErrorType.NETWORK_ERROR, True),
        ("Connection refused", cm.ErrorType.NETWORK_ERROR, True),
        ("HTTP 429 too many requests", cm.ErrorType.API_ERROR, True),
        ("permission denied", cm.ErrorType.PERMISSION_DENIED, False),
        ("ModuleNotFoundError: No module named 'x'", cm.ErrorType.DEPENDENCY_ERROR, False),
        ("bash: foo: command not found", cm.ErrorType.NOT_FOUND, False),
        ("SyntaxError: invalid syntax", cm.ErrorType.SYNTAX_ERROR, False),
        ("MemoryError", cm.ErrorType.MEMORY_ERROR, False),
        ("No space left on device", cm.ErrorType.DISK_FULL, False),
        ("شيء غامض تماماً", cm.ErrorType.UNKNOWN, False),
        ("", cm.ErrorType.UNKNOWN, False),
    ],
)
def test_error_classification(text, expected, fixable):
    analysis = cm.ErrorAnalyzer().analyze(make_job(last_error=text))
    assert analysis.error_type == expected
    assert analysis.auto_fixable == fixable


def test_analyze_falls_back_to_error_reason():
    job = make_job(last_error=None, last_error_reason="permission denied")
    assert cm.ErrorAnalyzer().analyze(job).error_type == cm.ErrorType.PERMISSION_DENIED


# ============================================================
# جلب المهام من openclaw
# ============================================================

SAMPLE_JOB = {
    "id": "abc",
    "name": "Job A",
    "enabled": True,
    "schedule": {"expr": "0 */12 * * *"},
    "payload": {"timeoutSeconds": 60},
    "state": {
        "lastStatus": "error",
        "lastError": "Request timed out",
        "consecutiveErrors": 3,
        "lastRunAtMs": 1700000000000,
        "nextRunAtMs": 1700003600000,
    },
}


def test_get_all_jobs_parses_wrapped_list(monkeypatch):
    monkeypatch.setattr(cm, "run_openclaw", lambda *a, **k: make_completed(stdout=json.dumps({"jobs": [SAMPLE_JOB]})))
    jobs = cm.OpenClawCronParser().get_all_jobs()
    assert len(jobs) == 1
    j = jobs[0]
    assert j.id == "abc"
    assert j.timeout_seconds == 60
    assert j.consecutive_errors == 3
    assert j.is_failed
    assert j.last_run_at is not None
    assert j.raw == SAMPLE_JOB


def test_get_all_jobs_parses_bare_list(monkeypatch):
    monkeypatch.setattr(cm, "run_openclaw", lambda *a, **k: make_completed(stdout=json.dumps([SAMPLE_JOB])))
    assert len(cm.OpenClawCronParser().get_all_jobs()) == 1


def test_get_all_jobs_raises_on_cli_failure(monkeypatch):
    monkeypatch.setattr(cm, "run_openclaw", lambda *a, **k: make_completed(returncode=1, stderr="boom"))
    with pytest.raises(cm.OpenClawError):
        cm.OpenClawCronParser().get_all_jobs()


def test_get_all_jobs_raises_on_bad_json(monkeypatch):
    monkeypatch.setattr(cm, "run_openclaw", lambda *a, **k: make_completed(stdout="not json"))
    with pytest.raises(cm.OpenClawError):
        cm.OpenClawCronParser().get_all_jobs()


def test_run_openclaw_missing_cli_raises(monkeypatch):
    def raise_fnf(*a, **k):
        raise FileNotFoundError("openclaw")

    monkeypatch.setattr(cm.subprocess, "run", raise_fnf)
    with pytest.raises(cm.OpenClawError):
        cm.run_openclaw("cron", "list", "--json")


# ============================================================
# الترشيح
# ============================================================


def test_filter_failed_excludes_disabled():
    jobs = [
        make_job(id="a", last_status="error"),
        make_job(id="b", last_status="error", enabled=False),
        make_job(id="c", last_status="ok"),
    ]
    assert [j.id for j in cm.OpenClawCronParser.filter_failed(jobs)] == ["a"]


def test_filter_critical_threshold():
    jobs = [make_job(id="a", consecutive_errors=1), make_job(id="b", consecutive_errors=5)]
    assert [j.id for j in cm.OpenClawCronParser.filter_critical(jobs, threshold=2)] == ["b"]


def test_filter_silent():
    past = datetime.now() - timedelta(hours=10)
    soon = datetime.now() + timedelta(hours=1)
    jobs = [
        make_job(id="stale", next_run_at=past),
        make_job(id="future", next_run_at=soon),
        make_job(id="disabled", next_run_at=past, enabled=False),
        make_job(id="no_schedule", next_run_at=None),
    ]
    assert [j.id for j in cm.OpenClawCronParser.filter_silent(jobs, grace_hours=6)] == ["stale"]


# ============================================================
# الإصلاح التلقائي
# ============================================================


def test_compute_new_timeout_increment(monkeypatch):
    monkeypatch.setattr(cm.Config, "TIMEOUT_INCREMENT", 120)
    monkeypatch.setattr(cm.Config, "MAX_TIMEOUT", 900)
    assert cm.AutoFixer.compute_new_timeout(60) == (60, 180)


def test_compute_new_timeout_defaults_to_30(monkeypatch):
    monkeypatch.setattr(cm.Config, "TIMEOUT_INCREMENT", 120)
    monkeypatch.setattr(cm.Config, "MAX_TIMEOUT", 900)
    assert cm.AutoFixer.compute_new_timeout(None) == (30, 150)


def test_compute_new_timeout_caps_at_max(monkeypatch):
    monkeypatch.setattr(cm.Config, "MAX_TIMEOUT", 900)
    assert cm.AutoFixer.compute_new_timeout(900) == (900, 900)
    assert cm.AutoFixer.compute_new_timeout(850)[1] == 900


def test_fix_timeout_applies_and_creates_backup(sandbox, monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args)
        return make_completed()

    monkeypatch.setattr(cm, "run_openclaw", fake_run)
    job = make_job(timeout_seconds=60, last_error="timed out", raw={"id": "j1", "name": "Test Job"})
    analysis = cm.ErrorAnalyzer().analyze(job)

    fixed = cm.AutoFixer().fix(analysis)

    assert fixed.fix_applied
    assert ("cron", "edit", "j1", "--timeout-seconds", "180") in calls
    backups = list(cm.Config.BACKUP_DIR.glob("j1_*.json"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8")) == {"id": "j1", "name": "Test Job"}


def test_fix_timeout_at_max_does_nothing(sandbox, monkeypatch):
    def fail_if_called(*a, **k):
        raise AssertionError("لا يجب استدعاء openclaw عند بلوغ الحد الأقصى")

    monkeypatch.setattr(cm, "run_openclaw", fail_if_called)
    job = make_job(timeout_seconds=cm.Config.MAX_TIMEOUT, last_error="timed out")
    fixed = cm.AutoFixer().fix(cm.ErrorAnalyzer().analyze(job))
    assert not fixed.fix_applied
    assert "الحد الأقصى" in fixed.fix_details


def test_fix_not_fixable_untouched():
    job = make_job(last_error="permission denied")
    analysis = cm.ErrorAnalyzer().analyze(job)
    assert cm.AutoFixer().fix(analysis) is analysis
    assert not analysis.fix_applied


# ============================================================
# مدير الحالة
# ============================================================


def test_state_roundtrip(tmp_path):
    state_file = tmp_path / "state.json"
    sm = cm.StateManager(state_file)
    sm.record_fix("a", "timeout", "x")
    sm.save()  # التسجيل يعدّل الحالة في الذاكرة؛ الكتابة للقرص تجري مرة واحدة عند الحفظ
    sm2 = cm.StateManager(state_file)
    assert len(sm2.state["fixes_applied"]) == 1
    assert sm2.state["fixes_applied"][0]["job_id"] == "a"


def test_record_fix_does_not_touch_disk(tmp_path):
    """الكتابة عند كل إصلاح كانت تستبدل ملف الحالة N مرة في الدورة الواحدة"""
    state_file = tmp_path / "state.json"
    sm = cm.StateManager(state_file)
    for i in range(5):
        sm.record_fix(f"job-{i}", "timeout", "x")
    assert not state_file.exists()
    assert len(sm.state["fixes_applied"]) == 5

    sm.save()
    assert len(cm.StateManager(state_file).state["fixes_applied"]) == 5


def test_state_corrupt_file_resets_with_defaults(tmp_path):
    state_file = tmp_path / "state.json"
    state_file.write_text("{ garbage", encoding="utf-8")
    sm = cm.StateManager(state_file)
    assert sm.state["fixes_applied"] == []
    assert sm.state["alerts"] == {}


def test_state_old_schema_gets_missing_keys(tmp_path):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"fixes_applied": [{"job_id": "a"}], "last_run": None}), encoding="utf-8")
    sm = cm.StateManager(state_file)
    assert sm.state["alerts"] == {}
    assert sm.state["failing_jobs"] == []
    assert len(sm.state["fixes_applied"]) == 1


def test_record_fix_keeps_last_100(tmp_path):
    sm = cm.StateManager(tmp_path / "state.json")
    for i in range(105):
        sm.record_fix(f"job{i}", "timeout", "x")
    assert len(sm.state["fixes_applied"]) == 100
    assert sm.state["fixes_applied"][-1]["job_id"] == "job104"


def test_atomic_save_leaves_no_tmp_file(tmp_path):
    sm = cm.StateManager(tmp_path / "state.json")
    sm.save()
    assert (tmp_path / "state.json").exists()
    assert not (tmp_path / "state.json.tmp").exists()


def test_alert_dedup_cooldown(tmp_path, monkeypatch):
    monkeypatch.setattr(cm.Config, "ALERT_COOLDOWN_HOURS", 24)
    sm = cm.StateManager(tmp_path / "state.json")

    assert sm.should_alert("a", "timeout")
    sm.record_alert("a", "timeout")
    assert not sm.should_alert("a", "timeout")
    # نوع خطأ مختلف لنفس المهمة → تنبيه فوري
    assert sm.should_alert("a", "network_error")
    # مهمة أخرى → تنبيه فوري
    assert sm.should_alert("b", "timeout")

    # انقضاء فترة التهدئة → تنبيه مجدداً
    sm.state["alerts"]["a/timeout"] = (datetime.now() - timedelta(hours=25)).isoformat()
    assert sm.should_alert("a", "timeout")


def test_clear_alerts_for_job(tmp_path):
    sm = cm.StateManager(tmp_path / "state.json")
    sm.record_alert("a", "timeout")
    sm.record_alert("a", "silent")
    sm.record_alert("b", "timeout")
    sm.clear_alerts_for("a")
    assert not any(k.startswith("a/") for k in sm.state["alerts"])
    assert "b/timeout" in sm.state["alerts"]


# ============================================================
# الإعدادات
# ============================================================


def test_config_load_from_file_and_env(sandbox, monkeypatch):
    # تسجيل القيم الحالية لاستعادتها تلقائياً بعد الاختبار
    for attr in ("ALERT_THRESHOLD", "MAX_TIMEOUT", "TELEGRAM_CHAT_ID", "AUTO_RETRY"):
        monkeypatch.setattr(cm.Config, attr, getattr(cm.Config, attr))

    cm.Config.CONFIG_FILE.write_text(
        json.dumps({"alert_threshold": 5, "telegram_chat_id": "123", "auto_retry": False}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CRONMASTER_MAX_TIMEOUT", "1200")

    cm.Config.load()

    assert cm.Config.ALERT_THRESHOLD == 5
    assert cm.Config.TELEGRAM_CHAT_ID == "123"
    assert cm.Config.AUTO_RETRY is False
    assert cm.Config.MAX_TIMEOUT == 1200


def test_config_env_overrides_file(sandbox, monkeypatch):
    monkeypatch.setattr(cm.Config, "ALERT_THRESHOLD", cm.Config.ALERT_THRESHOLD)
    cm.Config.CONFIG_FILE.write_text(json.dumps({"alert_threshold": 5}), encoding="utf-8")
    monkeypatch.setenv("CRONMASTER_ALERT_THRESHOLD", "7")
    cm.Config.load()
    assert cm.Config.ALERT_THRESHOLD == 7


def test_config_bad_values_ignored(sandbox, monkeypatch):
    monkeypatch.setattr(cm.Config, "MAX_TIMEOUT", 900)
    monkeypatch.setenv("CRONMASTER_MAX_TIMEOUT", "ليس رقماً")
    cm.Config.load()
    assert cm.Config.MAX_TIMEOUT == 900


# ============================================================
# التنبيهات
# ============================================================


def test_send_telegram_without_chat_id_skips(monkeypatch):
    monkeypatch.setattr(cm.Config, "TELEGRAM_CHAT_ID", "")

    def fail_if_called(*a, **k):
        raise AssertionError("لا يجب استدعاء openclaw بدون chat id")

    monkeypatch.setattr(cm, "run_openclaw", fail_if_called)
    assert cm.AlertManager().send_telegram("test") is False


def test_format_alert_sections():
    job = make_job(last_error="timed out")
    analysis = cm.ErrorAnalyzer().analyze(job)
    recovered = [make_job(id="r", name="Recovered Job", last_status="ok")]
    silent = [make_job(id="s", name="Silent Job", next_run_at=datetime(2026, 1, 1, 8, 0))]

    msg = cm.AlertManager().format_alert([analysis], recovered, silent)

    assert "Test Job" in msg
    assert "مهام تعافت" in msg
    assert "Recovered Job" in msg
    assert "Silent Job" in msg
    assert "2026-01-01 08:00" in msg


# ============================================================
# التقارير
# ============================================================


def test_report_counts_consistent_with_analyses(tmp_path):
    """عدّاد الفاشلة في التقرير يطابق قائمة التحليلات — مهمة معطلة بحالة خطأ لا تُحسب"""
    jobs = [
        make_job(id="a", name="Failing", last_status="error", last_error="timed out"),
        make_job(id="b", name="DisabledError", last_status="error", enabled=False),
        make_job(id="c", name="Fine", last_status="ok"),
    ]
    failed = cm.OpenClawCronParser.filter_failed(jobs)
    analyses = [cm.ErrorAnalyzer().analyze(j) for j in failed]

    gen = cm.ReportGenerator(reports_dir=tmp_path)
    path = gen.generate_report(jobs, analyses, fmt="markdown")
    content = path.read_text(encoding="utf-8")

    assert "المهام الفاشلة (1)" in content
    assert "Failing" in content

    json_path = gen.generate_report(jobs, analyses, fmt="json")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["summary"]["error"] == 1
    assert data["summary"]["ok"] == 1
    assert data["summary"]["total"] == 3


# ============================================================
# المحرك: monitor
# ============================================================


def fake_cron_list(payload):
    def fake_run(*args, **kwargs):
        if args[:2] == ("cron", "list"):
            return make_completed(stdout=json.dumps(payload))
        raise AssertionError(f"استدعاء openclaw غير متوقع: {args}")

    return fake_run


def test_monitor_dry_run_makes_no_changes(sandbox, monkeypatch):
    monkeypatch.setattr(cm, "run_openclaw", fake_cron_list([SAMPLE_JOB]))
    master = cm.CronMaster()

    result = master.monitor(dry_run=True)

    assert result["dry_run"] is True
    assert result["failed_jobs"] == 1
    assert result["fixes_applied"] == 0
    assert "[dry-run]" in result["analyses"][0]["fix_details"]
    assert not cm.Config.STATE_FILE.exists()  # لا حفظ حالة في dry-run


def test_monitor_openclaw_down_reports_error(sandbox, monkeypatch):
    def raise_error(*a, **k):
        raise cm.OpenClawError("openclaw غير موجود")

    monkeypatch.setattr(cm, "run_openclaw", raise_error)
    master = cm.CronMaster()

    result = master.monitor(alert=False)

    assert "error" in result
    assert "total_jobs" not in result  # لا يُقدَّم أي إيحاء بالنجاح


def test_monitor_fixes_and_tracks_failing(sandbox, monkeypatch):
    def fake_run(*args, **kwargs):
        if args[:2] == ("cron", "list"):
            return make_completed(stdout=json.dumps([SAMPLE_JOB]))
        return make_completed()  # edit / run تنجح

    monkeypatch.setattr(cm, "run_openclaw", fake_run)
    master = cm.CronMaster()

    result = master.monitor(alert=False)

    assert result["fixes_applied"] == 1
    assert result["retries"] == 1
    assert master.state.get_failing() == ["abc"]


def test_monitor_detects_recovery(sandbox, monkeypatch):
    ok_job = dict(SAMPLE_JOB, state={"lastStatus": "ok"})
    monkeypatch.setattr(cm, "run_openclaw", fake_cron_list([ok_job]))
    master = cm.CronMaster()
    master.state.set_failing(["abc"])
    master.state.record_alert("abc", "timeout")
    master.state.save()

    result = master.monitor(alert=False)

    assert result["recovered_jobs"] == ["Job A"]
    assert master.state.get_failing() == []
    # تعافي المهمة يمسح سجل تنبيهاتها ليُنبَّه فوراً عند فشل جديد
    assert master.state.should_alert("abc", "timeout")


def test_status_success_rate_over_measurable_jobs(sandbox, monkeypatch):
    jobs = [
        dict(SAMPLE_JOB, id="a", state={"lastStatus": "ok"}),
        dict(SAMPLE_JOB, id="b", state={"lastStatus": "error", "lastError": "x"}),
        dict(SAMPLE_JOB, id="c", enabled=False, state={}),
        dict(SAMPLE_JOB, id="d", state={}),  # لم تعمل بعد — لا تدخل الحساب
    ]
    monkeypatch.setattr(cm, "run_openclaw", fake_cron_list(jobs))
    master = cm.CronMaster()

    result = master.status()

    assert result["total_jobs"] == 4
    assert result["ok"] == 1
    assert result["error"] == 1
    assert result["success_rate"] == 50.0


# ============================================================
# إعادة المحاولة للأخطاء العابرة (network / api)
# ============================================================


def net_job(**state_overrides):
    """مهمة فاشلة بخطأ شبكة"""
    state = {"lastStatus": "error", "lastError": "Connection refused", "consecutiveErrors": 1}
    state.update(state_overrides)
    return dict(SAMPLE_JOB, id="net", name="Net Job", payload={}, state=state)


def route(payload):
    """يوجّه cron list لقائمة معطاة ويسجّل بقية الاستدعاءات في .calls (وكلها تنجح)"""

    def fake_run(*args, **kwargs):
        fake_run.calls.append(args)
        if args[:2] == ("cron", "list"):
            return make_completed(stdout=json.dumps(payload))
        return make_completed()

    fake_run.calls = []
    return fake_run


def test_state_retry_counter_roundtrip(tmp_path):
    sm = cm.StateManager(tmp_path / "state.json")
    assert sm.get_retry_count("j") == 0
    sm.record_retry("j")
    sm.record_retry("j")
    assert sm.get_retry_count("j") == 2
    sm.clear_retries_for("j")
    assert sm.get_retry_count("j") == 0


def test_state_prune_retries(tmp_path):
    sm = cm.StateManager(tmp_path / "state.json")
    sm.record_retry("a")
    sm.record_retry("b")
    sm.prune_retries(["a"])
    assert sm.get_retry_count("a") == 1
    assert sm.get_retry_count("b") == 0


def test_network_error_retries_immediately(sandbox, monkeypatch):
    runner = route([net_job()])
    monkeypatch.setattr(cm, "run_openclaw", runner)
    master = cm.CronMaster()

    result = master.monitor(alert=False)

    assert result["fixes_applied"] == 1
    assert result["retries"] == 1
    assert ("cron", "run", "net") in runner.calls
    assert master.state.get_retry_count("net") == 1


def test_network_retry_stops_at_max(sandbox, monkeypatch):
    monkeypatch.setattr(cm.Config, "MAX_RETRIES", 3)
    runner = route([net_job()])
    monkeypatch.setattr(cm, "run_openclaw", runner)
    master = cm.CronMaster()
    master.state.state["retries"]["net"] = 3  # استُنفدت مسبقاً

    result = master.monitor(alert=False)

    assert result["retries"] == 0
    assert ("cron", "run", "net") not in runner.calls
    assert "استُنفدت" in result["analyses"][0]["fix_details"]


def test_api_error_defers_within_backoff(sandbox, monkeypatch):
    monkeypatch.setattr(cm.Config, "RETRY_BACKOFF_HOURS", 1)
    recent_ms = int((datetime.now() - timedelta(minutes=5)).timestamp() * 1000)
    job = dict(
        SAMPLE_JOB,
        id="api",
        name="API Job",
        payload={},
        state={"lastStatus": "error", "lastError": "HTTP 429", "consecutiveErrors": 1, "lastRunAtMs": recent_ms},
    )
    runner = route([job])
    monkeypatch.setattr(cm, "run_openclaw", runner)
    master = cm.CronMaster()

    result = master.monitor(alert=False)

    assert result["retries"] == 0
    assert ("cron", "run", "api") not in runner.calls
    assert "التهدئة" in result["analyses"][0]["fix_details"]
    assert master.state.get_retry_count("api") == 0


def test_api_error_retries_after_backoff(sandbox, monkeypatch):
    monkeypatch.setattr(cm.Config, "RETRY_BACKOFF_HOURS", 1)
    old_ms = int((datetime.now() - timedelta(hours=3)).timestamp() * 1000)
    job = dict(
        SAMPLE_JOB,
        id="api",
        name="API Job",
        payload={},
        state={
            "lastStatus": "error",
            "lastError": "rate limit exceeded",
            "consecutiveErrors": 1,
            "lastRunAtMs": old_ms,
        },
    )
    runner = route([job])
    monkeypatch.setattr(cm, "run_openclaw", runner)
    master = cm.CronMaster()

    result = master.monitor(alert=False)

    assert result["retries"] == 1
    assert ("cron", "run", "api") in runner.calls


def test_recovery_resets_retry_counter(sandbox, monkeypatch):
    ok_job = dict(SAMPLE_JOB, id="net", name="Net Job", payload={}, state={"lastStatus": "ok"})
    monkeypatch.setattr(cm, "run_openclaw", route([ok_job]))
    master = cm.CronMaster()
    master.state.set_failing(["net"])
    master.state.record_retry("net")
    master.state.record_retry("net")
    master.state.save()

    master.monitor(alert=False)

    assert master.state.get_retry_count("net") == 0


def test_transient_dry_run_does_not_retry(sandbox, monkeypatch):
    runner = route([net_job()])
    monkeypatch.setattr(cm, "run_openclaw", runner)
    master = cm.CronMaster()

    result = master.monitor(dry_run=True)

    assert result["retries"] == 0
    assert ("cron", "run", "net") not in runner.calls
    assert "[dry-run]" in result["analyses"][0]["fix_details"]
    assert master.state.get_retry_count("net") == 0


def test_transient_retry_disabled_by_config(sandbox, monkeypatch):
    monkeypatch.setattr(cm.Config, "AUTO_RETRY_TRANSIENT", False)
    runner = route([net_job()])
    monkeypatch.setattr(cm, "run_openclaw", runner)
    master = cm.CronMaster()

    result = master.monitor(alert=False)

    assert result["fixes_applied"] == 0
    assert ("cron", "run", "net") not in runner.calls


def test_fix_job_transient_retries(sandbox, monkeypatch):
    runner = route([net_job()])
    monkeypatch.setattr(cm, "run_openclaw", runner)
    master = cm.CronMaster()

    result = master.fix_job("net")

    assert result["fix_applied"] is True
    assert ("cron", "run", "net") in runner.calls
    assert master.state.get_retry_count("net") == 1

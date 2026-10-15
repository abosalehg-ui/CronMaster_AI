# -*- coding: utf-8 -*-
"""اختبارات انحدار لخطة مراجعة مِحَك الثانية (docs/مراجعة-CronMaster_AI-2026-10.md).

كل اختبار هنا يفحص مساراً «لم يحدث فيه المتوقع»: تنبيه لم يخرج، قيمة بنوع غير
متوقع، استثناء غير BackendError، أو أمران يعملان في وقت واحد. ملاحظات 🔴/🟠 في
المراجعة ثُبِّتت باختبارات فحص على الكود قبل الإصلاح، ثم صارت هنا اختبارات دائمة.
"""

import json
import re
from datetime import datetime, timedelta
from unittest import mock

import pytest
from conftest import FakeBackend, failing_job, make_job

import cronmaster.core as core_module
from cronmaster.cli import _early_lang, build_arg_parser, main
from cronmaster.config import Config, parse_bool
from cronmaster.core import EXIT_MONITOR_FAILURE, CronMaster
from cronmaster.i18n import set_lang
from cronmaster.lock import ExecutionLock
from cronmaster.notifiers import FAILED, QUEUED, SENT, AlertManager
from cronmaster.notifiers.telegram import TelegramNotifier

_ARABIC = re.compile("[؀-ۿ]")


@pytest.fixture(autouse=True)
def _restore_lang():
    yield
    set_lang("ar")


def _master(jobs, sent=None, ok=True, monkeypatch=None):
    master = CronMaster(backend=FakeBackend(jobs))
    if monkeypatch is not None:
        sink = sent if sent is not None else []
        monkeypatch.setattr(master.alerter, "dispatch", lambda m: sink.append(m) or ok)
    return master


# ============================================================
# 1) القيم المنطقية النصية
# ============================================================


@pytest.mark.parametrize(
    "raw,expected",
    [("false", False), ("False", False), ("no", False), ("0", False), ("", False),
     ("true", True), ("yes", True), ("1", True), ("on", True), (0, False), (1, True), (None, False)],
)
def test_parse_bool(raw, expected):
    assert parse_bool(raw) is expected


def test_parse_bool_rejects_garbage():
    assert parse_bool("maybe") is None
    assert parse_bool([True]) is None


@pytest.mark.parametrize("key", sorted(k.lower() for k in Config._BOOL_KEYS))
def test_string_false_in_config_file_turns_the_feature_off(sandbox, key):
    """كانت "false" بين علامتي تنصيص تُقرأ True فتُشغّل ما حاول المستخدم إطفاءه"""
    Config.CONFIG_FILE.write_text(json.dumps({key: "false"}), encoding="utf-8")
    Config.load()
    assert getattr(Config, key.upper()) is False
    assert key in Config.coerced_keys  # يظهر في doctor


def test_unparseable_bool_falls_back_to_false(sandbox):
    Config.CONFIG_FILE.write_text(json.dumps({"circuit_breaker_enabled": "sometimes"}), encoding="utf-8")
    Config.load()
    assert Config.CIRCUIT_BREAKER_ENABLED is False


def test_real_bools_are_left_alone(sandbox):
    Config.CONFIG_FILE.write_text(json.dumps({"auto_reschedule": True}), encoding="utf-8")
    Config.load()
    assert Config.AUTO_RESCHEDULE is True
    assert "auto_reschedule" not in Config.coerced_keys


def test_env_bool_is_not_reported_as_coerced(sandbox, monkeypatch):
    """متغيرات البيئة نصوص دائماً — لا يصح أن يحذّر doctor من كل واحد منها"""
    monkeypatch.setenv("CRONMASTER_AUTO_RESCHEDULE", "true")
    monkeypatch.setenv("CRONMASTER_AUTO_RETRY", "off")
    Config.load()
    assert Config.AUTO_RESCHEDULE is True
    assert Config.AUTO_RETRY is False
    assert Config.coerced_keys == []


# ============================================================
# 2) فترة الهدوء لا تكرر التنبيه في الطابور
# ============================================================


def test_quiet_hours_queue_one_copy_across_cycles(sandbox, monkeypatch):
    """5 دورات في فترة الهدوء مع مهمة فاشلة واحدة كانت تعطي 5 نسخ في الطابور"""
    Config.QUIET_HOURS = {"from": "00:00", "to": "23:59"}
    job = failing_job(error="permission denied", consecutive_errors=4)
    sent = []
    results = [_master([job], sent, monkeypatch=monkeypatch).monitor() for _ in range(5)]

    queued = json.loads(Config.STATE_FILE.read_text(encoding="utf-8"))["queued_alerts"]
    assert len(queued) == 1
    assert sent == []  # لا شيء خرج أثناء فترة الهدوء
    assert results[0]["alert_queued"] is True
    assert not any(r["alert_queued"] for r in results[1:])


def test_digest_is_not_followed_by_a_duplicate(sandbox, monkeypatch):
    """بعد فترة الهدوء يخرج الملخص، والدورة التالية لا تعيد إرسال الفشل نفسه"""
    job = failing_job(error="permission denied", consecutive_errors=4)
    Config.QUIET_HOURS = {"from": "00:00", "to": "23:59"}
    _master([job], monkeypatch=monkeypatch).monitor()

    Config.QUIET_HOURS = {}
    sent = []
    _master([job], sent, monkeypatch=monkeypatch).monitor()
    _master([job], sent, monkeypatch=monkeypatch).monitor()

    assert len(sent) == 1, sent
    assert "permission" in sent[0] or "صلاحي" in sent[0]


def test_queue_alert_ignores_an_identical_message(sandbox):
    from cronmaster.storage import StateManager

    state = StateManager()
    assert state.queue_alert("نفس الرسالة") is True
    assert state.queue_alert("نفس الرسالة") is False
    assert state.queue_alert("رسالة أخرى") is True
    assert len(state.peek_queued_alerts()) == 2


def test_deliver_reports_three_outcomes(sandbox):
    from cronmaster.storage import StateManager

    state = StateManager()
    ok = AlertManager(notifiers=[mock.Mock(send=mock.Mock(return_value=True))], state=state)
    bad = AlertManager(notifiers=[mock.Mock(send=mock.Mock(return_value=False))], state=state)
    assert ok.deliver("x") == SENT
    assert bad.deliver("x") == FAILED
    Config.QUIET_HOURS = {"from": "00:00", "to": "23:59"}
    assert ok.deliver("y") == QUEUED
    assert ok.send("z") is False  # الواجهة القديمة: True فقط لما خرج فعلاً


# ============================================================
# 3) التهدئة تُسجَّل بعد الإرسال لا قبله
# ============================================================


def _futile_job_state(master, job):
    for _ in range(Config.MAX_TIMEOUT_FIXES):
        master.state.record_timeout_fix(job.id)
    master.state.save()


def _escalations(messages):
    return [m for m in messages if "المهلة لم يعد مجدياً" in m or "no longer helping" in m]


def test_futile_fix_warning_survives_failed_delivery(sandbox, monkeypatch):
    """القنوات تفشل ثلاث دورات ثم تعود: التحذير يجب أن يصل في الرابعة"""
    job = failing_job(error="Operation timed out", consecutive_errors=9)
    _futile_job_state(CronMaster(backend=FakeBackend([job])), job)

    for _ in range(3):
        _master([job], ok=False, monkeypatch=monkeypatch).monitor()
    sent = []
    _master([job], sent, monkeypatch=monkeypatch).monitor()

    assert len(_escalations(sent)) == 1


def test_futile_fix_warning_survives_no_alert_runs(sandbox, monkeypatch):
    """دورتان بـ --no-alert كانتا تسجّلان التحذير كأنه أُرسل فيختفي 24 ساعة"""
    job = failing_job(error="Operation timed out", consecutive_errors=9)
    _futile_job_state(CronMaster(backend=FakeBackend([job])), job)

    for _ in range(2):
        _master([job], monkeypatch=monkeypatch).monitor(alert=False)
    sent = []
    result = _master([job], sent, monkeypatch=monkeypatch).monitor()

    assert len(_escalations(sent)) == 1
    assert result["alert_sent"] is True


def test_duration_regression_survives_failed_delivery(sandbox, monkeypatch):
    job = make_job(id="slow", last_status="ok")
    master = CronMaster(backend=FakeBackend([job]))
    trend = {"samples": 10, "ratio": 3.0, "recent_mean": 30.0, "baseline_mean": 10.0}
    monkeypatch.setattr(type(master.history_store), "duration_trend", lambda self, job_id: trend)
    monkeypatch.setattr(type(master.history_store), "available", True, raising=False)

    monkeypatch.setattr(master.alerter, "dispatch", lambda m: False)
    master.monitor()
    assert master.state.should_alert("slow", "duration_regression") is True  # لم يُسجَّل

    sent = []
    monkeypatch.setattr(master.alerter, "dispatch", lambda m: sent.append(m) or True)
    master.monitor()
    assert len(sent) == 1
    assert master.state.should_alert("slow", "duration_regression") is False


# ============================================================
# 4) استثناءات غير BackendError لا تُسقط الدورة
# ============================================================


def test_one_crashing_job_does_not_stop_the_others(sandbox, monkeypatch):
    bad = failing_job(id="bad", name="Bad", error="permission denied", consecutive_errors=4)
    good = failing_job(id="good", name="Good", error="permission denied", consecutive_errors=4)
    sent = []
    master = _master([bad, good], sent, monkeypatch=monkeypatch)
    real = master.analyzer.analyze

    def analyze(job, **kwargs):
        if job.id == "bad":
            raise TypeError("create() got an unexpected keyword argument 'output_config'")
        return real(job, **kwargs)

    monkeypatch.setattr(master.analyzer, "analyze", analyze)
    result = master.monitor()

    assert [a["job_id"] for a in result["analyses"]] == ["good"]
    assert any("Bad" in n for n in result["notices"])
    assert len(sent) == 1 and "Good" in sent[0] and "Bad" in sent[0]
    assert Config.STATE_FILE.exists()  # الحالة حُفظت رغم الاستثناء


def test_unexpected_openclaw_shape_is_a_backend_error():
    from cronmaster.backends.base import BackendError
    from cronmaster.backends.openclaw import OpenClawBackend

    for stdout in ('{"items": []}', '"jobs"', '[1, 2]', '{"jobs": [{"id": "x", "schedule": "* * * * *"}]}'):
        result = mock.Mock(returncode=0, stdout=stdout, stderr="")
        with mock.patch("cronmaster.backends.openclaw.run_openclaw", return_value=result):
            with pytest.raises(BackendError):
                OpenClawBackend().list_jobs()


def test_bad_shape_takes_the_monitor_failure_path(sandbox, monkeypatch):
    """الشكل الغريب يمر على مسار «المراقب لا يستطيع القراءة»: تنبيه حرج + نتيجة error"""
    from cronmaster.backends.openclaw import OpenClawBackend

    result = mock.Mock(returncode=0, stdout='{"items": []}', stderr="")
    sent = []
    master = CronMaster(backend=OpenClawBackend())
    monkeypatch.setattr(master.alerter, "dispatch", lambda m: sent.append(m) or True)
    with mock.patch("cronmaster.backends.openclaw.run_openclaw", return_value=result):
        outcome = master.monitor()

    assert outcome.get("error")
    assert CronMaster.exit_code(outcome) == EXIT_MONITOR_FAILURE
    assert len(sent) == 1 and "openclaw" in sent[0]


# ============================================================
# 5) الأوامر المعدِّلة تحت القفل
# ============================================================


@pytest.fixture
def cli(sandbox, monkeypatch):
    def run(argv, jobs=None):
        backend = FakeBackend(jobs=jobs or [])
        monkeypatch.setattr(core_module, "get_backend", lambda *a, **k: backend)
        monkeypatch.setattr("sys.argv", ["cronmaster", *argv])
        try:
            main()
            code = 0
        except SystemExit as e:
            code = e.code or 0
        return code, backend

    return run


@pytest.mark.parametrize("argv", [["fix", "j1"], ["restore", "j1", "--yes"]])
def test_mutating_commands_wait_for_the_lock(cli, monkeypatch, capsys, argv):
    monkeypatch.setattr("cronmaster.cli.LOCK_WAIT_SECONDS", 0.3)
    held = ExecutionLock(Config.lock_file())
    assert held.acquire()
    try:
        code, backend = cli(argv, jobs=[failing_job()])
    finally:
        held.release()

    assert code == 1
    assert backend.calls == []  # لم يُقرأ شيء ولم يُعدَّل شيء
    assert "دورة مراقبة تعمل الآن" in capsys.readouterr().err


def test_fix_takes_the_lock_once_free(cli):
    code, backend = cli(["fix", "j1", "--json"], jobs=[failing_job()])
    assert code == 0
    assert ("list",) in backend.calls
    # القفل حُرِّر بعد الأمر
    after = ExecutionLock(Config.lock_file())
    assert after.acquire() is True
    after.release()


def test_restore_list_needs_no_lock(cli, capsys):
    held = ExecutionLock(Config.lock_file())
    assert held.acquire()
    try:
        code, _ = cli(["restore", "j1", "--list"])
    finally:
        held.release()
    assert code == 1  # لا نسخ — لكنه لم ينتظر القفل
    assert "دورة مراقبة تعمل الآن" not in capsys.readouterr().err


def test_state_is_loaded_after_the_lock(cli, monkeypatch):
    """بناء CronMaster (الذي يحمّل state.json) يحدث بعد أخذ القفل لا قبله"""
    order = []
    real_acquire = ExecutionLock.acquire
    real_init = CronMaster.__init__

    def acquire(self, *a, **k):
        order.append("lock")
        return real_acquire(self, *a, **k)

    def init(self, *a, **k):
        order.append("state")
        real_init(self, *a, **k)

    monkeypatch.setattr(ExecutionLock, "acquire", acquire)
    monkeypatch.setattr(CronMaster, "__init__", init)
    cli(["monitor", "--no-alert"])
    assert order[:2] == ["lock", "state"]


def test_lock_timeout_waits_then_gives_up(tmp_path):
    held = ExecutionLock(tmp_path / "x.lock")
    assert held.acquire()
    other = ExecutionLock(tmp_path / "x.lock")
    started = datetime.now()
    assert other.acquire(timeout=0.4) is False
    assert datetime.now() - started >= timedelta(seconds=0.35)
    held.release()
    assert other.acquire(timeout=0.4) is True
    other.release()


# ============================================================
# 6) لا عربي في --lang en
# ============================================================


def test_doctor_details_follow_english(sandbox):
    set_lang("en")
    result = CronMaster(backend=FakeBackend([make_job(id="a", last_status="ok")])).doctor()
    leaked = [c["detail"] for c in result["checks"] if _ARABIC.search(str(c["detail"]))]
    assert leaked == []


def test_backend_failure_alert_is_english_and_names_the_backend(sandbox, monkeypatch):
    set_lang("en")
    sent = []
    master = CronMaster(backend=FakeBackend(fail_list=True))
    monkeypatch.setattr(master.alerter, "dispatch", lambda m: sent.append(m) or True)
    master.monitor()
    assert len(sent) == 1
    assert "fake" in sent[0] and "OpenClaw" not in sent[0]
    assert not _ARABIC.search(sent[0].split("\n", 1)[0])


def test_sent_alert_text_is_english(sandbox, monkeypatch):
    """الاختبار القديم فحص نتيجة monitor(alert=False) فقط، لا الرسالة المرسلة فعلاً"""
    set_lang("en")
    jobs = [
        failing_job(id="a", error="permission denied", consecutive_errors=4),
        failing_job(id="b", error="Operation timed out", consecutive_errors=4),
    ]
    sent = []
    _master(jobs, sent, monkeypatch=monkeypatch).monitor()
    assert sent and not any(_ARABIC.search(m) for m in sent), sent


def test_help_follows_the_language_flag(monkeypatch):
    assert _early_lang(["--lang", "en", "status"]) == "en"
    assert _early_lang(["--lang=en"]) == "en"
    monkeypatch.setenv("CRONMASTER_LANG", "en")
    assert _early_lang(["status"]) == "en"

    set_lang("en")
    text = build_arg_parser().format_help()
    assert not _ARABIC.search(text), text
    assert "crontab" in text


# ============================================================
# 7) جدول المهام الفاشلة في HTML
# ============================================================


def test_failures_table_is_accessible_and_mobile_ready(sandbox):
    from cronmaster.analysis import ErrorAnalyzer
    from cronmaster.reporting.html import render_html

    job = failing_job(error="permission denied")
    page = render_html([job], [ErrorAnalyzer.analyze_regex(job)], summary={}, stats=[], trend=[], history={})
    section = page[page.index("<h2>❌"):]
    section = section[: section.index("</section>")]

    assert "<caption>" in section
    assert section.count('scope="col"') == 4
    assert section.count("data-label=") == 4
    assert 'class="wrap"' in section


# ============================================================
# 9) قص رسائل Telegram
# ============================================================


def test_telegram_truncates_long_messages(monkeypatch):
    captured = {}

    def fake_run(*args, **kwargs):
        captured["message"] = args[args.index("--message") + 1]
        return mock.Mock(returncode=0, stderr="")

    monkeypatch.setattr("cronmaster.notifiers.telegram._openclaw.run_openclaw", fake_run)
    assert TelegramNotifier(chat_id="1").send("x" * 9000) is True
    assert len(captured["message"]) == TelegramNotifier.MAX_LENGTH
    assert captured["message"].endswith("…")


# ============================================================
# 12) --limit موجب
# ============================================================


@pytest.mark.parametrize("value", ["0", "-3", "abc"])
def test_history_limit_must_be_positive(value):
    with pytest.raises(SystemExit):
        build_arg_parser().parse_args(["history", "--limit", value])


def test_history_limit_accepts_positive():
    assert build_arg_parser().parse_args(["history", "--limit", "5"]).limit == 5


# ============================================================
# 14) معاينة التراجع تطابق التنفيذ
# ============================================================


def test_dry_run_rollback_only_announced_when_it_would_happen(sandbox):
    Config.ROLLBACK_TIMEOUT_ENABLED = True
    Config.ROLLBACK_AFTER_CYCLES = 3
    job = failing_job(error="Operation timed out", consecutive_errors=4)
    master = CronMaster(backend=FakeBackend([job]))
    master.state.set_pending_rollback(job.id, 60)

    def dry_notices():
        master.policies.reset()
        master.policies.check_rollback(job, master.analyzer.analyze(job), dry_run=True)
        return master.policies.notices

    assert dry_notices() == []  # الدورة الحقيقية التالية = 1 < 3
    master.state.bump_rollback_cycle(job.id)
    master.state.bump_rollback_cycle(job.id)
    assert len(dry_notices()) == 1  # الدورة الحقيقية التالية = 3 → تراجع فعلي


# ============================================================
# 16) crontab يُطلق المهمة ولا ينتظرها
# ============================================================


def test_crontab_run_job_does_not_wait(monkeypatch):
    from cronmaster.backends import crontab as crontab_module
    from cronmaster.backends.crontab import SystemCrontabBackend

    line = "*/5 * * * * /usr/local/bin/backup.sh"
    monkeypatch.setattr(
        crontab_module, "run_crontab",
        lambda *a, **k: mock.Mock(returncode=0, stdout=line + "\n", stderr=""),
    )
    popen = mock.Mock()
    monkeypatch.setattr(crontab_module.subprocess, "Popen", popen)
    monkeypatch.setattr(crontab_module.subprocess, "run", mock.Mock(side_effect=AssertionError("انتظار")))

    backend = SystemCrontabBackend()
    job_id = backend.list_jobs()[0].id
    assert backend.run_job(job_id) is True
    kwargs = popen.call_args.kwargs
    assert kwargs["start_new_session"] is True
    assert popen.call_args.args[0] == ["/bin/sh", "-c", "/usr/local/bin/backup.sh"]

# -*- coding: utf-8 -*-
"""اختبارات الإعدادات، اللغة، واجهة سطر الأوامر، وغلاف التوافق."""

import json

import pytest

import cronmaster.core as core_module
from cronmaster.cli import build_arg_parser, main
from cronmaster.config import Config
from cronmaster.core import EXIT_FIXES_APPLIED, EXIT_JOBS_FAILING, EXIT_MONITOR_FAILURE
from cronmaster.i18n import MESSAGES, SUPPORTED_LANGS, get_lang, set_lang, t

from conftest import FakeBackend, failing_job, make_job


@pytest.fixture(autouse=True)
def _restore_lang():
    yield
    set_lang("ar")


@pytest.fixture
def cli(sandbox, monkeypatch):
    """يشغّل الـ CLI فوق واجهة خلفية مزيفة"""

    def run(argv, jobs=None, fail_list=False):
        backend = FakeBackend(jobs=jobs or [], fail_list=fail_list)
        monkeypatch.setattr(core_module, "get_backend", lambda *a, **k: backend)
        monkeypatch.setattr("sys.argv", ["cronmaster", *argv])
        try:
            main()
            code = 0
        except SystemExit as e:
            code = e.code or 0
        return code, backend

    return run


# ============================================================
# الإعدادات
# ============================================================


def test_new_keys_are_configurable_from_file(sandbox):
    Config.CONFIG_FILE.write_text(
        json.dumps(
            {
                "backend": "crontab",
                "lang": "en",
                "history_retention_days": 30,
                "duration_regression_factor": 3.5,
                "circuit_breaker_enabled": True,
                "notifiers": [{"type": "slack", "url": "https://example.test/x"}],
                "quiet_hours": {"from": "22:00", "to": "07:00", "tz": "Asia/Riyadh"},
            }
        ),
        encoding="utf-8",
    )
    Config.load()

    assert Config.BACKEND == "crontab"
    assert Config.LANG == "en"
    assert Config.HISTORY_RETENTION_DAYS == 30
    assert Config.DURATION_REGRESSION_FACTOR == 3.5
    assert Config.CIRCUIT_BREAKER_ENABLED is True
    assert Config.NOTIFIERS[0]["type"] == "slack"
    assert Config.QUIET_HOURS["tz"] == "Asia/Riyadh"


def test_env_overrides_file_for_new_keys(sandbox, monkeypatch):
    Config.CONFIG_FILE.write_text(json.dumps({"history_retention_days": 30}), encoding="utf-8")
    monkeypatch.setenv("CRONMASTER_HISTORY_RETENTION_DAYS", "7")
    monkeypatch.setenv("CRONMASTER_LLM_MIN_CONFIDENCE", "0.6")
    monkeypatch.setenv("CRONMASTER_AUTO_RESCHEDULE", "true")
    Config.load()

    assert Config.HISTORY_RETENTION_DAYS == 7
    assert Config.LLM_MIN_CONFIDENCE == 0.6
    assert Config.AUTO_RESCHEDULE is True


def test_list_and_dict_env_vars_parse_json(sandbox, monkeypatch):
    monkeypatch.setenv("CRONMASTER_NOTIFIERS", '[{"type": "null"}]')
    monkeypatch.setenv("CRONMASTER_QUIET_HOURS", '{"from": "23:00", "to": "06:00"}')
    Config.load()
    assert Config.NOTIFIERS == [{"type": "null"}]
    assert Config.QUIET_HOURS["from"] == "23:00"


def test_bad_json_env_var_is_ignored(sandbox, monkeypatch):
    monkeypatch.setattr(Config, "NOTIFIERS", [])
    monkeypatch.setenv("CRONMASTER_NOTIFIERS", "not json at all")
    Config.load()
    assert Config.NOTIFIERS == []


def test_unknown_keys_are_recorded_for_doctor(sandbox):
    Config.CONFIG_FILE.write_text(json.dumps({"telegram_chat_id": "1", "wat": 5}), encoding="utf-8")
    Config.load()
    assert Config.unknown_keys == ["wat"]


def test_validation_repairs_impossible_values(sandbox, monkeypatch):
    monkeypatch.setattr(Config, "MAX_RETRIES", -5)
    monkeypatch.setattr(Config, "LLM_MIN_CONFIDENCE", 4.2)
    monkeypatch.setattr(Config, "DURATION_REGRESSION_FACTOR", 0.1)
    monkeypatch.setattr(Config, "NOTIFIERS", "not a list")
    monkeypatch.setattr(Config, "QUIET_HOURS", "not a dict")

    Config.validate()

    assert Config.MAX_RETRIES == 3
    assert Config.LLM_MIN_CONFIDENCE == 0.8
    assert Config.DURATION_REGRESSION_FACTOR == 2.0
    assert Config.NOTIFIERS == []
    assert Config.QUIET_HOURS == {}


def test_non_object_config_file_is_ignored(sandbox):
    Config.CONFIG_FILE.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    Config.load()  # لا يرفع


def test_derived_paths_follow_work_dir(sandbox):
    assert Config.history_db() == sandbox / "history.db"
    assert Config.lock_file() == sandbox / "cronmaster.lock"
    assert Config.log_file() == sandbox / "cronmaster.log"


def test_work_dir_is_private(sandbox):
    Config.init_dirs()
    assert Config.WORK_DIR.stat().st_mode & 0o777 == 0o700
    assert Config.BACKUP_DIR.stat().st_mode & 0o777 == 0o700


def test_backups_are_written_private(sandbox):
    from cronmaster.fixers import AutoFixer

    Config.init_dirs()
    fixer = AutoFixer(backend=FakeBackend())
    path = fixer._backup_job(failing_job())
    assert path is not None
    assert path.stat().st_mode & 0o777 == 0o600


# ============================================================
# اللغة
# ============================================================


def test_default_language_is_arabic():
    assert get_lang() == "ar"
    assert t("status.title") == "📊 حالة OpenClaw Cron Jobs"


def test_english_catalog_is_complete():
    """كل مفتاح عربي له مقابل إنجليزي — لا سقوط صامت إلى العربية"""
    assert set(MESSAGES["ar"]) == set(MESSAGES["en"])


def test_switching_language():
    set_lang("en")
    assert t("status.total") == "Total jobs:"
    set_lang("ar")
    assert t("status.total") == "إجمالي المهام:"


def test_unknown_language_keeps_current(caplog):
    set_lang("klingon")
    assert get_lang() == "ar"
    set_lang("")
    assert get_lang() == "ar"


def test_missing_key_returns_the_key_itself():
    assert t("no.such.key") == "no.such.key"


def test_format_errors_do_not_crash():
    assert "{" not in t("restore.no_backups", job_id="x")
    # مفتاح ناقص في التنسيق يعيد النص الخام بدل أن يرفع
    assert t("restore.no_backups") == MESSAGES["ar"]["restore.no_backups"]


def test_supported_langs_are_the_catalog_keys():
    assert set(SUPPORTED_LANGS) == set(MESSAGES)


# ============================================================
# واجهة سطر الأوامر
# ============================================================


def test_parser_exposes_all_commands():
    parser = build_arg_parser()
    for command in ("monitor", "status", "report", "list", "fix", "history", "stats", "restore", "doctor"):
        assert parser.parse_args([command] + (["x"] if command in ("fix", "restore") else [])).command == command


def test_legacy_monitor_flags_still_parse():
    args = build_arg_parser().parse_args(["monitor", "--no-fix", "--no-alert", "--no-retry", "--dry-run"])
    assert (args.no_fix, args.no_alert, args.no_retry, args.dry_run) == (True, True, True, True)


def test_report_formats():
    for fmt in ("markdown", "json", "html"):
        assert build_arg_parser().parse_args(["report", "-f", fmt]).fmt == fmt


def test_version_flag_exits_cleanly(capsys):
    with pytest.raises(SystemExit) as excinfo:
        build_arg_parser().parse_args(["--version"])
    assert excinfo.value.code == 0
    assert "CronMaster_AI" in capsys.readouterr().out


def test_status_output_is_arabic_by_default(cli, capsys):
    cli(["status"], jobs=[make_job(id="a", last_status="ok")])
    out = capsys.readouterr().out
    assert "📊 حالة OpenClaw Cron Jobs" in out
    assert "إجمالي المهام:   1" in out


def test_lang_en_translates_cli_output(cli, capsys):
    cli(["--lang", "en", "status"], jobs=[make_job(id="a", last_status="ok")])
    out = capsys.readouterr().out
    assert "Scheduled job status" in out
    assert "Total jobs:" in out


def test_monitor_exit_code_when_jobs_fail(cli):
    code, _ = cli(["monitor", "--no-alert", "--no-fix"], jobs=[failing_job(error="permission denied")])
    assert code == EXIT_JOBS_FAILING


def test_monitor_exit_code_when_fixes_applied(cli):
    code, _ = cli(["monitor", "--no-alert"], jobs=[failing_job(error="Request timed out")])
    assert code == EXIT_FIXES_APPLIED


def test_monitor_exit_code_on_backend_failure(cli):
    code, _ = cli(["monitor", "--no-alert"], fail_list=True)
    assert code == EXIT_MONITOR_FAILURE


def test_monitor_holds_a_lock(cli, capsys):
    from cronmaster.lock import ExecutionLock

    held = ExecutionLock(Config.lock_file())
    assert held.acquire() is True
    try:
        code, backend = cli(["monitor", "--no-alert"], jobs=[failing_job()])
    finally:
        held.release()

    assert code == 0  # التخطي ليس خطأ
    assert backend.calls == []  # لم تُقرأ المهام أصلاً
    assert "تعمل الآن" in capsys.readouterr().out


def test_prometheus_flag_writes_file(cli, sandbox):
    target = sandbox / "metrics.prom"
    cli(["monitor", "--no-alert", "--prometheus-textfile", str(target)],
        jobs=[make_job(id="a", last_status="ok")])
    assert "cronmaster_monitor_success 1" in target.read_text(encoding="utf-8")


def test_stats_without_history_says_so(cli, capsys):
    cli(["stats"], jobs=[])
    assert "لا يوجد سجل تاريخي بعد" in capsys.readouterr().out


def test_stats_after_monitor_shows_numbers(cli, capsys):
    jobs = [make_job(id="a", name="Alpha", last_status="ok")]
    cli(["monitor", "--no-alert"], jobs=jobs)
    capsys.readouterr()
    cli(["stats"], jobs=jobs)
    out = capsys.readouterr().out
    assert "Alpha" in out
    assert "100%" in out


def test_doctor_fails_when_backend_is_down(cli, capsys):
    code, _ = cli(["doctor"], fail_list=True)
    assert code == 1
    out = capsys.readouterr().out
    assert "🩺 فحص CronMaster" in out
    assert "❌" in out


def test_doctor_passes_with_a_healthy_backend(cli, capsys):
    code, _ = cli(["doctor"], jobs=[make_job(id="a")])
    assert code == 0
    assert "كل الفحوص الأساسية سليمة" in capsys.readouterr().out


def test_restore_list_only_does_not_mutate(cli, sandbox, capsys):
    Config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    (Config.BACKUP_DIR / "j1_20260101_010101.json").write_text(
        json.dumps({"id": "j1", "payload": {"timeoutSeconds": 45}}), encoding="utf-8"
    )
    code, backend = cli(["restore", "j1", "--list"], jobs=[failing_job()])
    assert code == 0
    assert backend.mutations() == []
    assert "j1_20260101_010101.json" in capsys.readouterr().out


def test_restore_with_yes_applies(cli, sandbox):
    Config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    (Config.BACKUP_DIR / "j1_20260101_010101.json").write_text(
        json.dumps({"id": "j1", "payload": {"timeoutSeconds": 45}}), encoding="utf-8"
    )
    code, backend = cli(["restore", "j1", "--yes"], jobs=[failing_job()])
    assert code == 0
    assert ("set_timeout", "j1", 45) in backend.calls


def test_backend_flag_selects_backend(sandbox, monkeypatch, capsys):
    seen = {}

    def fake_get_backend(*a, **k):
        seen["backend"] = Config.BACKEND
        return FakeBackend(jobs=[])

    monkeypatch.setattr(core_module, "get_backend", fake_get_backend)
    monkeypatch.setattr("sys.argv", ["cronmaster", "--backend", "crontab", "status"])
    main()
    assert seen["backend"] == "crontab"


def test_no_command_prints_help(sandbox, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["cronmaster"])
    main()
    assert "أمثلة:" in capsys.readouterr().out


# ============================================================
# غلاف التوافق CronMaster_AI.py
# ============================================================


def test_shim_reexports_public_names():
    import CronMaster_AI as cm

    from cronmaster.models import Job

    assert cm.OpenClawJob is Job
    assert cm.OpenClawCronParser.__name__ == "OpenClawBackend"
    for name in ("Config", "CronMaster", "ErrorAnalyzer", "AutoFixer", "AlertManager",
                 "ReportGenerator", "StateManager", "ERROR_DATABASE", "ErrorType", "main"):
        assert hasattr(cm, name)


def test_shim_patching_reaches_the_package():
    """ترقيع CronMaster_AI.run_openclaw يجب أن يراه كود الحزمة"""
    import CronMaster_AI as cm
    from cronmaster.backends import openclaw as openclaw_module

    original = openclaw_module.run_openclaw
    sentinel = object()
    try:
        cm.run_openclaw = sentinel
        assert openclaw_module.run_openclaw is sentinel
    finally:
        cm.run_openclaw = original
        assert openclaw_module.run_openclaw is original


def test_shim_exposes_version():
    import CronMaster_AI as cm

    assert cm.__version__.count(".") == 2


# ============================================================
# وضعا الإخراج: بشري و JSON
# ============================================================


def test_monitor_defaults_to_json_when_not_a_tty(cli, capsys):
    """المخرجات غير الطرفية تبقى JSON حتى لا تنكسر السكربتات القائمة"""
    cli(["monitor", "--no-alert", "--no-fix"], jobs=[make_job(id="a", last_status="ok")])
    payload = json.loads(capsys.readouterr().out)
    assert payload["total_jobs"] == 1


def test_monitor_prints_human_summary_on_a_tty(cli, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    cli(["monitor", "--no-alert"], jobs=[failing_job(error="permission denied")])
    out = capsys.readouterr().out
    assert t("human.monitor_title") in out
    assert t("human.failed") in out
    assert "permission_denied" in out
    assert "{" not in out  # لا JSON خام


def test_json_flag_forces_json_on_a_tty(cli, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    cli(["monitor", "--no-alert", "--json"], jobs=[make_job(id="a", last_status="ok")])
    assert json.loads(capsys.readouterr().out)["total_jobs"] == 1


@pytest.mark.parametrize("command", [["list"], ["history"], ["fix", "j1"]])
def test_human_mode_for_every_json_command(cli, capsys, monkeypatch, command):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    cli(command, jobs=[failing_job(error="permission denied")])
    out = capsys.readouterr().out
    assert out.strip()
    assert not out.lstrip().startswith(("{", "["))


@pytest.mark.parametrize("command", [["list"], ["history"], ["fix", "j1"]])
def test_json_flag_for_every_json_command(cli, capsys, monkeypatch, command):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    cli([*command, "--json"], jobs=[failing_job(error="permission denied")])
    json.loads(capsys.readouterr().out)  # يرفع عند مخرجات غير صالحة


def test_unexpected_exception_is_reported_not_traced(cli, capsys, monkeypatch):
    """أي استثناء غير متوقع يصل المستخدم كسطر واضح لا كـ traceback"""
    monkeypatch.setattr(core_module.CronMaster, "status", lambda self: (_ for _ in ()).throw(OSError("disk on fire")))
    code, _ = cli(["status"])
    err = capsys.readouterr().err
    assert code == EXIT_MONITOR_FAILURE
    assert "disk on fire" in err
    assert "cronmaster.log" in err
    assert "Traceback" not in err

# -*- coding: utf-8 -*-
"""اختبارات السلوكيات المضافة في جولة الإصلاح: تحقق المدخلات، الأمن، التنسيق، الأداء."""

import json
import sqlite3
from datetime import datetime, timedelta

import pytest
from conftest import FakeBackend, failing_job, make_job

from cronmaster import format as fmt
from cronmaster.analysis.llm import REDACTED, redact
from cronmaster.backends.crontab import CrontabError, SystemCrontabBackend
from cronmaster.backends.base import Capability
from cronmaster.config import Config
from cronmaster.fixers import AutoFixer
from cronmaster.core import CronMaster
from cronmaster.i18n import MESSAGES, set_lang, t
from cronmaster.notifiers.webhook import DiscordNotifier, SlackNotifier, WebhookNotifier
from cronmaster.storage import HistoryStore


# ============================================================
# التحقق من أنواع الإعدادات (البند 3)
# ============================================================


@pytest.mark.parametrize(
    "raw, attr, expected",
    [
        ({"lang": 5}, "LANG", "5"),
        ({"backend": 7}, "BACKEND", "7"),
        ({"telegram_chat_id": 123456}, "TELEGRAM_CHAT_ID", "123456"),
        ({"llm_model": None}, "LLM_MODEL", ""),
        ({"healthcheck_ping_url": ["a"]}, "HEALTHCHECK_PING_URL", ""),
    ],
)
def test_non_string_config_is_coerced_not_fatal(sandbox, raw, attr, expected):
    Config.CONFIG_FILE.write_text(json.dumps(raw), encoding="utf-8")
    Config.load()
    assert getattr(Config, attr) == expected
    assert list(raw)[0] in Config.coerced_keys


def test_numeric_chat_id_no_longer_breaks_the_notifier(sandbox):
    """معرّف Telegram الرقمي هو الخطأ الأشيع: كان يوقف التنبيهات بصمت"""
    Config.CONFIG_FILE.write_text(json.dumps({"telegram_chat_id": 123456}), encoding="utf-8")
    Config.load()
    from cronmaster.notifiers import build_notifiers

    notifier = build_notifiers()[0]
    assert isinstance(notifier.chat_id, str)
    assert notifier.validate() is True


def test_coerced_keys_surface_in_doctor(sandbox):
    Config.CONFIG_FILE.write_text(json.dumps({"lang": 5}), encoding="utf-8")
    Config.load()
    master = CronMaster(backend=FakeBackend([]))
    check = next(c for c in master.doctor()["checks"] if c["name"] == t("doctor.coerced_keys"))
    assert check["ok"] is False
    assert "lang" in check["detail"]
    assert check["hard"] is False  # تحذير لا فشل جوهري


def test_config_snapshot_covers_every_configurable_key():
    """اللقطة مشتقة من المفاتيح لا من قائمة يدوية، فلا يتسرب إعداد جديد"""
    snapshot = Config.snapshot()
    assert set(Config.configurable_keys()) <= set(snapshot)
    assert "WORK_DIR" in snapshot


# ============================================================
# حجب الأسرار قبل مغادرة الجهاز (البند 4)
# ============================================================


@pytest.mark.parametrize(
    "secret, text",
    [
        ("sk-abc123DEF456ghi789", "POST https://api.x/v1?api_key=sk-abc123DEF456ghi789 failed"),
        ("S3cr3tPass", "could not connect to postgres://admin:S3cr3tPass@db.internal:5432/prod"),
        ("ghp_1234567890abcdefghij", 'rejected: {"api_key": "ghp_1234567890abcdefghij"}'),
        ("abcdefghijklmnop", "export TOKEN=abcdefghijklmnop && run"),
        ("hunter2hunter2", "Authorization: Bearer hunter2hunter2"),
    ],
)
def test_redact_removes_secrets(secret, text):
    cleaned = redact(text)
    assert secret not in cleaned
    assert REDACTED in cleaned


def test_redact_leaves_benign_errors_intact():
    text = "ModuleNotFoundError: No module named 'requests'"
    assert redact(text) == text


def test_llm_payload_is_redacted_before_sending(sandbox, fake_anthropic, monkeypatch):
    """نص الخطأ لا يغادر الجهاز بأسراره حتى مع تفعيل المصنّف"""
    from cronmaster.analysis.llm import LLMClassifier

    captured = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            raise fake_anthropic.APIConnectionError("no network")

    class FakeClient:
        messages = FakeMessages()

    Config.LLM_ENABLED = True
    job = failing_job(error="auth failed: api_key=sk-VERYSECRETVALUE123")
    classifier = LLMClassifier(client=FakeClient())
    from cronmaster.analysis import ErrorAnalyzer

    classifier.classify(job, ErrorAnalyzer.analyze_regex(job))

    payload = json.dumps(captured.get("messages", []), ensure_ascii=False)
    assert "sk-VERYSECRETVALUE123" not in payload
    assert REDACTED in payload


# ============================================================
# قنوات التنبيه على https (البند 11)
# ============================================================


def test_http_webhook_rejected_by_default():
    assert WebhookNotifier("http://hooks.example/x").validate() is False
    assert WebhookNotifier("https://hooks.example/x").validate() is True


def test_http_webhook_allowed_with_explicit_opt_in():
    assert WebhookNotifier("http://hooks.example/x", allow_insecure=True).validate() is True


def test_http_opt_in_via_config(sandbox):
    Config.ALLOW_INSECURE_WEBHOOKS = True
    assert WebhookNotifier("http://hooks.example/x").validate() is True


def test_insecure_webhook_does_not_send(monkeypatch):
    calls = []
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: calls.append(a))
    for notifier in (WebhookNotifier, SlackNotifier, DiscordNotifier):
        assert notifier("http://hooks.example/x").send("مرحباً") is False
    assert calls == []


def test_notifier_spec_can_opt_in(sandbox):
    from cronmaster.notifiers import build_notifiers

    built = build_notifiers([{"type": "webhook", "url": "http://internal/x", "allow_insecure": True}])
    assert built[0].validate() is True


# ============================================================
# crontab: لا فقدان صامت لتعديلات المستخدم (البند 12)
# ============================================================


def _crontab_stub(states, writes):
    """يحاكي crontab: كل قراءة تعيد الحالة التالية، والكتابة تُسجَّل"""

    class Result:
        def __init__(self, stdout="", returncode=0, stderr=""):
            self.stdout, self.returncode, self.stderr = stdout, returncode, stderr

    def run(*args, stdin=None, timeout=30):
        if args and args[0] == "-l":
            return Result(stdout=states.pop(0) if len(states) > 1 else states[0])
        writes.append(stdin)
        return Result()

    return run


def test_crontab_rewrite_aborts_when_table_changed(monkeypatch):
    original = "0 3 * * * /usr/bin/backup\n"
    changed = "0 3 * * * /usr/bin/backup\n30 4 * * * /usr/bin/new-job\n"
    writes = []
    monkeypatch.setattr(
        "cronmaster.backends.crontab.run_crontab",
        _crontab_stub([original, changed], writes),
    )
    backend = SystemCrontabBackend()
    job_id = backend._job_id("/usr/bin/backup")
    with pytest.raises(CrontabError, match="تغيّر crontab"):
        backend.set_schedule(job_id, "15 3 * * *")
    assert writes == []  # لم تُكتب أي نسخة تفقد مهمة المستخدم الجديدة


def test_crontab_rewrite_proceeds_when_table_unchanged(monkeypatch):
    original = "0 3 * * * /usr/bin/backup\n"
    writes = []
    monkeypatch.setattr(
        "cronmaster.backends.crontab.run_crontab",
        _crontab_stub([original, original], writes),
    )
    backend = SystemCrontabBackend()
    assert backend.set_schedule(backend._job_id("/usr/bin/backup"), "15 3 * * *") is True
    assert writes and "15 3 * * * /usr/bin/backup" in writes[0]


# ============================================================
# حصر مصدر النسخ الاحتياطية (البند 23)
# ============================================================


def test_backup_outside_dir_is_refused(sandbox, tmp_path):
    from cronmaster.cli import _resolve_backup

    Config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "elsewhere.json"
    outside.write_text("{}", encoding="utf-8")
    assert _resolve_backup(str(outside), None) is None


def test_backup_by_name_resolves_inside_dir(sandbox):
    Config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    inside = Config.BACKUP_DIR / "j1_20260101_000000.json"
    inside.write_text("{}", encoding="utf-8")
    assert _resolved(inside.name) == inside.resolve()
    assert _resolved(str(inside)) == inside.resolve()


def _resolved(value):
    from cronmaster.cli import _resolve_backup

    return _resolve_backup(value, None)


# ============================================================
# التنسيق والمحاذاة (البندان 8 و14)
# ============================================================


@pytest.mark.parametrize(
    "text, width",
    [
        ("إجمالي المهام:", 14),  # العربية خانة لكل حرف
        ("ناجحة ✅:", 9),  # الإيموجي خانتان
        ("حرجة ⚠️:", 8),  # محدِّد التقديم يرقّي المحرف السابق
        ("Total jobs:", 11),
    ],
)
def test_display_width_counts_terminal_columns(text, width):
    assert fmt.display_width(text) == width


def test_status_alignment_matches_historic_arabic_output(sandbox, capsys):
    """المحاذاة صارت محسوبة، ومخرجات العربية تبقى مطابقة حرفياً"""
    from cronmaster.cli import _print_status

    set_lang("ar")
    _print_status({"total_jobs": 1, "ok": 1, "error": 0, "critical": 0, "silent": 0, "success_rate": 100.0})
    out = capsys.readouterr().out
    assert "إجمالي المهام:   1" in out
    assert "نسبة النجاح:     100.0%" in out


def test_status_alignment_is_correct_in_english(sandbox, capsys):
    from cronmaster.cli import _print_status

    set_lang("en")
    _print_status({"total_jobs": 8, "ok": 7, "error": 1, "critical": 0, "silent": 0, "success_rate": 87.5})
    lines = [line for line in capsys.readouterr().out.splitlines() if ":" in line and "=" not in line]
    columns = {fmt.display_width(line.split(":")[0] + ":") + _gap(line) for line in lines}
    assert len(columns) == 1, f"أعمدة غير متساوية: {columns}"


def _gap(line):
    label, _, rest = line.partition(":")
    return len(rest) - len(rest.lstrip(" "))


def test_format_helpers_are_shared_between_cli_and_html():
    """مصدر واحد لصيغة العرض: لا انحراف صامت بين الطرفية والتقرير"""
    from cronmaster.reporting import html

    assert html._pct is fmt.pct
    assert html._secs is fmt.secs
    assert fmt.pct(None) == fmt.secs(None) == fmt.duration(None) == fmt.EMPTY


# ============================================================
# i18n مكتمل (البند 7)
# ============================================================


def test_error_catalog_is_translated_both_ways():
    from cronmaster.analysis import ERROR_DATABASE

    for sig in ERROR_DATABASE:
        for lang in ("ar", "en"):
            set_lang(lang)
            assert sig.description and sig.description != f"{sig.message_key}.desc"
            assert sig.suggested_fix and sig.suggested_fix != f"{sig.message_key}.fix"
    set_lang("ar")


def test_english_run_has_no_arabic_analysis_text(sandbox):
    """كان --lang en يعطي واجهة إنجليزية وتحليلاً عربياً"""
    set_lang("en")
    jobs = [
        failing_job(error="Request timed out"),
        failing_job(id="j2", error="permission denied"),
        failing_job(id="j3", error="Connection refused"),
        failing_job(id="j4", error="!!! unclassifiable !!!"),
    ]
    result = CronMaster(backend=FakeBackend(jobs)).monitor(alert=False)
    text = json.dumps(result, ensure_ascii=False)
    assert not any("؀" <= ch <= "ۿ" for ch in text), "بقي نص عربي في مخرجات الإنجليزية"
    set_lang("ar")


def test_message_catalogs_have_the_same_keys():
    missing = set(MESSAGES["ar"]) - set(MESSAGES["en"])
    assert not missing, f"مفاتيح بلا ترجمة إنجليزية: {sorted(missing)}"


def test_boom_is_not_out_of_memory():
    """النمط OOM بلا حدود كلمات كان يبتلع أي كلمة فيها 'oom'"""
    from cronmaster.analysis import ErrorAnalyzer
    from cronmaster.models import ErrorType

    analysis = ErrorAnalyzer.analyze_regex(failing_job(error="the process went boom unexpectedly"))
    assert analysis.error_type is ErrorType.UNKNOWN


# ============================================================
# الأداء: عدد الاستعلامات وعمليات الالتزام (البندان 5 و6)
# ============================================================


@pytest.fixture
def populated_store(sandbox):
    store = HistoryStore(db_path=sandbox / "h.db", enabled=True)
    jobs = [make_job(id=f"j{i}", name=f"Job {i}", last_status="ok", last_duration_seconds=float(i + 1))
            for i in range(20)]
    for cycle in range(10):
        for job in jobs:
            job.last_run_at = datetime.now() - timedelta(minutes=cycle * 60 + int(job.id[1:]))
        store.record_cycle(jobs)
    return store


def _trace(store):
    statements = []
    store._conn.set_trace_callback(statements.append)
    return statements


def test_summaries_do_not_scale_five_queries_per_job(populated_store):
    """كانت job_summary تكلّف 5 استعلامات لكل مهمة (101 لعشرين مهمة)"""
    statements = _trace(populated_store)
    ids = populated_store.job_ids()
    statements.clear()

    populated_store.job_summaries(ids, days=90)
    selects = [s for s in statements if s.strip().upper().startswith("SELECT")]
    populated_store._conn.set_trace_callback(None)

    assert len(selects) <= len(ids) + 1, f"{len(selects)} استعلاماً لـ {len(ids)} مهمة"


def test_record_cycle_commits_once(populated_store):
    """كل commit عملية fsync: 20 مهمة كانت تعني 20 مزامنة"""
    jobs = [make_job(id=f"j{i}", last_status="ok", last_run_at=datetime.now() - timedelta(seconds=i))
            for i in range(20)]
    statements = _trace(populated_store)
    populated_store.record_cycle(jobs)
    populated_store._conn.set_trace_callback(None)
    assert len([s for s in statements if "COMMIT" in s.upper()]) == 1


def test_summaries_match_the_single_job_helper(populated_store):
    ids = populated_store.job_ids()
    batched = {s["job_id"]: s for s in populated_store.job_summaries(ids, days=90)}
    for job_id in ids:
        assert batched[job_id] == populated_store.job_summary(job_id, days=90)


def test_mtbf_is_window_bounded(sandbox):
    """كانت تسحب كل صفوف المهمة بلا حد لحساب رقم واحد"""
    store = HistoryStore(db_path=sandbox / "h.db", enabled=True)
    base = datetime(2026, 5, 1)
    rows = [("j1", "Job", (base + timedelta(hours=i)).isoformat(), f"k{i}", "error") for i in range(300)]
    store._conn.executemany(
        "INSERT INTO runs (job_id, job_name, observed_at, run_key, status) VALUES (?,?,?,?,?)", rows
    )
    store._conn.commit()

    statements = _trace(store)
    store.mean_time_between_failures("j1")
    store._conn.set_trace_callback(None)
    assert any("LIMIT" in s.upper() for s in statements)


def test_history_survives_a_broken_connection(sandbox):
    """التدهور اللطيف باقٍ بعد إعادة كتابة مسار الإدراج"""
    store = HistoryStore(db_path=sandbox / "h.db", enabled=True)
    store._conn.close()  # اتصال ميت
    with pytest.raises(sqlite3.ProgrammingError):
        store._conn.execute("SELECT 1")
    assert store.record_cycle([make_job(last_status="ok")]) == 0
    assert store.available is False


# ============================================================
# مسارات AutoFixer غير المغطاة: القدرات الناقصة وفشل الواجهة
# ============================================================


def test_fixer_refuses_operations_the_backend_cannot_do(sandbox):
    """واجهة محدودة تُنتج رفضاً واضحاً لا انهياراً"""
    backend = FakeBackend([], capabilities={Capability.LIST})
    fixer = AutoFixer(backend=backend)
    job = failing_job()

    assert fixer.retry_job(job.id) is False
    assert fixer.set_timeout(job, 300) is False
    assert fixer.disable_job(job) is False
    assert fixer.reschedule(job, "5 * * * *") is False
    assert backend.mutations() == []


def test_fixer_reports_backend_refusal(sandbox):
    backend = FakeBackend([], fail_ops=True)
    fixer = AutoFixer(backend=backend)
    job = failing_job()

    assert fixer.set_timeout(job, 300) is False
    assert fixer.disable_job(job) is False
    assert fixer.reschedule(job, "5 * * * *") is False
    assert fixer.retry_job(job.id) is False


def test_restore_skips_fields_the_backend_cannot_set(sandbox):
    """الاستعادة الجزئية تفصّل ما استُعيد وما تُخطّي ولماذا"""
    backend = FakeBackend([], capabilities={Capability.LIST, Capability.SET_SCHEDULE})
    fixer = AutoFixer(backend=backend)
    backup = sandbox / "b.json"
    backup.write_text(
        json.dumps({"payload": {"timeoutSeconds": 90}, "schedule": {"expr": "0 4 * * *"}, "enabled": True}),
        encoding="utf-8",
    )

    outcome = fixer.restore("j1", backup)
    assert outcome["restored"] == {"schedule": "0 4 * * *"}
    assert set(outcome["skipped"]) == {"timeout_seconds", "enabled"}


def test_restore_rejects_a_backup_with_nothing_restorable(sandbox):
    fixer = AutoFixer(backend=FakeBackend([]))
    backup = sandbox / "b.json"
    backup.write_text(json.dumps({"name": "شيء آخر"}), encoding="utf-8")
    with pytest.raises(ValueError):
        fixer.restore("j1", backup)


def test_backup_failure_does_not_stop_the_fix(sandbox, monkeypatch):
    """تعذّر حفظ النسخة الاحتياطية يُسجَّل ولا يُسقط الإصلاح"""
    monkeypatch.setattr(
        "cronmaster.fixers.write_secure",
        lambda *a, **k: (_ for _ in ()).throw(OSError("read-only")),
    )
    backend = FakeBackend([failing_job(error="Request timed out")])
    fixer = AutoFixer(backend=backend)
    assert fixer._backup_job(failing_job()) is None
    assert fixer.set_timeout(failing_job(), 300) is True

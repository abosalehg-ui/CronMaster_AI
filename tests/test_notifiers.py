# -*- coding: utf-8 -*-
"""اختبارات قنوات التنبيه وفترة الهدوء."""

import json
from datetime import datetime

import pytest

import cronmaster.notifiers.webhook as webhook_module
from cronmaster.config import Config
from cronmaster.notifiers import (
    AlertManager,
    DiscordNotifier,
    NullNotifier,
    SlackNotifier,
    TelegramNotifier,
    WebhookNotifier,
    build_notifiers,
    in_quiet_hours,
)
from cronmaster.notifiers.base import Notifier
from cronmaster.storage import StateManager


class RecordingNotifier(Notifier):
    """قناة اختبار: تسجّل ما أُرسل وتنجح أو تفشل حسب الطلب"""

    def __init__(self, name="recorder", ok=True, raises=False):
        super().__init__()
        self.name = name
        self.ok = ok
        self.raises = raises
        self.sent = []

    def send(self, message: str) -> bool:
        if self.raises:
            raise RuntimeError("القناة انفجرت")
        self.sent.append(message)
        return self.ok


@pytest.fixture
def state(tmp_path):
    return StateManager(tmp_path / "state.json")


# ============================================================
# بناء القنوات
# ============================================================


def test_telegram_chat_id_is_a_shorthand(sandbox, monkeypatch):
    monkeypatch.setattr(Config, "TELEGRAM_CHAT_ID", "123")
    notifiers = build_notifiers(specs=[])
    assert [n.name for n in notifiers] == ["telegram"]


def test_no_config_means_no_notifiers(sandbox):
    assert build_notifiers(specs=[], telegram_chat_id="") == []


def test_build_from_specs(sandbox):
    notifiers = build_notifiers(
        specs=[
            {"type": "slack", "url": "https://hooks.example/slack"},
            {"type": "discord", "url": "https://hooks.example/discord"},
            {"type": "webhook", "url": "https://hooks.example/generic", "field": "body"},
            {"type": "telegram", "chat_id": "999"},
            {"type": "null"},
        ],
        telegram_chat_id="",
    )
    assert [n.name for n in notifiers] == ["slack", "discord", "webhook", "telegram", "null"]
    assert notifiers[2].field == "body"
    assert notifiers[3].chat_id == "999"


def test_unknown_notifier_type_is_skipped(sandbox, caplog):
    notifiers = build_notifiers(specs=[{"type": "carrier-pigeon", "url": "x"}], telegram_chat_id="")
    assert notifiers == []


def test_malformed_spec_is_skipped(sandbox):
    assert build_notifiers(specs=["not-a-dict"], telegram_chat_id="") == []


# ============================================================
# قنوات Webhook
# ============================================================


class FakeResponse:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_webhook_posts_json(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["headers"] = request.headers
        return FakeResponse(200)

    monkeypatch.setattr(webhook_module.urllib.request, "urlopen", fake_urlopen)
    assert WebhookNotifier("https://example.test/hook").send("مرحباً") is True
    assert captured["url"] == "https://example.test/hook"
    assert captured["body"] == {"text": "مرحباً"}


def test_slack_and_discord_field_names(monkeypatch):
    bodies = []
    monkeypatch.setattr(
        webhook_module.urllib.request,
        "urlopen",
        lambda request, timeout=None: bodies.append(json.loads(request.data.decode("utf-8"))) or FakeResponse(200),
    )
    SlackNotifier("https://example.test/s").send("hi")
    DiscordNotifier("https://example.test/d").send("hi")
    assert bodies == [{"text": "hi"}, {"content": "hi"}]


def test_discord_truncates_long_messages(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        webhook_module.urllib.request,
        "urlopen",
        lambda request, timeout=None: captured.update(json.loads(request.data.decode("utf-8"))) or FakeResponse(200),
    )
    DiscordNotifier("https://example.test/d").send("x" * 5000)
    assert len(captured["content"]) == DiscordNotifier.MAX_LENGTH


def test_webhook_network_failure_returns_false(monkeypatch):
    def boom(request, timeout=None):
        raise OSError("الشبكة مقطوعة")

    monkeypatch.setattr(webhook_module.urllib.request, "urlopen", boom)
    assert WebhookNotifier("https://example.test/hook").send("hi") is False


def test_webhook_rejects_invalid_url(monkeypatch):
    def fail(*a, **k):
        raise AssertionError("يجب ألا يُفتح اتصال لعنوان غير صالح")

    monkeypatch.setattr(webhook_module.urllib.request, "urlopen", fail)
    assert WebhookNotifier("ftp://nope").send("hi") is False
    assert WebhookNotifier("").validate() is False


def test_null_notifier_always_succeeds():
    assert NullNotifier().send("hi") is True


def test_telegram_without_chat_id_never_calls_backend(sandbox, monkeypatch):
    monkeypatch.setattr(Config, "TELEGRAM_CHAT_ID", "")
    assert TelegramNotifier().validate() is False
    assert TelegramNotifier().send("hi") is False


# ============================================================
# التوزيع والفشل الجزئي
# ============================================================


def test_partial_failure_still_reports_success(state):
    good = RecordingNotifier("good", ok=True)
    bad = RecordingNotifier("bad", ok=False)
    manager = AlertManager(notifiers=[bad, good], state=state)

    assert manager.dispatch("تنبيه") is True
    assert good.sent == ["تنبيه"]


def test_exploding_notifier_does_not_stop_the_others(state):
    exploding = RecordingNotifier("boom", raises=True)
    good = RecordingNotifier("good", ok=True)
    manager = AlertManager(notifiers=[exploding, good], state=state)

    assert manager.dispatch("تنبيه") is True
    assert good.sent == ["تنبيه"]


def test_all_channels_failing_reports_failure(state):
    manager = AlertManager(notifiers=[RecordingNotifier(ok=False), RecordingNotifier(ok=False)], state=state)
    assert manager.dispatch("تنبيه") is False


# ============================================================
# فترة الهدوء
# ============================================================


@pytest.mark.parametrize(
    "hour,expected",
    [(23, True), (2, True), (6, True), (7, False), (12, False), (21, False), (22, True)],
)
def test_quiet_hours_crossing_midnight(hour, expected):
    config = {"from": "22:00", "to": "07:00"}
    assert in_quiet_hours(config, now=datetime(2026, 5, 1, hour, 0)) is expected


@pytest.mark.parametrize("hour,expected", [(0, False), (9, True), (12, False), (17, False)])
def test_quiet_hours_same_day(hour, expected):
    config = {"from": "09:00", "to": "12:00"}
    assert in_quiet_hours(config, now=datetime(2026, 5, 1, hour, 0)) is expected


def test_quiet_hours_empty_or_malformed_is_off():
    assert in_quiet_hours({}, now=datetime(2026, 5, 1, 3, 0)) is False
    assert in_quiet_hours({"from": "nope", "to": "07:00"}, now=datetime(2026, 5, 1, 3, 0)) is False
    assert in_quiet_hours({"from": "22:00", "to": "22:00"}, now=datetime(2026, 5, 1, 22, 0)) is False


def test_unknown_timezone_falls_back_to_local(monkeypatch):
    # لا يرفع استثناءً حتى لو كانت المنطقة الزمنية مجهولة
    assert in_quiet_hours({"from": "00:00", "to": "23:59", "tz": "Mars/Olympus"}) in (True, False)


# ============================================================
# التأجيل والملخص
# ============================================================


def _quiet_now(monkeypatch, quiet: bool):
    import cronmaster.notifiers as notifiers_module

    monkeypatch.setattr(notifiers_module, "in_quiet_hours", lambda *a, **k: quiet)


def test_non_critical_alert_is_queued_during_quiet_hours(state, monkeypatch):
    _quiet_now(monkeypatch, True)
    channel = RecordingNotifier()
    manager = AlertManager(notifiers=[channel], state=state)

    assert manager.send("تنبيه عادي") is False
    assert channel.sent == []
    assert len(state.peek_queued_alerts()) == 1


def test_critical_alert_bypasses_quiet_hours(state, monkeypatch):
    _quiet_now(monkeypatch, True)
    channel = RecordingNotifier()
    manager = AlertManager(notifiers=[channel], state=state)

    assert manager.send("قاطع الدائرة", critical=True) is True
    assert channel.sent == ["قاطع الدائرة"]
    assert state.peek_queued_alerts() == []


def test_digest_flushes_once_quiet_hours_end(state, monkeypatch):
    _quiet_now(monkeypatch, True)
    channel = RecordingNotifier()
    manager = AlertManager(notifiers=[channel], state=state)
    manager.send("أول")
    manager.send("ثانٍ")
    assert manager.flush_digest() is False  # ما زلنا في فترة الهدوء

    _quiet_now(monkeypatch, False)
    assert manager.flush_digest() is True
    assert len(channel.sent) == 1
    digest = channel.sent[0]
    assert "أول" in digest and "ثانٍ" in digest
    assert state.peek_queued_alerts() == []


def test_digest_keeps_queue_when_all_channels_fail(state, monkeypatch):
    _quiet_now(monkeypatch, True)
    manager = AlertManager(notifiers=[RecordingNotifier(ok=False)], state=state)
    manager.send("مؤجل")

    _quiet_now(monkeypatch, False)
    assert manager.flush_digest() is False
    assert len(state.peek_queued_alerts()) == 1  # لم يُفقد التنبيه


def test_empty_digest_is_a_noop(state, monkeypatch):
    _quiet_now(monkeypatch, False)
    manager = AlertManager(notifiers=[RecordingNotifier()], state=state)
    assert manager.flush_digest() is False


def test_queue_is_bounded(state):
    for i in range(80):
        state.queue_alert(f"تنبيه {i}")
    assert len(state.peek_queued_alerts()) == 50

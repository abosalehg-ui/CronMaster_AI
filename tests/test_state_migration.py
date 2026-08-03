# -*- coding: utf-8 -*-
"""اختبارات ترقية ملف الحالة وتوافقه مع النسخ القديمة."""

import json

from cronmaster.storage import STATE_SCHEMA_VERSION, StateManager

# ملف حالة بصيغة الإصدار السابق تماماً: بلا schema_version وبلا المفاتيح الجديدة
OLD_STATE = {
    "fixes_applied": [
        {"job_id": "a", "fix_type": "timeout", "details": "رُفعت المهلة", "timestamp": "2026-01-01T00:00:00"},
        {"job_id": "b", "fix_type": "network_error", "details": "أُعيد التشغيل", "timestamp": "2026-01-02T00:00:00"},
    ],
    "alerts": {"a/timeout": "2026-01-01T00:00:00"},
    "failing_jobs": ["a", "b"],
    "retries": {"b": 2},
    "last_run": "2026-01-02T03:04:05",
}


def write_old_state(path):
    path.write_text(json.dumps(OLD_STATE, ensure_ascii=False), encoding="utf-8")
    return path


def test_old_state_loads_without_data_loss(tmp_path):
    state_file = write_old_state(tmp_path / "state.json")

    sm = StateManager(state_file)

    # لا شيء فُقد
    assert len(sm.state["fixes_applied"]) == 2
    assert sm.state["fixes_applied"][0]["job_id"] == "a"
    assert sm.state["alerts"] == {"a/timeout": "2026-01-01T00:00:00"}
    assert sm.state["failing_jobs"] == ["a", "b"]
    assert sm.get_retry_count("b") == 2
    assert sm.state["last_run"] == "2026-01-02T03:04:05"


def test_old_state_gains_new_keys_and_version(tmp_path):
    sm = StateManager(write_old_state(tmp_path / "state.json"))

    assert sm.state["schema_version"] == STATE_SCHEMA_VERSION
    for key in ("queued_alerts", "timeout_fixes", "pending_rollback", "circuit_open", "reschedules", "last_prune"):
        assert key in sm.state

    assert sm.get_timeout_fixes("a") == 0
    assert sm.is_circuit_open("a") is False
    assert sm.peek_queued_alerts() == []


def test_migration_is_persisted(tmp_path):
    state_file = write_old_state(tmp_path / "state.json")
    StateManager(state_file).save()

    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved["schema_version"] == STATE_SCHEMA_VERSION
    assert len(saved["fixes_applied"]) == 2


def test_migration_is_idempotent(tmp_path):
    state_file = write_old_state(tmp_path / "state.json")
    StateManager(state_file).save()
    second = StateManager(state_file)
    second.save()

    assert second.state["schema_version"] == STATE_SCHEMA_VERSION
    assert len(second.state["fixes_applied"]) == 2


def test_defaults_are_not_shared_between_instances(tmp_path):
    """خطأ كلاسيكي: قاموس افتراضي مشترك بين النسخ"""
    first = StateManager(tmp_path / "a.json")
    first.record_retry("x")
    second = StateManager(tmp_path / "b.json")
    assert second.get_retry_count("x") == 0


def test_corrupt_state_starts_fresh_but_versioned(tmp_path):
    state_file = tmp_path / "state.json"
    state_file.write_text("{ هذا ليس JSON", encoding="utf-8")

    sm = StateManager(state_file)

    assert sm.state["fixes_applied"] == []
    assert sm.state["schema_version"] == STATE_SCHEMA_VERSION


def test_state_file_written_with_restricted_permissions(tmp_path):
    state_file = tmp_path / "state.json"
    StateManager(state_file).save()
    assert state_file.stat().st_mode & 0o777 == 0o600


def test_prune_marker_gates_daily_cleanup(tmp_path):
    sm = StateManager(tmp_path / "state.json")
    assert sm.should_prune() is True
    sm.mark_pruned()
    assert sm.should_prune() is False
    sm.state["last_prune"] = "ليس تاريخاً"
    assert sm.should_prune() is True

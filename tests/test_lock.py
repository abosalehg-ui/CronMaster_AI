# -*- coding: utf-8 -*-
"""اختبارات قفل التنفيذ."""

import os

import cronmaster.lock as lock_module
from cronmaster.lock import ExecutionLock


def test_acquire_and_release(tmp_path):
    lock = ExecutionLock(tmp_path / "cronmaster.lock")
    assert lock.acquire() is True
    assert lock.acquired is True
    assert (tmp_path / "cronmaster.lock").exists()
    lock.release()
    assert lock.acquired is False


def test_second_acquisition_while_held_is_refused(tmp_path):
    path = tmp_path / "cronmaster.lock"
    first = ExecutionLock(path)
    assert first.acquire() is True

    second = ExecutionLock(path)
    assert second.acquire() is False  # لا استثناء، مجرد "مشغول"
    assert second.acquired is False

    first.release()
    # بعد التحرير يمكن أخذه من جديد
    assert second.acquire() is True
    second.release()


def test_reacquire_by_same_object_is_idempotent(tmp_path):
    lock = ExecutionLock(tmp_path / "cronmaster.lock")
    assert lock.acquire() is True
    assert lock.acquire() is True
    lock.release()


def test_stale_lock_is_reclaimed(tmp_path):
    """ملف قفل خلّفته عملية منتهية لا يمنع الأخذ"""
    path = tmp_path / "cronmaster.lock"
    path.write_text("999999999", encoding="utf-8")  # رقم عملية شبه مستحيل الوجود

    lock = ExecutionLock(path)
    assert lock.acquire() is True
    assert path.read_text(encoding="utf-8").strip() == str(os.getpid())
    lock.release()


def test_pidfile_fallback_without_fcntl(tmp_path, monkeypatch):
    """المسار البديل على الأنظمة التي لا توفر fcntl"""
    monkeypatch.setattr(lock_module, "fcntl", None)
    path = tmp_path / "cronmaster.lock"

    first = ExecutionLock(path)
    assert first.acquire() is True
    assert path.read_text(encoding="utf-8").strip() == str(os.getpid())

    # عملية "أخرى" حية تحمل القفل
    path.write_text("424242", encoding="utf-8")
    monkeypatch.setattr(lock_module, "_pid_alive", lambda pid: True)
    second = ExecutionLock(path)
    assert second.acquire() is False

    # نفس الملف لكن صاحبه ميت → يُستعاد
    monkeypatch.setattr(lock_module, "_pid_alive", lambda pid: False)
    third = ExecutionLock(path)
    assert third.acquire() is True
    third.release()
    assert not path.exists()


def test_context_manager_releases(tmp_path):
    path = tmp_path / "cronmaster.lock"
    lock = ExecutionLock(path)
    with lock as acquired:
        assert acquired is True
        assert ExecutionLock(path).acquire() is False
    assert ExecutionLock(path).acquire() is True


def test_unwritable_lock_dir_does_not_block_run(tmp_path, monkeypatch):
    """القفل احتياط: تعذّر إنشائه لا يمنع المراقبة من العمل"""
    monkeypatch.setattr(lock_module, "fcntl", None)
    lock = ExecutionLock(tmp_path / "cronmaster.lock")

    def boom(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(type(lock.path), "write_text", boom)
    assert lock.acquire() is True


def test_pid_alive_helper():
    assert lock_module._pid_alive(os.getpid()) is True
    assert lock_module._pid_alive(0) is False
    assert lock_module._pid_alive(-5) is False

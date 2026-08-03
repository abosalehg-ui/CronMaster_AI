# -*- coding: utf-8 -*-
"""اختبارات السجل التاريخي (SQLite)."""

from datetime import datetime, timedelta

import pytest

from cronmaster.config import Config
from cronmaster.storage import HistoryStore

from conftest import make_job


@pytest.fixture
def store(tmp_path):
    s = HistoryStore(db_path=tmp_path / "history.db", enabled=True)
    yield s
    s.close()


def observe(store, job_id="j1", status="ok", run_at=None, duration=None, name="Job", errors=0):
    job = make_job(
        id=job_id,
        name=name,
        last_status=status,
        last_run_at=run_at or datetime.now(),
        last_duration_seconds=duration,
        consecutive_errors=errors,
    )
    return store.record_observation(job)


# ============================================================
# الإنشاء والتكرار
# ============================================================


def test_store_creates_schema(store, tmp_path):
    assert store.available
    assert (tmp_path / "history.db").exists()
    assert store.integrity_check()


def test_dedup_by_last_run_at(store):
    run_at = datetime(2026, 5, 1, 12, 0)
    assert observe(store, run_at=run_at) is True
    # دورة مراقبة ثانية بين تشغيلين فعليين: نفس المفتاح، لا صف جديد
    assert observe(store, run_at=run_at) is False
    assert observe(store, run_at=run_at) is False
    assert store.run_count("j1") == 1

    # تشغيل فعلي جديد → صف جديد
    assert observe(store, run_at=run_at + timedelta(hours=1)) is True
    assert store.run_count("j1") == 2


def test_record_cycle_skips_disabled_jobs(store):
    enabled = make_job(id="on", last_status="ok", last_run_at=datetime.now())
    disabled = make_job(id="off", enabled=False, last_status="ok", last_run_at=datetime.now())
    assert store.record_cycle([enabled, disabled]) == 1
    assert store.job_ids() == ["on"]


# ============================================================
# الحسابات المشتقة
# ============================================================


def test_success_rate_and_flakiness(store):
    base = datetime(2026, 5, 1, 0, 0)
    for i, status in enumerate(["ok", "ok", "error", "ok", "error"]):
        observe(store, status=status, run_at=base + timedelta(hours=i))

    assert store.success_rate("j1") == pytest.approx(3 / 5)
    assert store.flakiness_score("j1") == pytest.approx(2 / 5)
    assert store.success_rate("unknown") is None
    assert store.flakiness_score("unknown") is None


def test_success_rate_ignores_unknown_status(store):
    base = datetime(2026, 5, 1, 0, 0)
    observe(store, status="ok", run_at=base)
    observe(store, status=None, run_at=base + timedelta(hours=1))
    assert store.success_rate("j1") == 1.0


def test_duration_trend_detects_regression(store):
    base = datetime(2026, 5, 1, 0, 0)
    # أربع تشغيلات بطيئة تلي أربع سريعة → النسبة ينبغي أن تتجاوز 2
    for i, seconds in enumerate([10, 10, 10, 10, 30, 30, 30, 30]):
        observe(store, run_at=base + timedelta(hours=i), duration=seconds)

    trend = store.duration_trend("j1")
    assert trend["samples"] == 8
    assert trend["baseline_mean"] == pytest.approx(10.0)
    assert trend["recent_mean"] == pytest.approx(30.0)
    assert trend["ratio"] == pytest.approx(3.0)
    assert trend["overall_mean"] == pytest.approx(20.0)


def test_duration_trend_needs_enough_samples(store):
    base = datetime(2026, 5, 1, 0, 0)
    for i in range(3):
        observe(store, run_at=base + timedelta(hours=i), duration=5)
    trend = store.duration_trend("j1")
    assert trend["samples"] == 3
    assert trend["ratio"] is None  # لا نُطلق إنذاراً على ثلاث عينات


def test_mean_time_between_failures(store):
    base = datetime(2026, 5, 1, 0, 0)
    for i in range(3):
        observe(store, status="error", run_at=base + timedelta(hours=i))
    # صفوف الملاحظة تُسجَّل بوقت "الآن"، لذا الفجوات صغيرة لكن العدد صحيح
    assert store.mean_time_between_failures("j1") is not None
    assert store.mean_time_between_failures("no-such-job") is None


def test_job_summary_shape(store):
    observe(store, status="error", run_at=datetime.now(), duration=12.5, name="Backup")
    summary = store.job_summary("j1", days=30)
    assert summary["job_id"] == "j1"
    assert summary["job_name"] == "Backup"
    assert summary["runs"] == 1
    assert summary["success_rate"] == 0.0


def test_daily_success_rates(store):
    observe(store, status="ok", run_at=datetime.now())
    rates = store.daily_success_rates(days=30)
    assert len(rates) == 1
    assert rates[0]["rate"] == 1.0


# ============================================================
# التنظيف
# ============================================================


def test_prune_removes_old_rows_only(store):
    observe(store, status="ok", run_at=datetime.now())
    # صف قديم يُدرج مباشرة لأن record_observation يختم بـ "الآن" دائماً
    old = (datetime.now() - timedelta(days=120)).isoformat()
    store._conn.execute(
        "INSERT INTO runs (job_id, job_name, observed_at, run_key, status) VALUES (?,?,?,?,?)",
        ("j1", "Job", old, "old-run", "ok"),
    )
    store._conn.commit()
    assert store.run_count("j1") == 2

    assert store.prune(retention_days=90) == 1
    assert store.run_count("j1") == 1


def test_prune_respects_retention_config(store, monkeypatch):
    monkeypatch.setattr(Config, "HISTORY_RETENTION_DAYS", 1)
    old = (datetime.now() - timedelta(days=3)).isoformat()
    store._conn.execute(
        "INSERT INTO runs (job_id, job_name, observed_at, run_key, status) VALUES (?,?,?,?,?)",
        ("j1", "Job", old, "old", "ok"),
    )
    store._conn.commit()
    assert store.prune() == 1


# ============================================================
# التدهور اللطيف
# ============================================================


def test_corrupt_database_degrades_gracefully(tmp_path, caplog):
    db = tmp_path / "history.db"
    db.write_bytes(b"this is definitely not a sqlite database")

    store = HistoryStore(db_path=db, enabled=True)

    assert store.available is False
    # كل عملية تصبح no-op بدل أن ترفع
    assert store.record_observation(make_job()) is False
    assert store.success_rate("j1") is None
    assert store.flakiness_score("j1") is None
    assert store.duration_trend("j1")["samples"] == 0
    assert store.prune() == 0
    assert store.job_ids() == []
    assert store.integrity_check() is False


def test_unopenable_path_degrades_gracefully(tmp_path):
    # مسار يشير إلى مجلد: sqlite لا يستطيع فتحه
    directory = tmp_path / "as_dir"
    directory.mkdir()
    store = HistoryStore(db_path=directory, enabled=True)
    assert store.available is False
    assert store.record_cycle([make_job()]) == 0


def test_disabled_store_never_touches_disk(tmp_path):
    db = tmp_path / "nope.db"
    store = HistoryStore(db_path=db, enabled=False)
    assert store.available is False
    assert not db.exists()


def test_mid_flight_corruption_disables_store(store):
    """خطأ SQLite أثناء العمل يُعطّل السجل ولا يُمرَّر للمستدعي"""
    store._conn.close()  # يُحاكي قاعدة صارت غير قابلة للاستخدام
    assert store.record_observation(make_job()) is False
    assert store.available is False


# ============================================================
# ذاكرة أحكام المصنّف
# ============================================================


def test_llm_cache_roundtrip_and_expiry(store):
    store.set_llm_verdict("abc", {"error_type": "network_error", "confidence": 0.9})
    assert store.get_llm_verdict("abc", 30)["error_type"] == "network_error"
    # مفتاح غير موجود
    assert store.get_llm_verdict("zzz", 30) is None

    # إدخال قديم يخرج من الصلاحية
    old = (datetime.now() - timedelta(days=40)).isoformat()
    store._conn.execute("UPDATE llm_cache SET created_at = ? WHERE hash = 'abc'", (old,))
    store._conn.commit()
    assert store.get_llm_verdict("abc", 30) is None
    assert store.prune_llm_cache(30) == 1

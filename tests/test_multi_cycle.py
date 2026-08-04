# -*- coding: utf-8 -*-
"""خصائص تسري عبر عدة دورات مراقبة متتالية.

كل الاختبارات الأخرى تفحص **دورة واحدة**، ولهذا مرّت ثغرة تكرار التنبيه بلا
كشف رغم تغطية عالية: لم يسأل أحد ماذا يحدث في الدورة الثانية والثالثة.
"""

from datetime import datetime, timedelta

import pytest
from conftest import FakeBackend, failing_job, make_job

from cronmaster.config import Config
from cronmaster.core import CronMaster


@pytest.fixture
def cycles(sandbox, monkeypatch):
    """يشغّل عدة دورات متتالية ويجمع الرسائل التي غادرت فعلاً إلى القنوات"""

    def run(jobs, count=5, **monitor_kwargs):
        master = CronMaster(backend=FakeBackend(jobs))
        sent = []
        monkeypatch.setattr(master.alerter, "dispatch", lambda message: sent.append(message) or True)
        results = [master.monitor(**monitor_kwargs) for _ in range(count)]
        return master, results, sent

    return run


# ============================================================
# تكرار التنبيه
# ============================================================


def test_futile_fix_escalation_respects_cooldown(cycles):
    """التصعيد بعد استنفاد رفعات المهلة يُرسل مرة واحدة لا في كل دورة"""
    job = failing_job(error="Operation timed out", consecutive_errors=9)
    master = CronMaster(backend=FakeBackend([job]))
    for _ in range(Config.MAX_TIMEOUT_FIXES):
        master.state.record_timeout_fix(job.id)
    master.state.save()

    _, results, sent = cycles([job], count=5)
    # الدورات الخمس تعيد بناء الحالة من نفس الملف، فالعدّاد محفوظ
    escalations = [m for m in sent if "المهلة لم يعد مجدياً" in m or "no longer helping" in m]
    assert len(escalations) <= 1, f"تكرر التصعيد {len(escalations)} مرة"
    assert all(r["fixes_applied"] == 0 for r in results)


def test_repeated_failure_alerts_once_within_cooldown(cycles):
    """نفس الفشل غير القابل للإصلاح لا يُنبَّه عنه في كل دورة"""
    job = failing_job(error="permission denied", consecutive_errors=4)
    _, _, sent = cycles([job], count=4)
    assert len(sent) == 1


def test_alert_returns_after_recovery(sandbox, monkeypatch):
    """التعافي يمسح سجل التنبيهات فيعود التنبيه فوراً إن عاد الفشل"""
    job = failing_job(error="permission denied", consecutive_errors=4)
    backend = FakeBackend([job])
    master = CronMaster(backend=backend)
    sent = []
    monkeypatch.setattr(master.alerter, "dispatch", lambda message: sent.append(message) or True)

    master.monitor()
    assert len(sent) == 1

    job.last_status = "ok"  # تعافت
    job.consecutive_errors = 0
    master.monitor()

    job.last_status = "error"  # فشلت من جديد
    job.consecutive_errors = 4
    master.monitor()
    assert len(sent) == 3  # فشل + تعافٍ + فشل جديد بلا انتظار التهدئة


# ============================================================
# العدّادات لا تنفلت
# ============================================================


def test_retry_counter_stops_at_max(cycles):
    """إعادة المحاولة تتوقف عند السقف مهما طالت الدورات"""
    job = failing_job(error="Connection refused", consecutive_errors=2)
    master, results, _ = cycles([job], count=6)
    assert sum(r["retries"] for r in results) == Config.MAX_RETRIES
    assert master.state.get_retry_count(job.id) == Config.MAX_RETRIES


def test_timeout_fixes_stop_at_guard(cycles):
    """رفع المهلة يتوقف عند حارس الإصلاح غير المجدي"""
    job = failing_job(error="Operation timed out", consecutive_errors=2, timeout_seconds=60)
    master, results, _ = cycles([job], count=8)
    assert sum(r["fixes_applied"] for r in results) == Config.MAX_TIMEOUT_FIXES
    assert master.state.get_timeout_fixes(job.id) == Config.MAX_TIMEOUT_FIXES


def test_state_file_does_not_grow_without_bound(cycles):
    """ملف الحالة لا ينمو بلا حد مع طول التشغيل"""
    job = failing_job(error="permission denied", consecutive_errors=4)
    master, _, _ = cycles([job], count=12)
    assert len(master.state.state["fixes_applied"]) <= 100
    assert len(master.state.state["alerts"]) <= 200
    assert len(master.state.state["queued_alerts"]) <= 50


# ============================================================
# السجل التاريخي
# ============================================================


def test_history_deduplicates_across_cycles(cycles):
    """دورات متكررة بين تشغيلين فعليين لا تُدرج صفوفاً مكررة"""
    run_at = datetime.now() - timedelta(hours=1)
    job = make_job(id="j1", last_status="ok", last_run_at=run_at)
    master, _, _ = cycles([job], count=5)
    assert master.history_store.run_count("j1") == 1

    job.last_run_at = datetime.now()  # تشغيل فعلي جديد
    master.monitor()
    assert master.history_store.run_count("j1") == 2


def test_dry_run_changes_nothing_across_cycles(sandbox, monkeypatch):
    """عدة دورات عرض فقط لا تُعدّل الواجهة الخلفية ولا الحالة ولا السجل"""
    backend = FakeBackend([failing_job(error="Operation timed out")])
    master = CronMaster(backend=backend)
    sent = []
    monkeypatch.setattr(master.alerter, "dispatch", lambda message: sent.append(message) or True)

    for _ in range(4):
        master.monitor(dry_run=True)

    assert backend.mutations() == []
    assert sent == []
    assert master.state.state["fixes_applied"] == []
    assert master.history_store.run_count("j1") == 0
    assert not Config.STATE_FILE.exists()

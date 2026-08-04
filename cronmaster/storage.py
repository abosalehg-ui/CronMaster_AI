# -*- coding: utf-8 -*-
"""التخزين: حالة المراقبة (JSON) والسجل التاريخي (SQLite)."""

import copy
import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .config import FILE_MODE, Config, _chmod_quiet, write_secure
from .models import Job

STATE_SCHEMA_VERSION = 2
HISTORY_SCHEMA_VERSION = 1


# ============================================================
# مدير الحالة
# ============================================================


class StateManager:
    """إدارة حالة المراقبة: سجل الإصلاحات، تهدئة التنبيهات، تتبع التعافي"""

    _DEFAULT_STATE: Dict[str, Any] = {
        "schema_version": STATE_SCHEMA_VERSION,
        "fixes_applied": [],
        "alerts": {},
        "failing_jobs": [],
        "retries": {},
        "last_run": None,
        # أُضيفت في الإصدار 3
        "queued_alerts": [],  # تنبيهات مؤجلة أثناء فترة الهدوء
        "timeout_fixes": {},  # كم مرة رُفعت مهلة كل مهمة (حارس الإصلاح غير المجدي)
        "pending_rollback": {},  # رفعات مهلة تنتظر إثبات جدواها
        "circuit_open": {},  # مهام عطّلها قاطع الدائرة
        "reschedules": {},  # عمليات إعادة جدولة مطبقة أو مقترحة
        "last_prune": None,  # آخر تنظيف للسجل التاريخي
    }

    def __init__(self, state_file: Optional[Path] = None):
        self.logger = logging.getLogger(__name__)
        self.state_file = state_file or Config.STATE_FILE
        self.state = self._load()

    def _load(self) -> dict:
        """تحميل الحالة، مع استكمال المفاتيح الناقصة من ملفات نسخ أقدم"""
        state = copy.deepcopy(self._DEFAULT_STATE)
        if self.state_file.exists():
            try:
                loaded = json.loads(self.state_file.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    state.update(loaded)
            except (OSError, json.JSONDecodeError) as e:
                self.logger.warning("ملف الحالة تالف أو غير مقروء (%s) — سيبدأ بحالة جديدة", e)
        return self._migrate(state)

    def _migrate(self, state: dict) -> dict:
        """ترقية صامتة للأمام: نُكمل ما ينقص ولا نحذف شيئاً من بيانات المستخدم"""
        version = state.get("schema_version")
        if version == STATE_SCHEMA_VERSION:
            return state

        for key, default in self._DEFAULT_STATE.items():
            state.setdefault(key, copy.deepcopy(default))

        if version is None:
            self.logger.info("ترقية ملف الحالة إلى الإصدار %s", STATE_SCHEMA_VERSION)
        state["schema_version"] = STATE_SCHEMA_VERSION
        return state

    def save(self):
        """حفظ الحالة بكتابة ذرّية: ملف مؤقت ثم استبدال —
        انقطاع منتصف الكتابة لا يترك ملفاً تالفاً"""
        self.state["last_run"] = datetime.now().isoformat()
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = self.state_file.with_suffix(".json.tmp")
        # الملف المؤقت يُخلق بأذوناته النهائية قبل أن يُكتب فيه شيء
        write_secure(tmp_file, json.dumps(self.state, indent=2, ensure_ascii=False))
        os.replace(tmp_file, self.state_file)

    def record_fix(self, job_id: str, fix_type: str, details: str):
        """تسجيل إصلاح"""
        self.state["fixes_applied"].append(
            {
                "job_id": job_id,
                "fix_type": fix_type,
                "details": details,
                "timestamp": datetime.now().isoformat(),
            }
        )
        # الاحتفاظ بآخر 100 إصلاح
        self.state["fixes_applied"] = self.state["fixes_applied"][-100:]
        # لا حفظ هنا: الحفظ يجري مرة واحدة في نهاية الدورة. الكتابة عند كل إصلاح
        # كانت تُسلسل الحالة كاملة وتستبدل الملف N مرة في الدورة الواحدة.

    # ---- تهدئة التنبيهات (dedup) ----

    def should_alert(self, job_id: str, kind: str) -> bool:
        """هل نرسل تنبيهاً؟ لا يُكرر نفس التنبيه (مهمة + نوع خطأ)
        قبل انقضاء ALERT_COOLDOWN_HOURS"""
        record = self.state.get("alerts", {}).get(f"{job_id}/{kind}")
        if not record:
            return True
        try:
            last = datetime.fromisoformat(record)
        except (ValueError, TypeError):
            return True
        return datetime.now() - last >= timedelta(hours=Config.ALERT_COOLDOWN_HOURS)

    def record_alert(self, job_id: str, kind: str):
        """تسجيل إرسال تنبيه"""
        alerts = self.state.setdefault("alerts", {})
        alerts[f"{job_id}/{kind}"] = datetime.now().isoformat()
        # حد أقصى لحجم السجل: إسقاط الأقدم
        if len(alerts) > 200:
            oldest = sorted(alerts, key=lambda k: alerts[k])[: len(alerts) - 200]
            for key in oldest:
                del alerts[key]

    def clear_alerts_for(self, job_id: str):
        """مسح سجل تنبيهات مهمة (عند تعافيها) حتى يُنبَّه فوراً إن فشلت مجدداً"""
        alerts = self.state.get("alerts", {})
        for key in [k for k in alerts if k.startswith(f"{job_id}/")]:
            del alerts[key]

    # ---- عدّاد إعادة المحاولة للأخطاء العابرة ----

    def get_retry_count(self, job_id: str) -> int:
        """كم مرة أُعيدت محاولة هذه المهمة تلقائياً منذ آخر نجاح"""
        return self.state.get("retries", {}).get(job_id, 0)

    def record_retry(self, job_id: str):
        """تسجيل إعادة محاولة (رفع العدّاد بواحد)"""
        retries = self.state.setdefault("retries", {})
        retries[job_id] = retries.get(job_id, 0) + 1

    def clear_retries_for(self, job_id: str):
        """تصفير عدّاد المحاولات (عند التعافي أو عند توقف المهمة عن الفشل)"""
        self.state.get("retries", {}).pop(job_id, None)

    def prune_retries(self, active_ids: List[str]):
        """إبقاء عدّادات المهام الفاشلة حالياً فقط — تنظيف بقايا مهام تعافت أو عُطّلت"""
        retries = self.state.get("retries", {})
        active = set(active_ids)
        for job_id in [k for k in retries if k not in active]:
            del retries[job_id]

    # ---- عدّاد رفعات المهلة (حارس الإصلاح غير المجدي) ----

    def get_timeout_fixes(self, job_id: str) -> int:
        return self.state.setdefault("timeout_fixes", {}).get(job_id, 0)

    def record_timeout_fix(self, job_id: str):
        fixes = self.state.setdefault("timeout_fixes", {})
        fixes[job_id] = fixes.get(job_id, 0) + 1

    def clear_timeout_fixes(self, job_id: str):
        self.state.setdefault("timeout_fixes", {}).pop(job_id, None)

    # ---- تتبع التراجع عن رفع المهلة ----

    def set_pending_rollback(self, job_id: str, previous_timeout: Optional[int]):
        self.state.setdefault("pending_rollback", {})[job_id] = {
            "previous": previous_timeout,
            "cycles": 0,
            "applied_at": datetime.now().isoformat(),
        }

    def get_pending_rollback(self, job_id: str) -> Optional[dict]:
        return self.state.setdefault("pending_rollback", {}).get(job_id)

    def bump_rollback_cycle(self, job_id: str) -> int:
        record = self.state.setdefault("pending_rollback", {}).get(job_id)
        if not record:
            return 0
        record["cycles"] = int(record.get("cycles", 0)) + 1
        return record["cycles"]

    def clear_pending_rollback(self, job_id: str):
        self.state.setdefault("pending_rollback", {}).pop(job_id, None)

    # ---- قاطع الدائرة ----

    def is_circuit_open(self, job_id: str) -> bool:
        return job_id in self.state.setdefault("circuit_open", {})

    def open_circuit(self, job_id: str, errors: int):
        self.state.setdefault("circuit_open", {})[job_id] = {
            "opened_at": datetime.now().isoformat(),
            "consecutive_errors": errors,
        }

    def close_circuit(self, job_id: str):
        self.state.setdefault("circuit_open", {}).pop(job_id, None)

    # ---- إعادة الجدولة ----

    def record_reschedule(self, job_id: str, old: str, new: str, applied: bool):
        self.state.setdefault("reschedules", {})[job_id] = {
            "from": old,
            "to": new,
            "applied": applied,
            "at": datetime.now().isoformat(),
        }

    def has_reschedule(self, job_id: str) -> bool:
        return job_id in self.state.setdefault("reschedules", {})

    # ---- تنبيهات فترة الهدوء ----

    def queue_alert(self, message: str):
        queue = self.state.setdefault("queued_alerts", [])
        queue.append({"message": message, "at": datetime.now().isoformat()})
        # سقف معقول حتى لا ينمو الملف بلا حدود لو طالت فترة الهدوء
        self.state["queued_alerts"] = queue[-50:]

    def take_queued_alerts(self) -> List[dict]:
        """سحب التنبيهات المؤجلة وتفريغ الطابور"""
        queue = self.state.setdefault("queued_alerts", [])
        self.state["queued_alerts"] = []
        return queue

    def peek_queued_alerts(self) -> List[dict]:
        return list(self.state.setdefault("queued_alerts", []))

    # ---- تنظيف السجل التاريخي (مرة يومياً كحد أقصى) ----

    def should_prune(self) -> bool:
        last = self.state.get("last_prune")
        if not last:
            return True
        try:
            return datetime.now() - datetime.fromisoformat(last) >= timedelta(days=1)
        except (ValueError, TypeError):
            return True

    def mark_pruned(self):
        self.state["last_prune"] = datetime.now().isoformat()

    # ---- تتبع التعافي ----

    def get_failing(self) -> List[str]:
        return self.state.get("failing_jobs", [])

    def set_failing(self, job_ids: List[str]):
        self.state["failing_jobs"] = sorted(job_ids)

    def get_history(self, limit: int = 20) -> Dict[str, Any]:
        """آخر الإصلاحات والتنبيهات المسجلة"""
        return {
            "fixes": self.state.get("fixes_applied", [])[-limit:],
            "alerts": self.state.get("alerts", {}),
            "retries": self.state.get("retries", {}),
            "last_run": self.state.get("last_run"),
            "circuit_open": self.state.get("circuit_open", {}),
            "reschedules": self.state.get("reschedules", {}),
            "queued_alerts": self.state.get("queued_alerts", []),
        }


# ============================================================
# السجل التاريخي (SQLite)
# ============================================================

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS runs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id             TEXT    NOT NULL,
    job_name           TEXT,
    observed_at        TEXT    NOT NULL,
    run_key            TEXT    NOT NULL,
    status             TEXT,
    error_type         TEXT,
    consecutive_errors INTEGER DEFAULT 0,
    duration_seconds   REAL,
    timeout_seconds    INTEGER
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_dedup   ON runs(job_id, run_key);
CREATE INDEX        IF NOT EXISTS idx_runs_job_obs ON runs(job_id, observed_at);

CREATE TABLE IF NOT EXISTS llm_cache (
    hash       TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class HistoryStore:
    """سجل تشغيلات المهام في SQLite.

    التدهور اللطيف مبدأ أساسي هنا: قاعدة تالفة أو غير قابلة للكتابة تُعطِّل
    السجل لهذه الدورة مع تحذير، ولا تُسقط ``monitor`` أبداً.
    """

    def __init__(self, db_path: Optional[Path] = None, enabled: Optional[bool] = None):
        self.logger = logging.getLogger(__name__)
        self.db_path = Path(db_path) if db_path else Config.history_db()
        self.enabled = Config.HISTORY_ENABLED if enabled is None else enabled
        self.available = False
        self._conn: Optional[sqlite3.Connection] = None
        if self.enabled:
            self._connect()

    # ------------------------------------------------------------
    # الاتصال والمخطط
    # ------------------------------------------------------------

    def _connect(self):
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            conn.executescript(_SCHEMA)
            row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            if row is None:
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (HISTORY_SCHEMA_VERSION,))
            conn.commit()
        except (sqlite3.Error, OSError) as e:
            self.logger.warning("تعذر فتح السجل التاريخي (%s) — سيُعطَّل لهذه الدورة", e)
            self.available = False
            self._conn = None
            return

        _chmod_quiet(self.db_path, FILE_MODE)
        self._conn = conn
        self.available = True

    def close(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None
            self.available = False

    def _disable(self, error: Exception):
        self.logger.warning("خطأ في السجل التاريخي (%s) — سيُعطَّل لهذه الدورة", error)
        self.close()

    # ------------------------------------------------------------
    # الكتابة
    # ------------------------------------------------------------

    @staticmethod
    def _run_key(job: Job) -> str:
        """مفتاح إزالة التكرار: (المهمة، وقت آخر تشغيل فعلي).

        دورات المراقبة المتكررة بين تشغيلين فعليين تتشارك نفس المفتاح فلا تُدرج صفوفاً مكررة.
        """
        return job.last_run_at.isoformat() if job.last_run_at else ""

    _INSERT_RUN = (
        "INSERT OR IGNORE INTO runs "
        "(job_id, job_name, observed_at, run_key, status, error_type, "
        " consecutive_errors, duration_seconds, timeout_seconds) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )

    def _insert_runs(self, items: List[Tuple[Job, Optional[str]]]) -> int:
        """إدراج الملاحظات في **معاملة واحدة**.

        كل ``commit`` في SQLite عملية fsync على القرص؛ الإدراج صفاً صفاً بـ commit
        لكل صف كان يعني عشرات المزامنات في الدورة الواحدة، والمطلوب واحدة.
        """
        if not self.available or self._conn is None or not items:
            return 0
        observed_at = datetime.now().isoformat()
        rows = [
            (
                job.id,
                job.name,
                observed_at,
                self._run_key(job),
                job.last_status,
                error_type,
                job.consecutive_errors,
                job.last_duration_seconds,
                job.timeout_seconds,
            )
            for job, error_type in items
        ]
        try:
            with self._conn:  # معاملة واحدة: commit عند النجاح وrollback عند الفشل
                cursor = self._conn.executemany(self._INSERT_RUN, rows)
            return max(0, cursor.rowcount or 0)
        except sqlite3.Error as e:
            self._disable(e)
            return 0

    def record_observation(self, job: Job, error_type: Optional[str] = None) -> bool:
        """تسجيل ملاحظة واحدة لمهمة. يعيد True إن أُدرج صف جديد."""
        return self._insert_runs([(job, error_type)]) > 0

    def record_cycle(self, jobs: List[Job], error_types: Optional[Dict[str, str]] = None) -> int:
        """تسجيل دورة مراقبة كاملة: صف لكل مهمة مفعّلة. يعيد عدد الصفوف الجديدة."""
        error_types = error_types or {}
        return self._insert_runs([(job, error_types.get(job.id)) for job in jobs if job.enabled])

    # ------------------------------------------------------------
    # الاستعلامات المشتقة
    # ------------------------------------------------------------

    #: أكبر نافذة تحتاجها المقاييس المشتقة من الصفوف الأخيرة (التذبذب 50، المدد 40)
    RECENT_WINDOW = 50

    def _rows(self, job_id: str, since: Optional[datetime] = None, limit: Optional[int] = None) -> List[sqlite3.Row]:
        """صفوف مهمة، الأحدث أولاً. تُنتقى الأعمدة المستخدمة فقط لا ``SELECT *``."""
        if not self.available or self._conn is None:
            return []
        query = (
            "SELECT job_id, job_name, observed_at, status, error_type, "
            "consecutive_errors, duration_seconds, timeout_seconds FROM runs WHERE job_id = ?"
        )
        params: List[Any] = [job_id]
        if since is not None:
            query += " AND observed_at >= ?"
            params.append(since.isoformat())
        query += " ORDER BY observed_at DESC"
        if limit:
            query += " LIMIT ?"
            params.append(limit)
        try:
            return list(self._conn.execute(query, params))
        except sqlite3.Error as e:
            self._disable(e)
            return []

    def _scalar_row(self, query: str, params: Iterable[Any]) -> Optional[sqlite3.Row]:
        """تنفيذ استعلام تجميعي يعيد صفاً واحداً، أو None عند التعطّل"""
        if not self.available or self._conn is None:
            return None
        try:
            return self._conn.execute(query, list(params)).fetchone()
        except sqlite3.Error as e:
            self._disable(e)
            return None

    def job_ids(self) -> List[str]:
        if not self.available or self._conn is None:
            return []
        try:
            return [r[0] for r in self._conn.execute("SELECT DISTINCT job_id FROM runs ORDER BY job_id")]
        except sqlite3.Error as e:
            self._disable(e)
            return []

    def run_count(self, job_id: str, since: Optional[datetime] = None) -> int:
        """عدد الملاحظات — يُحسب في SQL بلا تحميل الصفوف إلى الذاكرة"""
        query = "SELECT COUNT(*) AS n FROM runs WHERE job_id = ?"
        params: List[Any] = [job_id]
        if since is not None:
            query += " AND observed_at >= ?"
            params.append(since.isoformat())
        row = self._scalar_row(query, params)
        return int(row["n"]) if row else 0

    def success_rate(self, job_id: str, since: Optional[datetime] = None) -> Optional[float]:
        """نسبة التشغيلات الناجحة (0..1) بين التشغيلات ذات الحالة المعروفة"""
        query = (
            "SELECT COUNT(*) AS known, SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok "
            "FROM runs WHERE job_id = ? AND status IS NOT NULL"
        )
        params: List[Any] = [job_id]
        if since is not None:
            query += " AND observed_at >= ?"
            params.append(since.isoformat())
        row = self._scalar_row(query, params)
        if row is None or not row["known"]:
            return None
        return (row["ok"] or 0) / row["known"]

    @staticmethod
    def _flakiness_from(rows: Iterable[sqlite3.Row]) -> Optional[float]:
        known = [r for r in rows if r["status"]]
        if not known:
            return None
        return sum(1 for r in known if r["status"] == "error") / len(known)

    def flakiness_score(self, job_id: str, window: int = 50) -> Optional[float]:
        """نسبة التشغيلات الفاشلة ضمن آخر ``window`` ملاحظة (0..1)"""
        return self._flakiness_from(self._rows(job_id, limit=window))

    @staticmethod
    def _durations_from(rows: Iterable[sqlite3.Row], window: int = 40) -> List[float]:
        recent = list(rows)[:window]
        values = [r["duration_seconds"] for r in recent if r["duration_seconds"] is not None]
        return list(reversed(values))

    def durations(self, job_id: str, window: int = 40) -> List[float]:
        """مدد آخر التشغيلات، من الأقدم إلى الأحدث"""
        return self._durations_from(self._rows(job_id, limit=window), window)

    def duration_trend(self, job_id: str, window: int = 40) -> Dict[str, Any]:
        """مقارنة متوسط النصف الأحدث بمتوسط النصف الأقدم لكشف التدهور.

        يعيد قاموساً فيه المتوسطان والنسبة وعدد العينات — بما يكفي ليقرر
        المستدعي إن كان هناك تدهور يستحق تنبيهاً مبكراً.
        """
        return self._trend_from(self.durations(job_id, window))

    @staticmethod
    def _trend_from(values: List[float]) -> Dict[str, Any]:
        """حساب الاتجاه من مدد جاهزة — يفصل الحساب عن الاستعلام فيُعاد استخدامه"""
        result: Dict[str, Any] = {
            "samples": len(values),
            "baseline_mean": None,
            "recent_mean": None,
            "ratio": None,
            "overall_mean": None,
        }
        if not values:
            return result

        result["overall_mean"] = sum(values) / len(values)
        if len(values) < 4:
            return result

        split = len(values) // 2
        baseline = values[:split]
        recent = values[split:]
        baseline_mean = sum(baseline) / len(baseline)
        recent_mean = sum(recent) / len(recent)
        result["baseline_mean"] = baseline_mean
        result["recent_mean"] = recent_mean
        result["ratio"] = (recent_mean / baseline_mean) if baseline_mean > 0 else None
        return result

    @staticmethod
    def _mtbf_from(rows: Iterable[sqlite3.Row]) -> Optional[float]:
        stamps = []
        for row in rows:
            if row["status"] != "error":
                continue
            try:
                stamps.append(datetime.fromisoformat(row["observed_at"]))
            except (ValueError, TypeError):
                continue
        if len(stamps) < 2:
            return None
        stamps.sort()
        gaps = [(b - a).total_seconds() for a, b in zip(stamps, stamps[1:])]
        return sum(gaps) / len(gaps)

    def mean_time_between_failures(self, job_id: str, window: Optional[int] = None) -> Optional[float]:
        """متوسط الزمن بالثواني بين حالات الفشل ضمن آخر ``window`` ملاحظة.

        كانت تسحب كل صفوف المهمة بلا حد: مع احتفاظ 90 يوماً ودورة كل خمس دقائق
        يعني ذلك عشرات آلاف الصفوف تُقرأ وتُحوَّل في الذاكرة لحساب رقم واحد.
        """
        return self._mtbf_from(self._rows(job_id, limit=window or self.RECENT_WINDOW))

    def daily_success_rates(self, job_id: Optional[str] = None, days: int = 30) -> List[Dict[str, Any]]:
        """نسبة النجاح اليومية — تُستخدم في رسم اتجاه تقرير HTML"""
        if not self.available or self._conn is None:
            return []
        since = (datetime.now() - timedelta(days=days)).isoformat()
        query = (
            "SELECT substr(observed_at, 1, 10) AS day, "
            "SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok, "
            "COUNT(*) AS total "
            "FROM runs WHERE observed_at >= ? AND status IS NOT NULL "
        )
        params: List[Any] = [since]
        if job_id:
            query += "AND job_id = ? "
            params.append(job_id)
        query += "GROUP BY day ORDER BY day"
        try:
            rows = list(self._conn.execute(query, params))
        except sqlite3.Error as e:
            self._disable(e)
            return []
        return [{"day": r["day"], "rate": (r["ok"] / r["total"]) if r["total"] else 0.0, "total": r["total"]}
                for r in rows]

    def window_aggregates(self, days: int) -> Dict[str, Dict[str, Any]]:
        """عدد التشغيلات ونسبة النجاح لكل المهام في **استعلام تجميعي واحد**"""
        if not self.available or self._conn is None:
            return {}
        since = (datetime.now() - timedelta(days=days)).isoformat()
        try:
            rows = list(
                self._conn.execute(
                    "SELECT job_id, COUNT(*) AS runs, "
                    "SUM(CASE WHEN status IS NOT NULL THEN 1 ELSE 0 END) AS known, "
                    "SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok "
                    "FROM runs WHERE observed_at >= ? GROUP BY job_id",
                    (since,),
                )
            )
        except sqlite3.Error as e:
            self._disable(e)
            return {}
        return {
            r["job_id"]: {
                "runs": r["runs"] or 0,
                "success_rate": ((r["ok"] or 0) / r["known"]) if r["known"] else None,
            }
            for r in rows
        }

    def job_summaries(self, job_ids: Iterable[str], days: int) -> List[Dict[str, Any]]:
        """ملخصات جاهزة للعرض لعدة مهام دفعة واحدة.

        كانت كل مهمة تكلّف خمسة استعلامات مستقلة على الجدول نفسه (تجميع النافذة،
        نسبة النجاح، التذبذب، المدد، ومتوسط الزمن بين الأعطال)، فيصير ``stats``
        وتقرير HTML عمليتَي N+1. هنا: استعلام تجميعي واحد لكل المهام، ثم استعلام
        واحد **مسقوف** لكل مهمة يغذّي المقاييس المشتقة من الصفوف الأخيرة.
        """
        aggregates = self.window_aggregates(days)
        summaries = []
        for job_id in job_ids:
            recent = self._rows(job_id, limit=self.RECENT_WINDOW)
            trend = self._trend_from(self._durations_from(recent))
            window = aggregates.get(job_id, {})
            summaries.append(
                {
                    "job_id": job_id,
                    "job_name": recent[0]["job_name"] if recent else job_id,
                    "runs": window.get("runs", 0),
                    "success_rate": window.get("success_rate"),
                    "flakiness": self._flakiness_from(recent),
                    "avg_duration": trend["overall_mean"],
                    "duration_ratio": trend["ratio"],
                    "samples": trend["samples"],
                    "mtbf_seconds": self._mtbf_from(recent),
                }
            )
        return summaries

    def job_summary(self, job_id: str, days: int) -> Dict[str, Any]:
        """ملخص مهمة واحدة — غلاف رفيع حول ``job_summaries``"""
        return self.job_summaries([job_id], days)[0]

    # ------------------------------------------------------------
    # الصيانة
    # ------------------------------------------------------------

    def prune(self, retention_days: Optional[int] = None) -> int:
        """حذف الصفوف الأقدم من مدة الاحتفاظ. يعيد عدد الصفوف المحذوفة."""
        if not self.available or self._conn is None:
            return 0
        days = retention_days if retention_days is not None else Config.HISTORY_RETENTION_DAYS
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        try:
            cursor = self._conn.execute("DELETE FROM runs WHERE observed_at < ?", (cutoff,))
            self._conn.commit()
            return cursor.rowcount or 0
        except sqlite3.Error as e:
            self._disable(e)
            return 0

    def integrity_check(self) -> bool:
        """فحص سلامة قاعدة البيانات (يستخدمه أمر doctor)"""
        if not self.available or self._conn is None:
            return False
        try:
            row = self._conn.execute("PRAGMA integrity_check").fetchone()
        except sqlite3.Error:
            return False
        return bool(row) and row[0] == "ok"

    # ------------------------------------------------------------
    # ذاكرة أحكام المصنّف الذكي
    # ------------------------------------------------------------

    def get_llm_verdict(self, key: str, max_age_days: int) -> Optional[dict]:
        """حكم مخزَّن لم تنقضِ صلاحيته، أو None"""
        if not self.available or self._conn is None:
            return None
        cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
        try:
            row = self._conn.execute(
                "SELECT payload FROM llm_cache WHERE hash = ? AND created_at >= ?",
                (key, cutoff),
            ).fetchone()
        except sqlite3.Error as e:
            self._disable(e)
            return None
        if row is None:
            return None
        try:
            payload = json.loads(row["payload"])
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def set_llm_verdict(self, key: str, verdict: dict) -> None:
        if not self.available or self._conn is None:
            return
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO llm_cache (hash, payload, created_at) VALUES (?, ?, ?)",
                (key, json.dumps(verdict, ensure_ascii=False), datetime.now().isoformat()),
            )
            self._conn.commit()
        except sqlite3.Error as e:
            self._disable(e)

    def prune_llm_cache(self, max_age_days: Optional[int] = None) -> int:
        if not self.available or self._conn is None:
            return 0
        days = max_age_days if max_age_days is not None else Config.LLM_CACHE_DAYS
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        try:
            cursor = self._conn.execute("DELETE FROM llm_cache WHERE created_at < ?", (cutoff,))
            self._conn.commit()
            return cursor.rowcount or 0
        except sqlite3.Error as e:
            self._disable(e)
            return 0

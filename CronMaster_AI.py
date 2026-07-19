#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CronMaster_AI - مدير ومراقب ذكي للمهام المجدولة
==================================================

نظام متكامل لـ:
- مراقبة OpenClaw Cron Jobs واكتشاف الفشل لحظياً
- تحليل الأخطاء وتصنيفها
- الإصلاح الذاتي (زيادة timeout، إعادة التشغيل) مع نسخ احتياطي قبل أي تعديل
- التنبيهات الفورية عبر Telegram (مع عتبة وتهدئة لمنع التكرار)
- كشف المهام الصامتة وإشعارات التعافي
- التقارير وسجل الإصلاحات

المطور: Pipbot لـ عبدالكريم
التاريخ: 2026-04-13
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ============================================================
# الإعدادات والثوابت
# ============================================================


class Config:
    """إعدادات النظام المركزية.

    القيم الافتراضية أدناه، ويمكن تجاوزها من:
    1. ملف ~/.cronmaster/config.json (مفاتيح بأحرف صغيرة، مثل "alert_threshold")
    2. متغيرات البيئة CRONMASTER_<KEY> (لها الأولوية على الملف)
    """

    # مجلد العمل
    WORK_DIR = Path.home() / ".cronmaster"
    BACKUP_DIR = WORK_DIR / "backups"
    REPORTS_DIR = WORK_DIR / "reports"
    STATE_FILE = WORK_DIR / "state.json"
    CONFIG_FILE = WORK_DIR / "config.json"

    # إعدادات التنبيهات
    ALERT_THRESHOLD = 2  # عدد الفشل المتتالي قبل التنبيه
    ALERT_COOLDOWN_HOURS = 24  # لا يُكرر نفس التنبيه لنفس المهمة قبل مرور هذه المدة

    # إعدادات الإصلاح التلقائي
    AUTO_FIX_TIMEOUT = True  # زيادة timeout تلقائياً عند فشل timeout
    TIMEOUT_INCREMENT = 120  # زيادة 120 ثانية
    MAX_TIMEOUT = 900  # حد أقصى 15 دقيقة
    AUTO_RETRY = True  # إعادة التشغيل تلقائياً بعد الإصلاح

    # كشف المهام الصامتة: مهمة مفعّلة فات موعدها المجدول بأكثر من هذه المدة
    SILENT_GRACE_HOURS = 6

    # Telegram — يُضبط في config.json أو CRONMASTER_TELEGRAM_CHAT_ID
    TELEGRAM_CHAT_ID = ""

    _INT_KEYS = frozenset(
        {"ALERT_THRESHOLD", "ALERT_COOLDOWN_HOURS", "TIMEOUT_INCREMENT", "MAX_TIMEOUT", "SILENT_GRACE_HOURS"}
    )
    _BOOL_KEYS = frozenset({"AUTO_FIX_TIMEOUT", "AUTO_RETRY"})
    _STR_KEYS = frozenset({"TELEGRAM_CHAT_ID"})

    @classmethod
    def init_dirs(cls):
        """إنشاء المجلدات المطلوبة"""
        cls.WORK_DIR.mkdir(parents=True, exist_ok=True)
        cls.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        cls.REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    @classmethod
    def load(cls):
        """تحميل الإعدادات من الملف ثم من متغيرات البيئة"""
        logger = logging.getLogger(__name__)
        configurable = cls._INT_KEYS | cls._BOOL_KEYS | cls._STR_KEYS

        if cls.CONFIG_FILE.exists():
            try:
                data = json.loads(cls.CONFIG_FILE.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                logger.warning("تعذر قراءة %s: %s", cls.CONFIG_FILE, e)
                data = {}
            for key, value in data.items():
                attr = key.upper()
                if attr in configurable:
                    setattr(cls, attr, value)
                else:
                    logger.warning("مفتاح إعدادات غير معروف في config.json: %s", key)

        for attr in configurable:
            env_value = os.environ.get(f"CRONMASTER_{attr}")
            if env_value is None:
                continue
            if attr in cls._INT_KEYS:
                try:
                    setattr(cls, attr, int(env_value))
                except ValueError:
                    logger.warning("قيمة غير رقمية في CRONMASTER_%s: %s", attr, env_value)
            elif attr in cls._BOOL_KEYS:
                setattr(cls, attr, env_value.strip().lower() in ("1", "true", "yes"))
            else:
                setattr(cls, attr, env_value)


def setup_logging():
    """إعداد التسجيل مع تدوير للملف حتى لا ينمو بلا حدود"""
    log_file = Config.WORK_DIR / "cronmaster.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


# ============================================================
# واجهة OpenClaw CLI
# ============================================================


class OpenClawError(Exception):
    """فشل التواصل مع openclaw CLI نفسه (غير مثبت، مهلة، خرج بخطأ...)"""


def run_openclaw(*args: str, timeout: int = 30) -> "subprocess.CompletedProcess[str]":
    """تنفيذ أمر openclaw بمهلة موحدة.

    يرفع OpenClawError إذا تعذر تنفيذ الأمر نفسه — فشل المراقب يجب أن
    يكون حدثاً صريحاً، لا قائمة فارغة تُفسَّر على أنها "كل شيء سليم".
    """
    try:
        return subprocess.run(
            ["openclaw", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as e:
        raise OpenClawError("أمر openclaw غير موجود في PATH — هل هو مثبت؟") from e
    except subprocess.TimeoutExpired as e:
        raise OpenClawError(f"انتهت مهلة تنفيذ: openclaw {' '.join(args)}") from e


# ============================================================
# تصنيفات الأخطاء
# ============================================================


class ErrorType(Enum):
    """أنواع الأخطاء المعروفة"""

    TIMEOUT = "timeout"
    PERMISSION_DENIED = "permission_denied"
    NOT_FOUND = "not_found"
    DEPENDENCY_ERROR = "dependency_error"
    SYNTAX_ERROR = "syntax_error"
    NETWORK_ERROR = "network_error"
    DISK_FULL = "disk_full"
    MEMORY_ERROR = "memory_error"
    API_ERROR = "api_error"
    UNKNOWN = "unknown"


@dataclass
class ErrorSignature:
    """توقيع الخطأ للتعرف عليه"""

    pattern: str
    error_type: ErrorType
    description_ar: str
    suggested_fix: str
    auto_fixable: bool = False


# قاعدة بيانات الأخطاء المعروفة.
# الترتيب مهم: الأنماط الأكثر تحديداً أولاً، ونمط TIMEOUT العام أخيراً حتى لا
# تُصنَّف أخطاء مثل "Connection timed out" كمهلة قابلة للإصلاح فيُرفع
# timeout المهمة بلا جدوى.
ERROR_DATABASE = [
    ErrorSignature(
        pattern=r"permission denied|EACCES|Operation not permitted",
        error_type=ErrorType.PERMISSION_DENIED,
        description_ar="نقص في الصلاحيات",
        suggested_fix="تحقق من صلاحيات الملف أو شغّل بـ sudo",
    ),
    ErrorSignature(
        pattern=r"ModuleNotFoundError|ImportError|cannot find module",
        error_type=ErrorType.DEPENDENCY_ERROR,
        description_ar="مكتبة أو موديول ناقص",
        suggested_fix="ثبّت المكتبة باستخدام pip install",
    ),
    ErrorSignature(
        pattern=r"not found|No such file|ENOENT|command not found",
        error_type=ErrorType.NOT_FOUND,
        description_ar="ملف أو أمر غير موجود",
        suggested_fix="تحقق من المسار الكامل",
    ),
    ErrorSignature(
        pattern=r"SyntaxError|invalid syntax",
        error_type=ErrorType.SYNTAX_ERROR,
        description_ar="خطأ في صياغة الكود",
        suggested_fix="راجع السطر المذكور في رسالة الخطأ",
    ),
    ErrorSignature(
        pattern=r"MemoryError|Cannot allocate memory|out of memory|OOM",
        error_type=ErrorType.MEMORY_ERROR,
        description_ar="نفاد الذاكرة",
        suggested_fix="قلّل حجم المعالجة أو زد ذاكرة الجهاز",
    ),
    ErrorSignature(
        pattern=r"No space left|ENOSPC|disk full",
        error_type=ErrorType.DISK_FULL,
        description_ar="القرص ممتلئ",
        suggested_fix="احذف ملفات غير ضرورية",
    ),
    ErrorSignature(
        pattern=r"rate.?limit|\b429\b|too many requests",
        error_type=ErrorType.API_ERROR,
        description_ar="تجاوز حد الطلبات (Rate Limit)",
        suggested_fix="انتظر قبل المحاولة مجدداً",
    ),
    ErrorSignature(
        pattern=r"Connection refused|Connection timed out|ETIMEDOUT|Network unreachable|DNS|ENETUNREACH|getaddrinfo",
        error_type=ErrorType.NETWORK_ERROR,
        description_ar="مشكلة في الاتصال بالشبكة",
        suggested_fix="تحقق من الاتصال بالإنترنت",
    ),
    ErrorSignature(
        pattern=r"timeout|timed out|deadline exceeded",
        error_type=ErrorType.TIMEOUT,
        description_ar="انتهاء المهلة الزمنية - العملية أخذت وقت أطول من المسموح",
        suggested_fix="زيادة timeout",
        auto_fixable=True,
    ),
]


# ============================================================
# هياكل البيانات
# ============================================================


@dataclass
class OpenClawJob:
    """مهمة OpenClaw cron"""

    id: str
    name: str
    enabled: bool
    schedule: str
    last_status: Optional[str] = None
    last_error: Optional[str] = None
    last_error_reason: Optional[str] = None
    consecutive_errors: int = 0
    timeout_seconds: Optional[int] = None
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    raw: dict = field(default_factory=dict)  # التعريف الخام كما أرجعه openclaw، للنسخ الاحتياطي

    @property
    def is_failed(self) -> bool:
        """المعيار الموحد للفشل في كل النظام"""
        return self.enabled and self.last_status == "error"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "schedule": self.schedule,
            "last_status": self.last_status,
            "last_error": self.last_error,
            "last_error_reason": self.last_error_reason,
            "consecutive_errors": self.consecutive_errors,
            "timeout_seconds": self.timeout_seconds,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
        }


@dataclass
class FailureAnalysis:
    """تحليل فشل مهمة"""

    job: OpenClawJob
    error_type: ErrorType
    description: str
    suggested_fix: str
    auto_fixable: bool = False
    fix_applied: bool = False
    fix_details: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "job_id": self.job.id,
            "job_name": self.job.name,
            "error_type": self.error_type.value,
            "description": self.description,
            "suggested_fix": self.suggested_fix,
            "auto_fixable": self.auto_fixable,
            "fix_applied": self.fix_applied,
            "fix_details": self.fix_details,
        }


# ============================================================
# محلل OpenClaw Cron
# ============================================================


def _ms_to_datetime(ms: Any) -> Optional[datetime]:
    """تحويل timestamp بالميلي ثانية إلى datetime، أو None عند قيمة غير صالحة"""
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


class OpenClawCronParser:
    """قراءة وتحليل OpenClaw cron jobs"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def get_all_jobs(self) -> List[OpenClawJob]:
        """جلب جميع المهام. يرفع OpenClawError عند تعذر الجلب —
        القائمة الفارغة تعني حرفياً "لا توجد مهام"، لا "فشل الجلب"."""
        result = run_openclaw("cron", "list", "--json")

        if result.returncode != 0:
            raise OpenClawError(f"openclaw cron list فشل: {result.stderr.strip() or 'بدون تفاصيل'}")

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise OpenClawError(f"مخرجات openclaw ليست JSON صالحاً: {e}") from e

        # OpenClaw يرجع {"jobs": [...]} أو [...]
        jobs_data = data.get("jobs", data) if isinstance(data, dict) else data
        jobs = []

        for job in jobs_data:
            # استخراج timeout من payload
            timeout = None
            payload = job.get("payload", {})
            if isinstance(payload, dict):
                timeout = payload.get("timeoutSeconds")

            schedule = job.get("schedule", {})
            state = job.get("state", {})

            jobs.append(
                OpenClawJob(
                    id=job.get("id", ""),
                    name=job.get("name", "Unknown"),
                    enabled=job.get("enabled", False),
                    schedule=schedule.get("expr", "unknown"),
                    last_status=state.get("lastStatus"),
                    last_error=state.get("lastError"),
                    last_error_reason=state.get("lastErrorReason"),
                    consecutive_errors=state.get("consecutiveErrors", 0),
                    timeout_seconds=timeout,
                    last_run_at=_ms_to_datetime(state.get("lastRunAtMs")),
                    next_run_at=_ms_to_datetime(state.get("nextRunAtMs")),
                    raw=job,
                )
            )

        return jobs

    @staticmethod
    def filter_failed(jobs: List[OpenClawJob]) -> List[OpenClawJob]:
        """المهام الفاشلة فقط"""
        return [j for j in jobs if j.is_failed]

    @staticmethod
    def filter_critical(jobs: List[OpenClawJob], threshold: Optional[int] = None) -> List[OpenClawJob]:
        """المهام الحرجة (فشل متتالي)"""
        threshold = threshold if threshold is not None else Config.ALERT_THRESHOLD
        return [j for j in jobs if j.enabled and j.consecutive_errors >= threshold]

    @staticmethod
    def filter_silent(jobs: List[OpenClawJob], grace_hours: Optional[int] = None) -> List[OpenClawJob]:
        """المهام الصامتة: مفعّلة لكن موعد تشغيلها المجدول فات بمدة كبيرة —
        مؤشر على أن المجدول نفسه لا يشغّلها"""
        grace = grace_hours if grace_hours is not None else Config.SILENT_GRACE_HOURS
        cutoff = datetime.now() - timedelta(hours=grace)
        return [j for j in jobs if j.enabled and j.next_run_at and j.next_run_at < cutoff]


# ============================================================
# محلل الأخطاء
# ============================================================


class ErrorAnalyzer:
    """تحليل وتصنيف الأخطاء"""

    def analyze(self, job: OpenClawJob) -> FailureAnalysis:
        """تحليل سبب الفشل"""
        error_text = job.last_error or job.last_error_reason or ""

        # البحث عن نمط معروف
        for sig in ERROR_DATABASE:
            if re.search(sig.pattern, error_text, re.IGNORECASE):
                return FailureAnalysis(
                    job=job,
                    error_type=sig.error_type,
                    description=sig.description_ar,
                    suggested_fix=sig.suggested_fix,
                    auto_fixable=sig.auto_fixable,
                )

        # خطأ غير معروف
        return FailureAnalysis(
            job=job,
            error_type=ErrorType.UNKNOWN,
            description="خطأ غير مصنف",
            suggested_fix="راجع السجلات",
        )


# ============================================================
# مصلح الأخطاء التلقائي
# ============================================================


class AutoFixer:
    """إصلاح الأخطاء تلقائياً"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    @staticmethod
    def compute_new_timeout(current: Optional[int]) -> Tuple[int, int]:
        """حساب المهلة الجديدة: (الحالية، الجديدة بعد الزيادة والسقف)"""
        current = current or 30
        return current, min(current + Config.TIMEOUT_INCREMENT, Config.MAX_TIMEOUT)

    def fix(self, analysis: FailureAnalysis) -> FailureAnalysis:
        """محاولة إصلاح الخطأ تلقائياً"""
        if not analysis.auto_fixable:
            return analysis

        if analysis.error_type == ErrorType.TIMEOUT:
            return self._fix_timeout(analysis)

        return analysis

    def _backup_job(self, job: OpenClawJob):
        """نسخة احتياطية من تعريف المهمة الخام قبل أي تعديل عليها"""
        try:
            Config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            backup_file = Config.BACKUP_DIR / f"{job.id}_{datetime.now():%Y%m%d_%H%M%S}.json"
            backup_file.write_text(json.dumps(job.raw, indent=2, ensure_ascii=False), encoding="utf-8")
            self.logger.info(f"💾 نسخة احتياطية: {backup_file}")
        except OSError as e:
            self.logger.warning(f"تعذر حفظ النسخة الاحتياطية لـ {job.name}: {e}")

    def _fix_timeout(self, analysis: FailureAnalysis) -> FailureAnalysis:
        """إصلاح مشكلة Timeout بزيادة المهلة"""
        job = analysis.job
        current_timeout, new_timeout = self.compute_new_timeout(job.timeout_seconds)

        if new_timeout == current_timeout:
            analysis.fix_details = f"الـ timeout وصل الحد الأقصى ({Config.MAX_TIMEOUT}s)"
            return analysis

        self._backup_job(job)

        try:
            result = run_openclaw("cron", "edit", job.id, "--timeout-seconds", str(new_timeout))
        except OpenClawError as e:
            analysis.fix_details = f"تعذر تنفيذ الإصلاح: {e}"
            self.logger.error(analysis.fix_details)
            return analysis

        if result.returncode == 0:
            analysis.fix_applied = True
            analysis.fix_details = f"تم زيادة timeout من {current_timeout}s إلى {new_timeout}s"
            self.logger.info(f"✅ {job.name}: {analysis.fix_details}")
        else:
            analysis.fix_details = f"فشل تحديث timeout: {result.stderr.strip()}"
            self.logger.error(analysis.fix_details)

        return analysis

    def retry_job(self, job_id: str) -> bool:
        """إعادة تشغيل مهمة"""
        try:
            result = run_openclaw("cron", "run", job_id)
        except OpenClawError as e:
            self.logger.error(f"فشل إعادة التشغيل: {e}")
            return False
        return result.returncode == 0


# ============================================================
# نظام التنبيهات
# ============================================================


class AlertManager:
    """إدارة التنبيهات"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def send_telegram(self, message: str) -> bool:
        """إرسال تنبيه عبر OpenClaw message tool"""
        if not Config.TELEGRAM_CHAT_ID:
            self.logger.warning(
                "TELEGRAM_CHAT_ID غير مضبوط — لن يُرسل التنبيه. "
                "اضبطه في config.json أو متغير البيئة CRONMASTER_TELEGRAM_CHAT_ID"
            )
            return False

        try:
            result = run_openclaw(
                "message", "send",
                "--channel", "telegram",
                "--to", Config.TELEGRAM_CHAT_ID,
                "--message", message,
            )
        except OpenClawError as e:
            self.logger.error(f"خطأ في الإرسال: {e}")
            return False

        if result.returncode == 0:
            self.logger.info("تم إرسال التنبيه")
            return True
        self.logger.error(f"فشل الإرسال: {result.stderr.strip()}")
        return False

    def format_alert(
        self,
        analyses: List[FailureAnalysis],
        recovered: Optional[List[OpenClawJob]] = None,
        silent: Optional[List[OpenClawJob]] = None,
    ) -> str:
        """تنسيق رسالة التنبيه (نص عادي — بدون Markdown لتجنب مشاكل المحارف الخاصة)"""
        lines = ["🚨 تنبيه CronMaster", ""]

        for a in analyses:
            emoji = "✅" if a.fix_applied else "❌"
            lines.append(f"{emoji} {a.job.name}")
            lines.append(f"   نوع الخطأ: {a.error_type.value}")
            lines.append(f"   التحليل: {a.description}")

            if a.fix_applied:
                lines.append(f"   🔧 الإصلاح: {a.fix_details}")
            else:
                lines.append(f"   💡 الحل: {a.suggested_fix}")

            lines.append("")

        if recovered:
            lines.append("💚 مهام تعافت:")
            for j in recovered:
                lines.append(f"   ✅ {j.name}")
            lines.append("")

        if silent:
            lines.append("😴 مهام مفعّلة فات موعد تشغيلها (تحقق من المجدول):")
            for j in silent:
                missed = j.next_run_at.strftime("%Y-%m-%d %H:%M") if j.next_run_at else "?"
                lines.append(f"   ⏰ {j.name} (الموعد الفائت: {missed})")
            lines.append("")

        return "\n".join(lines).rstrip()


# ============================================================
# مولد التقارير
# ============================================================


class ReportGenerator:
    """توليد التقارير"""

    def __init__(self, reports_dir: Optional[Path] = None):
        self.reports_dir = reports_dir or Config.REPORTS_DIR
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def generate_report(
        self,
        jobs: List[OpenClawJob],
        analyses: List[FailureAnalysis],
        fmt: str = "markdown",
    ) -> Path:
        """توليد تقرير"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if fmt == "json":
            return self._generate_json(jobs, analyses, timestamp)
        return self._generate_markdown(jobs, analyses, timestamp)

    @staticmethod
    def _categorize(jobs: List[OpenClawJob]) -> Tuple[List[OpenClawJob], List[OpenClawJob], List[OpenClawJob]]:
        """تصنيف موحد: (ناجحة، فاشلة، أخرى) — نفس معيار is_failed في كل النظام"""
        ok = [j for j in jobs if j.last_status == "ok"]
        failed = [j for j in jobs if j.is_failed]
        other = [j for j in jobs if j.last_status != "ok" and not j.is_failed]
        return ok, failed, other

    def _generate_markdown(
        self,
        jobs: List[OpenClawJob],
        analyses: List[FailureAnalysis],
        timestamp: str,
    ) -> Path:
        """توليد تقرير Markdown"""
        report_file = self.reports_dir / f"report_{timestamp}.md"
        ok_jobs, failed_jobs, other_jobs = self._categorize(jobs)

        content = f"""# 📊 تقرير CronMaster

**التاريخ:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

---

## 📈 الإحصائيات

| المقياس | القيمة |
|---------|--------|
| إجمالي المهام | {len(jobs)} |
| ناجحة ✅ | {len(ok_jobs)} |
| فاشلة ❌ | {len(failed_jobs)} |
| أخرى ⏸️ | {len(other_jobs)} |

---

## ❌ المهام الفاشلة ({len(failed_jobs)})

"""

        if not failed_jobs:
            content += "_لا توجد مهام فاشلة 🎉_\n"
        else:
            for a in analyses:
                fix_status = "✅ تم الإصلاح" if a.fix_applied else "⏳ يحتاج مراجعة"
                content += f"""### {a.job.name}
- **الخطأ:** {a.error_type.value}
- **التحليل:** {a.description}
- **الحل:** {a.suggested_fix}
- **الحالة:** {fix_status}
{f"- **تفاصيل الإصلاح:** {a.fix_details}" if a.fix_details else ""}

"""

        content += f"""
---

## ✅ المهام الناجحة ({len(ok_jobs)})

"""
        for j in ok_jobs[:10]:
            content += f"- {j.name}\n"

        if len(ok_jobs) > 10:
            content += f"- _... و {len(ok_jobs) - 10} مهمة أخرى_\n"

        content += "\n---\n_تم التوليد بواسطة CronMaster_AI_\n"

        report_file.write_text(content, encoding="utf-8")
        return report_file

    def _generate_json(
        self,
        jobs: List[OpenClawJob],
        analyses: List[FailureAnalysis],
        timestamp: str,
    ) -> Path:
        """توليد تقرير JSON"""
        report_file = self.reports_dir / f"report_{timestamp}.json"
        ok_jobs, failed_jobs, other_jobs = self._categorize(jobs)

        data = {
            "timestamp": datetime.now().isoformat(),
            "summary": {
                "total": len(jobs),
                "ok": len(ok_jobs),
                "error": len(failed_jobs),
                "other": len(other_jobs),
            },
            "jobs": [j.to_dict() for j in jobs],
            "analyses": [a.to_dict() for a in analyses],
        }

        report_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return report_file


# ============================================================
# مدير الحالة
# ============================================================


class StateManager:
    """إدارة حالة المراقبة: سجل الإصلاحات، تهدئة التنبيهات، تتبع التعافي"""

    _DEFAULT_STATE = {"fixes_applied": [], "alerts": {}, "failing_jobs": [], "last_run": None}

    def __init__(self, state_file: Optional[Path] = None):
        self.logger = logging.getLogger(__name__)
        self.state_file = state_file or Config.STATE_FILE
        self.state = self._load()

    def _load(self) -> dict:
        """تحميل الحالة، مع استكمال المفاتيح الناقصة من ملفات نسخ أقدم"""
        state = dict(self._DEFAULT_STATE)
        state["fixes_applied"] = []
        state["alerts"] = {}
        state["failing_jobs"] = []
        if self.state_file.exists():
            try:
                loaded = json.loads(self.state_file.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    state.update(loaded)
            except (OSError, json.JSONDecodeError) as e:
                self.logger.warning(f"ملف الحالة تالف أو غير مقروء ({e}) — سيبدأ بحالة جديدة")
        return state

    def save(self):
        """حفظ الحالة بكتابة ذرّية: ملف مؤقت ثم استبدال —
        انقطاع منتصف الكتابة لا يترك ملفاً تالفاً"""
        self.state["last_run"] = datetime.now().isoformat()
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = self.state_file.with_suffix(".json.tmp")
        tmp_file.write_text(json.dumps(self.state, indent=2, ensure_ascii=False), encoding="utf-8")
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
        self.save()

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
            "last_run": self.state.get("last_run"),
        }


# ============================================================
# المحرك الرئيسي
# ============================================================


class CronMaster:
    """المحرك الرئيسي"""

    def __init__(self):
        Config.init_dirs()

        self.parser = OpenClawCronParser()
        self.analyzer = ErrorAnalyzer()
        self.fixer = AutoFixer()
        self.alerter = AlertManager()
        self.reporter = ReportGenerator()
        self.state = StateManager()
        self.logger = logging.getLogger(__name__)

    def monitor(
        self,
        auto_fix: bool = True,
        alert: bool = True,
        retry: bool = True,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """مراقبة وإصلاح المهام.

        dry_run: يعرض ما سيُفعل (إصلاحات وتنبيهات) دون تنفيذ أي تعديل أو إرسال.
        """
        self.logger.info("بدء المراقبة..." + (" [dry-run]" if dry_run else ""))

        try:
            all_jobs = self.parser.get_all_jobs()
        except OpenClawError as e:
            # فشل المراقب نفسه حدث حرج — يُبلَّغ عنه صراحة ولا يُقدَّم كنجاح
            self.logger.error(f"تعذر جلب المهام: {e}")
            if alert and not dry_run:
                self.alerter.send_telegram(f"🚨 CronMaster لا يستطيع قراءة مهام OpenClaw:\n{e}")
            return {"timestamp": datetime.now().isoformat(), "error": str(e)}

        failed_jobs = self.parser.filter_failed(all_jobs)
        silent_jobs = self.parser.filter_silent(all_jobs)

        analyses: List[FailureAnalysis] = []
        fixes_applied = 0
        retries = 0

        for job in failed_jobs:
            analysis = self.analyzer.analyze(job)

            if auto_fix and analysis.auto_fixable and Config.AUTO_FIX_TIMEOUT:
                if dry_run:
                    current, new = AutoFixer.compute_new_timeout(job.timeout_seconds)
                    if new != current:
                        analysis.fix_details = f"[dry-run] سيُرفع timeout من {current}s إلى {new}s"
                    else:
                        analysis.fix_details = f"[dry-run] الـ timeout بلغ الحد الأقصى ({Config.MAX_TIMEOUT}s)"
                else:
                    analysis = self.fixer.fix(analysis)
                    if analysis.fix_applied:
                        fixes_applied += 1
                        self.state.record_fix(job.id, analysis.error_type.value, analysis.fix_details or "")

                        # إعادة التشغيل بعد الإصلاح
                        if retry and Config.AUTO_RETRY and self.fixer.retry_job(job.id):
                            retries += 1
                            self.logger.info(f"🔄 أعيد تشغيل: {job.name}")

            analyses.append(analysis)

        # كشف التعافي: مهام كانت فاشلة في الدورة السابقة وأصبحت ناجحة
        prev_failing = set(self.state.get_failing())
        now_failing = {j.id for j in failed_jobs}
        recovered = [j for j in all_jobs if j.id in prev_failing - now_failing and j.last_status == "ok"]

        # اختيار ما يستحق التنبيه: إصلاح مطبق، أو فشل بلغ العتبة ولم يُنبَّه عنه مؤخراً
        alert_analyses = [
            a
            for a in analyses
            if a.fix_applied
            or (
                a.job.consecutive_errors >= Config.ALERT_THRESHOLD
                and self.state.should_alert(a.job.id, a.error_type.value)
            )
        ]
        alert_silent = [j for j in silent_jobs if self.state.should_alert(j.id, "silent")]

        alert_sent = False
        if alert and (alert_analyses or recovered or alert_silent):
            message = self.alerter.format_alert(alert_analyses, recovered, alert_silent)
            if dry_run:
                self.logger.info(f"[dry-run] تنبيه لن يُرسل:\n{message}")
            elif self.alerter.send_telegram(message):
                alert_sent = True
                for a in alert_analyses:
                    self.state.record_alert(a.job.id, a.error_type.value)
                for j in alert_silent:
                    self.state.record_alert(j.id, "silent")

        if not dry_run:
            for j in recovered:
                self.state.clear_alerts_for(j.id)
            self.state.set_failing(list(now_failing))
            self.state.save()

        result = {
            "timestamp": datetime.now().isoformat(),
            "dry_run": dry_run,
            "total_jobs": len(all_jobs),
            "failed_jobs": len(failed_jobs),
            "silent_jobs": [j.name for j in silent_jobs],
            "recovered_jobs": [j.name for j in recovered],
            "fixes_applied": fixes_applied,
            "retries": retries,
            "alert_sent": alert_sent,
            "analyses": [a.to_dict() for a in analyses],
        }

        self.logger.info(
            f"انتهت المراقبة: {len(failed_jobs)} فشل، {fixes_applied} إصلاح، "
            f"{retries} إعادة تشغيل، {len(recovered)} تعافٍ"
        )
        return result

    def status(self) -> Dict[str, Any]:
        """حالة سريعة. يرفع OpenClawError عند تعذر الجلب."""
        jobs = self.parser.get_all_jobs()

        # نسبة النجاح تُحسب على المهام المفعّلة التي نُفِّذت مرة على الأقل
        measurable = [j for j in jobs if j.enabled and j.last_status]
        ok = len([j for j in measurable if j.last_status == "ok"])
        error = len(self.parser.filter_failed(jobs))
        critical = len(self.parser.filter_critical(jobs))
        silent = len(self.parser.filter_silent(jobs))

        return {
            "total_jobs": len(jobs),
            "ok": ok,
            "error": error,
            "critical": critical,
            "silent": silent,
            "success_rate": (ok / len(measurable) * 100) if measurable else 100.0,
        }

    def report(self, fmt: str = "markdown") -> Path:
        """توليد تقرير. يرفع OpenClawError عند تعذر الجلب."""
        jobs = self.parser.get_all_jobs()
        failed = self.parser.filter_failed(jobs)
        analyses = [self.analyzer.analyze(j) for j in failed]

        return self.reporter.generate_report(jobs, analyses, fmt)

    def list_jobs(self) -> List[Dict]:
        """قائمة المهام. يرفع OpenClawError عند تعذر الجلب."""
        return [j.to_dict() for j in self.parser.get_all_jobs()]

    def fix_job(self, job_id: str, retry: bool = True) -> Dict[str, Any]:
        """إصلاح مهمة محددة. يرفع OpenClawError عند تعذر الجلب."""
        jobs = self.parser.get_all_jobs()
        job = next((j for j in jobs if j.id == job_id), None)

        if not job:
            return {"error": "المهمة غير موجودة"}

        analysis = self.analyzer.analyze(job)

        if analysis.auto_fixable:
            analysis = self.fixer.fix(analysis)

            if analysis.fix_applied:
                self.state.record_fix(job.id, analysis.error_type.value, analysis.fix_details or "")
                if retry:
                    self.fixer.retry_job(job_id)

        return analysis.to_dict()

    def history(self, limit: int = 20) -> Dict[str, Any]:
        """سجل الإصلاحات والتنبيهات"""
        return self.state.get_history(limit)


# ============================================================
# واجهة سطر الأوامر
# ============================================================


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CronMaster_AI - مدير ذكي لـ OpenClaw Cron Jobs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
أمثلة:
  python CronMaster_AI.py monitor            # مراقبة وإصلاح تلقائي
  python CronMaster_AI.py monitor --dry-run  # عرض ما سيحدث دون تنفيذ
  python CronMaster_AI.py monitor --no-fix   # مراقبة بدون إصلاح
  python CronMaster_AI.py status             # حالة سريعة
  python CronMaster_AI.py report             # توليد تقرير
  python CronMaster_AI.py list               # قائمة المهام
  python CronMaster_AI.py fix <job_id>       # إصلاح مهمة محددة
  python CronMaster_AI.py history            # سجل الإصلاحات
""",
    )

    subparsers = parser.add_subparsers(dest="command", help="الأوامر")

    # monitor
    mon = subparsers.add_parser("monitor", help="مراقبة وإصلاح")
    mon.add_argument("--no-fix", action="store_true", help="بدون إصلاح تلقائي")
    mon.add_argument("--no-alert", action="store_true", help="بدون تنبيهات")
    mon.add_argument("--no-retry", action="store_true", help="بدون إعادة تشغيل")
    mon.add_argument("--dry-run", action="store_true", help="عرض ما سيحدث دون أي تنفيذ")

    # status
    subparsers.add_parser("status", help="حالة سريعة")

    # report
    rep = subparsers.add_parser("report", help="توليد تقرير")
    rep.add_argument("--format", "-f", dest="fmt", choices=["markdown", "json"], default="markdown")

    # list
    subparsers.add_parser("list", help="قائمة المهام")

    # fix
    fix = subparsers.add_parser("fix", help="إصلاح مهمة")
    fix.add_argument("job_id", help="معرف المهمة")
    fix.add_argument("--no-retry", action="store_true", help="بدون إعادة تشغيل")

    # history
    hist = subparsers.add_parser("history", help="سجل الإصلاحات والتنبيهات")
    hist.add_argument("--limit", type=int, default=20, help="عدد السجلات (افتراضي 20)")

    return parser


def main():
    args = build_arg_parser().parse_args()

    if not args.command:
        build_arg_parser().print_help()
        return

    Config.init_dirs()
    setup_logging()
    Config.load()

    master = CronMaster()

    try:
        if args.command == "monitor":
            result = master.monitor(
                auto_fix=not args.no_fix,
                alert=not args.no_alert,
                retry=not args.no_retry,
                dry_run=args.dry_run,
            )
            print(json.dumps(result, indent=2, ensure_ascii=False))
            if result.get("error"):
                sys.exit(1)

        elif args.command == "status":
            result = master.status()
            print("=" * 40)
            print("📊 حالة OpenClaw Cron Jobs")
            print("=" * 40)
            print(f"إجمالي المهام:   {result['total_jobs']}")
            print(f"ناجحة ✅:        {result['ok']}")
            print(f"فاشلة ❌:        {result['error']}")
            print(f"حرجة ⚠️:         {result['critical']}")
            print(f"صامتة 😴:        {result['silent']}")
            print(f"نسبة النجاح:     {result['success_rate']:.1f}%")
            print("=" * 40)

        elif args.command == "report":
            path = master.report(fmt=args.fmt)
            print(f"✅ التقرير: {path}")

        elif args.command == "list":
            print(json.dumps(master.list_jobs(), indent=2, ensure_ascii=False))

        elif args.command == "fix":
            result = master.fix_job(args.job_id, retry=not args.no_retry)
            print(json.dumps(result, indent=2, ensure_ascii=False))

        elif args.command == "history":
            print(json.dumps(master.history(limit=args.limit), indent=2, ensure_ascii=False))

    except OpenClawError as e:
        print(f"❌ تعذر التواصل مع OpenClaw: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

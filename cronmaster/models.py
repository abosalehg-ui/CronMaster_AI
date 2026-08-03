# -*- coding: utf-8 -*-
"""هياكل البيانات المشتركة: المهمة، نوع الخطأ، وتحليل الفشل."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


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
class Job:
    """مهمة مجدولة كما تراها الواجهة الخلفية.

    كان اسمها ``OpenClawJob``؛ الاسم القديم ما يزال مُصدَّراً كاسم مهجور.
    """

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
    last_duration_seconds: Optional[float] = None
    raw: dict = field(default_factory=dict)  # التعريف الخام كما أرجعته الواجهة، للنسخ الاحتياطي

    @property
    def is_failed(self) -> bool:
        """المعيار الموحد للفشل في كل النظام"""
        return self.enabled and self.last_status == "error"

    @property
    def error_text(self) -> str:
        """نص الخطأ المعتمد في التصنيف"""
        return self.last_error or self.last_error_reason or ""

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
            "duration_seconds": self.last_duration_seconds,
        }


@dataclass
class FailureAnalysis:
    """تحليل فشل مهمة"""

    job: Job
    error_type: ErrorType
    description: str
    suggested_fix: str
    auto_fixable: bool = False
    fix_applied: bool = False
    fix_details: Optional[str] = None
    source: str = "regex"  # regex | llm | llm-cache — من أين جاء التشخيص
    confidence: Optional[float] = None
    is_transient: Optional[bool] = None

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


def ms_to_datetime(ms: Any) -> Optional[datetime]:
    """تحويل timestamp بالميلي ثانية إلى datetime، أو None عند قيمة غير صالحة"""
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000)
    except (TypeError, ValueError, OSError, OverflowError):
        return None

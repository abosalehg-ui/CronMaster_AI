# -*- coding: utf-8 -*-
"""CronMaster_AI — مدير ومراقب ذكي للمهام المجدولة.

الحزمة مقسّمة إلى طبقات: الإعدادات، الواجهات الخلفية، التحليل، الإصلاح،
التخزين، التنبيهات، التقارير، المقاييس، ثم المنسّق وواجهة سطر الأوامر.
"""

__version__ = "3.1.0"

from .config import Config, setup_logging
from .core import (
    EXIT_FIXES_APPLIED,
    EXIT_JOBS_FAILING,
    EXIT_MONITOR_FAILURE,
    EXIT_OK,
    CronMaster,
)
from .models import ErrorType, FailureAnalysis, Job

__all__ = [
    "Config",
    "CronMaster",
    "ErrorType",
    "EXIT_FIXES_APPLIED",
    "EXIT_JOBS_FAILING",
    "EXIT_MONITOR_FAILURE",
    "EXIT_OK",
    "FailureAnalysis",
    "Job",
    "__version__",
    "setup_logging",
]

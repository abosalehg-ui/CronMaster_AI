#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CronMaster_AI - مدير ومراقب ذكي للمهام المجدولة
==================================================

هذا الملف أصبح غلافاً رفيعاً للحزمة ``cronmaster`` بعد تقسيم الكود إلى وحدات.
يبقى موجوداً حتى تستمر مداخل cron القديمة (``python3 CronMaster_AI.py monitor``)
والاستيرادات القديمة (``import CronMaster_AI as cm``) في العمل دون أي تعديل.

الأسماء القديمة المخصّصة لمزوّد واحد (``OpenClawJob``، ``OpenClawCronParser``)
ما تزال مُصدَّرة كأسماء مهجورة تشير إلى الأسماء الجديدة العامة.

المطور: Pipbot لـ عبدالكريم
التاريخ: 2026-04-13
"""

import json  # noqa: F401 — مُصدَّر للتوافق مع كود قديم يستورده من هنا
import logging  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import subprocess  # noqa: F401 — الاختبارات ترقّع subprocess.run عبر هذا الاسم
import sys
from datetime import datetime, timedelta  # noqa: F401
from pathlib import Path  # noqa: F401
from types import ModuleType

from cronmaster import __version__
from cronmaster.analysis import ERROR_DATABASE, ErrorAnalyzer, ErrorSignature
from cronmaster.backends import openclaw as _openclaw_module
from cronmaster.backends.base import BackendError, Capability, CronBackend
from cronmaster.backends.crontab import CrontabError, SystemCrontabBackend
from cronmaster.backends.openclaw import (
    OpenClawBackend,
    OpenClawCronParser,
    OpenClawError,
    run_openclaw,
)
from cronmaster.cli import build_arg_parser, main
from cronmaster.config import Config, setup_logging
from cronmaster.core import CronMaster
from cronmaster.fixers import AutoFixer
from cronmaster.lock import ExecutionLock
from cronmaster.metrics import ping_healthcheck, write_prometheus_textfile
from cronmaster.models import ErrorType, FailureAnalysis, Job
from cronmaster.models import ms_to_datetime as _ms_to_datetime  # noqa: F401
from cronmaster.notifiers import AlertManager, TelegramNotifier
from cronmaster.reporting import ReportGenerator
from cronmaster.storage import HistoryStore, StateManager

# ============================================================
# أسماء مهجورة (deprecated aliases)
# ============================================================

#: الاسم القديم لـ :class:`cronmaster.models.Job`
OpenClawJob = Job

# ============================================================
# تمرير الترقيع إلى الوحدات الأصلية
# ============================================================

# الأسماء المُعاد تصديرها هنا هي مجرد مراجع؛ لو رقّع أحدهم ``CronMaster_AI.X``
# فلن يلاحظ ذلك الكود داخل الحزمة. الوحدة أدناه تمرّر أي إسناد إلى موطنه
# الأصلي، فيبقى ترقيع الاختبارات والكود القديم عاملاً كما كان في الملف الواحد.
_ORIGINS = {
    "run_openclaw": _openclaw_module,
}


class _CompatModule(ModuleType):
    """وحدة توافق تمرّر الإسناد إلى الوحدة التي يعيش فيها الاسم فعلاً."""

    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        origin = _ORIGINS.get(name)
        if origin is not None:
            setattr(origin, name, value)


sys.modules[__name__].__class__ = _CompatModule


__all__ = [
    "ERROR_DATABASE",
    "AlertManager",
    "AutoFixer",
    "BackendError",
    "Capability",
    "Config",
    "CronBackend",
    "CronMaster",
    "CrontabError",
    "ErrorAnalyzer",
    "ErrorSignature",
    "ErrorType",
    "ExecutionLock",
    "FailureAnalysis",
    "HistoryStore",
    "Job",
    "OpenClawBackend",
    "OpenClawCronParser",
    "OpenClawError",
    "OpenClawJob",
    "ReportGenerator",
    "StateManager",
    "SystemCrontabBackend",
    "TelegramNotifier",
    "__version__",
    "build_arg_parser",
    "main",
    "ping_healthcheck",
    "run_openclaw",
    "setup_logging",
    "write_prometheus_textfile",
]


if __name__ == "__main__":
    main()

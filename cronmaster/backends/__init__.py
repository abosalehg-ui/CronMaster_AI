# -*- coding: utf-8 -*-
"""اختيار الواجهة الخلفية."""

import logging
from typing import Dict, Type

from ..config import Config
from .base import BackendError, Capability, CronBackend
from .crontab import CrontabError, SystemCrontabBackend
from .openclaw import OpenClawBackend, OpenClawCronParser, OpenClawError, run_openclaw

BACKENDS: Dict[str, Type[CronBackend]] = {
    "openclaw": OpenClawBackend,
    "crontab": SystemCrontabBackend,
}

DEFAULT_BACKEND = "openclaw"


def get_backend(name: str = None) -> CronBackend:
    """بناء الواجهة الخلفية المطلوبة. الاسم المجهول يسقط على الافتراضية مع تحذير."""
    name = (name or Config.BACKEND or DEFAULT_BACKEND).strip().lower()
    backend_cls = BACKENDS.get(name)
    if backend_cls is None:
        logging.getLogger(__name__).warning(
            "واجهة خلفية غير معروفة: %s — سيُستخدم %s (المتاح: %s)",
            name,
            DEFAULT_BACKEND,
            ", ".join(sorted(BACKENDS)),
        )
        backend_cls = BACKENDS[DEFAULT_BACKEND]
    return backend_cls()


__all__ = [
    "BACKENDS",
    "BackendError",
    "Capability",
    "CronBackend",
    "CrontabError",
    "OpenClawBackend",
    "OpenClawCronParser",
    "OpenClawError",
    "SystemCrontabBackend",
    "get_backend",
    "run_openclaw",
]

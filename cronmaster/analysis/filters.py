# -*- coding: utf-8 -*-
"""ترشيح المهام حسب حالتها — منطق مستقل عن أي واجهة خلفية."""

from datetime import datetime, timedelta
from typing import List, Optional

from ..config import Config
from ..models import Job


def filter_failed(jobs: List[Job]) -> List[Job]:
    """المهام الفاشلة فقط"""
    return [j for j in jobs if j.is_failed]


def filter_critical(jobs: List[Job], threshold: Optional[int] = None) -> List[Job]:
    """المهام الحرجة (فشل متتالي)"""
    threshold = threshold if threshold is not None else Config.ALERT_THRESHOLD
    return [j for j in jobs if j.enabled and j.consecutive_errors >= threshold]


def filter_silent(jobs: List[Job], grace_hours: Optional[int] = None) -> List[Job]:
    """المهام الصامتة: مفعّلة لكن موعد تشغيلها المجدول فات بمدة كبيرة —
    مؤشر على أن المجدول نفسه لا يشغّلها"""
    grace = grace_hours if grace_hours is not None else Config.SILENT_GRACE_HOURS
    cutoff = datetime.now() - timedelta(hours=grace)
    return [j for j in jobs if j.enabled and j.next_run_at and j.next_run_at < cutoff]

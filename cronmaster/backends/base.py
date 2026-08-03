# -*- coding: utf-8 -*-
"""عقد الواجهة الخلفية لمصدر المهام المجدولة."""

import logging
from typing import List, Optional, Set

from ..analysis.filters import filter_critical, filter_failed, filter_silent
from ..models import Job


class BackendError(Exception):
    """فشل التواصل مع الواجهة الخلفية نفسها (غير مثبتة، مهلة، خرجت بخطأ...)"""


class Capability:
    """أسماء العمليات التي قد تدعمها واجهة خلفية.

    المنسّق يسأل ``supports()`` قبل أي عملية بدل أن يفترض، فواجهة محدودة
    (crontab مثلاً) تُنتج رسالة "غير مدعوم" واضحة لا انهياراً.
    """

    LIST = "list"
    SET_TIMEOUT = "set_timeout"
    RUN = "run"
    SET_ENABLED = "set_enabled"
    SET_SCHEDULE = "set_schedule"
    LAST_STATUS = "last_status"  # هل تعرف الواجهة حالة آخر تشغيل؟
    DURATION = "duration"  # هل تعرف مدة آخر تشغيل؟


class CronBackend:
    """الواجهة الأساسية. ترث منها كل الواجهات الفعلية."""

    name = "base"
    capabilities: Set[str] = set()
    error_type = BackendError

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__module__)

    # ---- القراءة ----

    def list_jobs(self) -> List[Job]:
        """جلب جميع المهام. ترفع BackendError عند تعذر الجلب —
        القائمة الفارغة تعني حرفياً "لا توجد مهام"، لا "فشل الجلب"."""
        raise NotImplementedError

    def get_all_jobs(self) -> List[Job]:
        """اسم مهجور لـ list_jobs، محفوظ لتوافق الكود القديم"""
        return self.list_jobs()

    # ---- التعديل ----

    def set_timeout(self, job_id: str, seconds: int) -> bool:
        raise NotImplementedError

    def run_job(self, job_id: str) -> bool:
        raise NotImplementedError

    def set_enabled(self, job_id: str, enabled: bool) -> bool:
        raise NotImplementedError

    def set_schedule(self, job_id: str, expr: str) -> bool:
        raise NotImplementedError

    # ---- مساعدات ----

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def ping(self) -> bool:
        """هل الواجهة الخلفية حاضرة وتستجيب؟ (يستخدمه أمر doctor)"""
        try:
            self.list_jobs()
        except BackendError:
            return False
        return True

    # ---- مرشِّحات ثابتة، محفوظة هنا لتوافق الاسم القديم OpenClawCronParser ----

    @staticmethod
    def filter_failed(jobs: List[Job]) -> List[Job]:
        """المهام الفاشلة فقط"""
        return filter_failed(jobs)

    @staticmethod
    def filter_critical(jobs: List[Job], threshold: Optional[int] = None) -> List[Job]:
        """المهام الحرجة (فشل متتالي)"""
        return filter_critical(jobs, threshold)

    @staticmethod
    def filter_silent(jobs: List[Job], grace_hours: Optional[int] = None) -> List[Job]:
        """المهام الصامتة: مفعّلة لكن موعد تشغيلها المجدول فات بمدة كبيرة"""
        return filter_silent(jobs, grace_hours)

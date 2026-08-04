# -*- coding: utf-8 -*-
"""تحليل وتصنيف أخطاء المهام."""

import logging
import re
from typing import Optional

from ..i18n import t
from ..models import ErrorType, FailureAnalysis, Job
from .errors_db import ERROR_DATABASE


class ErrorAnalyzer:
    """تحليل وتصنيف الأخطاء.

    المصنّف النمطي (regex) يعمل دائماً أولاً. المصنّف الاختياري بالذكاء
    الاصطناعي — إن مُرِّر — لا يُستشار إلا حين تكون النتيجة ``UNKNOWN``،
    ولا يملك صلاحية الإذن بأي إصلاح تلقائي بنفسه.
    """

    def __init__(self, llm_classifier=None):
        self.logger = logging.getLogger(__name__)
        self.llm = llm_classifier

    def analyze(self, job: Job, use_llm: bool = True) -> FailureAnalysis:
        """تحليل سبب الفشل"""
        analysis = self.analyze_regex(job)

        if analysis.error_type is ErrorType.UNKNOWN and use_llm and self.llm is not None:
            enriched = self.llm.classify(job, analysis)
            if enriched is not None:
                return enriched

        return analysis

    @staticmethod
    def analyze_regex(job: Job) -> FailureAnalysis:
        """التصنيف النمطي البحت — لا شبكة ولا اعتماديات"""
        error_text = job.error_text

        for sig in ERROR_DATABASE:
            if re.search(sig.pattern, error_text, re.IGNORECASE):
                return FailureAnalysis(
                    job=job,
                    error_type=sig.error_type,
                    description=sig.description,
                    suggested_fix=sig.suggested_fix,
                    auto_fixable=sig.auto_fixable,
                )

        # خطأ غير معروف
        return FailureAnalysis(
            job=job,
            error_type=ErrorType.UNKNOWN,
            description=t("error.unknown.desc"),
            suggested_fix=t("error.unknown.fix"),
        )


def error_type_from_value(value: Optional[str]) -> ErrorType:
    """تحويل نص إلى ErrorType مع سقوط آمن إلى UNKNOWN"""
    if not value:
        return ErrorType.UNKNOWN
    try:
        return ErrorType(value)
    except ValueError:
        return ErrorType.UNKNOWN

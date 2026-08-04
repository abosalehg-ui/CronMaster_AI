# -*- coding: utf-8 -*-
"""قاعدة أنماط الأخطاء المعروفة.

التوقيع يحمل **مفتاح** رسالة لا نصاً: الوصف والحل يُجلبان من كتالوج ``i18n``
وقت التحليل، فيتبعان لغة المخرجات بدل أن يبقيا عربيين في واجهة إنجليزية.
"""

from dataclasses import dataclass

from ..i18n import t
from ..models import ErrorType


@dataclass
class ErrorSignature:
    """توقيع الخطأ للتعرف عليه"""

    pattern: str
    error_type: ErrorType
    auto_fixable: bool = False

    @property
    def message_key(self) -> str:
        """جذر مفاتيح الرسائل المشتق من نوع الخطأ"""
        return f"error.{self.error_type.value}"

    @property
    def description(self) -> str:
        """وصف الخطأ باللغة الفعّالة"""
        return t(f"{self.message_key}.desc")

    @property
    def suggested_fix(self) -> str:
        """الحل المقترح باللغة الفعّالة"""
        return t(f"{self.message_key}.fix")


# قاعدة بيانات الأخطاء المعروفة.
# الترتيب مهم: الأنماط الأكثر تحديداً أولاً، ونمط TIMEOUT العام أخيراً حتى لا
# تُصنَّف أخطاء مثل "Connection timed out" كمهلة قابلة للإصلاح فيُرفع
# timeout المهمة بلا جدوى.
ERROR_DATABASE = [
    ErrorSignature(
        pattern=r"permission denied|EACCES|Operation not permitted",
        error_type=ErrorType.PERMISSION_DENIED,
    ),
    ErrorSignature(
        pattern=r"ModuleNotFoundError|ImportError|cannot find module",
        error_type=ErrorType.DEPENDENCY_ERROR,
    ),
    ErrorSignature(
        pattern=r"not found|No such file|ENOENT|command not found",
        error_type=ErrorType.NOT_FOUND,
    ),
    ErrorSignature(
        pattern=r"SyntaxError|invalid syntax",
        error_type=ErrorType.SYNTAX_ERROR,
    ),
    ErrorSignature(
        pattern=r"MemoryError|Cannot allocate memory|out of memory|\bOOM\b",
        error_type=ErrorType.MEMORY_ERROR,
    ),
    ErrorSignature(
        pattern=r"No space left|ENOSPC|disk full",
        error_type=ErrorType.DISK_FULL,
    ),
    ErrorSignature(
        pattern=r"rate.?limit|\b429\b|too many requests",
        error_type=ErrorType.API_ERROR,
        auto_fixable=True,
    ),
    ErrorSignature(
        pattern=(
            r"Connection refused|Connection timed out|ETIMEDOUT|Network unreachable|"
            r"\bDNS\b|ENETUNREACH|getaddrinfo"
        ),
        error_type=ErrorType.NETWORK_ERROR,
        auto_fixable=True,
    ),
    ErrorSignature(
        pattern=r"timeout|timed out|deadline exceeded",
        error_type=ErrorType.TIMEOUT,
        auto_fixable=True,
    ),
]

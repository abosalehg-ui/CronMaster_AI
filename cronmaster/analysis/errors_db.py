# -*- coding: utf-8 -*-
"""قاعدة أنماط الأخطاء المعروفة."""

from dataclasses import dataclass

from ..models import ErrorType


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
        suggested_fix="إعادة المحاولة تلقائياً بعد فترة تهدئة",
        auto_fixable=True,
    ),
    ErrorSignature(
        pattern=r"Connection refused|Connection timed out|ETIMEDOUT|Network unreachable|DNS|ENETUNREACH|getaddrinfo",
        error_type=ErrorType.NETWORK_ERROR,
        description_ar="مشكلة في الاتصال بالشبكة",
        suggested_fix="إعادة المحاولة تلقائياً (خطأ عابر غالباً)",
        auto_fixable=True,
    ),
    ErrorSignature(
        pattern=r"timeout|timed out|deadline exceeded",
        error_type=ErrorType.TIMEOUT,
        description_ar="انتهاء المهلة الزمنية - العملية أخذت وقت أطول من المسموح",
        suggested_fix="زيادة timeout",
        auto_fixable=True,
    ),
]

# -*- coding: utf-8 -*-
"""تنسيق القيم للعرض — مصدر واحد تشترك فيه الطرفية وتقرير HTML.

كانت هذي الدوال مكررة حرفياً بين ``cli.py`` و``reporting/html.py``، فتغيير
صيغة العرض كان يتطلب تعديلين ونسيان أحدهما لا يكشفه أي اختبار.
"""

import unicodedata
from typing import Iterable, Optional

from .i18n import t

#: يُعرض بدل القيمة الغائبة في كل المخرجات
EMPTY = "—"

#: فراغ بين أطول تسمية وقيمتها في جداول الطرفية
LABEL_GAP = 3

#: محدِّد العرض (Variation Selector-16): يرقّي المحرف السابق إلى تقديم إيموجي
_VS16 = "️"


def pct(value: Optional[float]) -> str:
    """نسبة من 0..1 إلى مئوية"""
    return EMPTY if value is None else f"{value * 100:.0f}%"


def secs(value: Optional[float]) -> str:
    """ثوانٍ بخانة عشرية واحدة"""
    return EMPTY if value is None else f"{value:.1f}s"


def duration(seconds: Optional[float]) -> str:
    """مدة طويلة بالساعات أو الدقائق حسب حجمها"""
    if seconds is None:
        return EMPTY
    hours = seconds / 3600
    return f"{hours:.1f}h" if hours >= 1 else f"{seconds / 60:.0f}m"


def trend(ratio: Optional[float]) -> str:
    """رمز اتجاه المدة: تصاعد، تراجع، أو ثبات"""
    if ratio is None:
        return EMPTY
    if ratio >= 1.25:
        return t("stats.trend_up")
    if ratio <= 0.8:
        return t("stats.trend_down")
    return t("stats.trend_flat")


# ============================================================
# محاذاة الطرفية
# ============================================================


def display_width(text: str) -> int:
    """عرض النص بخانات الطرفية لا بعدد محارفه.

    ``len()`` لا يصلح للمحاذاة: الإيموجي يشغل خانتين ويُحسب محرفاً واحداً،
    ومحدِّدات التقديم والمحارف المركّبة تشغل صفراً وتُحسب واحداً. ولهذا كانت
    المسافات في ``status`` مضبوطة يدوياً على أطوال النصوص العربية، فتتعرّج
    المحاذاة بمجرد تغيير اللغة.
    """
    width = 0
    counted_any = False
    for char in text:
        if char == _VS16:
            # يرقّي المحرف السابق من تقديم نصي (خانة) إلى تقديم إيموجي (خانتان)
            if counted_any:
                width += 1
            continue
        if unicodedata.category(char) in ("Mn", "Me", "Cf"):
            continue
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        counted_any = True
    return width


def label_column(labels: Iterable[str], gap: int = LABEL_GAP) -> int:
    """عرض عمود التسميات: أعرض تسمية زائداً فراغاً ثابتاً"""
    widths = [display_width(label) for label in labels]
    return (max(widths) if widths else 0) + gap


def pad_label(label: str, column: int) -> str:
    """حشو تسمية إلى عرض العمود بحساب خانات العرض الفعلية"""
    return label + " " * max(1, column - display_width(label))

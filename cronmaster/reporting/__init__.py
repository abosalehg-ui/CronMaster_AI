# -*- coding: utf-8 -*-
"""طبقة التقارير."""

from .generator import FORMATS, ReportGenerator
from .html import render_html

__all__ = ["FORMATS", "ReportGenerator", "render_html"]

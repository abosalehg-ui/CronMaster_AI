# -*- coding: utf-8 -*-
"""اختبارات تقرير HTML المكتفي بذاته."""

import re
from datetime import datetime

import pytest

from cronmaster.analysis import ErrorAnalyzer
from cronmaster.i18n import set_lang
from cronmaster.reporting import ReportGenerator
from cronmaster.reporting.html import render_html

from conftest import failing_job, make_job

STATS = [
    {"job_id": "a", "job_name": "Alpha", "runs": 40, "success_rate": 0.95,
     "flakiness": 0.05, "avg_duration": 12.0, "duration_ratio": 1.1, "samples": 40, "mtbf_seconds": 7200},
    {"job_id": "b", "job_name": "Beta", "runs": 30, "success_rate": 0.4,
     "flakiness": 0.6, "avg_duration": 90.0, "duration_ratio": 2.4, "samples": 30, "mtbf_seconds": 600},
]

TREND = [
    {"day": "2026-04-01", "rate": 1.0, "total": 8},
    {"day": "2026-04-02", "rate": 0.75, "total": 8},
    {"day": "2026-04-03", "rate": 0.5, "total": 8},
]

HISTORY = {
    "fixes": [{"job_id": "b", "fix_type": "timeout", "details": "رُفعت المهلة",
               "timestamp": "2026-04-03T10:00:00"}],
    "alerts": {"b/timeout": "2026-04-03T10:05:00"},
}


@pytest.fixture(autouse=True)
def _arabic():
    set_lang("ar")
    yield
    set_lang("ar")


def build_page(**overrides):
    kwargs = dict(
        jobs=[
            make_job(id="a", name="Alpha", last_status="ok"),
            failing_job(id="b", name="Beta"),
        ],
        analyses=[ErrorAnalyzer.analyze_regex(failing_job(id="b", name="Beta"))],
        summary={"total": 2, "ok": 1, "error": 1, "other": 0},
        stats=STATS,
        trend=TREND,
        history=HISTORY,
    )
    kwargs.update(overrides)
    return render_html(**kwargs)


# ============================================================
# الاكتفاء الذاتي
# ============================================================


def test_page_has_no_external_references():
    page = build_page()
    for forbidden in ("http://", "https://", "<script", "src=", "@import", "url("):
        assert forbidden not in page


def test_page_is_a_complete_document():
    page = build_page()
    assert page.startswith("<!DOCTYPE html>")
    assert "</html>" in page
    assert "<style>" in page  # CSS مضمّن لا ملف خارجي


def test_rtl_and_arabic_by_default():
    page = build_page()
    assert 'dir="rtl"' in page
    assert 'lang="ar"' in page
    assert "تقرير CronMaster" in page


def test_english_switches_direction_and_labels():
    set_lang("en")
    page = build_page()
    assert 'dir="ltr"' in page
    assert 'lang="en"' in page
    assert "CronMaster report" in page


def test_dark_mode_is_handled():
    assert "prefers-color-scheme: dark" in build_page()


def test_wide_tables_scroll_in_their_own_container():
    page = build_page()
    assert 'class="scroll"' in page
    assert "overflow-x: auto" in page
    assert "overflow-x: hidden" in page  # جسم الصفحة نفسه لا يتمرّر أفقياً


# ============================================================
# المحتوى
# ============================================================


def test_summary_and_job_rows_present():
    page = build_page()
    assert "Alpha" in page
    assert "Beta" in page
    assert "95%" in page  # نسبة النجاح من السجل التاريخي
    assert "60%" in page  # التذبذب


def test_charts_are_inline_svg():
    page = build_page()
    assert page.count("<svg") == 2  # اتجاه النجاح + أعمدة أكثر المهام فشلاً
    assert "<polyline" in page
    assert "<rect" in page


def test_history_section_lists_fixes_and_alerts():
    page = build_page()
    assert "رُفعت المهلة" in page
    assert "b/timeout" in page


def test_empty_data_renders_placeholders():
    page = build_page(stats=[], trend=[], history={}, analyses=[])
    assert "لا توجد بيانات كافية بعد" in page
    assert "<svg" not in page or page.count("<svg") == 0


def test_hostile_job_names_are_escaped():
    evil = make_job(id="x", name='<script>alert("xss")</script>', last_status="ok")
    page = build_page(jobs=[evil], analyses=[], stats=[], trend=[], history={})
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page


# ============================================================
# التوليد إلى ملف
# ============================================================


def test_generate_html_writes_a_file(tmp_path):
    generator = ReportGenerator(reports_dir=tmp_path)
    path = generator.generate_report(
        jobs=[make_job(id="a", name="Alpha", last_status="ok")],
        analyses=[],
        fmt="html",
        stats=STATS,
        trend=TREND,
        history=HISTORY,
    )
    assert path.suffix == ".html"
    assert re.match(r"report_\d{8}_\d{6}\.html", path.name)
    assert path.stat().st_mode & 0o777 == 0o600
    assert "<!DOCTYPE html>" in path.read_text(encoding="utf-8")


def test_markdown_output_is_unchanged(tmp_path):
    """الصيغة النصية القديمة يجب أن تبقى كما هي حرفياً"""
    jobs = [make_job(id="a", name="Fine", last_status="ok")]
    path = ReportGenerator(reports_dir=tmp_path).generate_report(jobs, [], fmt="markdown")
    content = path.read_text(encoding="utf-8")
    assert content.startswith("# 📊 تقرير CronMaster")
    assert "## 📈 الإحصائيات" in content
    assert "| إجمالي المهام | 1 |" in content
    assert "_لا توجد مهام فاشلة 🎉_" in content
    assert content.endswith("_تم التوليد بواسطة CronMaster_AI_\n")


def test_report_timestamp_is_recent(tmp_path):
    path = ReportGenerator(reports_dir=tmp_path).generate_report([], [], fmt="html")
    stamp = path.stem.replace("report_", "")
    parsed = datetime.strptime(stamp, "%Y%m%d_%H%M%S")
    assert abs((datetime.now() - parsed).total_seconds()) < 60

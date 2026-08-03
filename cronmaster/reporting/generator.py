# -*- coding: utf-8 -*-
"""توليد التقارير: Markdown و JSON و HTML."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import Config, write_secure
from ..i18n import t
from ..models import FailureAnalysis, Job
from .html import render_html

FORMATS = ("markdown", "json", "html")


class ReportGenerator:
    """توليد التقارير"""

    def __init__(self, reports_dir: Optional[Path] = None):
        self.reports_dir = reports_dir or Config.REPORTS_DIR
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def generate_report(
        self,
        jobs: List[Job],
        analyses: List[FailureAnalysis],
        fmt: str = "markdown",
        stats: Optional[List[Dict[str, Any]]] = None,
        trend: Optional[List[Dict[str, Any]]] = None,
        history: Optional[Dict[str, Any]] = None,
    ) -> Path:
        """توليد تقرير.

        ``stats`` و ``trend`` و ``history`` اختيارية ويستخدمها تقرير HTML؛
        صيغتا Markdown و JSON تبقيان كما كانتا بالضبط.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if fmt == "json":
            return self._generate_json(jobs, analyses, timestamp)
        if fmt == "html":
            return self._generate_html(jobs, analyses, timestamp, stats, trend, history)
        return self._generate_markdown(jobs, analyses, timestamp)

    @staticmethod
    def _categorize(jobs: List[Job]) -> Tuple[List[Job], List[Job], List[Job]]:
        """تصنيف موحد: (ناجحة، فاشلة، أخرى) — نفس معيار is_failed في كل النظام"""
        ok = [j for j in jobs if j.last_status == "ok"]
        failed = [j for j in jobs if j.is_failed]
        other = [j for j in jobs if j.last_status != "ok" and not j.is_failed]
        return ok, failed, other

    def _generate_markdown(
        self,
        jobs: List[Job],
        analyses: List[FailureAnalysis],
        timestamp: str,
    ) -> Path:
        """توليد تقرير Markdown"""
        report_file = self.reports_dir / f"report_{timestamp}.md"
        ok_jobs, failed_jobs, other_jobs = self._categorize(jobs)

        content = f"""# {t('report.title')}

**{t('report.date')}:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

---

## {t('report.stats')}

| {t('report.metric')} | {t('report.value')} |
|---------|--------|
| {t('report.total_jobs')} | {len(jobs)} |
| {t('report.ok_jobs')} | {len(ok_jobs)} |
| {t('report.failed_jobs')} | {len(failed_jobs)} |
| {t('report.other_jobs')} | {len(other_jobs)} |

---

## {t('report.failed_section')} ({len(failed_jobs)})

"""

        if not failed_jobs:
            content += f"_{t('report.no_failures')}_\n"
        else:
            for a in analyses:
                fix_status = t("report.fixed") if a.fix_applied else t("report.needs_review")
                content += f"""### {a.job.name}
- **{t('report.error')}:** {a.error_type.value}
- **{t('report.analysis')}:** {a.description}
- **{t('report.fix')}:** {a.suggested_fix}
- **{t('report.state')}:** {fix_status}
{f"- **{t('report.fix_details')}:** {a.fix_details}" if a.fix_details else ""}

"""

        content += f"""
---

## {t('report.ok_section')} ({len(ok_jobs)})

"""
        for j in ok_jobs[:10]:
            content += f"- {j.name}\n"

        if len(ok_jobs) > 10:
            content += f"- _{t('report.and_more', count=len(ok_jobs) - 10)}_\n"

        content += f"\n---\n_{t('report.footer')}_\n"

        report_file.write_text(content, encoding="utf-8")
        return report_file

    def _generate_json(
        self,
        jobs: List[Job],
        analyses: List[FailureAnalysis],
        timestamp: str,
    ) -> Path:
        """توليد تقرير JSON"""
        report_file = self.reports_dir / f"report_{timestamp}.json"
        ok_jobs, failed_jobs, other_jobs = self._categorize(jobs)

        data = {
            "timestamp": datetime.now().isoformat(),
            "summary": {
                "total": len(jobs),
                "ok": len(ok_jobs),
                "error": len(failed_jobs),
                "other": len(other_jobs),
            },
            "jobs": [j.to_dict() for j in jobs],
            "analyses": [a.to_dict() for a in analyses],
        }

        report_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return report_file

    def _generate_html(
        self,
        jobs: List[Job],
        analyses: List[FailureAnalysis],
        timestamp: str,
        stats: Optional[List[Dict[str, Any]]],
        trend: Optional[List[Dict[str, Any]]],
        history: Optional[Dict[str, Any]],
    ) -> Path:
        """توليد تقرير HTML مكتفٍ بذاته"""
        report_file = self.reports_dir / f"report_{timestamp}.html"
        ok_jobs, failed_jobs, other_jobs = self._categorize(jobs)
        content = render_html(
            jobs=jobs,
            analyses=analyses,
            summary={"total": len(jobs), "ok": len(ok_jobs), "error": len(failed_jobs), "other": len(other_jobs)},
            stats=stats or [],
            trend=trend or [],
            history=history or {},
        )
        write_secure(report_file, content)
        return report_file

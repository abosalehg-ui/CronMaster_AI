# -*- coding: utf-8 -*-
"""التكامل مع أنظمة المراقبة: ملف Prometheus النصي ونبضة healthcheck."""

import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import Config
from .models import Job

PING_TIMEOUT = 5


def _escape_label(value: str) -> str:
    """تهريب قيمة تسمية Prometheus"""
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def build_prometheus_text(result: Dict[str, Any], jobs: Optional[List[Job]] = None) -> str:
    """بناء نص exposition من نتيجة دورة مراقبة"""
    failed_ok = "error" not in result
    lines: List[str] = []

    def metric(name: str, help_text: str, kind: str, samples: List[str]):
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {kind}")
        lines.extend(samples)

    metric(
        "cronmaster_monitor_success",
        "1 if the last monitor cycle completed without a backend failure.",
        "gauge",
        [f"cronmaster_monitor_success {1 if failed_ok else 0}"],
    )
    metric(
        "cronmaster_last_run_timestamp_seconds",
        "Unix timestamp of the last monitor cycle.",
        "gauge",
        [f"cronmaster_last_run_timestamp_seconds {time.time():.0f}"],
    )

    if failed_ok:
        metric(
            "cronmaster_jobs_total",
            "Total number of scheduled jobs known to the backend.",
            "gauge",
            [f"cronmaster_jobs_total {result.get('total_jobs', 0)}"],
        )
        metric(
            "cronmaster_jobs_failed",
            "Number of enabled jobs whose last run failed.",
            "gauge",
            [f"cronmaster_jobs_failed {result.get('failed_jobs', 0)}"],
        )
        metric(
            "cronmaster_jobs_silent",
            "Number of enabled jobs that missed their scheduled run.",
            "gauge",
            [f"cronmaster_jobs_silent {len(result.get('silent_jobs', []))}"],
        )
        metric(
            "cronmaster_jobs_critical",
            "Number of jobs at or above the consecutive-failure alert threshold.",
            "gauge",
            [f"cronmaster_jobs_critical {result.get('critical_jobs', 0)}"],
        )
        metric(
            "cronmaster_fixes_applied_total",
            "Number of automatic fixes applied in the last monitor cycle.",
            "gauge",
            [f"cronmaster_fixes_applied_total {result.get('fixes_applied', 0)}"],
        )

        samples = [
            f'cronmaster_job_consecutive_errors{{job="{_escape_label(j.name)}",job_id="{_escape_label(j.id)}"}} '
            f"{j.consecutive_errors}"
            for j in (jobs or [])
            if j.enabled
        ]
        if samples:
            metric(
                "cronmaster_job_consecutive_errors",
                "Consecutive failure count per enabled job.",
                "gauge",
                samples,
            )

    return "\n".join(lines) + "\n"


def write_prometheus_textfile(path: str, result: Dict[str, Any], jobs: Optional[List[Job]] = None) -> bool:
    """كتابة ذرّية لملف Prometheus النصي.

    الكتابة عبر ملف مؤقت ثم ``os.replace`` حتى لا يقرأ node_exporter ملفاً نصفياً.
    أي فشل يُسجَّل ولا يُسقط دورة المراقبة.
    """
    logger = logging.getLogger(__name__)
    target = Path(path)
    tmp = target.with_name(target.name + ".tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(build_prometheus_text(result, jobs), encoding="utf-8")
        os.replace(tmp, target)
    except OSError as e:
        logger.warning("تعذرت كتابة ملف Prometheus (%s): %s", path, e)
        return False
    logger.info("كُتب ملف Prometheus: %s", target)
    return True


def ping_healthcheck(url: Optional[str], success: bool) -> bool:
    """نبضة healthchecks.io: العنوان عند النجاح و``<url>/fail`` عند الفشل.

    مهلة قصيرة، ولا يُمرَّر أي استثناء للخارج — النبضة مساعدة لا مهمة.
    """
    url = (url if url is not None else Config.HEALTHCHECK_PING_URL) or ""
    url = url.strip()
    if not url:
        return False
    if not url.startswith(("http://", "https://")):
        logging.getLogger(__name__).warning("healthcheck_ping_url ليس عنواناً صالحاً — أُهمل")
        return False

    target = url if success else url.rstrip("/") + "/fail"
    try:
        with urllib.request.urlopen(target, timeout=PING_TIMEOUT) as response:  # noqa: S310
            status = getattr(response, "status", 200)
    except (urllib.error.URLError, OSError, ValueError) as e:
        logging.getLogger(__name__).warning("تعذرت نبضة healthcheck: %s", e)
        return False
    return 200 <= int(status) < 300

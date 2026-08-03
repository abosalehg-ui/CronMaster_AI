# -*- coding: utf-8 -*-
"""واجهة خلفية لـ crontab النظام.

crontab لا يحتفظ بحالة تشغيل ولا بمهلة لكل مهمة، لذا تُعلن هذه الواجهة
قدرات مقلّصة: لا ``set_timeout`` ولا ``last_status`` ولا ``duration``.
المنسّق يقرأ ``capabilities`` قبل أي عملية فيتدرّج بلطف بدل أن ينهار.
"""

import re
import subprocess
from typing import List, Optional, Tuple

from ..models import Job
from .base import BackendError, Capability, CronBackend


class CrontabError(BackendError):
    """فشل قراءة أو كتابة crontab"""


# سطر cron قياسي: خمسة حقول جدولة ثم الأمر
_CRON_LINE = re.compile(
    r"^\s*(?P<disabled>#\s*CRONMASTER-DISABLED\s+)?"
    r"(?P<expr>(?:[^\s#]+\s+){4}[^\s#]+)\s+"
    r"(?P<command>\S.*)$"
)
_SPECIAL_LINE = re.compile(
    r"^\s*(?P<disabled>#\s*CRONMASTER-DISABLED\s+)?"
    r"(?P<expr>@(?:reboot|yearly|annually|monthly|weekly|daily|midnight|hourly))\s+"
    r"(?P<command>\S.*)$"
)
# علامة التعطيل: نُعلّق السطر مع بادئة يمكننا التعرف عليها لاحقاً لإعادة تفعيله
DISABLED_PREFIX = "# CRONMASTER-DISABLED "


def run_crontab(*args: str, stdin: Optional[str] = None, timeout: int = 30):
    """تنفيذ أمر crontab بمهلة موحدة"""
    try:
        return subprocess.run(
            ["crontab", *args],
            capture_output=True,
            text=True,
            input=stdin,
            timeout=timeout,
        )
    except FileNotFoundError as e:
        raise CrontabError("أمر crontab غير موجود في PATH") from e
    except subprocess.TimeoutExpired as e:
        raise CrontabError(f"انتهت مهلة تنفيذ: crontab {' '.join(args)}") from e


class SystemCrontabBackend(CronBackend):
    """قراءة وتعديل crontab المستخدم الحالي."""

    name = "crontab"
    capabilities = {
        Capability.LIST,
        Capability.RUN,
        Capability.SET_ENABLED,
        Capability.SET_SCHEDULE,
    }
    error_type = CrontabError

    # ------------------------------------------------------------
    # القراءة
    # ------------------------------------------------------------

    def read_raw(self) -> str:
        """محتوى crontab كنص. جدول فارغ ليس خطأً."""
        result = run_crontab("-l")
        if result.returncode != 0:
            stderr = (result.stderr or "").lower()
            if "no crontab" in stderr:
                return ""
            raise CrontabError(f"crontab -l فشل: {result.stderr.strip() or 'بدون تفاصيل'}")
        return result.stdout or ""

    def list_jobs(self) -> List[Job]:
        jobs: List[Job] = []
        for index, line in enumerate(self.read_raw().splitlines()):
            parsed = self._parse_line(line)
            if parsed is None:
                continue
            expr, command, enabled = parsed
            jobs.append(
                Job(
                    id=self._job_id(command),
                    name=command if len(command) <= 60 else command[:57] + "...",
                    enabled=enabled,
                    schedule=expr,
                    # crontab لا يعرف حالة آخر تشغيل — نتركها None بدل اختلاق "ok"
                    last_status=None,
                    raw={"line": line, "index": index, "expr": expr, "command": command, "enabled": enabled},
                )
            )
        return jobs

    @staticmethod
    def _parse_line(line: str) -> Optional[Tuple[str, str, bool]]:
        """(الجدولة، الأمر، مفعّلة) أو None للأسطر التي ليست مهام"""
        stripped = line.strip()
        if not stripped:
            return None
        if stripped.startswith("#") and not stripped.startswith(DISABLED_PREFIX.strip()):
            return None
        if "=" in stripped.split()[0] and not stripped.startswith("#"):
            return None  # سطر متغير بيئة مثل PATH=...

        for pattern in (_SPECIAL_LINE, _CRON_LINE):
            match = pattern.match(line)
            if match:
                return (
                    match.group("expr").strip(),
                    match.group("command").strip(),
                    match.group("disabled") is None,
                )
        return None

    @staticmethod
    def _job_id(command: str) -> str:
        """معرّف مستقر مشتق من الأمر نفسه — crontab لا يعطي معرّفات"""
        import hashlib

        return hashlib.sha1(command.encode("utf-8")).hexdigest()[:12]

    # ------------------------------------------------------------
    # التعديل
    # ------------------------------------------------------------

    def _rewrite(self, job_id: str, transform) -> bool:
        """إعادة كتابة السطر الموافق للمهمة عبر دالة تحويل"""
        lines = self.read_raw().splitlines()
        changed = False

        for i, line in enumerate(lines):
            parsed = self._parse_line(line)
            if parsed is None:
                continue
            expr, command, enabled = parsed
            if self._job_id(command) != job_id:
                continue
            new_line = transform(expr, command, enabled)
            if new_line is not None and new_line != line:
                lines[i] = new_line
                changed = True
            break

        if not changed:
            return False

        content = "\n".join(lines) + "\n"
        result = run_crontab("-", stdin=content)
        if result.returncode != 0:
            raise CrontabError(f"تعذرت كتابة crontab: {result.stderr.strip() or 'بدون تفاصيل'}")
        return True

    def set_enabled(self, job_id: str, enabled: bool) -> bool:
        def transform(expr, command, current):
            if current == enabled:
                return None
            return f"{expr} {command}" if enabled else f"{DISABLED_PREFIX}{expr} {command}"

        return self._rewrite(job_id, transform)

    def set_schedule(self, job_id: str, expr: str) -> bool:
        def transform(_expr, command, current):
            prefix = "" if current else DISABLED_PREFIX
            return f"{prefix}{expr} {command}"

        return self._rewrite(job_id, transform)

    def run_job(self, job_id: str) -> bool:
        """تشغيل أمر المهمة مباشرة عبر الصدفة"""
        for job in self.list_jobs():
            if job.id != job_id:
                continue
            command = job.raw.get("command", "")
            try:
                result = subprocess.run(["/bin/sh", "-c", command], capture_output=True, text=True, timeout=300)
            except (OSError, subprocess.TimeoutExpired) as e:
                raise CrontabError(f"تعذر تشغيل المهمة: {e}") from e
            return result.returncode == 0
        return False

    def set_timeout(self, job_id: str, seconds: int) -> bool:
        raise CrontabError("crontab لا يدعم مهلة لكل مهمة")

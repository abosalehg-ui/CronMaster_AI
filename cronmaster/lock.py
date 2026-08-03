# -*- coding: utf-8 -*-
"""قفل تنفيذ استشاري يمنع تشغيل دورتَي مراقبة متزامنتين."""

import logging
import os
from pathlib import Path
from typing import Optional

from .config import FILE_MODE, Config, _chmod_quiet
from .i18n import t

try:  # POSIX فقط
    import fcntl
except ImportError:  # pragma: no cover — Windows
    fcntl = None


def _pid_alive(pid: int) -> bool:
    """هل العملية ما تزال حية؟"""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # موجودة لكن لمستخدم آخر
    except OSError:
        return False
    return True


class ExecutionLock:
    """قفل ملف استشاري.

    على POSIX يعتمد على ``fcntl.flock`` — وهو ما يتحرر تلقائياً إذا مات
    صاحبه. على الأنظمة التي لا توفر ``fcntl`` نسقط إلى ملف يحمل رقم العملية،
    ونستعيد القفل إذا كانت تلك العملية قد انتهت.
    """

    def __init__(self, path: Optional[Path] = None):
        self.logger = logging.getLogger(__name__)
        self.path = Path(path) if path else Config.lock_file()
        self.acquired = False
        self._handle = None

    def acquire(self) -> bool:
        """محاولة أخذ القفل. False تعني أن نسخة أخرى تعمل الآن."""
        if self.acquired:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)

        if fcntl is None:
            return self._acquire_pidfile()

        try:
            handle = open(self.path, "a+", encoding="utf-8")
        except OSError as e:
            self.logger.warning("تعذر فتح ملف القفل %s: %s — سيستمر بلا قفل", self.path, e)
            return True  # القفل احتياط، لا شرط لعمل الأداة

        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False

        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        _chmod_quiet(self.path, FILE_MODE)
        self._handle = handle
        self.acquired = True
        return True

    def _acquire_pidfile(self) -> bool:
        """بديل بلا fcntl: ملف يحمل رقم العملية مع استعادة الأقفال المهجورة"""
        if self.path.exists():
            try:
                pid = int(self.path.read_text(encoding="utf-8").strip() or 0)
            except (OSError, ValueError):
                pid = 0
            if _pid_alive(pid) and pid != os.getpid():
                return False
            if pid:
                self.logger.info(t("lock.stale", pid=pid))
        try:
            self.path.write_text(str(os.getpid()), encoding="utf-8")
        except OSError as e:
            self.logger.warning("تعذرت كتابة ملف القفل: %s — سيستمر بلا قفل", e)
            return True
        _chmod_quiet(self.path, FILE_MODE)
        self.acquired = True
        return True

    def release(self):
        """تحرير القفل. آمن للاستدعاء أكثر من مرة."""
        if not self.acquired:
            return
        if self._handle is not None:
            try:
                if fcntl is not None:
                    fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                self._handle.close()
            except OSError:
                pass
            self._handle = None
        elif fcntl is None:
            try:
                if self.path.exists() and self.path.read_text(encoding="utf-8").strip() == str(os.getpid()):
                    self.path.unlink()
            except OSError:
                pass
        self.acquired = False

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False

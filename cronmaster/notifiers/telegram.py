# -*- coding: utf-8 -*-
"""قناة Telegram عبر أداة الرسائل في OpenClaw — السلوك الافتراضي دون تغيير."""

from typing import Optional

from ..backends import openclaw as _openclaw
from ..config import Config
from .base import Notifier


class TelegramNotifier(Notifier):
    """إرسال تنبيه عبر ``openclaw message send``."""

    name = "telegram"

    def __init__(self, chat_id: Optional[str] = None):
        super().__init__()
        self._chat_id = chat_id

    @property
    def chat_id(self) -> str:
        """المعرّف الصريح إن وُجد، وإلا القيمة المضبوطة في الإعدادات وقت الإرسال"""
        return self._chat_id if self._chat_id is not None else Config.TELEGRAM_CHAT_ID

    def validate(self) -> bool:
        return bool(self.chat_id)

    def send(self, message: str) -> bool:
        chat_id = self.chat_id
        if not chat_id:
            self.logger.warning(
                "TELEGRAM_CHAT_ID غير مضبوط — لن يُرسل التنبيه. "
                "اضبطه في config.json أو متغير البيئة CRONMASTER_TELEGRAM_CHAT_ID"
            )
            return False

        try:
            result = _openclaw.run_openclaw(
                "message", "send",
                "--channel", "telegram",
                "--to", chat_id,
                "--message", message,
            )
        except _openclaw.OpenClawError as e:
            self.logger.error(f"خطأ في الإرسال: {e}")
            return False

        if result.returncode == 0:
            self.logger.info("تم إرسال التنبيه")
            return True
        self.logger.error(f"فشل الإرسال: {result.stderr.strip()}")
        return False

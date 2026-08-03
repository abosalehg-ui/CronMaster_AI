# -*- coding: utf-8 -*-
"""قنوات Webhook عامة (JSON POST) وSlack وDiscord — بمكتبة urllib القياسية."""

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from .base import Notifier

DEFAULT_TIMEOUT = 10


class WebhookNotifier(Notifier):
    """POST بصيغة JSON إلى عنوان يحدده المستخدم."""

    name = "webhook"
    # اسم الحقل الذي يحمل نص الرسالة في الحمولة
    field = "text"

    def __init__(self, url: str, field: Optional[str] = None, headers: Optional[Dict[str, str]] = None):
        super().__init__()
        self.url = url or ""
        if field:
            self.field = field
        self.headers = headers or {}

    def validate(self) -> bool:
        return self.url.startswith(("http://", "https://"))

    def build_payload(self, message: str) -> Dict[str, Any]:
        return {self.field: message}

    def send(self, message: str) -> bool:
        if not self.validate():
            self.logger.warning("عنوان %s غير صالح — لن يُرسل التنبيه", self.name)
            return False

        data = json.dumps(self.build_payload(message), ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.url,
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8", **self.headers},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT) as response:  # noqa: S310
                status = getattr(response, "status", 200)
        except urllib.error.HTTPError as e:
            self.logger.error("فشل إرسال %s: HTTP %s", self.name, e.code)
            return False
        except (urllib.error.URLError, OSError, ValueError) as e:
            self.logger.error("فشل إرسال %s: %s", self.name, e)
            return False

        if 200 <= int(status) < 300:
            self.logger.info("تم إرسال التنبيه عبر %s", self.name)
            return True
        self.logger.error("فشل إرسال %s: HTTP %s", self.name, status)
        return False


class SlackNotifier(WebhookNotifier):
    """Slack incoming webhook — يتوقع الحقل ``text``."""

    name = "slack"
    field = "text"


class DiscordNotifier(WebhookNotifier):
    """Discord webhook — يتوقع الحقل ``content`` وبحد 2000 محرف."""

    name = "discord"
    field = "content"
    MAX_LENGTH = 2000

    def build_payload(self, message: str) -> Dict[str, Any]:
        if len(message) > self.MAX_LENGTH:
            message = message[: self.MAX_LENGTH - 1] + "…"
        return {self.field: message}

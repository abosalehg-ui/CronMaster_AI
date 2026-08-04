# -*- coding: utf-8 -*-
"""قنوات Webhook عامة (JSON POST) وSlack وDiscord — بمكتبة urllib القياسية."""

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from ..config import Config
from .base import Notifier

DEFAULT_TIMEOUT = 10


class WebhookNotifier(Notifier):
    """POST بصيغة JSON إلى عنوان يحدده المستخدم."""

    name = "webhook"
    # اسم الحقل الذي يحمل نص الرسالة في الحمولة
    field = "text"

    def __init__(
        self,
        url: str,
        field: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        allow_insecure: Optional[bool] = None,
    ):
        super().__init__()
        self.url = url or ""
        if field:
            self.field = field
        self.headers = headers or {}
        self.allow_insecure = Config.ALLOW_INSECURE_WEBHOOKS if allow_insecure is None else bool(allow_insecure)

    def validate(self) -> bool:
        """عنوان صالح: https دائماً، وhttp فقط عند السماح الصريح.

        نص التنبيه يحمل أسماء المهام وأوصاف الأخطاء، وإرساله على http يعني
        عبوره الشبكة بلا تشفير. تبقى http متاحة لشبكة داخلية لكن بقرار صريح.
        """
        if self.url.startswith("https://"):
            return True
        if self.url.startswith("http://"):
            if self.allow_insecure:
                self.logger.warning("قناة %s تستعمل http بلا تشفير: %s", self.name, self.url)
                return True
            self.logger.warning(
                "قناة %s ترفض http بلا تشفير — استخدم https أو فعّل allow_insecure_webhooks", self.name
            )
            return False
        return False

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

# -*- coding: utf-8 -*-
"""طبقة التنبيهات: بناء القنوات، فترة الهدوء، وتنسيق الرسائل."""

import logging
from datetime import datetime, time
from typing import Any, Dict, List, Optional

from ..config import Config
from ..i18n import t
from ..models import FailureAnalysis, Job
from .base import Notifier, NullNotifier
from .telegram import TelegramNotifier
from .webhook import DiscordNotifier, SlackNotifier, WebhookNotifier

NOTIFIER_TYPES = {
    "telegram": TelegramNotifier,
    "webhook": WebhookNotifier,
    "slack": SlackNotifier,
    "discord": DiscordNotifier,
    "null": NullNotifier,
}


def build_notifiers(specs: Optional[List[Dict[str, Any]]] = None, telegram_chat_id: Optional[str] = None
                    ) -> List[Notifier]:
    """بناء قائمة القنوات من الإعدادات.

    المفتاح القديم ``telegram_chat_id`` ما يزال اختصاراً لقناة Telegram واحدة،
    ويُستخدم وحده إذا لم تُعرَّف قائمة ``notifiers``.
    """
    logger = logging.getLogger(__name__)
    specs = Config.NOTIFIERS if specs is None else specs
    chat_id = Config.TELEGRAM_CHAT_ID if telegram_chat_id is None else telegram_chat_id

    notifiers: List[Notifier] = []
    for spec in specs or []:
        if not isinstance(spec, dict):
            logger.warning("عنصر notifiers ليس كائناً — أُهمل: %r", spec)
            continue
        kind = str(spec.get("type", "")).strip().lower()
        cls = NOTIFIER_TYPES.get(kind)
        if cls is None:
            logger.warning("نوع قناة تنبيه غير معروف: %s (المتاح: %s)", kind, ", ".join(sorted(NOTIFIER_TYPES)))
            continue
        try:
            if cls is TelegramNotifier:
                notifiers.append(TelegramNotifier(chat_id=spec.get("chat_id") or chat_id))
            elif cls is NullNotifier:
                notifiers.append(NullNotifier())
            else:
                notifiers.append(cls(url=spec.get("url", ""), field=spec.get("field"), headers=spec.get("headers")))
        except (TypeError, ValueError) as e:
            logger.warning("تعذر بناء قناة %s: %s", kind, e)

    # التوافق الخلفي: لا قائمة قنوات لكن هناك chat_id → قناة Telegram واحدة
    if not notifiers and chat_id:
        notifiers.append(TelegramNotifier(chat_id=chat_id))

    return notifiers


# ============================================================
# فترة الهدوء
# ============================================================


def _parse_hhmm(value: Any) -> Optional[time]:
    if not isinstance(value, str) or ":" not in value:
        return None
    try:
        hour, minute = value.split(":", 1)
        return time(int(hour), int(minute))
    except (TypeError, ValueError):
        return None


def _now_in_tz(tz_name: Optional[str]) -> datetime:
    """الوقت الحالي في المنطقة المطلوبة، مع سقوط إلى التوقيت المحلي"""
    if not tz_name:
        return datetime.now()
    try:
        from zoneinfo import ZoneInfo  # Python 3.9+
    except ImportError:
        logging.getLogger(__name__).warning(
            "zoneinfo غير متاحة على هذا الإصدار من Python — ستُحسب فترة الهدوء بالتوقيت المحلي"
        )
        return datetime.now()
    try:
        return datetime.now(ZoneInfo(tz_name))
    except Exception as e:  # noqa: BLE001 — ZoneInfoNotFoundError وغيرها
        logging.getLogger(__name__).warning("منطقة زمنية غير معروفة (%s): %s — سيُستخدم التوقيت المحلي", tz_name, e)
        return datetime.now()


def in_quiet_hours(config: Optional[Dict[str, Any]] = None, now: Optional[datetime] = None) -> bool:
    """هل نحن داخل فترة الهدوء؟ تدعم فترة تعبر منتصف الليل."""
    config = Config.QUIET_HOURS if config is None else config
    if not isinstance(config, dict) or not config:
        return False

    start = _parse_hhmm(config.get("from"))
    end = _parse_hhmm(config.get("to"))
    if start is None or end is None:
        logging.getLogger(__name__).warning("quiet_hours يحتاج from و to بصيغة HH:MM — أُهملت")
        return False

    current = (now or _now_in_tz(config.get("tz"))).time()
    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end  # فترة تعبر منتصف الليل


# ============================================================
# مدير التنبيهات
# ============================================================


class AlertManager:
    """إدارة التنبيهات: التوزيع على القنوات، فترة الهدوء، والتنسيق."""

    def __init__(self, notifiers: Optional[List[Notifier]] = None, state=None):
        self.logger = logging.getLogger(__name__)
        self._notifiers = notifiers
        self.state = state

    @property
    def notifiers(self) -> List[Notifier]:
        """القنوات تُبنى كسولاً حتى تُقرأ الإعدادات وقت الإرسال لا وقت الإنشاء"""
        if self._notifiers is None:
            self._notifiers = build_notifiers()
        return self._notifiers

    # ---- الإرسال ----

    def send_telegram(self, message: str) -> bool:
        """إرسال تنبيه عبر OpenClaw message tool (المسار التاريخي، دون تغيير)"""
        return TelegramNotifier().send(message)

    def dispatch(self, message: str) -> bool:
        """توزيع الرسالة على كل القنوات. النجاح = نجاح قناة واحدة على الأقل."""
        notifiers = self.notifiers
        if not notifiers:
            return self.send_telegram(message)

        sent = False
        for notifier in notifiers:
            try:
                if notifier.send(message):
                    sent = True
            except Exception as e:  # noqa: BLE001 — قناة واحدة يجب ألا تُسقط البقية
                self.logger.error("قناة %s رفعت استثناءً: %s", notifier.name, e)
        return sent

    def send(self, message: str, critical: bool = False) -> bool:
        """إرسال مع احترام فترة الهدوء.

        التنبيهات الحرجة (فشل المراقب نفسه، قاطع الدائرة) تخرج فوراً دائماً؛
        غير الحرجة تُخزَّن في الحالة وتُرسَل كملخص واحد في أول دورة خارج فترة الهدوء.
        """
        if not critical and self.state is not None and in_quiet_hours():
            self.state.queue_alert(message)
            self.logger.info("فترة هدوء — أُجّل التنبيه إلى ملخص لاحق")
            return False
        return self.dispatch(message)

    def flush_digest(self) -> bool:
        """إرسال التنبيهات المؤجلة كرسالة واحدة. يُستدعى في أول دورة خارج فترة الهدوء."""
        if self.state is None or in_quiet_hours():
            return False
        queued = self.state.peek_queued_alerts()
        if not queued:
            return False

        lines = [t("alert.digest", count=len(queued)), ""]
        for item in queued:
            lines.append(item.get("message", ""))
            lines.append("")
        message = "\n".join(lines).rstrip()

        if self.dispatch(message):
            self.state.take_queued_alerts()
            return True
        return False

    # ---- التنسيق ----

    def format_alert(
        self,
        analyses: List[FailureAnalysis],
        recovered: Optional[List[Job]] = None,
        silent: Optional[List[Job]] = None,
        extra: Optional[List[str]] = None,
    ) -> str:
        """تنسيق رسالة التنبيه (نص عادي — بدون Markdown لتجنب مشاكل المحارف الخاصة)"""
        lines = [t("alert.header"), ""]

        for a in analyses:
            emoji = "✅" if a.fix_applied else "❌"
            lines.append(f"{emoji} {a.job.name}")
            lines.append(f"   {t('alert.error_type')}: {a.error_type.value}")
            lines.append(f"   {t('alert.analysis')}: {a.description}")

            if a.fix_applied:
                lines.append(f"   {t('alert.fix')}: {a.fix_details}")
            else:
                lines.append(f"   {t('alert.suggestion')}: {a.suggested_fix}")

            lines.append("")

        if recovered:
            lines.append(t("alert.recovered"))
            for j in recovered:
                lines.append(f"   ✅ {j.name}")
            lines.append("")

        if silent:
            lines.append(t("alert.silent"))
            for j in silent:
                missed = j.next_run_at.strftime("%Y-%m-%d %H:%M") if j.next_run_at else "?"
                lines.append(f"   ⏰ {j.name} ({t('alert.missed_at')}: {missed})")
            lines.append("")

        for line in extra or []:
            lines.append(line)
            lines.append("")

        return "\n".join(lines).rstrip()


__all__ = [
    "AlertManager",
    "DiscordNotifier",
    "NOTIFIER_TYPES",
    "Notifier",
    "NullNotifier",
    "SlackNotifier",
    "TelegramNotifier",
    "WebhookNotifier",
    "build_notifiers",
    "in_quiet_hours",
]

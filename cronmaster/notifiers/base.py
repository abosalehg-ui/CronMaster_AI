# -*- coding: utf-8 -*-
"""عقد قناة التنبيه."""

import logging


class Notifier:
    """واجهة قناة تنبيه: اسم و ``send`` تعيد نجاح الإرسال.

    فشل قناة لا يرفع استثناءً بل يعيد False — حتى لا تُسقط قناة واحدة بقية القنوات.
    """

    name = "base"

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__module__)

    def send(self, message: str) -> bool:
        raise NotImplementedError

    def validate(self) -> bool:
        """تحقق بلا إرسال: هل القناة مضبوطة بما يكفي للعمل؟ (يستخدمه doctor)"""
        return True


class NullNotifier(Notifier):
    """قناة صامتة: تسجّل الرسالة في السجل ولا ترسل شيئاً."""

    name = "null"

    def send(self, message: str) -> bool:
        self.logger.info("[null] تنبيه لم يُرسل لأي قناة:\n%s", message)
        return True

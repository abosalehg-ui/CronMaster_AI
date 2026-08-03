# -*- coding: utf-8 -*-
"""مصنّف أخطاء اختياري يعتمد على Anthropic SDK.

قواعد التصميم:
- المصنّف النمطي يعمل أولاً ودائماً؛ هذا المصنّف لا يُستشار إلا حين تكون
  النتيجة ``unknown`` (أي رسالة "راجع السجلات" عديمة الفائدة).
- استيراد كسول داخل ``try/except ImportError``: غياب الحزمة أو المفتاح لا
  يكسر شيئاً، فقط سطر INFO مرة واحدة وسقوط إلى نتيجة regex.
- لا يملك هذا المصنّف صلاحية الإذن بأي إصلاح مدمّر أو مُعدِّل للإعدادات.
  أقصى ما يفعله: تشخيص أوضح، حلٌّ مقترح، وتلميح "خطأ عابر" يُستخدم في مسار
  إعادة المحاولة المسقوف فقط، وبشرط تجاوز ``llm_min_confidence``.
- المفتاح ``ANTHROPIC_API_KEY`` يُقرأ من البيئة ولا يُكتب أبداً في إعدادات أو
  حالة أو نسخ احتياطية أو تقارير أو تنبيهات.
"""

import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, Optional

from ..config import Config
from ..models import ErrorType, FailureAnalysis, Job

_VALID_TYPES = [e.value for e in ErrorType]

# مخطط الإخراج المُقيَّد — كل الحقول مطلوبة و additionalProperties ممنوعة
RESPONSE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "error_type": {"type": "string", "enum": _VALID_TYPES},
        "description_ar": {"type": "string"},
        "suggested_fix_ar": {"type": "string"},
        "is_transient": {"type": "boolean"},
        "confidence": {"type": "number"},
    },
    "required": ["error_type", "description_ar", "suggested_fix_ar", "is_transient", "confidence"],
    "additionalProperties": False,
}

# التعليمات الثابتة توضع في كتلة system مع cache_control حتى تُخزَّن مؤقتاً،
# والنص المتغيّر (خطأ المهمة) يأتي أخيراً في رسالة المستخدم.
# الحد الأدنى للتخزين المؤقت على هذا الموديل 512 رمزاً؛ إن كان النص أقصر
# فلن يُخزَّن — وهذا مقبول، ولا داعي لحشو النص.
SYSTEM_PROMPT = """أنت مصنّف أخطاء لمهام مجدولة (cron jobs). مهمتك تصنيف نص خطأ واحد
إلى أحد الأنواع المعروفة، وشرح السبب واقتراح حل — بالعربية.

تصنيفات الأخطاء المتاحة:
- timeout: العملية تجاوزت المهلة الزمنية المسموحة للمهمة نفسها.
- permission_denied: نقص صلاحيات على ملف أو مورد أو عملية.
- not_found: ملف أو أمر أو مسار غير موجود.
- dependency_error: مكتبة أو موديول أو حزمة ناقصة.
- syntax_error: خطأ في صياغة الكود أو ملف الإعدادات.
- network_error: تعذر الاتصال بالشبكة، رفض اتصال، فشل DNS، انقطاع مؤقت.
- disk_full: نفاد مساحة القرص.
- memory_error: نفاد الذاكرة أو قتل العملية بسبب الذاكرة.
- api_error: رفض من خدمة خارجية، تجاوز حد الطلبات، أو خطأ HTTP من واجهة برمجية.
- unknown: لا يمكن التصنيف بثقة معقولة.

قواعد ملزمة:
1. اختر unknown بدل التخمين إن لم يكن النص كافياً. التصنيف الخاطئ أسوأ من "غير معروف".
2. مشاكل الاتصال التي تحوي كلمة timeout هي network_error لا timeout؛ كلمة timeout
   وحدها لا تكفي — انظر إن كانت المهلة مهلة المهمة أم مهلة اتصال.
3. is_transient يكون true فقط للأخطاء التي يُرجَّح زوالها بإعادة تشغيل فورية
   دون أي تدخل بشري (انقطاع شبكة لحظي، ازدحام مؤقت). أي خطأ يحتاج تعديل كود أو
   صلاحيات أو بيئة هو false.
4. confidence رقم بين 0 و 1 يعبّر عن ثقتك الفعلية. لا تبالغ.
5. description_ar وsuggested_fix_ar جملتان قصيرتان بالعربية، محددتان وقابلتان للتنفيذ،
   بلا حشو وبلا إعادة صياغة لنص الخطأ.
6. لا تفترض أي صلاحية لتنفيذ إصلاح؛ أنت تشخّص فقط.
"""

_ANNOUNCED = set()


def _announce_once(logger: logging.Logger, key: str, message: str) -> None:
    """سطر INFO مرة واحدة لكل سبب — لا نُغرق السجل في كل دورة مراقبة"""
    if key in _ANNOUNCED:
        return
    _ANNOUNCED.add(key)
    logger.info(message)


def reset_announcements() -> None:
    """تصفير سجل الإعلانات (تستخدمه الاختبارات)"""
    _ANNOUNCED.clear()


def normalize_error_text(text: str) -> str:
    """توحيد نص الخطأ قبل التجزئة: تصغير، ضغط المسافات، وتعميم الأرقام والمعرّفات.

    الهدف أن يُعطي نفس الخطأ المتكرر بأرقام مختلفة (منافذ، معرّفات، طوابع زمنية)
    نفس المفتاح، فلا نُكرر الدفع على تصنيف واحد.
    """
    text = (text or "").strip().lower()
    text = re.sub(r"0x[0-9a-f]+", "<hex>", text)
    text = re.sub(r"\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}:\d{2}\S*", "<ts>", text)
    text = re.sub(r"\d+", "<n>", text)
    text = re.sub(r"\s+", " ", text)
    return text


def cache_key(text: str) -> str:
    """sha256 لنص الخطأ بعد التوحيد"""
    return hashlib.sha256(normalize_error_text(text).encode("utf-8")).hexdigest()


class LLMClassifier:
    """مصنّف اختياري عبر Anthropic Messages API."""

    def __init__(self, cache=None, dry_run: bool = False, client=None):
        self.logger = logging.getLogger(__name__)
        self.cache = cache  # كائن يوفر get_llm_verdict / set_llm_verdict (HistoryStore)
        self.dry_run = dry_run
        self._client = client
        self._client_ready = client is not None

    # ------------------------------------------------------------
    # العميل
    # ------------------------------------------------------------

    def _get_client(self):
        """بناء العميل كسولاً. يعيد None عند غياب الحزمة أو المفتاح."""
        if self._client_ready:
            return self._client
        self._client_ready = True

        try:
            import anthropic  # noqa: F401  (استيراد كسول: حزمة اختيارية)
        except ImportError:
            _announce_once(
                self.logger,
                "sdk-missing",
                "حزمة anthropic غير مثبتة — سيُكتفى بالتصنيف النمطي. للتفعيل: pip install 'cronmaster-ai[ai]'",
            )
            return None

        if not os.environ.get("ANTHROPIC_API_KEY"):
            _announce_once(
                self.logger,
                "no-key",
                "ANTHROPIC_API_KEY غير مضبوط — سيُكتفى بالتصنيف النمطي",
            )
            return None

        try:
            self._client = anthropic.Anthropic()
        except Exception as e:  # noqa: BLE001 — بناء العميل يجب ألا يُسقط دورة المراقبة
            self.logger.warning("تعذر تهيئة عميل Anthropic: %s", e)
            self._client = None
        return self._client

    # ------------------------------------------------------------
    # التصنيف
    # ------------------------------------------------------------

    def classify(self, job: Job, regex_analysis: FailureAnalysis) -> Optional[FailureAnalysis]:
        """يعيد تحليلاً مُثرى، أو None ليقع النظام على نتيجة regex."""
        if not Config.LLM_ENABLED:
            return None

        error_text = job.error_text.strip()
        if not error_text:
            return None

        if self.dry_run:
            self.logger.info("[dry-run] لن يُستدعى المصنّف الذكي للمهمة %s", job.name)
            return None

        key = cache_key(error_text)

        cached = self._read_cache(key)
        if cached is not None:
            return self._build(job, regex_analysis, cached, source="llm-cache")

        verdict = self._ask(error_text, job)
        if verdict is None:
            return None

        self._write_cache(key, verdict)
        return self._build(job, regex_analysis, verdict, source="llm")

    def _read_cache(self, key: str) -> Optional[dict]:
        if self.cache is None:
            return None
        try:
            return self.cache.get_llm_verdict(key, Config.LLM_CACHE_DAYS)
        except Exception as e:  # noqa: BLE001 — الذاكرة المؤقتة تحسين لا شرط
            self.logger.debug("تعذرت قراءة ذاكرة التصنيف: %s", e)
            return None

    def _write_cache(self, key: str, verdict: dict) -> None:
        if self.cache is None:
            return
        try:
            self.cache.set_llm_verdict(key, verdict)
        except Exception as e:  # noqa: BLE001
            self.logger.debug("تعذرت كتابة ذاكرة التصنيف: %s", e)

    def _ask(self, error_text: str, job: Job) -> Optional[dict]:
        """نداء واحد للـ API مع سقوط آمن عند أي فشل."""
        client = self._get_client()
        if client is None:
            return None

        try:
            import anthropic
        except ImportError:  # pragma: no cover — تحقق منه في _get_client
            return None

        user_text = (
            "صنّف الخطأ التالي لمهمة مجدولة.\n"
            f"اسم المهمة: {job.name}\n"
            f"جدولة المهمة: {job.schedule}\n"
            f"المهلة المضبوطة: {job.timeout_seconds if job.timeout_seconds is not None else 'غير محددة'}\n"
            f"عدد الفشل المتتالي: {job.consecutive_errors}\n"
            "نص الخطأ:\n"
            "<error>\n"
            f"{error_text[:4000]}\n"
            "</error>"
        )

        try:
            response = client.messages.create(
                model=Config.LLM_MODEL,
                max_tokens=1024,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_text}],
                output_config={
                    "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA},
                    "effort": "low",
                },
            )
        except anthropic.AuthenticationError as e:
            self.logger.warning("فشل التحقق من مفتاح Anthropic: %s — سيُكتفى بالتصنيف النمطي", e)
            return None
        except anthropic.RateLimitError as e:
            self.logger.warning("تجاوز حد طلبات Anthropic: %s — سيُكتفى بالتصنيف النمطي", e)
            return None
        except anthropic.APIStatusError as e:
            self.logger.warning("خطأ من Anthropic API (%s) — سيُكتفى بالتصنيف النمطي", e.status_code)
            return None
        except anthropic.APIConnectionError as e:
            self.logger.warning("تعذر الاتصال بـ Anthropic: %s — سيُكتفى بالتصنيف النمطي", e)
            return None

        # فحص الرفض قبل قراءة المحتوى: عند الرفض لا يوجد نص لنقرأه
        if getattr(response, "stop_reason", None) == "refusal":
            self.logger.warning("رفض النموذج تصنيف هذا الخطأ — سيُكتفى بالتصنيف النمطي")
            return None

        return self._parse(response)

    def _parse(self, response) -> Optional[dict]:
        """استخراج الحكم من الرد والتحقق من صحته"""
        text = ""
        for block in getattr(response, "content", None) or []:
            if getattr(block, "type", None) == "text":
                text += getattr(block, "text", "") or ""

        if not text.strip():
            self.logger.warning("رد فارغ من المصنّف الذكي — سيُكتفى بالتصنيف النمطي")
            return None

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            self.logger.warning("رد المصنّف الذكي ليس JSON صالحاً — سيُكتفى بالتصنيف النمطي")
            return None

        if not isinstance(data, dict) or data.get("error_type") not in _VALID_TYPES:
            self.logger.warning("رد المصنّف الذكي خارج المخطط المتوقع — سيُكتفى بالتصنيف النمطي")
            return None

        try:
            data["confidence"] = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            data["confidence"] = 0.0
        data["is_transient"] = bool(data.get("is_transient", False))
        return data

    # ------------------------------------------------------------
    # بناء التحليل النهائي
    # ------------------------------------------------------------

    @staticmethod
    def _build(job: Job, regex_analysis: FailureAnalysis, verdict: dict, source: str) -> FailureAnalysis:
        """تحويل حكم النموذج إلى FailureAnalysis مع كل قيود السلامة."""
        confidence = float(verdict.get("confidence", 0.0))
        error_type = ErrorType(verdict["error_type"])

        # ثقة منخفضة: نأخذ الوصف كإرشاد فقط ولا نغيّر التصنيف ولا نفتح أي مسار إصلاح
        if confidence < Config.LLM_MIN_CONFIDENCE:
            return FailureAnalysis(
                job=job,
                error_type=regex_analysis.error_type,
                description=f"{verdict.get('description_ar', '')} (ثقة منخفضة {confidence:.2f})".strip(),
                suggested_fix=verdict.get("suggested_fix_ar") or regex_analysis.suggested_fix,
                auto_fixable=False,
                source=source,
                confidence=confidence,
                is_transient=None,
            )

        is_transient = bool(verdict.get("is_transient"))

        # الإصلاح التلقائي المسموح به من هذا المسار: إعادة محاولة مسقوفة لخطأ عابر فقط.
        # تعديل المهلة يبقى محصوراً بالتصنيف النمطي، وأي إجراء مدمّر ممنوع تماماً.
        auto_fixable = is_transient and error_type in (ErrorType.NETWORK_ERROR, ErrorType.API_ERROR)

        return FailureAnalysis(
            job=job,
            error_type=error_type,
            description=verdict.get("description_ar") or regex_analysis.description,
            suggested_fix=verdict.get("suggested_fix_ar") or regex_analysis.suggested_fix,
            auto_fixable=auto_fixable,
            source=source,
            confidence=confidence,
            is_transient=is_transient,
        )

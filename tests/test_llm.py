# -*- coding: utf-8 -*-
"""اختبارات المصنّف الاختياري بالذكاء الاصطناعي.

لا يوجد أي نداء شبكة هنا: وحدة ``anthropic`` نفسها مزيفة والعميل محقون.
"""

import json
import sys

import pytest

from cronmaster.analysis.analyzer import ErrorAnalyzer
from cronmaster.analysis.llm import LLMClassifier, cache_key, normalize_error_text, reset_announcements
from cronmaster.config import Config
from cronmaster.models import ErrorType
from cronmaster.storage import HistoryStore

from conftest import failing_job

VALID_VERDICT = {
    "error_type": "network_error",
    "description_ar": "انقطاع مؤقت في الاتصال بالخدمة",
    "suggested_fix_ar": "إعادة المحاولة بعد لحظات",
    "is_transient": True,
    "confidence": 0.93,
}


# ============================================================
# عميل مزيف
# ============================================================


class StubBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class StubResponse:
    def __init__(self, payload=None, stop_reason="end_turn", blocks=None):
        self.stop_reason = stop_reason
        if blocks is not None:
            self.content = blocks
        else:
            self.content = [StubBlock(json.dumps(payload, ensure_ascii=False))] if payload is not None else []


class StubMessages:
    def __init__(self, owner):
        self.owner = owner

    def create(self, **kwargs):
        self.owner.calls.append(kwargs)
        if self.owner.exception is not None:
            raise self.owner.exception
        return self.owner.response


class StubClient:
    def __init__(self, response=None, exception=None):
        self.calls = []
        self.response = response
        self.exception = exception
        self.messages = StubMessages(self)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    reset_announcements()
    monkeypatch.setattr(Config, "LLM_ENABLED", True)
    monkeypatch.setattr(Config, "LLM_MIN_CONFIDENCE", 0.8)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")


@pytest.fixture
def store(tmp_path):
    s = HistoryStore(db_path=tmp_path / "history.db", enabled=True)
    yield s
    s.close()


def unknown_job():
    return failing_job(error="خطأ لا يطابق أي نمط معروف على الإطلاق")


def regex_of(job):
    return ErrorAnalyzer.analyze_regex(job)


# ============================================================
# التطبيع والمفتاح
# ============================================================


def test_normalization_collapses_volatile_parts():
    a = normalize_error_text("Connection to 10.0.0.5:8080 failed at 2026-05-01T10:00:00Z")
    b = normalize_error_text("Connection to 10.0.0.9:9090 failed at 2026-06-02T11:22:33Z")
    assert a == b
    assert cache_key("Boom") == cache_key("  boom  ")


# ============================================================
# مسارات السقوط الآمن
# ============================================================


def test_sdk_missing_falls_back_to_regex(monkeypatch, store):
    monkeypatch.setitem(sys.modules, "anthropic", None)  # يجعل الاستيراد يرفع ImportError
    classifier = LLMClassifier(cache=store)
    assert classifier.classify(unknown_job(), regex_of(unknown_job())) is None


def test_missing_api_key_falls_back_to_regex(monkeypatch, store, fake_anthropic):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    classifier = LLMClassifier(cache=store)
    assert classifier.classify(unknown_job(), regex_of(unknown_job())) is None


def test_disabled_by_config_never_calls(monkeypatch, store, fake_anthropic):
    monkeypatch.setattr(Config, "LLM_ENABLED", False)
    client = StubClient(response=StubResponse(VALID_VERDICT))
    classifier = LLMClassifier(cache=store, client=client)
    assert classifier.classify(unknown_job(), regex_of(unknown_job())) is None
    assert client.calls == []


def test_dry_run_makes_no_api_call(store, fake_anthropic):
    client = StubClient(response=StubResponse(VALID_VERDICT))
    classifier = LLMClassifier(cache=store, client=client, dry_run=True)
    assert classifier.classify(unknown_job(), regex_of(unknown_job())) is None
    assert client.calls == []


def test_empty_error_text_is_skipped(store, fake_anthropic):
    client = StubClient(response=StubResponse(VALID_VERDICT))
    classifier = LLMClassifier(cache=store, client=client)
    job = failing_job(error="")
    job.last_error_reason = None
    assert classifier.classify(job, regex_of(job)) is None
    assert client.calls == []


# ============================================================
# الرد الصحيح
# ============================================================


def test_valid_response_enriches_diagnosis(store, fake_anthropic):
    client = StubClient(response=StubResponse(VALID_VERDICT))
    classifier = LLMClassifier(cache=store, client=client)
    job = unknown_job()

    result = classifier.classify(job, regex_of(job))

    assert result is not None
    assert result.error_type is ErrorType.NETWORK_ERROR
    assert result.description == VALID_VERDICT["description_ar"]
    assert result.suggested_fix == VALID_VERDICT["suggested_fix_ar"]
    assert result.auto_fixable is True  # عابر + نوع يسمح بإعادة محاولة مسقوفة
    assert result.source == "llm"
    assert result.confidence == pytest.approx(0.93)


def test_request_shape_matches_contract(store, fake_anthropic):
    client = StubClient(response=StubResponse(VALID_VERDICT))
    classifier = LLMClassifier(cache=store, client=client)
    job = unknown_job()
    classifier.classify(job, regex_of(job))

    kwargs = client.calls[0]
    assert kwargs["model"] == "claude-opus-5"
    assert kwargs["max_tokens"] == 1024
    # معاملات أخذ العينات والتفكير الصريح ممنوعة على هذا الموديل
    for forbidden in ("temperature", "top_p", "top_k", "thinking"):
        assert forbidden not in kwargs

    system = kwargs["system"][0]
    assert system["cache_control"] == {"type": "ephemeral"}

    fmt = kwargs["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert kwargs["output_config"]["effort"] == "low"
    schema = fmt["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "error_type", "description_ar", "suggested_fix_ar", "is_transient", "confidence",
    }
    assert "unknown" in schema["properties"]["error_type"]["enum"]

    # النص المتغيّر يأتي في رسالة المستخدم لا في البادئة المخزَّنة مؤقتاً
    assert job.last_error in kwargs["messages"][0]["content"]


def test_non_transient_verdict_is_not_auto_fixable(store, fake_anthropic):
    verdict = dict(VALID_VERDICT, error_type="permission_denied", is_transient=False)
    client = StubClient(response=StubResponse(verdict))
    classifier = LLMClassifier(cache=store, client=client)
    job = unknown_job()

    result = classifier.classify(job, regex_of(job))

    assert result.error_type is ErrorType.PERMISSION_DENIED
    assert result.auto_fixable is False


def test_transient_timeout_never_authorizes_timeout_edit(store, fake_anthropic):
    """تعديل المهلة يبقى محصوراً بالتصنيف النمطي مهما قال النموذج"""
    verdict = dict(VALID_VERDICT, error_type="timeout", is_transient=True, confidence=0.99)
    client = StubClient(response=StubResponse(verdict))
    classifier = LLMClassifier(cache=store, client=client)
    job = unknown_job()

    result = classifier.classify(job, regex_of(job))

    assert result.error_type is ErrorType.TIMEOUT
    assert result.auto_fixable is False


# ============================================================
# الثقة المنخفضة والرفض والأخطاء
# ============================================================


def test_low_confidence_keeps_regex_classification(store, fake_anthropic):
    verdict = dict(VALID_VERDICT, confidence=0.4)
    client = StubClient(response=StubResponse(verdict))
    classifier = LLMClassifier(cache=store, client=client)
    job = unknown_job()

    result = classifier.classify(job, regex_of(job))

    assert result.error_type is ErrorType.UNKNOWN  # لم يتغيّر التصنيف
    assert result.auto_fixable is False
    assert "ثقة منخفضة" in result.description


def test_refusal_is_checked_before_reading_content(store, fake_anthropic):
    class ExplodingContent(list):
        def __iter__(self):
            raise AssertionError("يجب ألا يُقرأ المحتوى عند الرفض")

    response = StubResponse(stop_reason="refusal")
    response.content = ExplodingContent()
    client = StubClient(response=response)
    classifier = LLMClassifier(cache=store, client=client)
    job = unknown_job()

    assert classifier.classify(job, regex_of(job)) is None


def test_rate_limit_falls_back_to_regex(store, fake_anthropic):
    client = StubClient(exception=fake_anthropic.RateLimitError("slow down", status_code=429))
    classifier = LLMClassifier(cache=store, client=client)
    job = unknown_job()
    assert classifier.classify(job, regex_of(job)) is None


@pytest.mark.parametrize("exc_name", ["AuthenticationError", "APIStatusError", "APIConnectionError"])
def test_typed_exceptions_fall_back_to_regex(store, fake_anthropic, exc_name):
    exc_cls = getattr(fake_anthropic, exc_name)
    client = StubClient(exception=exc_cls("boom"))
    classifier = LLMClassifier(cache=store, client=client)
    job = unknown_job()
    assert classifier.classify(job, regex_of(job)) is None


def test_malformed_json_falls_back_to_regex(store, fake_anthropic):
    client = StubClient(response=StubResponse(blocks=[StubBlock("{not json")]))
    classifier = LLMClassifier(cache=store, client=client)
    job = unknown_job()
    assert classifier.classify(job, regex_of(job)) is None


def test_out_of_schema_error_type_rejected(store, fake_anthropic):
    verdict = dict(VALID_VERDICT, error_type="cosmic_ray")
    client = StubClient(response=StubResponse(verdict))
    classifier = LLMClassifier(cache=store, client=client)
    job = unknown_job()
    assert classifier.classify(job, regex_of(job)) is None


# ============================================================
# الذاكرة المؤقتة
# ============================================================


def test_repeated_error_is_paid_for_once(store, fake_anthropic):
    client = StubClient(response=StubResponse(VALID_VERDICT))
    classifier = LLMClassifier(cache=store, client=client)

    first = classifier.classify(unknown_job(), regex_of(unknown_job()))
    second = classifier.classify(unknown_job(), regex_of(unknown_job()))

    assert len(client.calls) == 1
    assert first.source == "llm"
    assert second.source == "llm-cache"
    assert second.error_type is first.error_type


def test_cache_survives_a_new_classifier_instance(store, fake_anthropic):
    client = StubClient(response=StubResponse(VALID_VERDICT))
    LLMClassifier(cache=store, client=client).classify(unknown_job(), regex_of(unknown_job()))

    other = StubClient(response=StubResponse(VALID_VERDICT))
    result = LLMClassifier(cache=store, client=other).classify(unknown_job(), regex_of(unknown_job()))

    assert other.calls == []
    assert result.source == "llm-cache"


# ============================================================
# التكامل مع المحلل
# ============================================================


def test_analyzer_calls_llm_only_for_unknown(store, fake_anthropic):
    client = StubClient(response=StubResponse(VALID_VERDICT))
    analyzer = ErrorAnalyzer(llm_classifier=LLMClassifier(cache=store, client=client))

    known = failing_job(error="permission denied")
    result = analyzer.analyze(known)

    assert result.error_type is ErrorType.PERMISSION_DENIED
    assert client.calls == []  # النمط عرفه، فلا داعي لأي نداء

    analyzer.analyze(unknown_job())
    assert len(client.calls) == 1


def test_analyzer_use_llm_false_skips_classifier(store, fake_anthropic):
    client = StubClient(response=StubResponse(VALID_VERDICT))
    analyzer = ErrorAnalyzer(llm_classifier=LLMClassifier(cache=store, client=client))
    analyzer.analyze(unknown_job(), use_llm=False)
    assert client.calls == []

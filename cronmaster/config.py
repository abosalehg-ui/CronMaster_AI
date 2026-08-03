# -*- coding: utf-8 -*-
"""الإعدادات المركزية وإعداد التسجيل."""

import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List

# أذونات مقيدة: مجلد العمل للمالك فقط، والملفات قد تحتوي حمولات مهام فيها أسرار
DIR_MODE = 0o700
FILE_MODE = 0o600


class Config:
    """إعدادات النظام المركزية.

    القيم الافتراضية أدناه، ويمكن تجاوزها من:
    1. ملف ~/.cronmaster/config.json (مفاتيح بأحرف صغيرة، مثل "alert_threshold")
    2. متغيرات البيئة CRONMASTER_<KEY> (لها الأولوية على الملف)
    """

    # مجلد العمل
    WORK_DIR = Path.home() / ".cronmaster"
    BACKUP_DIR = WORK_DIR / "backups"
    REPORTS_DIR = WORK_DIR / "reports"
    STATE_FILE = WORK_DIR / "state.json"
    CONFIG_FILE = WORK_DIR / "config.json"

    # إعدادات التنبيهات
    ALERT_THRESHOLD = 2  # عدد الفشل المتتالي قبل التنبيه
    ALERT_COOLDOWN_HOURS = 24  # لا يُكرر نفس التنبيه لنفس المهمة قبل مرور هذه المدة

    # إعدادات الإصلاح التلقائي
    AUTO_FIX_TIMEOUT = True  # زيادة timeout تلقائياً عند فشل timeout
    TIMEOUT_INCREMENT = 120  # زيادة 120 ثانية
    MAX_TIMEOUT = 900  # حد أقصى 15 دقيقة
    AUTO_RETRY = True  # إعادة التشغيل تلقائياً بعد الإصلاح

    # إعادة المحاولة للأخطاء العابرة (network_error / api_error)
    AUTO_RETRY_TRANSIENT = True  # إعادة تشغيل تلقائية مسقوفة للأخطاء العابرة
    MAX_RETRIES = 3  # حد أقصى لعدد إعادات المحاولة قبل التصعيد للمستخدم
    RETRY_BACKOFF_HOURS = 1  # لأخطاء الـ API (429): لا تُعاد المحاولة قبل مرور هذه المدة على آخر تشغيل

    # كشف المهام الصامتة: مهمة مفعّلة فات موعدها المجدول بأكثر من هذه المدة
    SILENT_GRACE_HOURS = 6

    # Telegram — يُضبط في config.json أو CRONMASTER_TELEGRAM_CHAT_ID
    TELEGRAM_CHAT_ID = ""

    # ---- إعدادات أُضيفت في الإصدار 3 ----

    # الواجهة الخلفية للمهام: openclaw (افتراضي) أو crontab
    BACKEND = "openclaw"
    # لغة المخرجات: ar (افتراضي) أو en
    LANG = "ar"

    # السجل التاريخي (SQLite)
    HISTORY_ENABLED = True
    HISTORY_RETENTION_DAYS = 90

    # كشف تدهور مدة التنفيذ قبل أن يتحول إلى انتهاء مهلة
    DURATION_REGRESSION_FACTOR = 2.0
    DURATION_REGRESSION_MIN_SAMPLES = 5

    # حارس الإصلاح غير المجدي: بعد هذا العدد من رفعات المهلة نتوقف ونصعّد
    MAX_TIMEOUT_FIXES = 3

    # قاطع الدائرة: تعطيل المهمة بعد فشل متتالٍ طويل بخطأ غير قابل للإصلاح
    CIRCUIT_BREAKER_ENABLED = False
    CIRCUIT_BREAKER_THRESHOLD = 10

    # التراجع عن رفع المهلة إذا لم يُجدِ
    ROLLBACK_TIMEOUT_ENABLED = False
    ROLLBACK_AFTER_CYCLES = 2

    # إعادة الجدولة عند تكرار أخطاء حد الطلبات
    AUTO_RESCHEDULE = False
    RESCHEDULE_SHIFT_MINUTES = 17
    RESCHEDULE_AFTER_ERRORS = 3

    # المصنّف الاختياري بالذكاء الاصطناعي (Anthropic SDK)
    LLM_ENABLED = False
    LLM_MODEL = "claude-opus-5"
    LLM_MIN_CONFIDENCE = 0.8
    LLM_CACHE_DAYS = 30

    # قنوات التنبيه: [{"type": "telegram", "chat_id": "..."}, {"type": "slack", "url": "..."}]
    NOTIFIERS: List[Dict[str, Any]] = []
    # فترة الهدوء: {"from": "22:00", "to": "07:00", "tz": "Asia/Riyadh"}
    QUIET_HOURS: Dict[str, Any] = {}

    # التكامل مع أنظمة المراقبة
    HEALTHCHECK_PING_URL = ""
    PROMETHEUS_TEXTFILE = ""

    _INT_KEYS = frozenset(
        {
            "ALERT_THRESHOLD",
            "ALERT_COOLDOWN_HOURS",
            "TIMEOUT_INCREMENT",
            "MAX_TIMEOUT",
            "MAX_RETRIES",
            "RETRY_BACKOFF_HOURS",
            "SILENT_GRACE_HOURS",
            "HISTORY_RETENTION_DAYS",
            "DURATION_REGRESSION_MIN_SAMPLES",
            "MAX_TIMEOUT_FIXES",
            "CIRCUIT_BREAKER_THRESHOLD",
            "ROLLBACK_AFTER_CYCLES",
            "RESCHEDULE_SHIFT_MINUTES",
            "RESCHEDULE_AFTER_ERRORS",
            "LLM_CACHE_DAYS",
        }
    )
    _FLOAT_KEYS = frozenset({"DURATION_REGRESSION_FACTOR", "LLM_MIN_CONFIDENCE"})
    _BOOL_KEYS = frozenset(
        {
            "AUTO_FIX_TIMEOUT",
            "AUTO_RETRY",
            "AUTO_RETRY_TRANSIENT",
            "HISTORY_ENABLED",
            "CIRCUIT_BREAKER_ENABLED",
            "ROLLBACK_TIMEOUT_ENABLED",
            "AUTO_RESCHEDULE",
            "LLM_ENABLED",
        }
    )
    _STR_KEYS = frozenset(
        {
            "TELEGRAM_CHAT_ID",
            "BACKEND",
            "LANG",
            "LLM_MODEL",
            "HEALTHCHECK_PING_URL",
            "PROMETHEUS_TEXTFILE",
        }
    )
    _LIST_KEYS = frozenset({"NOTIFIERS"})
    _DICT_KEYS = frozenset({"QUIET_HOURS"})

    # مفاتيح غير معروفة صادفناها في آخر تحميل — يستخدمها أمر doctor
    unknown_keys: List[str] = []

    # ------------------------------------------------------------
    # مسارات مشتقة: تُحسب وقت الاستدعاء لا وقت الاستيراد، حتى يكفي
    # تعديل WORK_DIR (في الاختبارات مثلاً) لإعادة توجيه كل شيء
    # ------------------------------------------------------------

    @classmethod
    def history_db(cls) -> Path:
        return cls.WORK_DIR / "history.db"

    @classmethod
    def lock_file(cls) -> Path:
        return cls.WORK_DIR / "cronmaster.lock"

    @classmethod
    def log_file(cls) -> Path:
        return cls.WORK_DIR / "cronmaster.log"

    @classmethod
    def configurable_keys(cls) -> frozenset:
        return cls._INT_KEYS | cls._FLOAT_KEYS | cls._BOOL_KEYS | cls._STR_KEYS | cls._LIST_KEYS | cls._DICT_KEYS

    @classmethod
    def init_dirs(cls):
        """إنشاء المجلدات المطلوبة بأذونات مقيدة على المالك"""
        for directory in (cls.WORK_DIR, cls.BACKUP_DIR, cls.REPORTS_DIR):
            directory.mkdir(parents=True, exist_ok=True)
            _chmod_quiet(directory, DIR_MODE)

    @classmethod
    def load(cls):
        """تحميل الإعدادات من الملف ثم من متغيرات البيئة"""
        logger = logging.getLogger(__name__)
        configurable = cls.configurable_keys()
        cls.unknown_keys = []

        if cls.CONFIG_FILE.exists():
            try:
                data = json.loads(cls.CONFIG_FILE.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                logger.warning("تعذر قراءة %s: %s", cls.CONFIG_FILE, e)
                data = {}
            if not isinstance(data, dict):
                logger.warning("محتوى %s ليس كائن JSON — سيُتجاهل", cls.CONFIG_FILE)
                data = {}
            for key, value in data.items():
                attr = key.upper()
                if attr in configurable:
                    setattr(cls, attr, value)
                else:
                    cls.unknown_keys.append(key)
                    logger.warning("مفتاح إعدادات غير معروف في config.json: %s", key)

        for attr in configurable:
            env_value = os.environ.get(f"CRONMASTER_{attr}")
            if env_value is None:
                continue
            if attr in cls._INT_KEYS:
                try:
                    setattr(cls, attr, int(env_value))
                except ValueError:
                    logger.warning("قيمة غير رقمية في CRONMASTER_%s: %s", attr, env_value)
            elif attr in cls._FLOAT_KEYS:
                try:
                    setattr(cls, attr, float(env_value))
                except ValueError:
                    logger.warning("قيمة غير رقمية في CRONMASTER_%s: %s", attr, env_value)
            elif attr in cls._BOOL_KEYS:
                setattr(cls, attr, env_value.strip().lower() in ("1", "true", "yes"))
            elif attr in cls._LIST_KEYS or attr in cls._DICT_KEYS:
                try:
                    setattr(cls, attr, json.loads(env_value))
                except json.JSONDecodeError:
                    logger.warning("قيمة ليست JSON صالحاً في CRONMASTER_%s", attr)
            else:
                setattr(cls, attr, env_value)

        cls.validate()

    @classmethod
    def validate(cls):
        """تصحيح القيم المستحيلة بهدوء بدل السقوط لاحقاً في منتصف دورة مراقبة"""
        logger = logging.getLogger(__name__)

        def _clamp(attr: str, minimum, default):
            value = getattr(cls, attr)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < minimum:
                logger.warning("قيمة غير صالحة لـ %s (%r) — أُعيدت إلى %r", attr, value, default)
                setattr(cls, attr, default)

        _clamp("ALERT_THRESHOLD", 1, 2)
        _clamp("ALERT_COOLDOWN_HOURS", 0, 24)
        _clamp("TIMEOUT_INCREMENT", 1, 120)
        _clamp("MAX_TIMEOUT", 1, 900)
        _clamp("MAX_RETRIES", 0, 3)
        _clamp("RETRY_BACKOFF_HOURS", 0, 1)
        _clamp("SILENT_GRACE_HOURS", 0, 6)
        _clamp("HISTORY_RETENTION_DAYS", 1, 90)
        _clamp("DURATION_REGRESSION_FACTOR", 1.0, 2.0)
        _clamp("DURATION_REGRESSION_MIN_SAMPLES", 2, 5)
        _clamp("MAX_TIMEOUT_FIXES", 1, 3)
        _clamp("CIRCUIT_BREAKER_THRESHOLD", 1, 10)
        _clamp("ROLLBACK_AFTER_CYCLES", 1, 2)
        _clamp("RESCHEDULE_SHIFT_MINUTES", 1, 17)
        _clamp("RESCHEDULE_AFTER_ERRORS", 1, 3)
        _clamp("LLM_CACHE_DAYS", 1, 30)

        if not isinstance(cls.LLM_MIN_CONFIDENCE, (int, float)) or not 0.0 <= cls.LLM_MIN_CONFIDENCE <= 1.0:
            logger.warning("llm_min_confidence يجب أن تكون بين 0 و 1 — أُعيدت إلى 0.8")
            cls.LLM_MIN_CONFIDENCE = 0.8

        if not isinstance(cls.NOTIFIERS, list):
            logger.warning("notifiers يجب أن تكون قائمة — أُهملت")
            cls.NOTIFIERS = []
        if not isinstance(cls.QUIET_HOURS, dict):
            logger.warning("quiet_hours يجب أن تكون كائناً — أُهملت")
            cls.QUIET_HOURS = {}


def _chmod_quiet(path: Path, mode: int) -> None:
    """ضبط الأذونات مع تجاهل الأنظمة التي لا تدعمها (Windows مثلاً)"""
    try:
        os.chmod(path, mode)
    except (OSError, NotImplementedError):
        logging.getLogger(__name__).debug("تعذر ضبط أذونات %s", path)


def write_secure(path: Path, text: str) -> None:
    """كتابة ملف بأذونات 0600 — قد يحوي حمولات مهام فيها أسرار"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    _chmod_quiet(path, FILE_MODE)


def setup_logging():
    """إعداد التسجيل مع تدوير للملف حتى لا ينمو بلا حدود"""
    log_file = Config.log_file()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    _chmod_quiet(log_file, FILE_MODE)

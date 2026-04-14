#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CronMaster_AI - مدير ومراقب ذكي للمهام المجدولة
==================================================

نظام متكامل لـ:
- مراقبة OpenClaw Cron Jobs واكتشاف الفشل لحظياً
- تحليل الأخطاء وتصنيفها
- الإصلاح الذاتي (زيادة timeout، إعادة التشغيل)
- التنبيهات الفورية عبر Telegram
- النسخ الاحتياطي والتقارير

المطور: Pipbot لـ عبدالكريم
التاريخ: 2026-04-13
التحديث: 2026-04-14 - دعم OpenClaw cron
"""

import os
import re
import sys
import json
import subprocess
import hashlib
import difflib
import logging
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any, Tuple
from enum import Enum
from collections import defaultdict
import shutil

# ============================================================
# الإعدادات والثوابت
# ============================================================

class Config:
    """إعدادات النظام المركزية"""
    
    # مجلد العمل
    WORK_DIR = Path.home() / ".cronmaster"
    BACKUP_DIR = WORK_DIR / "backups"
    REPORTS_DIR = WORK_DIR / "reports"
    STATE_FILE = WORK_DIR / "state.json"
    
    # إعدادات التنبيهات
    ALERT_THRESHOLD = 2  # عدد الفشل المتتالي قبل التنبيه
    
    # إعدادات الإصلاح التلقائي
    AUTO_FIX_TIMEOUT = True  # زيادة timeout تلقائياً عند فشل timeout
    TIMEOUT_INCREMENT = 60  # زيادة 60 ثانية
    MAX_TIMEOUT = 300  # حد أقصى 5 دقائق
    AUTO_RETRY = True  # إعادة التشغيل تلقائياً بعد الإصلاح
    
    # Telegram
    TELEGRAM_CHAT_ID = "6803381"
    
    @classmethod
    def init_dirs(cls):
        """إنشاء المجلدات المطلوبة"""
        cls.WORK_DIR.mkdir(parents=True, exist_ok=True)
        cls.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        cls.REPORTS_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# تصنيفات الأخطاء
# ============================================================

class ErrorType(Enum):
    """أنواع الأخطاء المعروفة"""
    TIMEOUT = "timeout"
    PERMISSION_DENIED = "permission_denied"
    NOT_FOUND = "not_found"
    DEPENDENCY_ERROR = "dependency_error"
    SYNTAX_ERROR = "syntax_error"
    NETWORK_ERROR = "network_error"
    DISK_FULL = "disk_full"
    MEMORY_ERROR = "memory_error"
    API_ERROR = "api_error"
    UNKNOWN = "unknown"


@dataclass
class ErrorSignature:
    """توقيع الخطأ للتعرف عليه"""
    pattern: str
    error_type: ErrorType
    description_ar: str
    suggested_fix: str
    auto_fixable: bool = False


# قاعدة بيانات الأخطاء المعروفة
ERROR_DATABASE = [
    ErrorSignature(
        pattern=r"timeout|timed out|Request timed out|deadline exceeded",
        error_type=ErrorType.TIMEOUT,
        description_ar="انتهاء المهلة الزمنية - العملية أخذت وقت أطول من المسموح",
        suggested_fix="زيادة timeout",
        auto_fixable=True
    ),
    ErrorSignature(
        pattern=r"permission denied|EACCES|Operation not permitted",
        error_type=ErrorType.PERMISSION_DENIED,
        description_ar="نقص في الصلاحيات",
        suggested_fix="تحقق من صلاحيات الملف أو شغّل بـ sudo",
        auto_fixable=False
    ),
    ErrorSignature(
        pattern=r"not found|No such file|ENOENT|command not found",
        error_type=ErrorType.NOT_FOUND,
        description_ar="ملف أو أمر غير موجود",
        suggested_fix="تحقق من المسار الكامل",
        auto_fixable=False
    ),
    ErrorSignature(
        pattern=r"ModuleNotFoundError|ImportError|cannot find module",
        error_type=ErrorType.DEPENDENCY_ERROR,
        description_ar="مكتبة أو موديول ناقص",
        suggested_fix="ثبّت المكتبة باستخدام pip install",
        auto_fixable=False
    ),
    ErrorSignature(
        pattern=r"rate.?limit|429|too many requests",
        error_type=ErrorType.API_ERROR,
        description_ar="تجاوز حد الطلبات (Rate Limit)",
        suggested_fix="انتظر قبل المحاولة مجدداً",
        auto_fixable=False
    ),
    ErrorSignature(
        pattern=r"Connection refused|Network unreachable|DNS|ENETUNREACH",
        error_type=ErrorType.NETWORK_ERROR,
        description_ar="مشكلة في الاتصال بالشبكة",
        suggested_fix="تحقق من الاتصال بالإنترنت",
        auto_fixable=False
    ),
    ErrorSignature(
        pattern=r"No space left|ENOSPC|disk full",
        error_type=ErrorType.DISK_FULL,
        description_ar="القرص ممتلئ",
        suggested_fix="احذف ملفات غير ضرورية",
        auto_fixable=False
    ),
]


# ============================================================
# هياكل البيانات
# ============================================================

@dataclass
class OpenClawJob:
    """مهمة OpenClaw cron"""
    id: str
    name: str
    enabled: bool
    schedule: str
    last_status: Optional[str] = None
    last_error: Optional[str] = None
    last_error_reason: Optional[str] = None
    consecutive_errors: int = 0
    timeout_seconds: Optional[int] = None
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "schedule": self.schedule,
            "last_status": self.last_status,
            "last_error": self.last_error,
            "last_error_reason": self.last_error_reason,
            "consecutive_errors": self.consecutive_errors,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass
class FailureAnalysis:
    """تحليل فشل مهمة"""
    job: OpenClawJob
    error_type: ErrorType
    description: str
    suggested_fix: str
    auto_fixable: bool = False
    fix_applied: bool = False
    fix_details: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            "job_id": self.job.id,
            "job_name": self.job.name,
            "error_type": self.error_type.value,
            "description": self.description,
            "suggested_fix": self.suggested_fix,
            "auto_fixable": self.auto_fixable,
            "fix_applied": self.fix_applied,
            "fix_details": self.fix_details,
        }


# ============================================================
# محلل OpenClaw Cron
# ============================================================

class OpenClawCronParser:
    """قراءة وتحليل OpenClaw cron jobs"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def get_all_jobs(self) -> List[OpenClawJob]:
        """جلب جميع المهام"""
        try:
            result = subprocess.run(
                ["openclaw", "cron", "list", "--json"],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                self.logger.error(f"فشل جلب المهام: {result.stderr}")
                return []
            
            data = json.loads(result.stdout)
            # OpenClaw يرجع {"jobs": [...]} أو [...]
            jobs_data = data.get("jobs", data) if isinstance(data, dict) else data
            jobs = []
            
            for job in jobs_data:
                # استخراج timeout من payload
                timeout = None
                payload = job.get("payload", {})
                if isinstance(payload, dict):
                    timeout = payload.get("timeoutSeconds")
                
                # استخراج الجدولة
                schedule = job.get("schedule", {})
                schedule_str = schedule.get("expr", "unknown")
                
                # استخراج الحالة
                state = job.get("state", {})
                
                # تحويل timestamps
                last_run = None
                if state.get("lastRunAtMs"):
                    last_run = datetime.fromtimestamp(state["lastRunAtMs"] / 1000)
                
                next_run = None
                if state.get("nextRunAtMs"):
                    next_run = datetime.fromtimestamp(state["nextRunAtMs"] / 1000)
                
                jobs.append(OpenClawJob(
                    id=job.get("id", ""),
                    name=job.get("name", "Unknown"),
                    enabled=job.get("enabled", False),
                    schedule=schedule_str,
                    last_status=state.get("lastStatus"),
                    last_error=state.get("lastError"),
                    last_error_reason=state.get("lastErrorReason"),
                    consecutive_errors=state.get("consecutiveErrors", 0),
                    timeout_seconds=timeout,
                    last_run_at=last_run,
                    next_run_at=next_run,
                ))
            
            return jobs
            
        except subprocess.TimeoutExpired:
            self.logger.error("Timeout عند جلب المهام")
            return []
        except json.JSONDecodeError as e:
            self.logger.error(f"خطأ في تحليل JSON: {e}")
            return []
        except Exception as e:
            self.logger.error(f"خطأ غير متوقع: {e}")
            return []
    
    def get_failed_jobs(self) -> List[OpenClawJob]:
        """جلب المهام الفاشلة فقط"""
        all_jobs = self.get_all_jobs()
        return [j for j in all_jobs if j.last_status == "error" and j.enabled]
    
    def get_critical_jobs(self, threshold: int = None) -> List[OpenClawJob]:
        """جلب المهام الحرجة (فشل متتالي)"""
        threshold = threshold or Config.ALERT_THRESHOLD
        all_jobs = self.get_all_jobs()
        return [j for j in all_jobs if j.consecutive_errors >= threshold and j.enabled]


# ============================================================
# محلل الأخطاء
# ============================================================

class ErrorAnalyzer:
    """تحليل وتصنيف الأخطاء"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def analyze(self, job: OpenClawJob) -> FailureAnalysis:
        """تحليل سبب الفشل"""
        error_text = job.last_error or job.last_error_reason or ""
        
        # البحث عن نمط معروف
        for sig in ERROR_DATABASE:
            if re.search(sig.pattern, error_text, re.IGNORECASE):
                return FailureAnalysis(
                    job=job,
                    error_type=sig.error_type,
                    description=sig.description_ar,
                    suggested_fix=sig.suggested_fix,
                    auto_fixable=sig.auto_fixable
                )
        
        # خطأ غير معروف
        return FailureAnalysis(
            job=job,
            error_type=ErrorType.UNKNOWN,
            description="خطأ غير مصنف",
            suggested_fix="راجع السجلات",
            auto_fixable=False
        )


# ============================================================
# مصلح الأخطاء التلقائي
# ============================================================

class AutoFixer:
    """إصلاح الأخطاء تلقائياً"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def fix(self, analysis: FailureAnalysis) -> FailureAnalysis:
        """محاولة إصلاح الخطأ تلقائياً"""
        if not analysis.auto_fixable:
            return analysis
        
        if analysis.error_type == ErrorType.TIMEOUT:
            return self._fix_timeout(analysis)
        
        return analysis
    
    def _fix_timeout(self, analysis: FailureAnalysis) -> FailureAnalysis:
        """إصلاح مشكلة Timeout بزيادة المهلة"""
        job = analysis.job
        current_timeout = job.timeout_seconds or 30
        new_timeout = min(current_timeout + Config.TIMEOUT_INCREMENT, Config.MAX_TIMEOUT)
        
        if new_timeout == current_timeout:
            analysis.fix_details = f"الـ timeout وصل الحد الأقصى ({Config.MAX_TIMEOUT}s)"
            return analysis
        
        try:
            result = subprocess.run(
                ["openclaw", "cron", "edit", job.id, "--timeout-seconds", str(new_timeout)],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                analysis.fix_applied = True
                analysis.fix_details = f"تم زيادة timeout من {current_timeout}s إلى {new_timeout}s"
                self.logger.info(f"✅ {job.name}: {analysis.fix_details}")
            else:
                analysis.fix_details = f"فشل تحديث timeout: {result.stderr}"
                self.logger.error(analysis.fix_details)
                
        except Exception as e:
            analysis.fix_details = f"خطأ: {e}"
            self.logger.error(analysis.fix_details)
        
        return analysis
    
    def retry_job(self, job_id: str) -> bool:
        """إعادة تشغيل مهمة"""
        try:
            result = subprocess.run(
                ["openclaw", "cron", "run", job_id],
                capture_output=True,
                text=True,
                timeout=30
            )
            return result.returncode == 0
        except Exception as e:
            self.logger.error(f"فشل إعادة التشغيل: {e}")
            return False


# ============================================================
# نظام التنبيهات
# ============================================================

class AlertManager:
    """إدارة التنبيهات"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def send_telegram(self, message: str) -> bool:
        """إرسال تنبيه عبر OpenClaw message tool"""
        try:
            # استخدام openclaw CLI لإرسال الرسالة
            result = subprocess.run(
                [
                    "openclaw", "message", "send",
                    "--channel", "telegram",
                    "--to", Config.TELEGRAM_CHAT_ID,
                    "--message", message
                ],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                self.logger.info("تم إرسال التنبيه")
                return True
            else:
                self.logger.error(f"فشل الإرسال: {result.stderr}")
                return False
                
        except Exception as e:
            self.logger.error(f"خطأ في الإرسال: {e}")
            return False
    
    def format_alert(self, analyses: List[FailureAnalysis]) -> str:
        """تنسيق رسالة التنبيه"""
        lines = ["🚨 *تنبيه CronMaster*\n"]
        
        for a in analyses:
            emoji = "✅" if a.fix_applied else "❌"
            lines.append(f"{emoji} *{a.job.name}*")
            lines.append(f"   نوع الخطأ: {a.error_type.value}")
            lines.append(f"   التحليل: {a.description}")
            
            if a.fix_applied:
                lines.append(f"   🔧 الإصلاح: {a.fix_details}")
            else:
                lines.append(f"   💡 الحل: {a.suggested_fix}")
            
            lines.append("")
        
        return "\n".join(lines)


# ============================================================
# مولد التقارير
# ============================================================

class ReportGenerator:
    """توليد التقارير"""
    
    def __init__(self, reports_dir: Path = None):
        self.reports_dir = reports_dir or Config.REPORTS_DIR
        self.reports_dir.mkdir(parents=True, exist_ok=True)
    
    def generate_report(
        self,
        jobs: List[OpenClawJob],
        analyses: List[FailureAnalysis],
        format: str = "markdown"
    ) -> Path:
        """توليد تقرير"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if format == "json":
            return self._generate_json(jobs, analyses, timestamp)
        else:
            return self._generate_markdown(jobs, analyses, timestamp)
    
    def _generate_markdown(
        self,
        jobs: List[OpenClawJob],
        analyses: List[FailureAnalysis],
        timestamp: str
    ) -> Path:
        """توليد تقرير Markdown"""
        report_file = self.reports_dir / f"report_{timestamp}.md"
        
        ok_jobs = [j for j in jobs if j.last_status == "ok"]
        failed_jobs = [j for j in jobs if j.last_status == "error"]
        idle_jobs = [j for j in jobs if j.last_status in (None, "idle")]
        
        content = f"""# 📊 تقرير CronMaster

**التاريخ:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

---

## 📈 الإحصائيات

| المقياس | القيمة |
|---------|--------|
| إجمالي المهام | {len(jobs)} |
| ناجحة ✅ | {len(ok_jobs)} |
| فاشلة ❌ | {len(failed_jobs)} |
| معلقة ⏸️ | {len(idle_jobs)} |

---

## ❌ المهام الفاشلة ({len(failed_jobs)})

"""
        
        if not failed_jobs:
            content += "_لا توجد مهام فاشلة 🎉_\n"
        else:
            for a in analyses:
                fix_status = "✅ تم الإصلاح" if a.fix_applied else "⏳ يحتاج مراجعة"
                content += f"""### {a.job.name}
- **الخطأ:** {a.error_type.value}
- **التحليل:** {a.description}
- **الحل:** {a.suggested_fix}
- **الحالة:** {fix_status}
{f"- **تفاصيل الإصلاح:** {a.fix_details}" if a.fix_details else ""}

"""
        
        content += f"""
---

## ✅ المهام الناجحة ({len(ok_jobs)})

"""
        for j in ok_jobs[:10]:
            content += f"- {j.name}\n"
        
        if len(ok_jobs) > 10:
            content += f"- _... و {len(ok_jobs) - 10} مهمة أخرى_\n"
        
        content += "\n---\n_تم التوليد بواسطة CronMaster_AI_\n"
        
        report_file.write_text(content, encoding="utf-8")
        return report_file
    
    def _generate_json(
        self,
        jobs: List[OpenClawJob],
        analyses: List[FailureAnalysis],
        timestamp: str
    ) -> Path:
        """توليد تقرير JSON"""
        report_file = self.reports_dir / f"report_{timestamp}.json"
        
        data = {
            "timestamp": datetime.now().isoformat(),
            "summary": {
                "total": len(jobs),
                "ok": len([j for j in jobs if j.last_status == "ok"]),
                "error": len([j for j in jobs if j.last_status == "error"]),
                "idle": len([j for j in jobs if j.last_status in (None, "idle")]),
            },
            "jobs": [j.to_dict() for j in jobs],
            "analyses": [a.to_dict() for a in analyses],
        }
        
        report_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return report_file


# ============================================================
# مدير الحالة
# ============================================================

class StateManager:
    """إدارة حالة المراقبة"""
    
    def __init__(self, state_file: Path = None):
        self.state_file = state_file or Config.STATE_FILE
        self.state = self._load()
    
    def _load(self) -> dict:
        """تحميل الحالة"""
        if self.state_file.exists():
            try:
                return json.loads(self.state_file.read_text())
            except:
                pass
        return {"fixes_applied": [], "last_run": None}
    
    def save(self):
        """حفظ الحالة"""
        self.state["last_run"] = datetime.now().isoformat()
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(self.state, indent=2, ensure_ascii=False))
    
    def record_fix(self, job_id: str, fix_type: str, details: str):
        """تسجيل إصلاح"""
        self.state["fixes_applied"].append({
            "job_id": job_id,
            "fix_type": fix_type,
            "details": details,
            "timestamp": datetime.now().isoformat()
        })
        # الاحتفاظ بآخر 100 إصلاح
        self.state["fixes_applied"] = self.state["fixes_applied"][-100:]
        self.save()


# ============================================================
# المحرك الرئيسي
# ============================================================

class CronMaster:
    """المحرك الرئيسي"""
    
    def __init__(self):
        Config.init_dirs()
        
        self.parser = OpenClawCronParser()
        self.analyzer = ErrorAnalyzer()
        self.fixer = AutoFixer()
        self.alerter = AlertManager()
        self.reporter = ReportGenerator()
        self.state = StateManager()
        
        self._setup_logging()
    
    def _setup_logging(self):
        """إعداد التسجيل"""
        log_file = Config.WORK_DIR / "cronmaster.log"
        
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[
                logging.FileHandler(log_file, encoding="utf-8"),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
    
    def monitor(self, auto_fix: bool = True, alert: bool = True, retry: bool = True) -> Dict[str, Any]:
        """مراقبة وإصلاح المهام"""
        self.logger.info("بدء المراقبة...")
        
        # جلب المهام الفاشلة
        failed_jobs = self.parser.get_failed_jobs()
        all_jobs = self.parser.get_all_jobs()
        
        analyses = []
        fixes_applied = 0
        retries = 0
        
        for job in failed_jobs:
            # تحليل الخطأ
            analysis = self.analyzer.analyze(job)
            
            # محاولة الإصلاح التلقائي
            if auto_fix and analysis.auto_fixable:
                analysis = self.fixer.fix(analysis)
                
                if analysis.fix_applied:
                    fixes_applied += 1
                    self.state.record_fix(job.id, analysis.error_type.value, analysis.fix_details)
                    
                    # إعادة التشغيل بعد الإصلاح
                    if retry and Config.AUTO_RETRY:
                        if self.fixer.retry_job(job.id):
                            retries += 1
                            self.logger.info(f"🔄 أعيد تشغيل: {job.name}")
            
            analyses.append(analysis)
        
        # إرسال تنبيه إذا فيه فشل
        if alert and analyses:
            message = self.alerter.format_alert(analyses)
            self.alerter.send_telegram(message)
        
        self.state.save()
        
        result = {
            "timestamp": datetime.now().isoformat(),
            "total_jobs": len(all_jobs),
            "failed_jobs": len(failed_jobs),
            "fixes_applied": fixes_applied,
            "retries": retries,
            "analyses": [a.to_dict() for a in analyses]
        }
        
        self.logger.info(f"انتهت المراقبة: {len(failed_jobs)} فشل، {fixes_applied} إصلاح، {retries} إعادة تشغيل")
        return result
    
    def status(self) -> Dict[str, Any]:
        """حالة سريعة"""
        jobs = self.parser.get_all_jobs()
        
        ok = len([j for j in jobs if j.last_status == "ok"])
        error = len([j for j in jobs if j.last_status == "error"])
        critical = len(self.parser.get_critical_jobs())
        
        return {
            "total_jobs": len(jobs),
            "ok": ok,
            "error": error,
            "critical": critical,
            "success_rate": (ok / len(jobs) * 100) if jobs else 100
        }
    
    def report(self, format: str = "markdown") -> Path:
        """توليد تقرير"""
        jobs = self.parser.get_all_jobs()
        failed = self.parser.get_failed_jobs()
        analyses = [self.analyzer.analyze(j) for j in failed]
        
        return self.reporter.generate_report(jobs, analyses, format)
    
    def list_jobs(self) -> List[Dict]:
        """قائمة المهام"""
        jobs = self.parser.get_all_jobs()
        return [j.to_dict() for j in jobs]
    
    def fix_job(self, job_id: str, retry: bool = True) -> Dict[str, Any]:
        """إصلاح مهمة محددة"""
        jobs = self.parser.get_all_jobs()
        job = next((j for j in jobs if j.id == job_id), None)
        
        if not job:
            return {"error": "المهمة غير موجودة"}
        
        analysis = self.analyzer.analyze(job)
        
        if analysis.auto_fixable:
            analysis = self.fixer.fix(analysis)
            
            if analysis.fix_applied and retry:
                self.fixer.retry_job(job_id)
        
        return analysis.to_dict()


# ============================================================
# واجهة سطر الأوامر
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="CronMaster_AI - مدير ذكي لـ OpenClaw Cron Jobs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
أمثلة:
  python CronMaster_AI.py monitor          # مراقبة وإصلاح تلقائي
  python CronMaster_AI.py monitor --no-fix # مراقبة بدون إصلاح
  python CronMaster_AI.py status           # حالة سريعة
  python CronMaster_AI.py report           # توليد تقرير
  python CronMaster_AI.py list             # قائمة المهام
  python CronMaster_AI.py fix <job_id>     # إصلاح مهمة محددة
"""
    )
    
    subparsers = parser.add_subparsers(dest="command", help="الأوامر")
    
    # monitor
    mon = subparsers.add_parser("monitor", help="مراقبة وإصلاح")
    mon.add_argument("--no-fix", action="store_true", help="بدون إصلاح تلقائي")
    mon.add_argument("--no-alert", action="store_true", help="بدون تنبيهات")
    mon.add_argument("--no-retry", action="store_true", help="بدون إعادة تشغيل")
    
    # status
    subparsers.add_parser("status", help="حالة سريعة")
    
    # report
    rep = subparsers.add_parser("report", help="توليد تقرير")
    rep.add_argument("--format", "-f", choices=["markdown", "json"], default="markdown")
    
    # list
    subparsers.add_parser("list", help="قائمة المهام")
    
    # fix
    fix = subparsers.add_parser("fix", help="إصلاح مهمة")
    fix.add_argument("job_id", help="معرف المهمة")
    fix.add_argument("--no-retry", action="store_true", help="بدون إعادة تشغيل")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    master = CronMaster()
    
    if args.command == "monitor":
        result = master.monitor(
            auto_fix=not args.no_fix,
            alert=not args.no_alert,
            retry=not args.no_retry
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
    
    elif args.command == "status":
        result = master.status()
        print("=" * 40)
        print("📊 حالة OpenClaw Cron Jobs")
        print("=" * 40)
        print(f"إجمالي المهام:   {result['total_jobs']}")
        print(f"ناجحة ✅:        {result['ok']}")
        print(f"فاشلة ❌:        {result['error']}")
        print(f"حرجة ⚠️:         {result['critical']}")
        print(f"نسبة النجاح:     {result['success_rate']:.1f}%")
        print("=" * 40)
    
    elif args.command == "report":
        path = master.report(format=args.format)
        print(f"✅ التقرير: {path}")
    
    elif args.command == "list":
        jobs = master.list_jobs()
        print(json.dumps(jobs, indent=2, ensure_ascii=False))
    
    elif args.command == "fix":
        result = master.fix_job(args.job_id, retry=not args.no_retry)
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

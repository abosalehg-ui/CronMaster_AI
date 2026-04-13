#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CronMaster_AI - مدير ومراقب ذكي للمهام المجدولة
==================================================

نظام متكامل لـ:
- مراقبة Cron Jobs واكتشاف الفشل لحظياً
- تحليل الأخطاء وتصنيفها
- البحث التلقائي عن حلول
- الإصلاح الذاتي مع النسخ الاحتياطي
- التنبيهات الفورية عبر Telegram/Discord/Slack

المطور: Pipbot لـ عبدالكريم
التاريخ: 2026-04-13
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
import tempfile
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
    
    # مسارات السجلات
    LOG_PATHS = [
        "/var/log/syslog",
        "/var/log/cron",
        "/var/log/cron.log",
        "/var/log/messages",
    ]
    
    # مجلد العمل
    WORK_DIR = Path.home() / ".cronmaster"
    BACKUP_DIR = WORK_DIR / "backups"
    REPORTS_DIR = WORK_DIR / "reports"
    STATE_FILE = WORK_DIR / "state.json"
    
    # إعدادات التنبيهات
    ALERT_THRESHOLD = 3  # عدد الفشل المتتالي قبل التنبيه
    
    # أنماط البحث في السجلات
    CRON_PATTERNS = [
        r"CRON\[(\d+)\].*\((\w+)\)\s+CMD\s+\((.+)\)",  # تنفيذ عادي
        r"cron\.err.*?:\s+(.+)",  # خطأ
        r"CRON\[(\d+)\].*exit status (\d+)",  # كود الخروج
    ]
    
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
    PERMISSION_DENIED = "permission_denied"
    TIMEOUT = "timeout"
    NOT_FOUND = "not_found"
    DEPENDENCY_ERROR = "dependency_error"
    SYNTAX_ERROR = "syntax_error"
    NETWORK_ERROR = "network_error"
    DISK_FULL = "disk_full"
    MEMORY_ERROR = "memory_error"
    UNKNOWN = "unknown"


@dataclass
class ErrorSignature:
    """توقيع الخطأ للتعرف عليه"""
    pattern: str
    error_type: ErrorType
    description_ar: str
    suggested_fix: str


# قاعدة بيانات الأخطاء المعروفة
ERROR_DATABASE = [
    ErrorSignature(
        pattern=r"permission denied|EACCES|Operation not permitted",
        error_type=ErrorType.PERMISSION_DENIED,
        description_ar="نقص في الصلاحيات - الملف أو المجلد محمي",
        suggested_fix="تحقق من صلاحيات الملف (chmod) أو شغّل بـ sudo"
    ),
    ErrorSignature(
        pattern=r"timeout|timed out|ETIMEDOUT|deadline exceeded",
        error_type=ErrorType.TIMEOUT,
        description_ar="انتهاء المهلة الزمنية - العملية أخذت وقت أطول من المسموح",
        suggested_fix="زِد مهلة التنفيذ أو حسّن أداء السكربت"
    ),
    ErrorSignature(
        pattern=r"not found|No such file|ENOENT|command not found",
        error_type=ErrorType.NOT_FOUND,
        description_ar="ملف أو أمر غير موجود",
        suggested_fix="تحقق من المسار الكامل للأمر أو ثبّت الحزمة المطلوبة"
    ),
    ErrorSignature(
        pattern=r"ModuleNotFoundError|ImportError|cannot find module",
        error_type=ErrorType.DEPENDENCY_ERROR,
        description_ar="مكتبة أو موديول ناقص",
        suggested_fix="ثبّت المكتبة باستخدام pip install"
    ),
    ErrorSignature(
        pattern=r"SyntaxError|IndentationError|unexpected token",
        error_type=ErrorType.SYNTAX_ERROR,
        description_ar="خطأ في صياغة الكود",
        suggested_fix="راجع الكود وصحح الخطأ النحوي"
    ),
    ErrorSignature(
        pattern=r"Connection refused|Network unreachable|DNS|ENETUNREACH",
        error_type=ErrorType.NETWORK_ERROR,
        description_ar="مشكلة في الاتصال بالشبكة",
        suggested_fix="تحقق من الاتصال بالإنترنت أو عنوان الخادم"
    ),
    ErrorSignature(
        pattern=r"No space left|ENOSPC|disk full",
        error_type=ErrorType.DISK_FULL,
        description_ar="القرص ممتلئ",
        suggested_fix="احذف ملفات غير ضرورية أو وسّع مساحة القرص"
    ),
    ErrorSignature(
        pattern=r"MemoryError|Cannot allocate|ENOMEM|OOM",
        error_type=ErrorType.MEMORY_ERROR,
        description_ar="نفاد الذاكرة",
        suggested_fix="قلل استهلاك الذاكرة أو زِد الـ RAM"
    ),
]


# ============================================================
# هياكل البيانات
# ============================================================

@dataclass
class CronExecution:
    """سجل تنفيذ مهمة واحدة"""
    timestamp: datetime
    user: str
    command: str
    pid: Optional[int] = None
    exit_code: Optional[int] = None
    stderr: Optional[str] = None
    duration_seconds: Optional[float] = None
    
    @property
    def success(self) -> bool:
        return self.exit_code == 0 if self.exit_code is not None else True
    
    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "user": self.user,
            "command": self.command,
            "pid": self.pid,
            "exit_code": self.exit_code,
            "stderr": self.stderr,
            "duration_seconds": self.duration_seconds,
            "success": self.success
        }


@dataclass
class FailureAnalysis:
    """تحليل فشل مهمة"""
    execution: CronExecution
    error_type: ErrorType
    description: str
    suggested_fix: str
    search_query: Optional[str] = None
    web_solutions: List[Dict[str, str]] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            "execution": self.execution.to_dict(),
            "error_type": self.error_type.value,
            "description": self.description,
            "suggested_fix": self.suggested_fix,
            "search_query": self.search_query,
            "web_solutions": self.web_solutions
        }


@dataclass
class JobState:
    """حالة مهمة معينة"""
    command_hash: str
    command: str
    total_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0
    consecutive_failures: int = 0
    last_run: Optional[str] = None
    last_failure: Optional[str] = None
    last_error: Optional[str] = None
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "JobState":
        return cls(**data)


# ============================================================
# محلل السجلات
# ============================================================

class LogParser:
    """محلل سجلات Cron"""
    
    def __init__(self, log_paths: List[str] = None):
        self.log_paths = log_paths or Config.LOG_PATHS
        self.logger = logging.getLogger(__name__)
    
    def find_log_file(self) -> Optional[Path]:
        """البحث عن ملف السجل المتاح"""
        for path in self.log_paths:
            p = Path(path)
            if p.exists() and p.is_file():
                return p
        return None
    
    def parse_syslog_line(self, line: str) -> Optional[CronExecution]:
        """تحليل سطر من syslog"""
        # نمط: Apr 13 08:00:01 hostname CRON[12345]: (user) CMD (command)
        pattern = r"(\w+\s+\d+\s+\d+:\d+:\d+).*?CRON\[(\d+)\].*?\((\w+)\)\s+CMD\s+\((.+)\)"
        match = re.search(pattern, line)
        
        if match:
            timestamp_str, pid, user, command = match.groups()
            # تحويل الوقت (نفترض السنة الحالية)
            try:
                timestamp = datetime.strptime(
                    f"{datetime.now().year} {timestamp_str}",
                    "%Y %b %d %H:%M:%S"
                )
            except ValueError:
                timestamp = datetime.now()
            
            return CronExecution(
                timestamp=timestamp,
                user=user,
                command=command.strip(),
                pid=int(pid)
            )
        return None
    
    def parse_exit_status(self, line: str) -> Tuple[Optional[int], Optional[int]]:
        """استخراج كود الخروج من السجل"""
        # نمط: CRON[12345] exit status 1
        pattern = r"CRON\[(\d+)\].*?exit status (\d+)"
        match = re.search(pattern, line)
        if match:
            return int(match.group(1)), int(match.group(2))
        return None, None
    
    def get_recent_executions(self, hours: int = 24) -> List[CronExecution]:
        """جلب التنفيذات الأخيرة"""
        executions = []
        log_file = self.find_log_file()
        
        if not log_file:
            self.logger.warning("لم يُعثر على ملف سجل Cron")
            return executions
        
        cutoff = datetime.now() - timedelta(hours=hours)
        pid_to_execution: Dict[int, CronExecution] = {}
        
        try:
            # قراءة السجل (آخر 10000 سطر للأداء)
            result = subprocess.run(
                ["tail", "-n", "10000", str(log_file)],
                capture_output=True,
                text=True
            )
            
            for line in result.stdout.splitlines():
                if "CRON" not in line:
                    continue
                
                # تحليل تنفيذ جديد
                exec_record = self.parse_syslog_line(line)
                if exec_record and exec_record.timestamp > cutoff:
                    pid_to_execution[exec_record.pid] = exec_record
                    executions.append(exec_record)
                
                # تحديث كود الخروج
                pid, exit_code = self.parse_exit_status(line)
                if pid and pid in pid_to_execution:
                    pid_to_execution[pid].exit_code = exit_code
        
        except Exception as e:
            self.logger.error(f"خطأ في قراءة السجلات: {e}")
        
        return executions
    
    def get_failed_executions(self, hours: int = 24) -> List[CronExecution]:
        """جلب التنفيذات الفاشلة فقط"""
        all_execs = self.get_recent_executions(hours)
        return [e for e in all_execs if not e.success]


# ============================================================
# محلل الأخطاء
# ============================================================

class ErrorAnalyzer:
    """تحليل وتصنيف الأخطاء"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def analyze(self, execution: CronExecution) -> FailureAnalysis:
        """تحليل سبب الفشل"""
        error_text = execution.stderr or ""
        
        # البحث عن نمط معروف
        for sig in ERROR_DATABASE:
            if re.search(sig.pattern, error_text, re.IGNORECASE):
                return FailureAnalysis(
                    execution=execution,
                    error_type=sig.error_type,
                    description=sig.description_ar,
                    suggested_fix=sig.suggested_fix,
                    search_query=self._build_search_query(execution, sig.error_type)
                )
        
        # خطأ غير معروف
        return FailureAnalysis(
            execution=execution,
            error_type=ErrorType.UNKNOWN,
            description="خطأ غير مصنف - راجع رسالة الخطأ",
            suggested_fix="راجع السجلات الكاملة للمهمة",
            search_query=self._build_search_query(execution, ErrorType.UNKNOWN)
        )
    
    def _build_search_query(self, execution: CronExecution, error_type: ErrorType) -> str:
        """بناء استعلام بحث ذكي"""
        # استخراج اسم الأمر الرئيسي
        cmd_parts = execution.command.split()
        main_cmd = cmd_parts[0] if cmd_parts else "cron"
        
        # بناء الاستعلام
        query_parts = [main_cmd, "cron", error_type.value.replace("_", " ")]
        
        if execution.stderr:
            # إضافة أول 50 حرف من الخطأ
            error_snippet = execution.stderr[:50].strip()
            query_parts.append(f'"{error_snippet}"')
        
        return " ".join(query_parts)


# ============================================================
# البحث في الويب
# ============================================================

class WebSearcher:
    """البحث عن حلول في الإنترنت"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def search_solutions(self, query: str, max_results: int = 5) -> List[Dict[str, str]]:
        """
        البحث عن حلول
        ملاحظة: هذه الدالة تُرجع نتائج وهمية للتوضيح.
        في الإنتاج، استخدم API مثل Brave Search أو Google Custom Search.
        """
        self.logger.info(f"البحث عن: {query}")
        
        # في الإنتاج، استبدل هذا بـ API حقيقي
        # مثال: استخدام requests مع Brave Search API
        
        return [
            {
                "title": f"حل مقترح لـ: {query[:30]}...",
                "url": "https://stackoverflow.com/questions/example",
                "snippet": "راجع الإجابة المقبولة للحصول على خطوات الحل..."
            }
        ]
    
    def search_with_brave(self, query: str, api_key: str = None) -> List[Dict[str, str]]:
        """البحث باستخدام Brave Search API"""
        # تنفيذ حقيقي مع Brave API
        import requests
        
        if not api_key:
            api_key = os.environ.get("BRAVE_API_KEY")
        
        if not api_key:
            self.logger.warning("Brave API key غير متوفر")
            return []
        
        try:
            headers = {"X-Subscription-Token": api_key}
            params = {"q": query, "count": 5}
            response = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers=headers,
                params=params,
                timeout=10
            )
            
            if response.ok:
                data = response.json()
                results = []
                for item in data.get("web", {}).get("results", []):
                    results.append({
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "snippet": item.get("description", "")
                    })
                return results
        except Exception as e:
            self.logger.error(f"خطأ في البحث: {e}")
        
        return []


# ============================================================
# مدير النسخ الاحتياطي
# ============================================================

class BackupManager:
    """إدارة النسخ الاحتياطي"""
    
    def __init__(self, backup_dir: Path = None):
        self.backup_dir = backup_dir or Config.BACKUP_DIR
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(__name__)
    
    def backup_crontab(self, user: str = None) -> Optional[Path]:
        """نسخ احتياطي لـ crontab"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        user_suffix = f"_{user}" if user else ""
        backup_file = self.backup_dir / f"crontab{user_suffix}_{timestamp}.bak"
        
        try:
            cmd = ["crontab", "-l"]
            if user:
                cmd.extend(["-u", user])
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                backup_file.write_text(result.stdout)
                self.logger.info(f"نسخة احتياطية: {backup_file}")
                return backup_file
            else:
                self.logger.warning(f"لا يوجد crontab للمستخدم {user or 'الحالي'}")
        except Exception as e:
            self.logger.error(f"فشل النسخ الاحتياطي: {e}")
        
        return None
    
    def backup_file(self, file_path: Path) -> Optional[Path]:
        """نسخ احتياطي لملف محدد"""
        if not file_path.exists():
            self.logger.warning(f"الملف غير موجود: {file_path}")
            return None
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = self.backup_dir / f"{file_path.name}_{timestamp}.bak"
        
        try:
            shutil.copy2(file_path, backup_file)
            self.logger.info(f"نسخة احتياطية: {backup_file}")
            return backup_file
        except Exception as e:
            self.logger.error(f"فشل النسخ الاحتياطي: {e}")
            return None
    
    def list_backups(self) -> List[Path]:
        """قائمة النسخ الاحتياطية"""
        return sorted(self.backup_dir.glob("*.bak"), reverse=True)
    
    def restore_crontab(self, backup_file: Path, user: str = None) -> bool:
        """استعادة crontab من نسخة احتياطية"""
        if not backup_file.exists():
            self.logger.error(f"النسخة غير موجودة: {backup_file}")
            return False
        
        try:
            cmd = ["crontab"]
            if user:
                cmd.extend(["-u", user])
            cmd.append(str(backup_file))
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                self.logger.info("تمت الاستعادة بنجاح")
                return True
            else:
                self.logger.error(f"فشلت الاستعادة: {result.stderr}")
        except Exception as e:
            self.logger.error(f"خطأ في الاستعادة: {e}")
        
        return False


# ============================================================
# مدير التجارب (Dry-Run)
# ============================================================

class DryRunManager:
    """تشغيل تجريبي آمن"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def test_command(self, command: str, timeout: int = 60) -> Dict[str, Any]:
        """تشغيل أمر تجريبي"""
        self.logger.info(f"تشغيل تجريبي: {command}")
        
        result = {
            "command": command,
            "success": False,
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "duration_seconds": 0,
            "timestamp": datetime.now().isoformat()
        }
        
        try:
            start_time = datetime.now()
            
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            
            duration = (datetime.now() - start_time).total_seconds()
            
            result.update({
                "success": proc.returncode == 0,
                "exit_code": proc.returncode,
                "stdout": proc.stdout[-2000:] if proc.stdout else "",  # آخر 2000 حرف
                "stderr": proc.stderr[-2000:] if proc.stderr else "",
                "duration_seconds": duration
            })
            
        except subprocess.TimeoutExpired:
            result["stderr"] = f"انتهت المهلة ({timeout} ثانية)"
            result["exit_code"] = -1
        except Exception as e:
            result["stderr"] = str(e)
            result["exit_code"] = -1
        
        return result
    
    def compare_versions(self, old_content: str, new_content: str) -> str:
        """مقارنة نسختين وإظهار الفروق"""
        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)
        
        diff = difflib.unified_diff(
            old_lines, new_lines,
            fromfile="القديم",
            tofile="الجديد",
            lineterm=""
        )
        
        return "".join(diff)


# ============================================================
# نظام التنبيهات
# ============================================================

class AlertManager:
    """إدارة التنبيهات الفورية"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "6803381")
        self.slack_webhook = os.environ.get("SLACK_WEBHOOK_URL")
        self.discord_webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    
    def send_alert(self, message: str, critical: bool = False) -> bool:
        """إرسال تنبيه عبر القنوات المتاحة"""
        success = False
        
        # محاولة Telegram أولاً
        if self.telegram_token:
            success = self._send_telegram(message) or success
        
        # Slack
        if self.slack_webhook:
            success = self._send_slack(message, critical) or success
        
        # Discord
        if self.discord_webhook:
            success = self._send_discord(message, critical) or success
        
        return success
    
    def _send_telegram(self, message: str) -> bool:
        """إرسال عبر Telegram"""
        try:
            import requests
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            payload = {
                "chat_id": self.telegram_chat_id,
                "text": message,
                "parse_mode": "Markdown"
            }
            response = requests.post(url, json=payload, timeout=10)
            return response.ok
        except Exception as e:
            self.logger.error(f"فشل Telegram: {e}")
            return False
    
    def _send_slack(self, message: str, critical: bool) -> bool:
        """إرسال عبر Slack Webhook"""
        try:
            import requests
            color = "#FF0000" if critical else "#FFA500"
            payload = {
                "attachments": [{
                    "color": color,
                    "title": "🚨 تنبيه CronMaster",
                    "text": message,
                    "footer": "CronMaster_AI"
                }]
            }
            response = requests.post(self.slack_webhook, json=payload, timeout=10)
            return response.ok
        except Exception as e:
            self.logger.error(f"فشل Slack: {e}")
            return False
    
    def _send_discord(self, message: str, critical: bool) -> bool:
        """إرسال عبر Discord Webhook"""
        try:
            import requests
            color = 0xFF0000 if critical else 0xFFA500
            payload = {
                "embeds": [{
                    "title": "🚨 تنبيه CronMaster",
                    "description": message,
                    "color": color
                }]
            }
            response = requests.post(self.discord_webhook, json=payload, timeout=10)
            return response.ok
        except Exception as e:
            self.logger.error(f"فشل Discord: {e}")
            return False


# ============================================================
# مدير الحالة
# ============================================================

class StateManager:
    """إدارة حالة المهام"""
    
    def __init__(self, state_file: Path = None):
        self.state_file = state_file or Config.STATE_FILE
        self.state: Dict[str, JobState] = {}
        self.logger = logging.getLogger(__name__)
        self._load()
    
    def _load(self):
        """تحميل الحالة من الملف"""
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text())
                self.state = {
                    k: JobState.from_dict(v) 
                    for k, v in data.get("jobs", {}).items()
                }
            except Exception as e:
                self.logger.error(f"خطأ في تحميل الحالة: {e}")
    
    def _save(self):
        """حفظ الحالة"""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "updated_at": datetime.now().isoformat(),
                "jobs": {k: v.to_dict() for k, v in self.state.items()}
            }
            self.state_file.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        except Exception as e:
            self.logger.error(f"خطأ في حفظ الحالة: {e}")
    
    def _hash_command(self, command: str) -> str:
        """إنشاء معرف فريد للأمر"""
        return hashlib.md5(command.encode()).hexdigest()[:12]
    
    def record_execution(self, execution: CronExecution):
        """تسجيل تنفيذ جديد"""
        cmd_hash = self._hash_command(execution.command)
        
        if cmd_hash not in self.state:
            self.state[cmd_hash] = JobState(
                command_hash=cmd_hash,
                command=execution.command
            )
        
        job = self.state[cmd_hash]
        job.total_runs += 1
        job.last_run = execution.timestamp.isoformat()
        
        if execution.success:
            job.successful_runs += 1
            job.consecutive_failures = 0
        else:
            job.failed_runs += 1
            job.consecutive_failures += 1
            job.last_failure = execution.timestamp.isoformat()
            job.last_error = execution.stderr
        
        self._save()
        return job
    
    def get_job_state(self, command: str) -> Optional[JobState]:
        """جلب حالة مهمة"""
        cmd_hash = self._hash_command(command)
        return self.state.get(cmd_hash)
    
    def get_critical_jobs(self, threshold: int = None) -> List[JobState]:
        """جلب المهام الحرجة (فشل متتالي)"""
        threshold = threshold or Config.ALERT_THRESHOLD
        return [j for j in self.state.values() if j.consecutive_failures >= threshold]
    
    def get_statistics(self) -> Dict[str, Any]:
        """إحصائيات عامة"""
        total_jobs = len(self.state)
        total_runs = sum(j.total_runs for j in self.state.values())
        total_failures = sum(j.failed_runs for j in self.state.values())
        critical_jobs = len(self.get_critical_jobs())
        
        return {
            "total_jobs": total_jobs,
            "total_runs": total_runs,
            "total_failures": total_failures,
            "success_rate": ((total_runs - total_failures) / total_runs * 100) if total_runs > 0 else 100,
            "critical_jobs": critical_jobs
        }


# ============================================================
# مولد التقارير
# ============================================================

class ReportGenerator:
    """توليد التقارير"""
    
    def __init__(self, reports_dir: Path = None):
        self.reports_dir = reports_dir or Config.REPORTS_DIR
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(__name__)
    
    def generate_weekly_report(
        self,
        executions: List[CronExecution],
        analyses: List[FailureAnalysis],
        stats: Dict[str, Any],
        format: str = "markdown"
    ) -> Path:
        """توليد تقرير أسبوعي"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if format == "json":
            return self._generate_json_report(executions, analyses, stats, timestamp)
        else:
            return self._generate_markdown_report(executions, analyses, stats, timestamp)
    
    def _generate_markdown_report(
        self,
        executions: List[CronExecution],
        analyses: List[FailureAnalysis],
        stats: Dict[str, Any],
        timestamp: str
    ) -> Path:
        """توليد تقرير بتنسيق Markdown"""
        report_file = self.reports_dir / f"weekly_report_{timestamp}.md"
        
        failed_execs = [e for e in executions if not e.success]
        successful_execs = [e for e in executions if e.success]
        
        content = f"""# 📊 تقرير CronMaster الأسبوعي

**تاريخ التقرير:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

---

## 📈 الإحصائيات العامة

| المقياس | القيمة |
|---------|--------|
| إجمالي المهام | {stats['total_jobs']} |
| إجمالي التنفيذات | {stats['total_runs']} |
| التنفيذات الناجحة | {stats['total_runs'] - stats['total_failures']} |
| التنفيذات الفاشلة | {stats['total_failures']} |
| نسبة النجاح | {stats['success_rate']:.1f}% |
| المهام الحرجة | {stats['critical_jobs']} |

---

## ❌ المهام الفاشلة ({len(failed_execs)})

"""
        
        if not failed_execs:
            content += "_لا توجد مهام فاشلة - ممتاز! 🎉_\n"
        else:
            for i, analysis in enumerate(analyses, 1):
                exec_data = analysis.execution
                content += f"""### {i}. `{exec_data.command[:60]}...`

- **الوقت:** {exec_data.timestamp.strftime('%Y-%m-%d %H:%M:%S')}
- **المستخدم:** {exec_data.user}
- **كود الخروج:** {exec_data.exit_code}
- **نوع الخطأ:** {analysis.error_type.value}
- **التحليل:** {analysis.description}
- **الحل المقترح:** {analysis.suggested_fix}

"""
                if analysis.web_solutions:
                    content += "**حلول من الويب:**\n"
                    for sol in analysis.web_solutions[:3]:
                        content += f"  - [{sol['title']}]({sol['url']})\n"
                content += "\n---\n\n"
        
        content += f"""
## ✅ المهام الناجحة ({len(successful_execs)})

"""
        # تجميع حسب الأمر
        cmd_counts = defaultdict(int)
        for e in successful_execs:
            cmd_short = e.command[:50] + "..." if len(e.command) > 50 else e.command
            cmd_counts[cmd_short] += 1
        
        for cmd, count in sorted(cmd_counts.items(), key=lambda x: -x[1])[:10]:
            content += f"- `{cmd}` — {count} مرة\n"
        
        content += """
---

## 💡 التوصيات

"""
        if stats['critical_jobs'] > 0:
            content += f"⚠️ **تنبيه:** {stats['critical_jobs']} مهمة حرجة تحتاج مراجعة عاجلة!\n\n"
        
        if stats['success_rate'] < 95:
            content += "📉 نسبة النجاح أقل من 95% — راجع المهام الفاشلة أعلاه.\n"
        else:
            content += "🎯 نسبة النجاح ممتازة! استمر على هذا الأداء.\n"
        
        content += f"""
---

_تم التوليد بواسطة CronMaster_AI_
"""
        
        report_file.write_text(content, encoding="utf-8")
        self.logger.info(f"تم توليد التقرير: {report_file}")
        return report_file
    
    def _generate_json_report(
        self,
        executions: List[CronExecution],
        analyses: List[FailureAnalysis],
        stats: Dict[str, Any],
        timestamp: str
    ) -> Path:
        """توليد تقرير بتنسيق JSON"""
        report_file = self.reports_dir / f"weekly_report_{timestamp}.json"
        
        data = {
            "report_date": datetime.now().isoformat(),
            "statistics": stats,
            "executions": [e.to_dict() for e in executions],
            "failure_analyses": [a.to_dict() for a in analyses]
        }
        
        report_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        self.logger.info(f"تم توليد التقرير: {report_file}")
        return report_file


# ============================================================
# المحرك الرئيسي
# ============================================================

class CronMaster:
    """المحرك الرئيسي لـ CronMaster_AI"""
    
    def __init__(self):
        Config.init_dirs()
        
        # تهيئة المكونات
        self.log_parser = LogParser()
        self.error_analyzer = ErrorAnalyzer()
        self.web_searcher = WebSearcher()
        self.backup_manager = BackupManager()
        self.dry_run_manager = DryRunManager()
        self.alert_manager = AlertManager()
        self.state_manager = StateManager()
        self.report_generator = ReportGenerator()
        
        # إعداد التسجيل
        self._setup_logging()
    
    def _setup_logging(self):
        """إعداد نظام التسجيل"""
        log_file = Config.WORK_DIR / "cronmaster.log"
        
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            handlers=[
                logging.FileHandler(log_file, encoding="utf-8"),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
    
    # -------------------- الأوامر الرئيسية --------------------
    
    def monitor(self, hours: int = 24, verbose: bool = False) -> Dict[str, Any]:
        """مراقبة وتحليل التنفيذات الأخيرة"""
        self.logger.info(f"بدء المراقبة (آخر {hours} ساعة)")
        
        executions = self.log_parser.get_recent_executions(hours)
        analyses = []
        alerts_sent = 0
        
        for exec_record in executions:
            # تسجيل في الحالة
            job_state = self.state_manager.record_execution(exec_record)
            
            # تحليل الفشل
            if not exec_record.success:
                analysis = self.error_analyzer.analyze(exec_record)
                analyses.append(analysis)
                
                # تنبيه إذا حرج
                if job_state.consecutive_failures >= Config.ALERT_THRESHOLD:
                    self._send_critical_alert(exec_record, analysis)
                    alerts_sent += 1
        
        # إحصائيات
        stats = self.state_manager.get_statistics()
        
        result = {
            "period_hours": hours,
            "total_executions": len(executions),
            "failed_executions": len(analyses),
            "alerts_sent": alerts_sent,
            "statistics": stats,
            "analyses": [a.to_dict() for a in analyses] if verbose else None
        }
        
        self.logger.info(f"المراقبة انتهت: {len(executions)} تنفيذ، {len(analyses)} فشل")
        return result
    
    def analyze_failure(self, command: str = None, search_web: bool = True) -> Optional[FailureAnalysis]:
        """تحليل فشل مهمة محددة"""
        # جلب آخر فشل
        failed = self.log_parser.get_failed_executions(hours=24)
        
        if command:
            failed = [e for e in failed if command in e.command]
        
        if not failed:
            self.logger.info("لا توجد مهام فاشلة")
            return None
        
        # تحليل آخر فشل
        latest = failed[-1]
        analysis = self.error_analyzer.analyze(latest)
        
        # البحث في الويب
        if search_web and analysis.search_query:
            analysis.web_solutions = self.web_searcher.search_solutions(analysis.search_query)
        
        return analysis
    
    def fix_job(
        self,
        command: str,
        new_command: str = None,
        dry_run: bool = True
    ) -> Dict[str, Any]:
        """إصلاح مهمة مع نسخ احتياطي"""
        result = {
            "original_command": command,
            "new_command": new_command,
            "backup_created": False,
            "dry_run_result": None,
            "applied": False
        }
        
        # نسخ احتياطي
        backup = self.backup_manager.backup_crontab()
        if backup:
            result["backup_created"] = True
            result["backup_path"] = str(backup)
        
        # تشغيل تجريبي
        if new_command and dry_run:
            result["dry_run_result"] = self.dry_run_manager.test_command(new_command)
        
        # لا نطبق تلقائياً - نترك القرار للمستخدم
        self.logger.info(f"الإصلاح جاهز - dry_run: {dry_run}")
        return result
    
    def generate_report(self, format: str = "markdown") -> Path:
        """توليد تقرير أسبوعي"""
        executions = self.log_parser.get_recent_executions(hours=168)  # أسبوع
        
        analyses = []
        for e in executions:
            if not e.success:
                analyses.append(self.error_analyzer.analyze(e))
        
        stats = self.state_manager.get_statistics()
        
        return self.report_generator.generate_weekly_report(
            executions, analyses, stats, format
        )
    
    def show_diff(self, old_file: Path, new_file: Path) -> str:
        """عرض الفروق بين ملفين"""
        old_content = old_file.read_text() if old_file.exists() else ""
        new_content = new_file.read_text() if new_file.exists() else ""
        return self.dry_run_manager.compare_versions(old_content, new_content)
    
    def list_backups(self) -> List[Dict[str, Any]]:
        """قائمة النسخ الاحتياطية"""
        backups = self.backup_manager.list_backups()
        return [
            {
                "path": str(b),
                "name": b.name,
                "size_bytes": b.stat().st_size,
                "created": datetime.fromtimestamp(b.stat().st_mtime).isoformat()
            }
            for b in backups
        ]
    
    def restore_backup(self, backup_path: str) -> bool:
        """استعادة نسخة احتياطية"""
        return self.backup_manager.restore_crontab(Path(backup_path))
    
    def _send_critical_alert(self, execution: CronExecution, analysis: FailureAnalysis):
        """إرسال تنبيه حرج"""
        message = f"""🚨 **فشل حرج في Cron Job**

**الأمر:** `{execution.command[:50]}...`
**الوقت:** {execution.timestamp.strftime('%Y-%m-%d %H:%M:%S')}
**نوع الخطأ:** {analysis.error_type.value}
**التحليل:** {analysis.description}

**الحل المقترح:** {analysis.suggested_fix}"""
        
        self.alert_manager.send_alert(message, critical=True)


# ============================================================
# واجهة سطر الأوامر
# ============================================================

def main():
    """نقطة الدخول الرئيسية"""
    parser = argparse.ArgumentParser(
        description="CronMaster_AI - مدير ومراقب ذكي للمهام المجدولة",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
أمثلة:
  # مراقبة آخر 24 ساعة
  python CronMaster_AI.py monitor
  
  # تحليل فشل مهمة معينة
  python CronMaster_AI.py analyze --command "backup.sh"
  
  # توليد تقرير أسبوعي
  python CronMaster_AI.py report --format markdown
  
  # قائمة النسخ الاحتياطية
  python CronMaster_AI.py backups
  
  # اختبار أمر (dry-run)
  python CronMaster_AI.py test "echo hello"
"""
    )
    
    subparsers = parser.add_subparsers(dest="command", help="الأوامر المتاحة")
    
    # monitor
    monitor_parser = subparsers.add_parser("monitor", help="مراقبة التنفيذات")
    monitor_parser.add_argument("--hours", type=int, default=24, help="عدد الساعات")
    monitor_parser.add_argument("--verbose", "-v", action="store_true", help="تفاصيل إضافية")
    
    # analyze
    analyze_parser = subparsers.add_parser("analyze", help="تحليل فشل مهمة")
    analyze_parser.add_argument("--command", "-c", help="جزء من الأمر للبحث عنه")
    analyze_parser.add_argument("--no-web", action="store_true", help="بدون بحث ويب")
    
    # report
    report_parser = subparsers.add_parser("report", help="توليد تقرير")
    report_parser.add_argument("--format", "-f", choices=["markdown", "json"], default="markdown")
    
    # backups
    subparsers.add_parser("backups", help="قائمة النسخ الاحتياطية")
    
    # restore
    restore_parser = subparsers.add_parser("restore", help="استعادة نسخة احتياطية")
    restore_parser.add_argument("backup_path", help="مسار النسخة")
    
    # test (dry-run)
    test_parser = subparsers.add_parser("test", help="تشغيل تجريبي لأمر")
    test_parser.add_argument("test_command", help="الأمر للاختبار")
    test_parser.add_argument("--timeout", type=int, default=60, help="مهلة بالثواني")
    
    # diff
    diff_parser = subparsers.add_parser("diff", help="مقارنة ملفين")
    diff_parser.add_argument("old_file", help="الملف القديم")
    diff_parser.add_argument("new_file", help="الملف الجديد")
    
    # status
    subparsers.add_parser("status", help="حالة النظام")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    # تنفيذ الأوامر
    master = CronMaster()
    
    if args.command == "monitor":
        result = master.monitor(hours=args.hours, verbose=args.verbose)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    
    elif args.command == "analyze":
        analysis = master.analyze_failure(
            command=args.command,
            search_web=not args.no_web
        )
        if analysis:
            print(json.dumps(analysis.to_dict(), indent=2, ensure_ascii=False))
        else:
            print("لا توجد مهام فاشلة")
    
    elif args.command == "report":
        report_path = master.generate_report(format=args.format)
        print(f"✅ تم توليد التقرير: {report_path}")
    
    elif args.command == "backups":
        backups = master.list_backups()
        if backups:
            print(json.dumps(backups, indent=2, ensure_ascii=False))
        else:
            print("لا توجد نسخ احتياطية")
    
    elif args.command == "restore":
        success = master.restore_backup(args.backup_path)
        print("✅ تمت الاستعادة" if success else "❌ فشلت الاستعادة")
    
    elif args.command == "test":
        result = master.dry_run_manager.test_command(
            args.test_command,
            timeout=args.timeout
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
    
    elif args.command == "diff":
        diff = master.show_diff(Path(args.old_file), Path(args.new_file))
        print(diff if diff else "الملفان متطابقان")
    
    elif args.command == "status":
        stats = master.state_manager.get_statistics()
        critical = master.state_manager.get_critical_jobs()
        
        print("=" * 50)
        print("📊 حالة CronMaster_AI")
        print("=" * 50)
        print(f"المهام المسجلة:    {stats['total_jobs']}")
        print(f"إجمالي التنفيذات:  {stats['total_runs']}")
        print(f"نسبة النجاح:       {stats['success_rate']:.1f}%")
        print(f"المهام الحرجة:     {stats['critical_jobs']}")
        print("=" * 50)
        
        if critical:
            print("\n⚠️ مهام حرجة تحتاج مراجعة:")
            for job in critical:
                print(f"  - {job.command[:50]}... ({job.consecutive_failures} فشل متتالي)")


if __name__ == "__main__":
    main()

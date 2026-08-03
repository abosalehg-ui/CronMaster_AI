# -*- coding: utf-8 -*-
"""واجهة سطر الأوامر."""

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .backends import BACKENDS
from .backends.base import BackendError
from .config import Config, setup_logging
from .core import EXIT_MONITOR_FAILURE, CronMaster
from .i18n import SUPPORTED_LANGS, set_lang, t
from .lock import ExecutionLock
from .reporting import FORMATS


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CronMaster_AI - مدير ذكي لـ OpenClaw Cron Jobs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
أمثلة:
  python CronMaster_AI.py monitor            # مراقبة وإصلاح تلقائي
  python CronMaster_AI.py monitor --dry-run  # عرض ما سيحدث دون تنفيذ
  python CronMaster_AI.py monitor --no-fix   # مراقبة بدون إصلاح
  python CronMaster_AI.py status             # حالة سريعة
  python CronMaster_AI.py report             # توليد تقرير
  python CronMaster_AI.py report -f html     # تقرير HTML مكتفٍ بذاته
  python CronMaster_AI.py list               # قائمة المهام
  python CronMaster_AI.py fix <job_id>       # إصلاح مهمة محددة
  python CronMaster_AI.py history            # سجل الإصلاحات
  python CronMaster_AI.py stats              # إحصائيات من السجل التاريخي
  python CronMaster_AI.py restore <job_id>   # استعادة مهمة من نسخة احتياطية
  python CronMaster_AI.py doctor             # فحص ذاتي شامل

أكواد خروج monitor: 0 سليم، 1 فشل المراقب، 2 مهام فاشلة، 3 طُبِّقت إصلاحات
""",
    )

    parser.add_argument("--version", action="version", version=f"CronMaster_AI {__version__}")
    parser.add_argument("--lang", choices=list(SUPPORTED_LANGS), help="لغة المخرجات (افتراضي ar)")
    parser.add_argument("--backend", choices=sorted(BACKENDS), help="الواجهة الخلفية للمهام")

    subparsers = parser.add_subparsers(dest="command", help="الأوامر")

    # monitor
    mon = subparsers.add_parser("monitor", help="مراقبة وإصلاح")
    mon.add_argument("--no-fix", action="store_true", help="بدون إصلاح تلقائي")
    mon.add_argument("--no-alert", action="store_true", help="بدون تنبيهات")
    mon.add_argument("--no-retry", action="store_true", help="بدون إعادة تشغيل")
    mon.add_argument("--dry-run", action="store_true", help="عرض ما سيحدث دون أي تنفيذ")
    mon.add_argument("--prometheus-textfile", metavar="PATH", help="كتابة مقاييس Prometheus إلى ملف نصي")

    # status
    subparsers.add_parser("status", help="حالة سريعة")

    # report
    rep = subparsers.add_parser("report", help="توليد تقرير")
    rep.add_argument("--format", "-f", dest="fmt", choices=list(FORMATS), default="markdown")

    # list
    subparsers.add_parser("list", help="قائمة المهام")

    # fix
    fix = subparsers.add_parser("fix", help="إصلاح مهمة")
    fix.add_argument("job_id", help="معرف المهمة")
    fix.add_argument("--no-retry", action="store_true", help="بدون إعادة تشغيل")

    # history
    hist = subparsers.add_parser("history", help="سجل الإصلاحات والتنبيهات")
    hist.add_argument("--limit", type=int, default=20, help="عدد السجلات (افتراضي 20)")

    # stats
    stats = subparsers.add_parser("stats", help="إحصائيات من السجل التاريخي")
    stats.add_argument("--job", dest="job", help="مهمة واحدة بدل كل المهام")
    stats.add_argument("--days", type=int, help="نافذة الأيام (افتراضي مدة الاحتفاظ)")

    # restore
    restore = subparsers.add_parser("restore", help="استعادة مهمة من نسخة احتياطية")
    restore.add_argument("job_id", help="معرف المهمة")
    restore.add_argument("--backup", help="ملف نسخة احتياطية محدد")
    restore.add_argument("--list", dest="list_only", action="store_true", help="عرض النسخ المتاحة فقط")
    restore.add_argument("--yes", "-y", action="store_true", help="بدون سؤال تأكيد")

    # doctor
    subparsers.add_parser("doctor", help="فحص ذاتي شامل")

    return parser


# ============================================================
# طباعة الأوامر
# ============================================================


def _print_status(result):
    print("=" * 40)
    print(t("status.title"))
    print("=" * 40)
    # المسافات مضبوطة يدوياً لتطابق المخرجات العربية التاريخية حرفياً
    print(f"{t('status.total')}   {result['total_jobs']}")
    print(f"{t('status.ok')}        {result['ok']}")
    print(f"{t('status.error')}        {result['error']}")
    print(f"{t('status.critical')}         {result['critical']}")
    print(f"{t('status.silent')}        {result['silent']}")
    print(f"{t('status.rate')}     {result['success_rate']:.1f}%")
    print("=" * 40)


def _fmt_pct(value):
    return "—" if value is None else f"{value * 100:.0f}%"


def _fmt_secs(value):
    return "—" if value is None else f"{value:.1f}s"


def _fmt_trend(ratio):
    if ratio is None:
        return "—"
    if ratio >= 1.25:
        return t("stats.trend_up")
    if ratio <= 0.8:
        return t("stats.trend_down")
    return t("stats.trend_flat")


def _fmt_duration(seconds):
    if seconds is None:
        return "—"
    hours = seconds / 3600
    return f"{hours:.1f}h" if hours >= 1 else f"{seconds / 60:.0f}m"


def _print_stats(result):
    if not result.get("available"):
        print(result.get("reason", ""))
        return
    print("=" * 60)
    print(t("stats.title"))
    print(t("stats.window", days=result["days"]))
    print("=" * 60)
    if not result["jobs"]:
        print(t("stats.no_history"))
        return
    for entry in result["jobs"]:
        print(f"• {entry['job_name']}  [{entry['job_id']}]")
        print(f"   {t('stats.success_rate')}: {_fmt_pct(entry['success_rate'])}"
              f"   {t('stats.flakiness')}: {_fmt_pct(entry['flakiness'])}"
              f"   ({entry['runs']} {t('stats.samples')})")
        print(f"   {t('stats.avg_duration')}: {_fmt_secs(entry['avg_duration'])}"
              f"   {t('stats.trend')}: {_fmt_trend(entry['duration_ratio'])}"
              f"   {t('stats.mtbf')}: {_fmt_duration(entry['mtbf_seconds'])}")
    print("=" * 60)


def _print_doctor(result):
    print("=" * 60)
    print(t("doctor.title"))
    print("=" * 60)
    for check in result["checks"]:
        if check["ok"]:
            mark, label = "✅", t("doctor.ok")
        elif check["hard"]:
            mark, label = "❌", t("doctor.fail")
        else:
            mark, label = "⚠️", t("doctor.warn")
        detail = f" — {check['detail']}" if check["detail"] else ""
        print(f"{mark} {check['name']}: {label}{detail}")
    print("=" * 60)
    print(t("doctor.passed") if result["ok"] else t("doctor.failed"))


def _run_restore(master, args) -> int:
    backups = master.list_backups(args.job_id)
    if not backups:
        print(t("restore.no_backups", job_id=args.job_id), file=sys.stderr)
        return 1

    if args.list_only:
        print(t("restore.available", job_id=args.job_id))
        for path in backups:
            print(f"  {path.name}")
        return 0

    chosen = Path(args.backup) if args.backup else backups[0]
    if not chosen.is_absolute() and not chosen.exists():
        chosen = Config.BACKUP_DIR / chosen.name

    if not args.yes:
        try:
            answer = input(t("restore.confirm", job_id=args.job_id, backup=chosen.name))
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes", "ن", "نعم"):
            print(t("restore.cancelled"))
            return 0

    result = master.restore(args.job_id, chosen)
    if result.get("error"):
        print(result["error"], file=sys.stderr)
        return 1

    print(t("restore.done", job_id=args.job_id, backup=chosen.name))
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0


def _run_monitor(master, args) -> int:
    """تنفيذ المراقبة تحت قفل يمنع تشابك دورتين"""
    lock = ExecutionLock()
    if not lock.acquire():
        # نسخة أخرى تعمل: هذا ليس خطأ، الجدولة تتداخل أحياناً
        master.logger.info(t("lock.busy"))
        print(t("lock.busy"))
        return 0

    try:
        result = master.monitor(
            auto_fix=not args.no_fix,
            alert=not args.no_alert,
            retry=not args.no_retry,
            dry_run=args.dry_run,
            prometheus_textfile=args.prometheus_textfile,
        )
    finally:
        lock.release()

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return CronMaster.exit_code(result)


# ============================================================
# نقطة الدخول
# ============================================================


def main():
    parser = build_arg_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    Config.init_dirs()
    setup_logging()
    Config.load()

    # أعلام سطر الأوامر لها الأولوية على الملف والبيئة
    if getattr(args, "backend", None):
        Config.BACKEND = args.backend
    set_lang(args.lang or Config.LANG)

    master = CronMaster(dry_run=getattr(args, "dry_run", False))

    try:
        if args.command == "monitor":
            sys.exit(_run_monitor(master, args))

        elif args.command == "status":
            _print_status(master.status())

        elif args.command == "report":
            path = master.report(fmt=args.fmt)
            print(t("report.generated", path=path))

        elif args.command == "list":
            print(json.dumps(master.list_jobs(), indent=2, ensure_ascii=False))

        elif args.command == "fix":
            result = master.fix_job(args.job_id, retry=not args.no_retry)
            print(json.dumps(result, indent=2, ensure_ascii=False))

        elif args.command == "history":
            print(json.dumps(master.history(limit=args.limit), indent=2, ensure_ascii=False))

        elif args.command == "stats":
            _print_stats(master.stats(job_id=args.job, days=args.days))

        elif args.command == "restore":
            sys.exit(_run_restore(master, args))

        elif args.command == "doctor":
            result = master.doctor()
            _print_doctor(result)
            sys.exit(0 if result["ok"] else 1)

    except BackendError as e:
        print(t("cli.backend_error", error=e), file=sys.stderr)
        sys.exit(EXIT_MONITOR_FAILURE)


if __name__ == "__main__":
    main()

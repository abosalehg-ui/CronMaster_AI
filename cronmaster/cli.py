# -*- coding: utf-8 -*-
"""واجهة سطر الأوامر."""

import argparse
import json
import logging
import sys
from pathlib import Path

from . import __version__
from . import format as fmt
from .backends import BACKENDS
from .backends.base import BackendError
from .config import Config, setup_logging
from .core import EXIT_MONITOR_FAILURE, CronMaster
from .format import label_column, pad_label
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
    mon.add_argument("--json", action="store_true", help="مخرجات JSON بدل العرض البشري")

    # status
    subparsers.add_parser("status", help="حالة سريعة")

    # report
    rep = subparsers.add_parser("report", help="توليد تقرير")
    rep.add_argument("--format", "-f", dest="fmt", choices=list(FORMATS), default="markdown")

    # list
    jobs = subparsers.add_parser("list", help="قائمة المهام")
    jobs.add_argument("--json", action="store_true", help="مخرجات JSON بدل العرض البشري")

    # fix
    fix = subparsers.add_parser("fix", help="إصلاح مهمة")
    fix.add_argument("job_id", help="معرف المهمة")
    fix.add_argument("--no-retry", action="store_true", help="بدون إعادة تشغيل")
    fix.add_argument("--json", action="store_true", help="مخرجات JSON بدل العرض البشري")

    # history
    hist = subparsers.add_parser("history", help="سجل الإصلاحات والتنبيهات")
    hist.add_argument("--limit", type=int, default=20, help="عدد السجلات (افتراضي 20)")
    hist.add_argument("--json", action="store_true", help="مخرجات JSON بدل العرض البشري")

    # stats
    stats = subparsers.add_parser("stats", help="إحصائيات من السجل التاريخي")
    stats.add_argument("--job", dest="job", help="مهمة واحدة بدل كل المهام")
    stats.add_argument("--days", type=int, help="نافذة الأيام (افتراضي مدة الاحتفاظ)")

    # restore
    restore = subparsers.add_parser("restore", help="استعادة مهمة من نسخة احتياطية")
    restore.add_argument("job_id", help="معرف المهمة")
    restore.add_argument("--backup", help="ملف نسخة احتياطية محدد (داخل مجلد النسخ)")
    restore.add_argument("--list", dest="list_only", action="store_true", help="عرض النسخ المتاحة فقط")
    restore.add_argument("--yes", "-y", action="store_true", help="بدون سؤال تأكيد")

    # doctor
    subparsers.add_parser("doctor", help="فحص ذاتي شامل")

    return parser


# ============================================================
# طباعة الأوامر
# ============================================================


def _print_pairs(pairs):
    """جدول تسمية/قيمة بمحاذاة محسوبة من عرض العرض الفعلي لا من عدد المحارف"""
    column = label_column(label for label, _ in pairs)
    for label, value in pairs:
        print(f"{pad_label(label, column)}{value}")


def _print_status(result):
    print("=" * 40)
    print(t("status.title"))
    print("=" * 40)
    _print_pairs(
        [
            (t("status.total"), result["total_jobs"]),
            (t("status.ok"), result["ok"]),
            (t("status.error"), result["error"]),
            (t("status.critical"), result["critical"]),
            (t("status.silent"), result["silent"]),
            (t("status.rate"), f"{result['success_rate']:.1f}%"),
        ]
    )
    print("=" * 40)


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
        print(f"   {t('stats.success_rate')}: {fmt.pct(entry['success_rate'])}"
              f"   {t('stats.flakiness')}: {fmt.pct(entry['flakiness'])}"
              f"   ({entry['runs']} {t('stats.samples')})")
        print(f"   {t('stats.avg_duration')}: {fmt.secs(entry['avg_duration'])}"
              f"   {t('stats.trend')}: {fmt.trend(entry['duration_ratio'])}"
              f"   {t('stats.mtbf')}: {fmt.duration(entry['mtbf_seconds'])}")
    print("=" * 60)


def _print_analysis(analysis):
    """كتلة تشخيص مهمة فاشلة واحدة"""
    mark = "✅" if analysis.get("fix_applied") else "❌"
    print(f"{mark} {analysis.get('job_name', '')}  [{analysis.get('job_id', '')}]")
    print(f"   {t('alert.error_type')}: {analysis.get('error_type', '')}")
    print(f"   {t('alert.analysis')}: {analysis.get('description', '')}")
    if analysis.get("fix_applied") or analysis.get("fix_details"):
        print(f"   {t('alert.fix')}: {analysis.get('fix_details') or ''}")
    else:
        print(f"   {t('alert.suggestion')}: {analysis.get('suggested_fix', '')}")


def _print_monitor(result):
    """ملخص دورة المراقبة بدل سكب كائن JSON كامل على الطرفية"""
    if result.get("error"):
        print(t("human.monitor_error", error=result["error"]), file=sys.stderr)
        return

    print("=" * 60)
    print(t("human.monitor_title"))
    if result.get("dry_run"):
        print(t("human.dry_run_note"))
    print("=" * 60)
    _print_pairs(
        [
            (t("human.total"), result.get("total_jobs", 0)),
            (t("human.failed"), result.get("failed_jobs", 0)),
            (t("human.fixes"), result.get("fixes_applied", 0)),
            (t("human.retries"), result.get("retries", 0)),
            (t("human.recovered"), len(result.get("recovered_jobs", []))),
            (t("human.silent"), len(result.get("silent_jobs", []))),
            (t("human.alert_sent"), t("human.yes") if result.get("alert_sent") else t("human.no")),
        ]
    )

    analyses = result.get("analyses", [])
    if analyses:
        print("-" * 60)
        for analysis in analyses:
            _print_analysis(analysis)

    notices = result.get("notices", [])
    if notices:
        print("-" * 60)
        print(t("human.notices"))
        for notice in notices:
            print(f"   {notice}")

    print("=" * 60)
    print(t("human.hint_json"))


def _print_jobs(jobs):
    print("=" * 60)
    print(t("human.jobs_title"))
    print("=" * 60)
    if not jobs:
        print(t("human.no_jobs"))
        return
    for job in jobs:
        state = job.get("last_status") or ("—" if job.get("enabled") else "disabled")
        mark = "❌" if state == "error" else ("✅" if state == "ok" else "⏸️")
        print(f"{mark} {job.get('name', '')}  [{job.get('id', '')}]")
        print(f"   {t('html.col_schedule')}: {job.get('schedule', '')}"
              f"   {t('html.col_status')}: {state}")
    print("=" * 60)


def _print_fix(result):
    if result.get("error"):
        print(result["error"], file=sys.stderr)
        return
    print("=" * 60)
    print(t("human.fix_title"))
    print("=" * 60)
    _print_analysis(result)
    print("=" * 60)


def _print_history(history):
    print("=" * 60)
    print(t("human.history_title"))
    print("=" * 60)
    fixes = history.get("fixes", [])
    if not fixes:
        print(t("human.no_history"))
    for fix in reversed(fixes):
        stamp = str(fix.get("timestamp", ""))[:19].replace("T", " ")
        print(f"• {stamp}  {fix.get('fix_type', '')}  [{fix.get('job_id', '')}]")
        if fix.get("details"):
            print(f"   {fix['details']}")
    if history.get("last_run"):
        print("-" * 60)
        print(f"{t('human.last_run')} {str(history['last_run'])[:19].replace('T', ' ')}")
    print("=" * 60)


def _wants_json(args) -> bool:
    """JSON عند طلبه صراحةً، أو حين لا تكون المخرجات طرفية — فلا تنكسر السكربتات"""
    if getattr(args, "json", False):
        return True
    try:
        return not sys.stdout.isatty()
    except (AttributeError, ValueError):  # مخرجات مغلقة أو مستبدلة
        return True


def _emit(data, args, printer):
    """طباعة بشرية أو JSON حسب السياق"""
    if _wants_json(args):
        print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    else:
        printer(data)


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


def _resolve_backup(requested, default_path):
    """اختيار ملف النسخة الاحتياطية، محصوراً في مجلد النسخ.

    المسار المطلق كان يمرّ كما هو فتُقرأ أي حمولة JSON على النظام وتُطبَّق على
    مهمة حيّة. الخطورة محدودة (المستخدم يملك صلاحياته) لكن مبدأ أقل دهشة يقتضي
    أن يكون مصدر الاستعادة هو مجلد النسخ لا أي مكان آخر.
    """
    if not requested:
        return default_path

    backup_dir = Config.BACKUP_DIR.resolve()
    candidate = Path(requested)
    if not candidate.is_absolute():
        candidate = backup_dir / candidate.name

    resolved = candidate.resolve()
    if resolved == backup_dir or backup_dir not in resolved.parents:
        return None
    return resolved


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

    chosen = _resolve_backup(args.backup, backups[0])
    if chosen is None:
        print(t("restore.outside_dir", dir=Config.BACKUP_DIR), file=sys.stderr)
        return 1

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

    _emit(result, args, _print_monitor)
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
            _emit(master.list_jobs(), args, _print_jobs)

        elif args.command == "fix":
            _emit(master.fix_job(args.job_id, retry=not args.no_retry), args, _print_fix)

        elif args.command == "history":
            _emit(master.history(limit=args.limit), args, _print_history)

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

    except Exception as e:  # noqa: BLE001 — الحد الأخير: لا نُخرج traceback خاماً للمستخدم
        logging.getLogger(__name__).exception("استثناء غير متوقع في الأمر %s", args.command)
        print(t("cli.unexpected_error", error=e, log=Config.log_file()), file=sys.stderr)
        sys.exit(EXIT_MONITOR_FAILURE)


if __name__ == "__main__":
    main()

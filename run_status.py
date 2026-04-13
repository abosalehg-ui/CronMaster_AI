#!/usr/bin/env python3
import sys
sys.path.insert(0, '/home/ubuntu/.openclaw/workspace/cronmaster')
from CronMaster_AI import CronMaster

master = CronMaster()
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

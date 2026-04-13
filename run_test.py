#!/usr/bin/env python3
"""اختبار سريع لـ CronMaster_AI"""
import sys
import json
sys.path.insert(0, '/home/ubuntu/.openclaw/workspace/cronmaster')
from CronMaster_AI import CronMaster

master = CronMaster()

# اختبار أمر
print("🧪 اختبار تنفيذ أمر...")
result = master.dry_run_manager.test_command("echo 'مرحبا من CronMaster!'", timeout=10)
print(json.dumps(result, indent=2, ensure_ascii=False))

print("\n" + "=" * 50)

# اختبار أمر فاشل
print("\n🧪 اختبار أمر فاشل...")
result2 = master.dry_run_manager.test_command("exit 1", timeout=5)
print(json.dumps(result2, indent=2, ensure_ascii=False))

"""轻量审批轮询器：自动批准指定任务的所有后续检查点，直到终态。"""

import json
import os
import sys
import time

import requests

tid = sys.argv[1]
BASE = os.environ.get("BACKEND_BASE_URL", "http://localhost:8000")
deadline = time.time() + 50 * 60
while time.time() < deadline:
    time.sleep(8)
    try:
        s = requests.get(f"{BASE}/status", params={"task_id": tid}, timeout=10).json()
    except Exception:
        continue
    phase = s.get("phase")
    if phase in ("completed", "failed", "cancelled"):
        print(f"TERMINAL {phase} | repairs={json.dumps(s.get('repair_rounds'))}", flush=True)
        print("OVERRIDES", json.dumps(s.get("override_history", []), ensure_ascii=False)[:300], flush=True)
        break
    try:
        p = requests.get(f"{BASE}/approval/{tid}", timeout=10).json()
        if p.get("pending"):
            requests.post(f"{BASE}/approval/{tid}", json={"action": "approve"}, timeout=15)
            print(f"APPROVED {p.get('checkpoint')}", flush=True)
    except Exception:
        pass

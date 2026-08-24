"""端到端审批闭环自动化测试：提交任务 → 自动批准全部检查点 → 验证终态与审计。"""

import csv
import io
import json
import os
import random
import time

import requests

BASE = os.environ.get("BACKEND_BASE_URL", "http://localhost:8001")


def build_task():
    random.seed(71)
    rows1 = []
    for day in ["2024-07-11", "2024-07-12"]:
        for i in range(50):
            uid, bid = f"U{i % 10 + 1}", f"B{i % 4 + 1}"
            act = random.choices(["1", "2", "3", "4"], weights=[55, 30, 10, 5])[0]
            hour = random.choices(
                range(24),
                weights=[1, 1, 1, 1, 1, 2, 3, 5, 7, 8, 9, 10, 10, 9, 8, 9, 10, 10, 9, 7, 5, 3, 2, 1],
            )[0]
            rows1.append([f"{day} {hour:02d}:15:00", uid, bid, act])
    buf1 = io.StringIO()
    w = csv.writer(buf1)
    w.writerow(["timestamp", "user_id", "blogger_id", "action_type"])
    w.writerows(rows1)
    rows2 = [
        [
            f"2024-07-13 {random.randint(8, 22):02d}:30:00",
            f"U{i % 8 + 1}",
            f"B{i % 3 + 1}",
            random.choices(["1", "2", "3"], weights=[6, 3, 1])[0],
        ]
        for i in range(40)
    ]
    buf2 = io.StringIO()
    w = csv.writer(buf2)
    w.writerow(["timestamp", "user_id", "blogger_id", "action_type"])
    w.writerows(rows2)
    problem = (
        "某社交媒体平台记录了用户与博主的互动行为，行为类型编码为：1=观看、2=点赞、3=评论、4=关注。"
        "附件1为2024年7月11日至12日的用户行为流水数据，附件2为2024年7月13日的补充数据。"
        "问题1：请基于附件1与附件2，统计各类行为的占比与用户活跃时段分布，建立简单的行为分析模型并给出平台运营建议。"
    )
    return problem, buf1.getvalue().encode(), buf2.getvalue().encode()


def main():
    problem, f1, f2 = build_task()
    r = requests.post(
        f"{BASE}/modeling",
        data={"ques_all": problem, "comp_template": "CHINA", "format_output": "Markdown"},
        files=[("files", ("附件1.csv", f1, "text/csv")), ("files", ("附件2.csv", f2, "text/csv"))],
        timeout=60,
    )
    tid = r.json()["task_id"]
    print(f"TASK {tid}", flush=True)

    approvals = []
    deadline = time.time() + 55 * 60
    while time.time() < deadline:
        time.sleep(10)
        try:
            s = requests.get(f"{BASE}/status", params={"task_id": tid}, timeout=10).json()
        except Exception:
            continue
        phase = s.get("phase")
        if phase in ("completed", "failed", "cancelled"):
            print(f"TERMINAL {phase}", flush=True)
            break
        p = requests.get(f"{BASE}/approval/{tid}", timeout=10).json()
        if p.get("pending"):
            cp = p.get("checkpoint")
            pl = p.get("payload", {})
            if cp == "model_review":
                print(f"ADVISORY {tid} {(pl.get('ai_advisory') or 'none')[:150]}", flush=True)
            r2 = requests.post(
                f"{BASE}/approval/{tid}", json={"action": "approve"}, timeout=15
            )
            if r2.json().get("success"):
                approvals.append(cp)
                print(f"APPROVED {cp}", flush=True)

    # 终态审计输出
    s = requests.get(f"{BASE}/status", params={"task_id": tid}, timeout=10).json()
    print("FINAL_PHASE", s.get("phase"), flush=True)
    print("OVERRIDES", json.dumps(s.get("override_history", []), ensure_ascii=False)[:400], flush=True)
    print("REPAIRS", json.dumps(s.get("repair_rounds", {})), flush=True)
    import pathlib

    vd = pathlib.Path("project/work_dir") / tid / "verify_report.md"
    if vd.exists():
        print("VERIFY_REPORT_BEGIN", flush=True)
        print(vd.read_text(encoding="utf-8")[:1800], flush=True)
        print("VERIFY_REPORT_END", flush=True)


if __name__ == "__main__":
    main()

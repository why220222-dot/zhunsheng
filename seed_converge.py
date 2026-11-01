# -*- coding: utf-8 -*-
"""自动采纳建议直到提交通过，再走审批/锚点/确认"""
import json
import urllib.parse
import urllib.request

B = "http://127.0.0.1:8899"


def H(w):
    r, _, n = w.partition(":")
    return {"X-As": r + (":" + urllib.parse.quote(n) if n else ""), "Content-Type": "application/json"}


def get(p, w="hr"):
    return json.load(urllib.request.urlopen(urllib.request.Request(B + p, headers=H(w)), timeout=120))


def post(p, b, w="hr", timeout=300):
    r = urllib.request.Request(B + p, data=json.dumps(b).encode(), headers=H(w), method="POST")
    try:
        return 200, json.load(urllib.request.urlopen(r, timeout=timeout))
    except Exception as e:
        body = e.read().decode() if hasattr(e, "read") else str(e)
        try:
            body = json.loads(body)
        except Exception:
            pass
        return getattr(e, "code", 0), body


def fix_until_pass(pid, actor, max_rounds=5):
    for i in range(max_rounds):
        c, r = post("/api/goals/submit", {"personId": pid}, actor)
        if c == 200:
            print(f"  {pid} 提交通过（第 {i+1} 轮）")
            return True
        details = r.get("details") if isinstance(r, dict) else None
        if not details:
            print(f"  {pid} 提交失败：{str(r)[:120]}")
            return False
        goals = get("/api/person/" + pid, actor)["goals"]
        by_id = {g["id"]: g for g in goals}
        for d in details:
            if d.get("suggestion"):
                by_id[d["goalId"]]["text"] = d["suggestion"]
                print(f"  {pid} 采纳建议：{d['suggestion'][:36]}…")
        post("/api/goals/save", {"personId": pid, "goals": [
            {"id": g["id"], "text": g["text"], "weight": g["weight"]} for g in goals]}, actor)
    return False


def main():
    post("/api/config", {"stage": "start"})
    jobs = [("p0", "emp:p0", "mgr:李明"), ("p1", "emp:p1", "mgr:李明"),
            ("p9000", "emp:p9000", "skip:王总")]
    for pid, emp, approver in jobs:
        print("==", pid)
        if not fix_until_pass(pid, emp):
            continue
        c, _ = post("/api/goals/review", {"personId": pid, "ok": True}, approver)
        print("  审批", c)
        print("  锚点", post("/api/anchors/batch", {"personId": pid}, approver)[0])
        print("  确认", post("/api/confirm/batch", {"personId": pid}, approver)[0])
    post("/api/config", {"stage": "end"})
    post("/api/compute", {"reason": "演示"})
    for x in get("/api/people"):
        print("  ", x["name"], x["role"], x["total"], x["rating"], x["todo"])


if __name__ == "__main__":
    main()

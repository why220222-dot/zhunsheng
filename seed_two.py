# -*- coding: utf-8 -*-
"""干净重置 2 人演示：p0 走完全流程，p1 留未提交"""
import json
import urllib.parse
import urllib.request

B = "http://127.0.0.1:8899"


def H(who):
    role, _, name = who.partition(":")
    return {"X-As": role + (":" + urllib.parse.quote(name) if name else ""),
            "Content-Type": "application/json"}


def get(p, who="hr"):
    return json.load(urllib.request.urlopen(urllib.request.Request(B + p, headers=H(who)), timeout=120))


def post(p, body, who="hr", timeout=300):
    r = urllib.request.Request(B + p, data=json.dumps(body).encode(), headers=H(who), method="POST")
    try:
        return 200, json.load(urllib.request.urlopen(r, timeout=timeout))
    except Exception as e:
        return getattr(e, "code", 0), (e.read().decode()[:200] if hasattr(e, "read") else str(e))


def main():
    print("reset:", post("/api/reset", {}))
    print("stage:", post("/api/config", {"stage": "start"})[1].get("config", {}).get("stage"))
    ppl = get("/api/people")
    print("人数", len(ppl), [x["name"] for x in ppl])
    print("submit p0:", post("/api/goals/submit", {"personId": "p0"}, "emp:p0"))
    print("approve p0:", post("/api/goals/review", {"personId": "p0", "ok": True}, "mgr:李明")[0])
    print("anchors p0:", post("/api/anchors/batch", {"personId": "p0"}, "mgr:李明")[0])
    print("confirm p0:", post("/api/confirm/batch", {"personId": "p0"}, "mgr:李明"))
    # 年终：给 p0 生成理由，制造"已完成"与"未提交"的对比
    print("stage:", post("/api/config", {"stage": "end"})[1].get("config", {}).get("stage"))
    print("reason p0:", post("/api/reason", {"personId": "p0"}, "mgr:李明")[0])
    pr = get("/api/progress")
    print("progress:", pr["byStatus"])
    for x in get("/api/people"):
        print("  ", x["name"], x["todo"], x["total"], x["rating"])


if __name__ == "__main__":
    main()

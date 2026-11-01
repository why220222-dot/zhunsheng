# -*- coding: utf-8 -*-
"""24 人演示数据：3 人未提交、2 人待审批、19 人已确认锚点"""
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


def post(p, body, who, timeout=300):
    r = urllib.request.Request(B + p, data=json.dumps(body).encode(), headers=H(who), method="POST")
    return json.load(urllib.request.urlopen(r, timeout=timeout))


def main():
    post("/api/config", {"stage": "start"}, "hr")
    people = get("/api/people", "hr")
    for i, p in enumerate(people):
        emp = "emp:" + p["id"]
        mgr = "mgr:" + p["manager"]
        try:
            if i < 3:
                continue                                  # 未提交
            post("/api/goals/submit", {"personId": p["id"]}, emp)
            if i < 5:
                continue                                  # 待审批
            post("/api/goals/review", {"personId": p["id"], "ok": True}, mgr)
            post("/api/anchors/batch", {"personId": p["id"]}, mgr)
            post("/api/confirm/batch", {"personId": p["id"]}, mgr)
        except Exception as e:
            print("skip", p["id"], p["name"], str(e)[:80])
    post("/api/config", {"stage": "end"}, "hr")
    pr = get("/api/progress")
    print("进度:", pr["byStatus"], "| 待审批变更", pr["pendingChanges"])


if __name__ == "__main__":
    main()

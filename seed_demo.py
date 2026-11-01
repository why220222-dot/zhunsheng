# -*- coding: utf-8 -*-
"""把演示状态调成"真实的样子"：大部分人锚点已确认，留 3 个待确认"""
import json, urllib.parse, urllib.request

B = "http://127.0.0.1:8899"

def H(who):
    role, _, name = who.partition(":")
    return {"X-As": role + (":" + urllib.parse.quote(name) if name else ""),
            "Content-Type": "application/json"}

def get(p, who="hr"):
    return json.load(urllib.request.urlopen(urllib.request.Request(B + p, headers=H(who)), timeout=180))

def post(p, body, who):
    r = urllib.request.Request(B + p, data=json.dumps(body).encode(),
                               headers=H(who), method="POST")
    return json.load(urllib.request.urlopen(r, timeout=300))

post("/api/config", {"stage": "start"}, "hr")
people = get("/api/people", "mgr:李明")
leave = 3
done = 0
for p in people[leave:]:
    post("/api/anchors/batch", {"personId": p["id"]}, "mgr:李明")
    post("/api/confirm/batch", {"personId": p["id"]}, "mgr:李明")
    done += 1
left = [p["name"] for p in people[:leave]]
print(f"已确认 {done} 人，留 {len(left)} 人待确认：{left}")

# 顺带确认几个人的评分完整性：给 3 个人生成理由
for p in people[leave:leave+3]:
    try:
        post("/api/reason", {"personId": p["id"]}, "mgr:李明")
    except Exception as e:
        print("reason skip", p["name"], e)
post("/api/config", {"stage": "end"}, "hr")
print("done")

# -*- coding: utf-8 -*-
"""预热：用后台任务生成全部锚点（限并发 4），再批量确认，最后回到年终阶段"""
import json
import time
import urllib.parse
import urllib.request

B = "http://127.0.0.1:8899"
MAXX = 4


def H(who):
    role, _, name = who.partition(":")
    return {"X-As": role + (":" + urllib.parse.quote(name) if name else ""),
            "Content-Type": "application/json"}


def get(p, who="hr"):
    return json.load(urllib.request.urlopen(urllib.request.Request(B + p, headers=H(who)), timeout=60))


def post(p, body, who, timeout=120):
    r = urllib.request.Request(B + p, data=json.dumps(body).encode(),
                               headers=H(who), method="POST")
    return json.load(urllib.request.urlopen(r, timeout=timeout))


def main():
    post("/api/config", {"stage": "start"}, "hr")
    people = get("/api/people", "mgr:李明")
    todo = []
    for p in people:
        d = get("/api/person/" + p["id"], "mgr:李明")
        for g in d["goals"]:
            todo.append((p["id"], g["id"]))
    print("目标总数", len(todo))

    running = []
    started = 0
    done = 0
    while todo or running:
        while todo and len(running) < MAXX:
            pid, gid = todo.pop(0)
            try:
                r = post("/api/anchors/async", {"personId": pid, "goalId": gid}, "mgr:李明")
                running.append(r["jobId"])
                started += 1
            except Exception as e:
                print("start fail", pid, gid, str(e)[:80])
        time.sleep(2)
        still = []
        for jid in running:
            try:
                j = get("/api/job/" + jid)
            except Exception:
                still.append(jid)
                continue
            if j["status"] == "running":
                still.append(jid)
            else:
                done += 1
        running = still
        print(f"  已启动 {started} 完成 {done} 进行中 {len(running)} 待启动 {len(todo)}")

    # 确认锚点：前 17 人确认，最后 3 人留待演示
    ok = 0
    for p in people[:17]:
        try:
            r = post("/api/confirm/batch", {"personId": p["id"]}, "mgr:李明")
            ok += r.get("confirmed", 0)
        except Exception as e:
            print("confirm fail", p["id"], str(e)[:80])
    print("确认锚点数", ok)
    post("/api/config", {"stage": "end"}, "hr")
    print("done, stage -> end")


if __name__ == "__main__":
    main()

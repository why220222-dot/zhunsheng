# -*- coding: utf-8 -*-
"""预热演示数据：为 8 位重点人物生成锚点与评级理由，并输出 demo.md"""
import json
import sys
import time
import urllib.request

B = "http://127.0.0.1:8899"

def get(p):
    return json.load(urllib.request.urlopen(B + p, timeout=120))

def post(p, body, timeout=300):
    req = urllib.request.Request(B + p, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    return json.load(urllib.request.urlopen(req, timeout=timeout))

def main():
    people = get("/api/people")
    ov = get("/api/overview")
    cut = {c["id"] for c in ov["cutline"]}
    A = [p for p in people if p["rating"] == "A"]
    C = [p for p in people if p["rating"] == "C"]
    B_cut = [p for p in people if p["id"] in cut]
    picks = []
    picks += A[:2]
    picks += B_cut[:3]
    picks += C[:2]
    mids = [p for p in people if p["rating"] == "B" and p["id"] not in cut]
    picks += mids[:1]
    seen, uniq = set(), []
    for p in picks:
        if p["id"] not in seen:
            seen.add(p["id"]); uniq.append(p)
    print("picked:", [(p["name"], p["dept"], p["rating"], p["total"]) for p in uniq])

    t0 = time.time()
    ok = 0
    for p in uniq:
        try:
            r = post("/api/anchors/batch", {"personId": p["id"]})
            ok += len([x for x in r.get("results", []) if x.get("ok")])
        except Exception as e:
            print("  anchors fail", p["id"], e)
    print(f"anchors warmed: {ok} goals in {round(time.time()-t0,1)}s")

    t1 = time.time()
    rows = []
    for p in uniq:
        try:
            r = post("/api/reason", {"personId": p["id"]})
            rows.append((p, r))
            print(f"  reason {p['name']} passed={r['passed']} sents={len(r['sentences'])}")
        except Exception as e:
            print("  reason fail", p["id"], e)
    print(f"reasons: {len(rows)} in {round(time.time()-t1,1)}s")

    # 写 demo.md
    L = ["# 演示数据说明（自动生成）", "",
         f"生成时间：{time.strftime('%Y-%m-%d %H:%M')}",
         f"演示人数：{len(uniq)}（总库 200 人）", "",
         "| 姓名 | 部门 | 岗位 | 总分 | 评级 | 排名/名额 | 用途 |",
         "|---|---|---|---|---|---|---|"]
    for p in uniq:
        r = get("/api/person/" + p["id"])["rating"]
        use = "A 样本" if p["rating"] == "A" else ("卡线样本" if p["id"] in cut else ("C 样本" if p["rating"] == "C" else "普通样本"))
        L.append(f"| {p['name']} | {p['dept']} | {p['role']} | {p['total']} | {p['rating']} | "
                 f"{r['rank']}/{r['deptCount']}·名额{r['quota']} | {use} |")
    L += ["", "## 推荐演示路径", "",
          "1. `#/hr` 看分布：某部门 A 超名额 N 人", "2. 点争议名单里第一个人的「看理由」",
          "3. 点「生成评级理由」，看逐句引用；点 G1/E1 展开原文",
          "4. 切到「年底·打分」改一个人的分数，回来看排名与评级变化",
          "5. 打开「审计」看刚才的每一步"]
    open(r"D:\Uesdate\DEEPSEEK\比赛\app\demo.md", "w", encoding="utf-8").write("\n".join(L))
    print("demo.md written")

if __name__ == "__main__":
    main()

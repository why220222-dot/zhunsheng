# -*- coding: utf-8 -*-
"""准绳自测 v2：从干净数据开始，一条流程跑到底，只输出 PASS/FAIL。

用法：先起服务（默认 8899），然后 python selftest.py
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

B = "http://127.0.0.1:8899"
PASS, FAIL = [], []


def H(who):
    role, _, name = who.partition(":")
    return {"X-As": role + (":" + urllib.parse.quote(name) if name else "")}


def req(path, who="hr", body=None, timeout=180):
    data = json.dumps(body).encode() if body is not None else None
    h = H(who)
    if data:
        h["Content-Type"] = "application/json"
    r = urllib.request.Request(B + path, data=data, headers=h,
                               method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw[:200]
    except Exception as e:
        return 0, str(e)


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (("  << " + str(detail)[:140]) if not cond else ""))


def goal(payload_id, text, target, baseline, unit, cycle, source, weight=100, direction="higher", name="指标"):
    return {"id": payload_id, "text": text, "weight": weight,
            "fields": {"name": name, "target": target, "baseline": baseline,
                       "unit": unit, "direction": direction, "cycle": cycle,
                       "source": source, "scope": "本人负责部分"}}


def main():
    t0 = time.time()

    # 0 清空 + 切到年初
    s, _ = req("/api/reset", "hr", {"reason": "自测"})
    check("HR 可清空数据", s == 200, s)
    s, _ = req("/api/config", "hr", {"stage": "start"})
    check("HR 可切阶段", s == 200, s)

    s, h = req("/api/health")
    check("health 200", s == 200)
    check("默认身份 HR", h.get("persona", {}).get("role") == "hr", h.get("persona"))

    # 1 可见范围
    s, allp = req("/api/people", "hr")
    check("HR 看到全部人", s == 200 and len(allp) >= 3, len(allp) if s == 200 else s)
    subs = [x for x in allp if x.get("manager") == "李明"]
    s, mgr_view = req("/api/people", "mgr:李明")
    check("领导看到 下属+本人", s == 200 and len(mgr_view) == len(subs) + 1,
          (len(subs) + 1, len(mgr_view) if s == 200 else s))
    s, emp_view = req("/api/people", "emp:p0")
    check("员工只看到自己", s == 200 and len(emp_view) == 1, len(emp_view) if s == 200 else s)

    # 2 越权
    s, _ = req("/api/person/p1", "emp:p0")
    check("员工看他人 403", s == 403, s)
    s, _ = req("/api/person/nope", "mgr:李明")
    check("看不存在的人 404", s == 404, s)
    s, _ = req("/api/audit", "mgr:李明")
    check("领导看审计 403", s == 403, s)
    s, _ = req("/api/appeals", "emp:p0")
    check("员工看申诉清单 403", s == 403, s)

    # 3 员工权限
    s, d = req("/api/person/p0", "emp:p0")
    perm = d.get("permissions", {}) if s == 200 else {}
    check("员工不能确认锚点", perm.get("confirm") is False, perm)
    check("员工不能打分", perm.get("score") is False, perm)
    check("员工可申诉", perm.get("appeal") is True, perm)

    # 4 填目标 → 提交 → 批准 → 锚点 → 确认
    g1 = goal("gt_1", "按 APM 每月统计，接口 P99 从 900ms 降到 400ms 以内",
              "400", "900", "ms", "月度", "APM", direction="lower",
              name="接口 P99 延迟")
    s, r = req("/api/goals/save", "emp:p0", {"personId": "p0", "goals": [g1]})
    check("员工保存目标", s == 200, r)

    s, r = req("/api/goals/submit", "emp:p0", {"personId": "p0"})
    check("员工提交目标", s == 200, r)

    s, r = req("/api/goals/review", "mgr:李明", {"personId": "p0", "ok": True})
    check("领导批准目标", s == 200, r)

    s, r = req("/api/anchors", "mgr:李明", {"personId": "p0", "goalId": "gt_1"})
    ok = s == 200 and isinstance(r, dict) and len((r.get("result") or {}).get("anchors", [])) == 3
    check("领导生成锚点三档", ok, r if not ok else "")

    s, r = req("/api/confirm", "mgr:李明", {"personId": "p0", "goalId": "gt_1"})
    check("领导确认锚点", s == 200, r)
    s, _ = req("/api/confirm", "emp:p0", {"personId": "p0", "goalId": "gt_1"})
    check("员工确认锚点 403", s == 403, s)

    # 5 阶段限制
    s, _ = req("/api/goals/submit", "emp:p0", {"personId": "p0"})
    check("已确认后不能再提交 400", s == 400, s)
    s, _ = req("/api/anchors", "mgr:李明", {"personId": "p0", "goalId": "gt_1"})
    check("已确认后重生成锚点 400", s == 400, s)

    # 6 年终打分
    req("/api/config", "hr", {"stage": "end"})
    s, r = req("/api/score", "mgr:李明", {"personId": "p0", "goalId": "gt_1", "value": 96})
    check("领导打分", s == 200 and "total" in r, r)
    s, _ = req("/api/score", "emp:p0", {"personId": "p0", "goalId": "gt_1", "value": 99})
    check("员工打分 403", s == 403, s)
    s, _ = req("/api/score", "mgr:李明", {"personId": "p0", "goalId": "gt_1", "value": 120})
    check("超范围分数 400", s == 400, s)
    s, _ = req("/api/score", "skip:王总", {"personId": "p0", "goalId": "gt_1", "value": 90})
    check("单评模式隔级打分被拒", s in (400, 403), s)

    # 7 理由 / 申诉 / 导出
    s, r = req("/api/reason", "mgr:李明", {"personId": "p0"})
    ok = s == 200 and isinstance(r, dict) and r.get("sentences")
    check("生成评级理由", ok, r if not ok else "")
    if ok:
        check("每句都有引用", all(x.get("cites") for x in r["sentences"]))
    s, _ = req("/api/reason", "emp:p0", {"personId": "p0"})
    check("员工生成理由 403", s == 403, s)

    s, r = req("/api/appeal", "emp:p0", {"personId": "p0", "text": "自测：对评分有异议"})
    check("员工可申诉", s == 200, r)
    s, _ = req("/api/appeal", "emp:p0", {"personId": "p1", "text": "越权"})
    check("替他人申诉 403", s == 403, s)
    s, lst = req("/api/appeals", "hr")
    check("HR 看到申诉", s == 200 and len(lst) >= 1, lst)
    if isinstance(lst, list) and lst:
        s, _ = req("/api/appeal/resolve", "hr",
                   {"id": lst[0]["id"], "status": "已处理", "reply": "自测"})
        check("HR 处理申诉", s == 200, s)

    s, md = req("/api/export/p0", "mgr:李明")
    check("导出 Markdown", s == 200 and "评级" in str(md), str(md)[:80])
    s, _ = req("/api/export/p1", "emp:p0")
    check("员工导出他人 403", s == 403, s)

    # 8 概览
    ov = req("/api/overview", "hr")[1]
    check("概览可读", isinstance(ov, dict), type(ov))
    if isinstance(ov, dict):
        rated = sum(ov.get("hist", {}).values())
        check("分布总数合理", 1 <= rated <= len(allp), ov.get("hist"))
        check("有经理统计", len(ov.get("mgr_stats", [])) >= 1, ov.get("mgr_stats"))

    # 9 年中变更
    req("/api/config", "hr", {"stage": "mid"})
    s, r = req("/api/change", "emp:p0", {"personId": "p0", "goalId": "gt_1",
                                         "text": "自测：目标调整", "evidence": "https://example.com/x",
                                         "newText": "按 APM 每月统计，接口 P99 从 900ms 降到 500ms 以内",
                                         "fields": dict(g1["fields"], target="500")})
    check("员工提交变更", s == 200, r)
    cid = (r.get("change") or {}).get("id") if isinstance(r, dict) else None
    if cid:
        s, d0 = req("/api/person/p0", "mgr:李明")
        v0 = next((g["version"] for g in d0["goals"] if g["id"] == "gt_1"), "v1")
        s, _ = req("/api/change/approve", "mgr:李明", {"id": cid, "ok": True})
        check("领导批准变更", s == 200, s)
        s, d = req("/api/person/p0", "mgr:李明")
        ver = next((g["version"] for g in d["goals"] if g["id"] == "gt_1"), "")
        check("批准后版本 +1", int(ver[1:]) == int(v0[1:]) + 1, (v0, ver))
    else:
        check("变更返回 id", False, r)

    # 10 HR 专属
    s, _ = req("/api/config", "mgr:李明", {"mode": "dual"})
    check("非 HR 改配置 403", s == 403, s)
    s, r = req("/api/config", "hr", {"mode": "dual"})
    check("HR 切双评", s == 200, r)
    s, r = req("/api/config", "hr", {"mode": "single"})
    check("HR 切回单评", s == 200, r)

    # 11 对话
    s, r = req("/api/chat", "emp:p0", {"message": "我现在要干什么？", "history": []})
    ok = s == 200 and isinstance(r, dict) and len(str(r.get("reply", ""))) > 4
    check("对话可回答", ok, r if not ok else "")
    if ok:
        reply = r["reply"]
        check("对话不泄漏内部编号", "p0" not in reply and "gt_1" not in reply, reply[:80])
        check("对话不带 markdown", "**" not in reply and "DSML" not in reply, reply[:80])

    # 12 静态
    s, html = req("/")
    check("首页可访问", s == 200 and "准绳" in str(html))
    s, _ = req("/.env")
    check("不能读到 .env", s == 404, s)

    # 收尾：恢复演示数据
    req("/api/demo/load", "hr", {})
    req("/api/config", "hr", {"stage": "start"})

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed, {round(time.time()-t0,1)}s")
    if FAIL:
        print("失败项：" + "; ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
准绳 · 绩效评审智能体 —— MVP 后端
- 零依赖（仅标准库）
- 四种身份：员工 / 直属领导 / 隔级领导 / HR，服务端强制权限边界
启动： python server.py [--port 8899] [--host 0.0.0.0]
"""
import base64
import html
import json
import os
import threading
import uuid
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent
STATIC = BASE / "static"
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)
UPLOAD_DIR = DATA / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
CACHE_FILE = DATA / "anchors_cache.json"
STATE_FILE = DATA / "state.json"

def load_env():
    env = {}
    p = BASE / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env

ENV = load_env()
API_KEY = ENV.get("GENESIS_API_KEY") or os.environ.get("GENESIS_API_KEY", "")
BASE_URL = ENV.get("GENESIS_BASE_URL", "https://token.moi.matrixorigin.cn/v1")
MODEL = ENV.get("GENESIS_MODEL", "qwen3.8-flash")
MODEL_FALLBACK = ["qwen3.8-max", "glm-5.3", "qwen3.8-flash"]
DEMO_PEOPLE = int(os.environ.get("DEMO_PEOPLE") or ENV.get("DEMO_PEOPLE") or 24)

# ---------------- 组织数据 ----------------
SURNAME = list("王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗")
GIVEN = list("伟芳娜秀英敏静丽强磊洋艳勇军杰娟涛明超霞平刚桂")
ROLES = [
    ("后端开发", "研发二部", [
        "按 APM 日报、每季度统计：生产环境订单接口 P99 从 820ms 降到 300ms 以内",
        "按缺陷系统季度报表：季度线上缺陷数从 12 个降到 5 个以内",
        "按故障系统年度记录：主导 1 次稳定性改造（以方案评审通过并上线验收为准，范围为本组后端服务），P1 故障全年不超过 1 次",
    ]),
    ("前端开发", "研发二部", [
        "首屏 LCP 从 3.4s 降到 2.0s 以内",
        "组件库复用率从 40% 提升到 70% 以上",
        "与后端联调返工不超过 2 次/季度",
    ]),
    ("测试工程师", "质量部", [
        "核心链路自动化覆盖率达到 80% 以上",
        "版本回归周期从 3 天缩短到 1 天以内",
        "线上问题复盘闭环率 100%，48 小时内完成",
    ]),
    ("产品经理", "产品部", [
        "按需求系统季度报表：需求验收一次通过率从 85% 提升到 95% 以上",
        "按留存分析平台季度数据：主导 2 个关键需求上线，留存提升 ≥3 个百分点",
        "按需求系统季度报表：需求平均返工次数控制在 1 次以内（返工＝上线 30 天内被判定需重做的需求）",
    ]),
    ("销售", "销售部", [
        "年度回款完成率 100% 以上（目标 1200 万）",
        "新签客户不少于 12 家，其中单笔 ≥50 万 2 家",
        "老客户续约率从 85% 提升到 90% 以上",
    ]),
    ("客服", "客户成功部", [
        "月度 CSAT 从 88% 提升到 95% 以上",
        "首响时间从 60 秒压缩到 30 秒以内",
        "投诉闭环率 98% 以上，超时工单不超过 2%",
    ]),
]
MANAGERS = ["李明", "王芳", "张磊", "刘洋", "陈静", "赵强", "孙丽", "周军", "吴敏", "郑涛"]
SKIP_OF = {"王总": ["李明", "王芳", "张磊", "刘洋", "陈静"],
           "陈总": ["赵强", "孙丽", "周军", "吴敏", "郑涛"]}
MGR_DIRECTOR = {m: d for d, ms in SKIP_OF.items() for m in ms}
ROLE_EVENTS = {
    "后端开发": ["Q2 主导订单服务拆分，接口 P99 从 820ms 降到 210ms", "全年处理 P1 故障 2 起，均在 30 分钟内恢复"],
    "前端开发": ["Q3 完成首屏优化，LCP 从 3.4s 降到 1.9s", "推动组件库复用，重复开发工时减少约 120 小时"],
    "测试工程师": ["搭建自动化回归流水线，回归周期从 3 天缩短到 6 小时", "全年拦截线上缺陷 37 个，其中高危 6 个"],
    "产品经理": ["主导会员体系改版上线，次月留存提升 4.2 个百分点", "完成 3 个季度需求验收，验收通过率 96%"],
    "销售": ["新签客户 23 家，年度回款完成率 108%", "老客户续约率 91%，同比提升 6 个百分点"],
    "客服": ["推动知识库自助解决率从 41% 提升到 63%", "全年投诉闭环率 98%，平均首响 28 秒"],
}
BIAS = {m: round(random.Random(i * 7).uniform(-4.5, 5.5), 1) for i, m in enumerate(MANAGERS)}

def gen_people(n=None, seed=42):
    n = n or DEMO_PEOPLE
    rnd = random.Random(seed)
    people = []
    # 让相邻的人落在不同部门：0,3,1,4,2,5 循环
    half = len(ROLES) // 2
    order = []
    for k in range(half):
        order.append(k)
        order.append(k + half)
    # 领导数量随人数缩减，人少时不要一堆领导
    mgr_count = max(1, min(len(MANAGERS), round(n / 2) or 1))
    group = max(1, -(-n // mgr_count))       # 向上取整
    for i in range(n):
        role, dept, goals = ROLES[order[i % len(order)]]
        name = rnd.choice(SURNAME) + rnd.choice(GIVEN)
        manager = MANAGERS[(i // group) % mgr_count]
        gs = [{"id": f"g{i}_{j}", "text": g, "weight": [40, 35, 25][j],
               "anchor": None, "status": "draft", "version": "v1",
               "fields": GOAL_FIELDS.get(g, {}), "kind": "metric"}
              for j, g in enumerate(goals)]
        ev = ROLE_EVENTS.get(role, [])
        events = [{"id": f"e{i}_{k}", "text": t, "date": "2026"} for k, t in enumerate(ev)]
        people.append({"id": f"p{i}", "name": name, "role": role, "dept": dept,
                       "manager": manager, "director": MGR_DIRECTOR.get(manager, "王总"),
                       "goals": gs, "events": events})
    return people

DEMO_METRICS = {
    "p0": {
        "g0_0": [
            {"id": "m1", "name": "线上缺陷率", "unit": "%", "type": "lower",
             "target": 1.0, "a_line": 0.6, "c_line": 1.5, "source": "缺陷系统月报", "weight": 60},
            {"id": "m2", "name": "P1 及以上故障次数", "unit": "次", "type": "gate",
             "target": 0, "a_line": 0, "c_line": 1, "direction": "lower",
             "source": "故障系统", "weight": 40, "fail_cap": 0},
        ]
    },
    "p3": {
        "g3_0": [
            {"id": "m1", "name": "需求验收通过率", "unit": "%", "type": "higher",
             "target": 90, "a_line": 98, "c_line": 80, "source": "需求系统", "weight": 70},
            {"id": "m2", "name": "需求返工次数", "unit": "次", "type": "lower",
             "target": 2, "a_line": 1, "c_line": 4, "source": "需求系统",
             "weight": 30, "proxy": True},
        ]
    },
}


GOAL_FIELDS = {
    "按 APM 日报、每季度统计：生产环境订单接口 P99 从 820ms 降到 300ms 以内": {
        "name": "订单接口 P99", "target": "300", "unit": "ms", "baseline": "820",
        "direction": "lower", "cycle": "季度", "source": "APM 日报（SkyWalking）",
        "scope": "生产环境 /order/* 接口"},
    "按缺陷系统季度报表：季度线上缺陷数从 12 个降到 5 个以内": {
        "name": "季度线上缺陷数", "target": "5", "unit": "个", "baseline": "12",
        "direction": "lower", "cycle": "季度", "source": "缺陷管理系统",
        "scope": "本组负责的后端服务"},
    "按故障系统年度记录：主导 1 次稳定性改造（以方案评审通过并上线验收为准，范围为本组后端服务），P1 故障全年不超过 1 次": {
        "name": "P1 故障次数", "target": "1", "unit": "次", "baseline": "2",
        "direction": "lower", "cycle": "年度", "source": "故障系统",
        "scope": "本组后端服务；改造以方案评审+上线验收为准"},
    "按需求系统季度报表：需求验收一次通过率从 85% 提升到 95% 以上": {
        "name": "需求验收一次通过率", "target": "95", "unit": "%", "baseline": "85",
        "direction": "higher", "cycle": "季度", "source": "需求管理系统",
        "scope": "本人负责的全部需求"},
    "按留存分析平台季度数据：主导 2 个关键需求上线，留存提升 ≥3 个百分点": {
        "name": "关键需求上线条数 / 留存提升", "target": "2 条（留存 +3pp）", "unit": "",
        "baseline": "0 条", "direction": "target", "cycle": "季度",
        "source": "留存分析平台", "scope": "本人负责的核心路径需求"},
    "按需求系统季度报表：需求平均返工次数控制在 1 次以内（返工＝上线 30 天内被判定需重做的需求）": {
        "name": "需求平均返工次数", "target": "1", "unit": "次", "baseline": "2",
        "direction": "lower", "cycle": "季度", "source": "需求管理系统",
        "scope": "本人负责上线的需求，返工按 30 天内判定"},
}

MGR_GOALS = [
    ("每月按项目管理系统月报，团队按期交付率达到 95% 以上", 40, {
        "name": "团队按期交付率", "target": "95", "unit": "%", "baseline": "90",
        "direction": "higher", "cycle": "月度", "source": "项目管理系统月报",
        "scope": "本部门全部在管项目"}),
    ("每季度按系统台账，A 名额使用与校准会议记录完整率 100%", 35, {
        "name": "A 名额使用与校准记录完整率", "target": "100", "unit": "%", "baseline": "80",
        "direction": "higher", "cycle": "季度", "source": "绩效系统台账",
        "scope": "本部门全部 A 名额使用记录"}),
    ("按 HR 系统年度数据，团队主动离职率控制在 8% 以内", 25, {
        "name": "团队主动离职率", "target": "8", "unit": "%", "baseline": "12",
        "direction": "lower", "cycle": "年度", "source": "HR 系统离职记录",
        "scope": "本部门在册正式员工（不含试用期）"}),
]


def build_managers():
    """直属领导也参与被评：由隔级领导打分，目标是团队级指标"""
    out = []
    used = sorted({p["manager"] for p in PEOPLE})
    for i, m in enumerate(used):
        dept = next((p["dept"] for p in PEOPLE if p["manager"] == m), "—")
        director = MGR_DIRECTOR.get(m, "王总")
        goals = [{"id": f"gm{i}_{k}", "text": t, "weight": w, "anchor": None,
                  "status": "draft", "version": "v1", "fields": f, "kind": "metric"}
                 for k, (t, w, f) in enumerate(MGR_GOALS)]
        out.append({"id": f"p900{i}", "name": m, "role": "部门负责人", "dept": dept,
                    "manager": director, "director": director, "isManager": True,
                    "goals": goals,
                    "events": [{"id": f"em{i}_0", "text": "主持 4 次季度校准会，名额使用与会议记录齐备", "date": "2026"},
                               {"id": f"em{i}_1", "text": "团队年度交付 12 个版本，按期率 96%", "date": "2026"}]})
    return out


def attach_demo_metrics():
    for pid, goals in DEMO_METRICS.items():
        per = BY_ID.get(pid)
        if not per:
            continue
        for g in per["goals"]:
            if g["id"] in goals:
                g["metrics"] = goals[g["id"]]


PEOPLE = gen_people()
BY_ID = {p["id"]: p for p in PEOPLE}


def restore_goals():
    saved = STATE.get("goals") or {}
    cleared = STATE.get("cleared")
    for per in PEOPLE:
        if not cleared:
            continue
        per["goals"] = saved.get(per["id"], [])
    if not cleared:
        for pid, gs in saved.items():
            per = BY_ID.get(pid)
            if per and gs:
                per["goals"] = gs
attach_demo_metrics()
PEOPLE.extend(build_managers())
BY_ID = {p["id"]: p for p in PEOPLE}
DEPT_NAMES = sorted({p["dept"] for p in PEOPLE})

DEFAULT_CONFIG = {"mode": "single", "quota_ratio": 0.15, "a_threshold": 95.0,
                  "c_threshold": 80.0, "rater_weights": {"直属领导": 60, "隔级领导": 40},
                  "stage": "end",   # start 年初填报 / mid 年中变更 / end 年终考核
                  "summary_min_words": 0,   # 年度自评最低字数，0 = 不要求
                  "qualitative_cap": 0.2}   # 定性目标权重上限

def fresh_state():
    _cfg = dict(DEFAULT_CONFIG)
    _cfg["stage"] = "start"
    return {"config": _cfg, "scores": {}, "ratings": {}, "reasons": {},
            "audit": [], "appeals": [], "changes": [], "selfsummaries": {}, "actuals": {}, "seeded": False}

def load_state():
    if STATE_FILE.exists():
        try:
            s = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            s.setdefault("config", dict(DEFAULT_CONFIG))
            s.setdefault("appeals", [])
            s.setdefault("changes", [])
            s.setdefault("selfsummaries", {})
            s.setdefault("actuals", {})
            return s
        except Exception:
            pass
    return fresh_state()

STATE = load_state()

def save_state():
    try:
        STATE["goals"] = {p["id"]: p["goals"] for p in PEOPLE}
    except Exception:
        pass
    STATE_FILE.write_text(json.dumps(STATE, ensure_ascii=False, indent=1), encoding="utf-8")

def audit(actor, action, target, before=None, after=None, reason=""):
    STATE["audit"].append({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "actor": actor,
                           "action": action, "target": target, "before": before,
                           "after": after, "reason": reason})
    save_state()

def load_cache():
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

CACHE = load_cache()

def save_cache():
    CACHE_FILE.write_text(json.dumps(CACHE, ensure_ascii=False, indent=2), encoding="utf-8")

# ---------------- 身份与权限 ----------------
class Persona:
    def __init__(self, raw):
        raw = (raw or "").strip() or "hr"
        self.raw = raw
        role, _, who = raw.partition(":")
        who = urllib.parse.unquote(who)
        self.role = role if role in ("hr", "emp", "mgr", "skip") else "hr"
        self.who = who
        self.person = BY_ID.get(who) if self.role == "emp" else None
        self.self_person = None
        if self.role == "mgr":
            self.self_person = next(
                (x for x in PEOPLE if x.get("isManager") and x["name"] == who), None)
        if self.role == "emp" and not self.person:
            self.role, self.who = "invalid", who      # 绝不退化成 HR

    @property
    def label(self):
        if self.role == "invalid":
            return "未知身份（已失效）"
        if self.role == "hr":
            return "HR 管理员"
        if self.role == "emp":
            return f"{self.person['name']}（员工）"
        if self.role == "mgr":
            dept = (self.self_person or {}).get("dept")
            return f"{self.who} · {dept}领导" if dept else f"{self.who}（直属领导）"
        return f"{self.who}（隔级领导）"

    def scope(self):
        if self.role == "invalid":
            return []
        if self.role == "hr":
            return PEOPLE
        if self.role == "mgr":
            subs = [x for x in PEOPLE if x["manager"] == self.who and not x.get("isManager")]
            return subs + ([self.self_person] if self.self_person else [])
        if self.role == "emp":
            return [self.person]
        if self.role == "mgr":
            return [p for p in PEOPLE if p["manager"] == self.who]
        return [p for p in PEOPLE if p["director"] == self.who]

    def can(self, action, person=None):
        if self.role == "invalid":
            return False
        if self.role == "hr":
            return True
        if person is None:
            return True
        in_scope = any(p["id"] == person["id"] for p in self.scope())
        if self.role == "mgr" and self.self_person and person["id"] == self.self_person["id"]:
            return action in ("view", "reason")
        if action == "view":
            return in_scope
        if action == "reason":          # 理由由打分方生成，员工只读
            return self.role in ("mgr", "skip") and in_scope
        if action == "confirm_anchor":
            return (self.role == "mgr" or (self.role == "skip" and person.get("isManager"))) and in_scope
        if action == "score_direct":
            return self.role == "mgr" and in_scope
        if action == "score_skip":
            return self.role == "skip" and in_scope
        if action == "appeal":
            return self.role == "emp" and self.person and person["id"] == self.person["id"]
        if action == "approve_change":
            return self.role == "mgr" and in_scope
        return False

STAGE_NAMES = {"start": "年初 · 绩效填报", "mid": "年中 · 目标回顾与变更", "end": "年终 · 考核与结果"}

def stage():
    return STATE["config"].get("stage", "end")

def need_stage(*allowed):
    s = stage()
    if s not in allowed:
        raise ValueError(f"当前阶段是「{STAGE_NAMES.get(s, s)}」，该操作只在{'／'.join(STAGE_NAMES[a] for a in allowed)}可用")
    return s

def raters():
    cfg = STATE["config"]
    if cfg["mode"] == "single":
        return [("直属领导", 100)]
    w = cfg["rater_weights"]
    return [("直属领导", w.get("直属领导", 60)), ("隔级领导", w.get("隔级领导", 40))]

def sim_score(person, goal):
    rnd = random.Random(hash((person["id"], goal["id"])) & 0xffffffff)
    base = 89 + rnd.random() * 7
    base += BIAS.get(person["manager"], 0)
    n = int(str(person["id"])[1:])
    if n % 37 == 0:
        base -= 14
    return max(55, min(100, round(base, 1)))

def ensure_scores():
    """不再自动生成分数：分数必须由领导打，或由指标自动算。
    保留函数以兼容旧调用点。"""
    return


# ---------------- 结构化口径 ----------------
FIELD_REQUIRED = [("name", "指标"), ("target", "目标值"), ("cycle", "统计周期"), ("source", "数据来源")]
FIELD_OPTIONAL = [("baseline", "基线"), ("scope", "统计范围"), ("unit", "单位")]
CYCLE_OPTIONS = ["月度", "季度", "半年", "年度"]
DIRECTIONS = {"lower": "越低越好", "higher": "越高越好", "target": "达标型"}


def goal_fields(g):
    return g.get("fields") or {}


def goal_missing_fields(g):
    """返回缺失的字段名列表（定性目标不要求）"""
    if g.get("kind") == "qualitative":
        return []
    f = goal_fields(g)
    return [label for key, label in FIELD_REQUIRED if not str(f.get(key) or "").strip()]


def build_anchor_from_fields(g):
    """按口径直接生成三档锚点：确定性、可复算、不再让模型编"""
    f = goal_fields(g)
    name = f.get("name") or "该指标"
    target = str(f.get("target") or "").strip()
    base = str(f.get("baseline") or "").strip()
    unit = str(f.get("unit") or "").strip()
    direction = f.get("direction") or "target"
    ev = "证据：" + "；".join(x for x in [
        ("数据来源 " + f["source"]) if f.get("source") else "",
        ("统计周期 " + f["cycle"]) if f.get("cycle") else "",
        (("基线 " + base + unit + " → 目标 " + target + unit) if base else
         (("目标 " + target + unit) if target else "")),
        ("范围 " + f["scope"]) if f.get("scope") else "",
    ] if x)

    if direction == "lower":
        a = f"{name} ≤ {target}{unit}" + (f"（基线 {base}{unit}）" if base else "")
        if base:
            b = f"{name} 在 {target}{unit} 与 {base}{unit} 之间"
            c = f"{name} > {base}{unit}"
        else:
            b, c = f"{name} 略高于 {target}{unit}", f"{name} 明显高于 {target}{unit}"
    elif direction == "higher":
        a = f"{name} ≥ {target}{unit}" + (f"（基线 {base}{unit}）" if base else "")
        if base:
            b = f"{name} 在 {base}{unit} 与 {target}{unit} 之间"
            c = f"{name} < {base}{unit}"
        else:
            b, c = f"{name} 略低于 {target}{unit}", f"{name} 明显低于 {target}{unit}"
    else:                                   # 达标型
        a = f"{name} 达到 {target}{unit}"
        b = f"{name} 基本达到（差距在一档以内）"
        c = f"{name} 未达到 {target}{unit}"

    return {"verdict": "ok", "problem": "", "source": "rule",
            "anchors": [
                {"level": 4, "desc": a + "，本项得 ≥95 分", "ev": ev, "evidence": ev},
                {"level": 3, "desc": b + "，本项得 80–94.9 分", "ev": ev, "evidence": ev},
                {"level": 2, "desc": c + "，本项得 <80 分", "ev": ev, "evidence": ev},
            ]}


# ---------------- 自然语言 → 口径表 ----------------
PARSE_SYS = (
    "你是绩效目标的审核人和整理器。分三步做：\n"
    "第一步 判定：这句话能不能变成一个年底能验、能算分的绩效目标？\n"
    "  - 能 → verdict=ok\n"
    "  - 明显是空话（认真工作、积极配合、加强沟通、完成领导交办任务这类），"
    "或者完全没有可度量的东西 → verdict=invalid，reason 用一句人话说清为什么不行，"
    "rewrite 给一句能用的改写，questions 留空。\n"
    "  注意：「把接口延迟降低」「提升客户满意度」这类——指标本身是可度量的，只是没写具体数字和口径，"
    "属于**可以补全**的情况，必须 verdict=ok，然后通过在 questions 里追问目标值/基线/周期/来源来补齐，"
    "不要判 invalid。\n"
    "  判 invalid 的门槛是：连「度量什么」都说不清（态度、配合、努力），而不是数字没写全。\n"
    "第二步 抽取：verdict=ok 时，抽出 name(指标名，只写指标本身，不超过 12 个字，"
    "不要带「按…统计」「从…到…」这类周期和来源信息)、target(纯数字)、baseline(纯数字)、unit、"
    "direction(lower/higher/target)、cycle、source、scope。只抽句子里有的，抽不到留空。\n"
    "第二步半 数字自洽（很重要）：越低越好的目标，目标值必须小于基线；越高越好则必须大于基线。\n"
    "  例如「从 300ms 降到 500ms 以内」是错的（越降越高），这种直接 verdict=invalid，"
    "reason 写清数字矛盾，rewrite 给出方向正确的写法。\n"
    "第三步 追问：把缺的字段变成选择题（基线 baseline 如果句子里没写，必须追问，"
    "给的候选值要和单位匹配，例如目标 400ms 就给 500ms/600ms/800ms/未记录）。每个缺失字段给一个问题（一句话，人话）和 3-4 个具体选项，"
    "选项要贴合这个指标（例如指标是「订单量」，来源选项就给 CRM 系统、财务系统、订单库，"
    "不要给缺陷管理系统）。questions 里 field 只能是 cycle / source / scope / target / baseline / unit。\n"
    "只输出 JSON，不要解释。"
)
PARSE_USER = (
    "岗位：{role}\n目标：「{text}」\n\n"
    "输出 JSON：{{\"verdict\":\"ok|invalid\",\"reason\":\"\",\"rewrite\":\"\","
    "\"name\":\"\",\"target\":\"\",\"baseline\":\"\",\"unit\":\"\","
    "\"direction\":\"lower|higher|target\",\"cycle\":\"\",\"source\":\"\",\"scope\":\"\","
    "\"questions\":[{{\"field\":\"cycle\",\"question\":\"按多久统计一次？\","
    "\"options\":[\"月度\",\"季度\",\"年度\"]}}]}}"
)

FIELD_REQUIRED = [("name", "指标"), ("target", "目标值"), ("cycle", "统计周期"), ("source", "数据来源")]

ROLE_SOURCE_HINTS = {
    "研发": ["APM 监控（SkyWalking）", "缺陷管理系统", "项目管理系统"],
    "测试": ["测试管理平台", "缺陷管理系统", "CI 流水线"],
    "产品": ["需求管理系统", "留存分析平台", "用户调研"],
    "销售": ["CRM 系统", "财务回款记录", "合同系统"],
    "客服": ["工单系统", "满意度问卷", "质检抽样"],
}
ROLE_SCOPE_HINTS = {
    "研发": ["本人负责的服务/接口", "本组负责的后端服务"],
    "测试": ["本人负责的测试模块", "本版本迭代范围"],
    "产品": ["本人负责的需求", "本人负责的产品线"],
    "销售": ["本人负责的客户", "本人负责的区域"],
    "客服": ["本人受理的工单", "本人负责的客户群"],
}


def _role_key(role):
    for k in ROLE_SOURCE_HINTS:
        if k in (role or ""):
            return k
    return "研发"


MEASURABLE_WORDS = ["率", "数", "次数", "时长", "时间", "占比", "比例", "金额", "数量",
                   "条", "个", "单", "分", "覆盖", "P99", "QPS", "CSAT", "NPS", "满意度",
                   "通过", "完成", "交付", "回款", "留存", "转化", "缺陷", "故障", "响应"]
VAGUE_WORDS = ["认真", "努力", "积极", "态度", "配合", "沟通", "负责", "做好", "加强", "提升自己", "优化一下"]

SOURCE_MATCH = {
    "APM": ["响应", "延迟", "P99", "可用性", "性能", "耗时", "吞吐", "QPS", "故障", "错误"],
    "监控": ["响应", "延迟", "P99", "可用性", "性能", "耗时", "吞吐", "QPS", "故障", "告警", "错误", "事故"],
    "告警": ["故障", "告警", "响应", "可用性", "事故"],
    "缺陷": ["缺陷", "bug", "Bug", "问题", "返工"],
    "故障": ["故障", "可用性", "事故", "告警"],
    "需求": ["需求", "验收", "返工", "上线", "通过率"],
    "CRM": ["客户", "订单", "签约", "跟进", "商机", "回款"],
    "HR": ["离职", "招聘", "人效", "编制", "考勤"],
    "工单": ["工单", "响应", "解决", "投诉", "满意度"],
    "问卷": ["满意度", "CSAT", "NPS", "评分"],
}


def validate_fields(fields):
    """返回逻辑问题列表：每条 {field, message}"""
    issues = []
    name = str(fields.get("name") or "")
    target = str(fields.get("target") or "")
    unit = str(fields.get("unit") or "")
    source = str(fields.get("source") or "")
    baseline = str(fields.get("baseline") or "")
    direction = fields.get("direction") or "target"

    if name:
        if any(w in name for w in VAGUE_WORDS):
            issues.append({"field": "指标",
                           "message": f"「{name}」是不可度量的描述，不是指标。改成有数字的，例如：订单接口 P99、需求验收通过率、季度线上缺陷数"})
        elif not any(w in name for w in MEASURABLE_WORDS) and len(name) < 12:
            issues.append({"field": "指标",
                           "message": f"「{name}」看起来不像可测量的指标。建议改成带「率/数/时长/占比」的写法"})

    if target and not re.match(r"^[0-9]+(\.[0-9]+)?$", target.strip()):
        issues.append({"field": "目标值", "message": f"目标值「{target}」不是纯数字，年底没法比较大小"})

    if target and re.match(r"^[0-9.]+$", target) and not unit:
        issues.append({"field": "单位", "message": "目标值没有单位（ms / % / 单 / 次），无法判断 500 是快还是慢"})

    if source and name:
        hit = [words for key, words in SOURCE_MATCH.items() if key in source]
        if hit and not any(any(w in name for w in words) for words in hit):
            allowed = "、".join(sorted({w for words in hit for w in words})[:6])
            issues.append({"field": "数据来源",
                           "message": f"「{source}」通常用于这几类指标（{allowed}），和「{name}」对不上；换个来源或换个指标"})

    try:
        t_val = float(target) if target else None
        b_val = float(baseline) if baseline else None
    except ValueError:
        t_val = b_val = None
    if t_val is not None and b_val is not None:
        if direction == "lower" and t_val > b_val:
            issues.append({"field": "目标值/基线", "message": f"「越低越好」但目标 {t_val} 高于基线 {b_val}，方向反了"})
        if direction == "higher" and t_val < b_val:
            issues.append({"field": "目标值/基线", "message": f"「越高越好」但目标 {t_val} 低于基线 {b_val}，方向反了"})

    return issues


def is_self(ps, p):
    """是否在操作自己的绩效（员工本人，或领导的团队目标）"""
    if ps.role == "hr":
        return True
    if ps.role == "emp" and ps.person and ps.person["id"] == p["id"]:
        return True
    if ps.role == "mgr" and ps.self_person and ps.self_person["id"] == p["id"]:
        return True
    return False


DIR_MAP = {"上升": "higher", "提高": "higher", "提升": "higher", "增加": "higher",
           "越高越好": "higher", "下降": "lower", "降低": "lower", "减少": "lower",
           "越低越好": "lower", "达标型": "target", "达到": "target", "完成": "target"}


def compose_goal_text(f):
    """把口径拼成一句人话（与前端 goalEditor 的规则一致）"""
    f = f or {}
    name = f.get("name") or ""
    target = f.get("target") or ""
    baseline = f.get("baseline") or ""
    unit = f.get("unit") or ""
    cyc = f.get("cycle") or ""
    src = f.get("source") or ""
    d = f.get("direction") or "target"
    word = {"lower": "降到", "higher": "提升到"}.get(d, "达到")
    head = f"按{src}每{cyc}统计，" if src and cyc else (f"按{cyc}统计，" if cyc else "")
    if baseline:
        body = f"{name}从 {baseline}{unit} {word} {target}{unit}"
    else:
        body = f"{name}{word} {target}{unit}"
    tail = " 以上" if d == "higher" else (" 以内" if d == "lower" else "")
    return (head + body + tail).strip()


def normalize_fields(f):
    """把模型/responses 给的口径洗干净"""
    f = dict(f or {})
    # 方向
    d = str(f.get("direction") or "").strip()
    if d and d not in ("lower", "higher", "target"):
        f["direction"] = DIR_MAP.get(d, "target")
    elif not d:
        f["direction"] = "target"
    # 目标值/基线：抽出数字，单位回填
    for key in ("target", "baseline"):
        raw = str(f.get(key) or "").strip()
        if not raw:
            continue
        m = re.search(r"(\d+(?:\.\d+)?)", raw)
        if not m:
            f[key] = ""
            continue
        f[key] = m.group(1)
        um = re.search(r"(ms|毫秒|%|单/天|单/月|单|万元|元|次|个|条|件|小时|天|分|笔)", raw)
        if um and not f.get("unit"):
            f["unit"] = um.group(1)
    # 数值合理性：比例/金额类不能太离谱
    for key in ("target", "baseline"):
        v = str(f.get(key) or "")
        try:
            num = float(v) if v else None
        except ValueError:
            num = None
        if num is not None:
            if num < 0 or num > 1000000:
                f[key] = ""
            elif f.get("unit") == "%" and num > 500:
                f[key] = ""      # 1000% 这种明显是解析错了
    # 指标名：去掉“提升20%”“增长20%”这类尾巴
    name = str(f.get("name") or "").strip()
    name = re.sub(r"(提升|增长|提高|降低|下降|减少)\s*\d+(\.\d+)?\s*%?", "", name).strip()
    f["name"] = name
    # 周期归一
    cyc = str(f.get("cycle") or "").strip()
    for k in ("月度", "季度", "半年", "年度"):
        if k in cyc:
            f["cycle"] = k
            break
    return f


def fields_sig(fields):
    f = fields or {}
    keys = ["name", "target", "baseline", "unit", "direction", "cycle", "source", "scope"]
    return "|".join(str(f.get(k) or "") for k in keys)


def numeric_conflict(fields):
    """确定性检查：方向与数字矛盾"""
    f = fields or {}
    def num(x):
        try:
            return float(str(x)) if str(x or "").strip() else None
        except ValueError:
            return None
    t, b = num(f.get("target")), num(f.get("baseline"))
    if t is None or b is None:
        return ""
    unit = f.get("unit") or ""
    d = f.get("direction") or "target"
    if d == "lower" and t >= b:
        return (f"目标值 {f.get('target')}{unit} 高于基线 {f.get('baseline')}{unit}，"
                "「越低越好」却往上走，逻辑反了")
    if d == "higher" and t <= b:
        return (f"目标值 {f.get('target')}{unit} 低于基线 {f.get('baseline')}{unit}，"
                "「越高越好」却往下走，逻辑反了")
    return ""


def judge_goal(role, g):
    """对当前口径做一次判定：先硬校验，再交给 AI；返回 {verdict, reason, rewrite, sig}"""
    f = g.get("fields") or {}
    sig = fields_sig(f)
    conflict = numeric_conflict(f)
    if conflict:
        name = f.get("name") or "该指标"
        unit = f.get("unit") or ""
        if (f.get("direction") or "target") == "lower":
            rw = f"改成：{name}从 {f.get('baseline')}{unit} 降到 {f.get('target')}{unit} 以内（目标值需低于基线）"
        else:
            rw = f"改成：{name}从 {f.get('baseline')}{unit} 提升到 {f.get('target')}{unit} 以上（目标值需高于基线）"
        return {"verdict": "invalid", "reason": conflict, "rewrite": rw, "sig": sig}
    try:
        res = parse_goal_text(role, g.get("text") or "")
    except Exception:
        return {"verdict": "ok", "reason": "", "rewrite": "", "sig": sig}
    return {"verdict": "ok" if res.get("aiVerdict") == "ok" else "invalid",
            "reason": res.get("aiReason") or "", "rewrite": res.get("aiRewrite") or "",
            "sig": sig}


CHAT_SYS = (
    "你是「准绳」绩效评审系统的助手，正在和公司里的一个人对话。\n"
    "你手上有他的真实数据（见下），所有回答必须基于数据，不许编造。\n"
    "说话规则（必须遵守）：\n"
    "1. 结论先行：第一句直接给答案或结论，不要铺垫、不要复述用户的问题；\n"
    "2. 简短：整段不超过 80 字；需要列举时最多 3 条，每条一行；\n"
    "3. 一次只问一个问题，问完立刻停，不要自己接着答；\n"
    "4. 不要重复上一轮说过的话，不要寒暄，不要「好的」「明白」「收到」这类开场；\n"
    "5. 数字必须来自数据，别估算；引用时带上来源（哪条目标、什么系统）。\n"
    "你**不能**直接改数据，只能提议动作，由用户点确认后才执行。\n\n"
    "输出必须是一个 JSON 对象：\n"
    "{\"reply\":\"给用户看的话\",\"action\":null,\"page\":\"\",\"choices\":[\"选项1\",\"选项2\"]}\n"
    "重要：如果你需要用户补充信息，**一次只问一件事**，并且**必须**给 choices（2-4 个短选项，每个不超过 14 字）。\n"
    "只有当你确实需要自由回答（比如具体数字、具体人名）时，choices 才可以为空。\n"
    "page 用来让界面自动跳到相关页面，取值只能是：me（我的绩效）、team（我的团队/下属列表）、"
    "approve（目标审批与锚点）、changes（变更审批）、change（发起变更）、org（条线分布）、"
    "appeals（我的申诉）。和问题无关就留空字符串。\n"
    "或者当用户明确要求执行某个操作时：\n"
    "{\"reply\":\"简短说明\",\"action\":{\"type\":\"动作名\",\"personId\":\"\",\"goalId\":\"\","
    "\"value\":0,\"page\":\"\",\"detail\":\"给用户确认的一句话\"}}\n\n"
    "你有执行能力：用户确认后你能提交目标、批准目标、生成/确认锚点、改锚点、改草稿目标、新增目标、打分。"
    "不要说自己没有权限——权限由系统在服务端校验，你只管提出动作。\n"
    "可用动作（只能从这些里选，参数必须来自下方数据）：\n"
    "submit_goals 提交自己的目标审批（只需 personId）\n"
    "approve_goals 批准某人的目标（personId）\n"
    "gen_anchor 为某条目标生成锚点（personId + goalId）\n"
    "confirm_anchor 确认某条目标的锚点（personId + goalId）\n"
    "score 给某条目标打分（personId + goalId + value 0-100）\n"
    "edit_anchor 修改锚点某一档的描述/阈值（personId + goalId + level 4|3|2 + desc 完整新描述）\n"
    "withdraw_goals 撤回**自己**已提交但还没被审批的目标（personId），撤回后可以再改\n"
    "change_request 年中给自己提交目标变更申请（personId + goalId + text 变更理由 + "
    "newText 新目标 + fields 新口径 + evidence 佐证）；提交后要等领导审批\n"
    "update_goal 修改**自己**的草稿目标的文字或口径（personId + goalId + text 可选 + fields 可选）；"
    "只有本人能改；领导不能直接改下属目标，只能退回让员工自己改；已提交/已批准的走变更申请\n"
    "add_goal 给本人新增一条目标（必须带：personId、text 一句话、weight 1-100、"
    "fields 对象含 name/target/cycle/source，可选 baseline/unit/scope/direction）；"
    "口径不全时不要提议，先用提问把缺的补齐。示例："
    "口径不全时不要提议，先用提问把缺的补齐。示例字段："
    "fields = {name, target, baseline, unit, direction, cycle, source, scope}"

    "open_page 打开右侧某个页面（page：me / team / approve / changes / change / org / appeals）\n\n"
    "不确定或用户只是提问时，action 必须是 null。宁可不做，也不要乱做。\n"
    "如果你准备动手（说了「请确认」「拟好」「待你确认」），action 就必须同时给出；反过来说，action 为 null 时不要写这类话。\n"
    "绝对不要建议用户「先随便提交、等被退回再改」这类绕过流程的做法。\n"
    "回复用户时不要出现 goalId、personId 这类内部编号（如 gx_9、p0、g715438），用目标内容或人名代替。\n"
    "reply 里不要出现换行符，也不要输出 JSON 以外的任何字符。"
    "只输出 JSON，不要 Markdown，不要在 JSON 前后加任何说明文字。\n"
    "严禁输出任何 XML / 工具调用语法（如 DSML、invoke、parameter、<calls>），你只能返回上面规定的 JSON。"
)


def docx_text(path):
    """极简 docx 取文本：直接读 word/document.xml"""
    import zipfile
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
    txt = re.sub(r"<[^>]+>", "", xml)
    return html.unescape(txt).strip()


def preview_html(name):
    f = UPLOAD_DIR / name
    if not f.exists() or not f.is_file():
        return None
    low = name.lower()
    body = ""
    if low.endswith(".docx"):
        try:
            body = "<pre>" + html.escape(docx_text(f)) + "</pre>"
        except Exception as e:
            body = "<p>解析失败：" + html.escape(str(e)) + "</p>"
    elif low.endswith((".txt", ".csv", ".md")):
        body = "<pre>" + html.escape(f.read_text(encoding="utf-8", errors="ignore")) + "</pre>"
    elif low.endswith((".png", ".jpg", ".jpeg", ".gif")):
        body = f'<img src="/uploads/{html.escape(name)}" style="max-width:100%">'
    elif low.endswith(".pdf"):
        body = f'<iframe src="/uploads/{html.escape(name)}" style="width:100%;height:80vh;border:0"></iframe>'
    else:
        body = f'<p>这个格式不能直接预览，<a href="/uploads/{html.escape(name)}">点此下载</a></p>'
    return ("<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            "<title>附件预览</title><style>body{font-family:-apple-system,'Segoe UI',sans-serif;"
            "max-width:820px;margin:32px auto;padding:0 20px;color:#253029;line-height:1.8}"
            "pre{white-space:pre-wrap;background:#F6F9F7;padding:16px;border-radius:10px}"
            "a{color:#2F6B54}h2{font-size:15px;color:#7A847E;font-weight:500}</style></head><body>"
            "<h2>" + html.escape(name) + " · 网页预览</h2>" + body + "</body></html>")


def action_allowed(ps, act):
    """这个身份在这个阶段能不能执行这个动作"""
    t = act.get("type")
    pid = act.get("personId") or ""
    per = BY_ID.get(pid)
    if not per:
        return False, "找不到这个人"
    if not any(x["id"] == pid for x in ps.scope()):
        return False, "这个人不在你能看到的范围里"
    st = stage()
    if t == "open_page":
        return True, ""
    if t == "submit_goals":
        if st != "start":
            return False, "现在不是年初填报阶段"
        if not is_self(ps, per):
            return False, "只能提交自己的目标"
        return True, ""
    if t == "approve_goals":
        if st != "start":
            return False, "现在不是年初审批阶段"
        if not (ps.can("approve_change", per) or
                (per.get("isManager") and ps.role == "skip" and per.get("manager") == ps.who)):
            return False, "你无权审批这个人的目标"
        return True, ""
    if t == "change_request":
        if st != "mid":
            return False, "只有年中阶段才能提交目标变更"
        if not is_self(ps, per):
            return False, "只能给自己提交变更申请"
        gid = act.get("goalId") or ""
        g = next((x for x in (per.get("goals") or []) if x["id"] == gid), None)
        if not g:
            return False, "找不到这条目标"
        if not (act.get("detail") or act.get("why") or act.get("text")):
            return False, "变更需要写理由"
        return True, ""
    if t == "withdraw_goals":
        if st != "start":
            return False, "现在不是年初阶段"
        if not is_self(ps, per):
            return False, "只能撤回自己的目标"
        if not any(g["status"] == "submitted" for g in per.get("goals") or []):
            return False, "没有处于待审批状态的目标"
        return True, ""
    if t == "update_goal":
        if st != "start":
            return False, "现在不是年初填报阶段"
        gid = act.get("goalId") or ""
        g = next((x for x in (per.get("goals") or []) if x["id"] == gid), None)
        if not g:
            return False, "找不到这条目标"
        if g.get("status") != "draft":
            return False, "这条目标已经不是草稿了，改它得走变更申请"
        if not is_self(ps, per):
            return False, "只有本人能改自己的目标；领导要改，只能退回让员工自己改"
        return True, ""
    if t == "add_goal":
        if st != "start":
            return False, "现在不是年初填报阶段"
        if not is_self(ps, per):
            return False, "只能给自己加目标"
        f = act.get("fields") or {}
        miss = [lbl for k, lbl in FIELD_REQUIRED if not str(f.get(k) or "").strip()]
        if miss:
            return False, "口径还缺 " + "、".join(miss)
        return True, ""
    if t == "edit_anchor":
        if st != "start":
            return False, "现在不是年初阶段"
        if not ps.can("confirm_anchor", per):
            return False, "你无权修改这个人的锚点"
        if not act.get("goalId"):
            return False, "要指定是哪条目标"
        if not act.get("desc") or len(str(act.get("desc"))) < 6:
            return False, "锚点描述太短"
        return True, ""
    if t in ("gen_anchor", "confirm_anchor"):
        if st != "start":
            return False, "现在不是年初阶段"
        if not ps.can("confirm_anchor", per):
            return False, "你无权确认这个人的锚点"
        return True, ""
    if t == "score":
        if st != "end":
            return False, "现在不是年终评分阶段"
        if not (ps.can("score_direct", per) or ps.can("score_skip", per)):
            return False, "你无权给这个人打分"
        return True, ""
    return False, "不支持的动作"


TOOL_JUNK = re.compile(r"</?\s*\|?\s*\|?\s*DSML[^>]*>", re.I)


def salvage(raw):
    """从非 JSON 文本里捞 reply 和 choices（兼容 [..] 和「A、B、C」两种写法）"""
    t = str(raw or "")
    choices = []
    # 1) 方括号形式：choices: ["A","B"]
    m = re.search(r"choices?\s*[:：]\s*(\[[^\]]*\])", t, re.I | re.S)
    if m:
        try:
            arr = json.loads(m.group(1))
            choices = [str(x).strip() for x in arr if str(x).strip()][:4]
        except Exception:
            choices = re.findall(r'"([^"]{1,16})"', m.group(1))[:4]
        t = t[:m.start()] + t[m.end():]
    else:
        # 2) 顿号/逗号分隔：choices: 提升10%、提升20%
        m2 = re.search(r"(choices?|选项|可选项)\s*[:：]\s*(.+)$", t, re.I | re.M)
        if m2:
            tail = m2.group(2).strip()
            parts = [x.strip(" 　\"'[]").strip("。;；") for x in re.split(r"[、,，;；/]|\s{2,}", tail)]
            choices = [x for x in parts if 1 <= len(x) <= 16][:4]
            # 括号补全：模型常写「其他（我来说」丢掉右括号
            choices = [c + "）" if c.count("（") > c.count("）") else c for c in choices]
            t = t[:m2.start()] + t[m2.end():]
    # 3) 清掉残留的键名
    t = re.sub(r'^\s*\{?\s*"reply"\s*[:：]\s*"', "", t)
    t = re.sub(r'^\s*\{?\s*reply\s*[:：]\s*', "", t, flags=re.I)
    t = re.sub(r'"\s*,\s*"action\s*[:：].*$', "", t, flags=re.S)
    t = t.replace('"choices"', "").replace("choices:", "")
    return t.strip(), choices


def clean_reply(text):
    """去掉 markdown 与泄漏的工具调用标记"""
    t = str(text or "")
    t = re.sub(r"\*\*|__|`", "", t)
    # DSML 工具调用块（可能跨行）
    t = re.sub(r"<\s*/?\s*[|｜]{0,2}\s*DSML[^>]*>[\s\S]*?<\s*/\s*[|｜]{0,2}\s*DSML[^>]*>", "", t, flags=re.I)
    t = TOOL_JUNK.sub("", t)
    # 残留的 invoke/parameter 片段
    t = re.sub(r"<\s*/?\s*(calls|invoke|parameter)[^>]*>", "", t, flags=re.I)
    # 只剩尖括号标记的行
    lines = [ln for ln in t.splitlines() if not re.fullmatch(r"\s*<[^>]{0,80}>\s*", ln)]
    t = "\n".join(lines)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def chat_reply(msgs, max_tokens=1200):
    """带回退的对话调用；返回 (文本, 用的模型)"""
    last = None
    for m in [MODEL] + [x for x in MODEL_FALLBACK if x != MODEL]:
        mt = max_tokens
        if "deepseek" in m.lower():
            mt = max(mt, 1800)
        try:
            resp = call_model(msgs, m, max_tokens=mt)
            txt = (resp["choices"][0]["message"]["content"] or "").strip()
            if txt:
                return txt, m
            last = "空回复"
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore")[:120]
            last = f"HTTP {e.code} {body}"
            continue
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            continue
    raise RuntimeError("模型都不可用：" + str(last))


CHOICE_SYS = (
    "你在帮一个对话式绩效系统生成「快捷选项」。\n"
    "针对用户看到的这个问题，给出 2-4 个**简短、互斥、能直接点选**的答案。\n"
    "规则：每个选项不超过 12 个字；只针对问题本身，不要写解释；\n"
    "如果这个问题只能自由作答（比如要填具体数字、具体名字），返回空数组。\n"
    "只输出 JSON：{\"choices\":[\"选项1\",\"选项2\"]}"
)


def gen_choices(question):
    """让模型为这个问题生成可点选项；失败就返回空"""
    try:
        msgs = [{"role": "system", "content": CHOICE_SYS},
                {"role": "user", "content": "问题：" + (question or "")[:200]}]
        out = []
        for attempt in range(2):
            raw, _ = chat_reply(msgs, max_tokens=300)
            obj = None
            try:
                obj = extract_json(raw)
            except Exception:
                obj = None
            cand = []
            if isinstance(obj, dict):
                cand = obj.get("choices") or []
            else:
                _, cand = salvage(raw)
            for c in cand:
                t = str(c).strip()
                if 1 <= len(t) <= 16 and t not in out and len(out) < 4:
                    out.append(t)
            if out:
                break
        return out
    except Exception:
        return []


def available_actions(ps):
    """按身份+阶段+数据状态，算出此刻真正能执行的动作"""
    st = stage()
    out = []
    me = None
    if ps.role == "emp":
        me = ps.person
    elif ps.role == "mgr":
        me = ps.self_person
    scope = ps.scope()

    if st == "start":
        if me and me.get("goals"):
            if any(g["status"] == "draft" for g in me["goals"]):
                out.append("submit_goals：你有草稿目标，可以提交审批")
            if any(g["status"] == "draft" for g in me["goals"]):
                out.append("update_goal：草稿目标可以改文字和口径")
            if any(g["status"] == "submitted" for g in me["goals"]):
                out.append("withdraw_goals：有目标已提交待审批，可以撤回后再改")
            if sum(g["weight"] for g in me["goals"]) < 100:
                out.append("add_goal：可以给自己新增目标（口径齐全时）")
        elif me:
            out.append("add_goal：你还没有目标，可以给自己新增")
        for per in scope:
            if per.get("isManager"):
                continue
            if ps.can("approve_change", per) and any(
                    g["status"] == "submitted" for g in per.get("goals") or []):
                out.append(f"approve_goals：可以批准 {per['name']}（id={per['id']}）的目标")
            if ps.can("confirm_anchor", per) and any(
                    g["status"] == "approved" for g in per.get("goals") or []):
                out.append(f"gen_anchor / confirm_anchor：可以处理 {per['name']}（id={per['id']}）的锚点")
            if ps.can("confirm_anchor", per) and any(
                    g.get("anchor") for g in per.get("goals") or []):
                out.append(f"edit_anchor：可以改 {per['name']}（id={per['id']}）的锚点描述")
    elif st == "mid":
        if me:
            for g in me.get("goals") or []:
                if g.get("status") in ("confirmed", "approved"):
                    out.append(f"change_request：可以给「{g['text'][:14]}」提交变更申请（需理由+佐证）")
        if any(per.get("isManager") is False and any(
                c["status"] == "待审批" and c["personId"] == per["id"]
                for c in STATE["changes"]) for per in scope):
            out.append("open_page：你名下有变更申请，可以在「变更审批」里处理")
    elif st == "end":
        for per in scope:
            if ps.can("score_direct", per) or ps.can("score_skip", per):
                if any(g["status"] == "confirmed" for g in per.get("goals") or []):
                    out.append(f"score：可以给 {per['name']}（id={per['id']}）打分")
    return out


def gotoal_state(per):
    """每个人每条目标的状态一句话，用于判断能不能动手"""
    bits = []
    for g in per.get("goals") or []:
        miss = [m for m in goal_missing_fields(g) if m in ("指标", "目标值", "统计周期", "数据来源")]
        s = f"目标(id={g['id']})「{g['text'][:18]}」状态={g['status']}"
        if miss:
            s += "，缺：" + "、".join(miss)
        bits.append(s)
    return bits


def chat_context(ps):
    """把当前身份能看到的关键数据整理成上下文"""
    lines = []
    lines.append("当前身份：" + ps.label + "（" + ps.role + "）")
    lines.append("当前阶段：" + {"start": "年初·绩效填报", "mid": "年中·目标回顾与变更",
                              "end": "年终·考核与结果"}.get(stage(), stage()))
    scope = ps.scope()
    lines.append("你能看到的人：" + "、".join(x["name"] for x in scope[:8]) + f"（共 {len(scope)} 人）")

    me = None
    if ps.role == "emp":
        me = ps.person
    elif ps.role == "mgr":
        me = ps.self_person
    if me:
        lines.append("")
        lines.append("【你自己的绩效】" + me["name"] + " · " + me.get("dept", "") + " · " + me.get("role", ""))
        lines.append("待办：" + todo_of(me))
        for line in gotoal_state(me):
            lines.append("  · " + line)
        for g in me["goals"]:
            f = g.get("fields") or {}
            lines.append(f"- 目标(id={g['id']})：{g['text']}（权重 {g['weight']}%，状态 {g['status']}）")
            if f:
                lines.append(f"  口径：指标 {f.get('name','')}，目标 {f.get('target','')}{f.get('unit','')}，"
                             f"基线 {f.get('baseline','')}{f.get('unit','')}，周期 {f.get('cycle','')}，"
                             f"来源 {f.get('source','')}，范围 {f.get('scope','')}")
            a = g.get("anchor") or {}
            for t in (a.get("anchors") or []):
                lines.append(f"  锚点 {t.get('level')} 档：{t.get('desc')}")
        r = rec_of(me["id"]) or {}
        if r:
            lines.append(f"评级：{r.get('rating')}，排名 {r.get('rank')}/{r.get('deptCount')}，A 名额 {r.get('quota')}")
        rn = STATE["reasons"].get(me["id"])
        if rn:
            lines.append("评级理由：" + "".join(x["text"] for x in rn.get("sentences", []))[:400])
        ss = STATE["selfsummaries"].get(me["id"])
        if ss:
            lines.append("年度自评（节选）：" + ss.get("text", "")[:200])

    if ps.role in ("mgr", "skip", "hr"):
        lines.append("")
        lines.append("【团队情况】")
        todo_keywords = ("待审批", "待生成", "待确认", "待打分", "待处理", "未提交")
        for x in [y for y in scope if not y.get("isManager")][:12]:
            t = todo_of(x)
            lines.append(f"- {x['name']}(id={x['id']}，{x.get('dept','')}，{x.get('role','')})：{t}")
            detail_needed = any(k in t for k in todo_keywords)
            for g in (x.get("goals") or [])[:4]:
                f = g.get("fields") or {}
                if detail_needed:
                    lines.append(f"  · 目标(id={g['id']})：{g['text']}（权重 {g['weight']}%，状态 {g['status']}）")
                    if f:
                        lines.append(f"    口径：{f.get('name','')}，目标 {f.get('target','')}{f.get('unit','')}，"
                                     f"基线 {f.get('baseline','')}{f.get('unit','')}，周期 {f.get('cycle','')}，"
                                     f"来源 {f.get('source','')}")
                    a = g.get("anchor") or {}
                    for tt in (a.get("anchors") or [])[:3]:
                        lines.append(f"    锚点 level={tt.get('level')}：{tt.get('desc')}")
                else:
                    lines.append(f"  · 目标：{g['text'][:24]}（{g['weight']}%）")
            rr = rec_of(x["id"]) or {}
            if rr and scores_visible(ps):
                lines.append(f"  评级：{rr.get('rating')}（{rr.get('score')} 分，排名 {rr.get('rank')}/{rr.get('deptCount')}）")
        ov_ready = bool(STATE.get("ratings"))
        if ov_ready and scores_visible(ps):
            lines.append("【评级分布】" + "，".join(
                f"{k} {v} 人" for k, v in sorted(STATE.get("ratings", {}).items())[:0]))
            hist = {}
            for r in STATE["ratings"].values():
                hist[r["rating"]] = hist.get(r["rating"], 0) + 1
            lines.append("【评级分布】" + "，".join(f"{k} {v} 人" for k, v in sorted(hist.items())))

    pend = [c for c in STATE["changes"] if c["status"] == "待审批"]
    if pend and ps.role in ("mgr", "skip", "hr"):
        lines.append("")
        lines.append(f"【待审批的变更】{len(pend)} 条")
        for c in pend[:5]:
            lines.append(f"- {c.get('name','')}：{c.get('newText') or c.get('text','')}")
    acts = available_actions(ps)
    lines.append("")
    if me:
        used = sum(g["weight"] for g in me.get("goals") or [])
        left = 100 - used
        lines.append("")
        lines.append(f"【权重】已用 {used}%，剩余 {left}%"
                     + ("；权重已满，不能再新增目标，需要先调低或删除某条" if left <= 0 else ""))
    if acts:
        lines.append("【此刻你确实能执行的动作】")
        lines.extend("- " + a for a in acts)
        lines.append("只能从上面这些里提议。如果用户要的事不在列表里，先判断是哪种原因再回答：")
        lines.append("(a) 已经做过了 → 直接说现状，例如「已经提交了，不用再提交」；")
        lines.append("(b) 缺字段 → 指出缺哪一项；")
        lines.append("(c) 阶段不对 → 说清现在是什么阶段；")
        lines.append("(d) 真的权限不够 → 说清谁有权限做。")
        lines.append("禁止笼统地说「我没有权限」，也不要在说不清时反复追问。")
    else:
        lines.append("【此刻你没有任何可执行动作】")
        lines.append("只能回答和查看，不要提议任何改动；用户要做改动时，说明为什么现在做不了、以及需要什么条件。")

    return "\n".join(lines)


POLISH_SYS = (
    "你是绩效评审的措辞编辑。给你三档评分标准（已经是确定的事实），"
    "请把每档改写成一句自然、具体、员工能看懂的中文。\n"
    "硬性要求：\n"
    "1. 所有数字、单位、比较方向必须原样保留，不能新增、删除或改动任何数字；\n"
    "2. 不要编造原文没有的指标或数据来源；\n"
    "3. 每档一句话，不超过 45 字；\n"
    "只输出 JSON：{\"a\":\"\",\"b\":\"\",\"c\":\"\"}"
)
POLISH_USER = (
    "指标：{name}（{direction}）\n"
    "A 档（≥95 分）：{a}\nB 档（80–94.9 分）：{b}\nC 档（<80 分）：{c}\n"
    "证据来源：{ev}"
)


def _nums(text):
    return sorted(re.findall(r"[0-9]+(?:\.[0-9]+)?", str(text or "")))


def polish_anchor(role, anchor_obj, fields):
    """让 AI 润色三档措辞；数字被改动就整体退回模板"""
    try:
        tiers = sorted(anchor_obj.get("anchors") or [], key=lambda x: -x["level"])
        if len(tiers) != 3:
            return anchor_obj
        d = fields.get("direction") or "target"
        user = POLISH_USER.format(
            name=fields.get("name") or "该指标",
            direction={"lower": "越低越好", "higher": "越高越好"}.get(d, "达标型"),
            a=tiers[0]["desc"], b=tiers[1]["desc"], c=tiers[2]["desc"],
            ev=(tiers[0].get("evidence") or ""))
        obj, model, _ = ask_json(POLISH_SYS, user, max_tokens=400)
        new_descs = [str(obj.get(k) or "").strip() for k in ("a", "b", "c")]
        if not all(len(x) >= 6 for x in new_descs):
            return anchor_obj
        for old, new in zip(tiers, new_descs):
            if _nums(old["desc"]) != _nums(new):
                return anchor_obj          # 数字被动过，不采用
        for tier, new in zip(tiers, new_descs):
            tier["desc_raw"] = tier["desc"]
            tier["desc"] = new
        anchor_obj["polished"] = model
        return anchor_obj
    except Exception:
        return anchor_obj


def parse_goal_text(role, text):
    empty = {"name": "", "target": "", "baseline": "", "unit": "",
             "direction": "target", "cycle": "", "source": "", "scope": ""}
    fields = dict(empty)
    model = "rule"
    obj = {}
    try:
        obj, model, _ = ask_json(PARSE_SYS, PARSE_USER.format(role=role, text=text), max_tokens=600)
        for k in fields:
            v = obj.get(k)
            if v not in (None, ""):
                fields[k] = str(v).strip()
        if fields.get("direction") not in ("lower", "higher", "target"):
            fields["direction"] = "target"
    except Exception:
        model = "rule"

    # 规则兜底：句子里有数字就抽出来
    nums = re.findall(r"(\d+(?:\.\d+)?)\s*(ms|毫秒|%|次|个|条|天|分|万|元)?", text or "")
    if nums and not fields.get("target"):
        fields["target"] = nums[-1][0]
        if nums[-1][1] and not fields.get("unit"):
            fields["unit"] = nums[-1][1]
        if len(nums) > 1 and not fields.get("baseline"):
            fields["baseline"] = nums[0][0]
    if not fields.get("name"):
        fields["name"] = (text or "")[:14]
    low = text or ""
    if any(w in low for w in ["降", "降低", "减少", "缩短", "压缩", "以内", "以下"]):
        fields["direction"] = "lower"
    elif any(w in low for w in ["提升", "提高", "达到", "以上", "增长"]):
        fields["direction"] = "higher"

    missing = [label for key, label in FIELD_REQUIRED if not str(fields.get(key) or "").strip()]
    key = _role_key(role)
    # 数字自洽硬校验：不依赖模型
    try:
        _t = float(fields.get("target") or "") if str(fields.get("target") or "").strip() else None
        _b = float(fields.get("baseline") or "") if str(fields.get("baseline") or "").strip() else None
    except ValueError:
        _t = _b = None
    _dir = fields.get("direction") or "target"
    _logic = ""
    if _t is not None and _b is not None:
        if _dir == "lower" and _t >= _b:
            _logic = (f"目标值 {fields['target']}{fields.get('unit', '')} 高于基线 "
                      f"{fields['baseline']}{fields.get('unit', '')}，「越低越好」却往上走，逻辑反了")
        elif _dir == "higher" and _t <= _b:
            _logic = (f"目标值 {fields['target']}{fields.get('unit', '')} 低于基线 "
                      f"{fields['baseline']}{fields.get('unit', '')}，「越高越好」却往下走，逻辑反了")

    ai_verdict = "ok" if str((obj or {}).get("verdict") or "").lower() == "ok" else ""
    ai_reason = str((obj or {}).get("reason") or "")[:140]
    ai_rewrite = str((obj or {}).get("rewrite") or "")[:160]
    if not ai_verdict:
        ai_verdict = "invalid"
        if not ai_reason:
            ai_reason = "这句话没法变成一个年底能验、能算分的目标"
    if _logic:
        ai_verdict = "invalid"
        ai_reason = _logic
        if not str(ai_rewrite or "").strip():
            ai_rewrite = ""
        ai_rewrite = ai_rewrite or (f"改成：{fields.get('name', '该指标')}从 {fields['baseline']}"
                                    f"{fields.get('unit', '')} 降到 {fields['target']}"
                                    f"{fields.get('unit', '')} 以内（目标值需低于基线）"
                                    if _dir == "lower" else
                                    f"改成：{fields.get('name', '该指标')}从 {fields['baseline']}"
                                    f"{fields.get('unit', '')} 提升到 {fields['target']}"
                                    f"{fields.get('unit', '')} 以上（目标值需高于基线）")

    questions = []
    seen_fields = set()
    for q in ((obj or {}).get("questions") or []):
        if not isinstance(q, dict):
            continue
        fld = str(q.get("field") or "").strip()
        raw_opts = [str(x)[:20] for x in (q.get("options") or [])]
        bad = ("未确定", "未记录", "未明确", "未知", "未定", "不限", "其他")
        opts = [x for x in raw_opts if not any(b in x for b in bad)][:4]
        if fld in ("cycle", "source", "scope", "target", "baseline", "unit") and opts and fld not in seen_fields:
            seen_fields.add(fld)
            questions.append({"field": fld,
                              "question": str(q.get("question") or "")[:40] or "补一项",
                              "options": opts})
    # 缺基线时补一个选择题（AI 漏了也保证能问出来）
    if ai_verdict == "ok" and not any(q.get("field") == "baseline" for q in questions):
        if not str(fields.get("baseline") or "").strip() and str(fields.get("target") or "").strip():
            try:
                tv = float(fields["target"])
                unit = fields.get("unit") or ""
                opts = []
                for k in (1.5, 2, 3):
                    v = tv * k
                    v = int(v) if abs(v - int(v)) < 0.01 else round(v, 1)
                    opts.append(f"{v}{unit}")

                questions.append({"field": "baseline",
                                  "question": f"现在的{f'「' + fields.get('name', '指标') + '」'}基线是多少？",
                                  "options": opts})
            except (TypeError, ValueError):
                pass

    if ai_verdict == "ok" and not questions:
        questions = [{"field": k, "question": "补上" + label, "options": []}
                     for k, label in FIELD_REQUIRED if not str(fields.get(k) or "").strip()]

    missing = [label for key, label in FIELD_REQUIRED if not str(fields.get(key) or "").strip()]
    return {"fields": fields, "missing": missing, "issues": [], "model": model,
            "aiVerdict": ai_verdict, "aiReason": ai_reason, "aiRewrite": ai_rewrite,
            "questions": questions, "options": {"cycle": CYCLE_OPTIONS}}


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def actual_key(pid, gid, mid):
    return f"{pid}|{gid}|{mid}"


def score_metric(value, m):
    """把实际值换算成 0–100 分。m: type/target/a_line/c_line/cap_ratio/proxy/fail_cap"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    kind = m.get("type") or "target"
    if kind == "gate":
        ok = v <= float(m.get("target") or 0) if (m.get("direction") or "lower") == "lower" \
            else v >= float(m.get("target") or 0)
        return 100.0 if ok else float(m.get("fail_cap") or 0)
    target = float(m.get("target") or 0)
    a_line = float(m.get("a_line") if m.get("a_line") is not None else target)
    c_line = float(m.get("c_line") if m.get("c_line") is not None else target)
    if kind == "higher":
        if v >= a_line:
            score = 100.0
        elif v >= target:
            score = 95 + 5 * clamp((v - target) / max(a_line - target, 1e-9))
        elif v > c_line:
            score = 70 + 25 * clamp((v - c_line) / max(target - c_line, 1e-9))
        else:
            score = 70 * clamp(v / max(c_line, 1e-9))
    else:
        if v <= a_line:
            score = 100.0
        elif v <= target:
            score = 95 + 5 * clamp((target - v) / max(target - a_line, 1e-9))
        elif v < c_line:
            score = 70 + 25 * clamp((c_line - v) / max(c_line - target, 1e-9))
        else:
            score = max(0.0, 70 * clamp((c_line / max(v, 1e-9))))
    if m.get("proxy"):
        score = min(score, 90.0)
    return round(score, 1)


def goal_auto_score(pid, g):
    """目标下的指标全部核验过，才用自动分；否则返回 None"""
    ms = g.get("metrics") or []
    if not ms:
        return None
    rows, total_w = [], 0.0
    for m in ms:
        a = STATE["actuals"].get(actual_key(pid, g["id"], m["id"])) or {}
        if a.get("status") != "verified":
            return None
        sc = score_metric(a.get("value"), m)
        if sc is None:
            return None
        w = float(m.get("weight") or 0)
        rows.append({"metricId": m["id"], "name": m.get("name"), "value": a.get("value"),
                     "score": sc, "weight": w})
        total_w += w
    if total_w <= 0:
        return None
    score = round(sum(r["score"] * r["weight"] for r in rows) / total_w, 1)
    return score, rows


def ensure_actuals():
    if STATE.get("actuals_seeded"):
        return
    demo = [("p0", "g0_0", "m1", 0.5, 0.9, "按全部项目口径统计，非仅主仓"),
            ("p0", "g0_0", "m2", 1, 1, "")]
    for pid, gid, mid, reported, final, reason in demo:
        per = BY_ID.get(pid)
        if not per or not any(x["id"] == gid for x in per["goals"]):
            continue
        STATE["actuals"][actual_key(pid, gid, mid)] = {
            "value": final, "reported": reported, "evidence": "https://demo/报表/" + mid,
            "reportedBy": per["name"], "reportedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "verified", "changedFrom": reported if final != reported else None,
            "reason": reason, "verifiedBy": per["manager"],
            "verifiedAt": time.strftime("%Y-%m-%d %H:%M:%S")}
    STATE["actuals_seeded"] = True
    save_state()


def precheck_goal(role, text):
    """兼容旧调用：直接复用新的解析"""
    res = parse_goal_text(role, text)
    return {"verdict": "ok" if res["aiVerdict"] == "ok" else "block",
            "forText": text, "missing": res["missing"],
            "suggestion": res["aiRewrite"], "model": res.get("model"),
            "ts": time.strftime("%Y-%m-%d %H:%M:%S")}


def apply_change(c, approver_label):
    """把一条变更落到目标上：版本 +1、锚点清空、状态回草稿"""
    p = BY_ID.get(c["personId"])
    if not p:
        return None
    g = next((x for x in p["goals"] if x["id"] == c.get("goalId")), None)
    if not g:
        return None
    old = g.get("version", "v1")
    g["version"] = f"v{int(old[1:]) + 1}"
    g["anchor"] = None
    g["status"] = "draft"
    if c.get("newText"):
        g["text"] = c["newText"]
    if c.get("fields"):
        g["fields"] = c["fields"]
    audit(approver_label, "变更生效", c["personId"], old, g["version"], (c.get("text") or "")[:40])
    return g


def sanitize_all_goals():
    "把库里已有的脏口径洗一遍（幂等）"
    changed = 0
    for per in PEOPLE:
        for g in per.get("goals") or []:
            f = g.get("fields") or {}
            if not f:
                continue
            nf = normalize_fields(f)
            if nf != f:
                g["fields"] = nf
                changed += 1
    if changed:
        save_state()
    return changed


def load_demo():
    """把演示数据装回去：目标 + 口径 + 锚点 + 评分"""
    for i, p_ in enumerate(PEOPLE):
        if p_.get("isManager"):
            gs = [{"id": f"gm{i}_{k}", "text": t, "weight": w, "fields": f,
                   "kind": "metric", "version": "v1"}
                  for k, (t, w, f) in enumerate(MGR_GOALS)]
        else:
            role, dept, texts = next((r for r in ROLES if r[0] == p_["role"]), ROLES[0])
            gs = [{"id": f"g{i}_{j}", "text": t, "weight": [40, 35, 25][j],
                   "fields": GOAL_FIELDS.get(t, {}), "kind": "metric", "version": "v1"}
                  for j, t in enumerate(texts)]
        for g in gs:
            g.update({"anchor": build_anchor_from_fields(g) if g["fields"] else None,
                      "verdict": "ok", "status": "confirmed",
                      "submittedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
                      "reviewedBy": p_["manager"],
                      "reviewedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
                      "reviewNote": ""})
        p_["goals"] = gs
    STATE["cleared"] = False
    STATE["config"]["stage"] = "start"
    STATE["scores"] = {}
    STATE["seeded"] = False
    STATE["ratings"] = {}
    STATE["reasons"] = {}
    STATE["appeals"] = []
    STATE["changes"] = []
    STATE["selfsummaries"] = {}
    STATE["actuals"] = {}
    STATE["actuals_seeded"] = False
    save_state()
    ensure_scores()
    ensure_actuals()
    compute_all()
    audit("系统", "载入演示数据", f"{len(PEOPLE)} 人", None, "已恢复", "")
    return {"ok": True, "people": len(PEOPLE)}


def person_total(p):

    rw = [("隔级领导", 100)] if p.get("isManager") else raters()
    total, detail = 0.0, []
    incomplete = False
    for g in p["goals"]:
        auto = goal_auto_score(p["id"], g)
        if auto:
            gs = auto[0]
            total += gs * g["weight"] / 100.0
            detail.append({"goalId": g["id"], "text": g["text"], "weight": g["weight"],
                           "score": gs, "source": "auto", "metrics": auto[1]})
            continue
        s, wsum = 0.0, 0
        for name, w in rw:
            v = STATE["scores"].get(f'{p["id"]}|{g["id"]}|{name}')
            if v is None:
                continue
            s += v * w
            wsum += w
        if not wsum:
            incomplete = True
            detail.append({"goalId": g["id"], "text": g["text"], "weight": g["weight"],
                           "score": None, "source": "pending"})
            continue
        gs = round(s / wsum, 1)
        total += gs * g["weight"] / 100.0
        detail.append({"goalId": g["id"], "text": g["text"], "weight": g["weight"],
                       "score": gs, "source": "manual"})
    return (None if incomplete else round(total, 1)), detail

def compute_all():
    if not PEOPLE:
        STATE["ratings"] = {}
        save_state()
        return {}
    cfg = STATE["config"]
    by_dept, rows, mgr_rows = {}, [], []
    pending = {}
    for p in PEOPLE:
        total, detail = person_total(p)
        unscored = [d for d in detail if d.get("score") is None]
        row = {"id": p["id"], "name": p["name"], "dept": p["dept"],
               "manager": p["manager"], "score": total, "detail": detail}
        rows.append(row)
        if unscored:
            # 还有目标没打分：不排名、不出评级
            pending[p["id"]] = len(unscored)
            continue
        if p.get("isManager"):
            mgr_rows.append(row)          # 经理单独排名，不占员工名额
        else:
            by_dept.setdefault(p["dept"], []).append(row)
    ratings = {}
    for dept, group in by_dept.items():
        group.sort(key=lambda r: -r["score"])
        n = len(group)
        quota = max(1, int(round(n * cfg["quota_ratio"])))
        for i, r in enumerate(group):
            rank = i + 1
            rating, status = "B", "ok"
            if r["score"] >= cfg["a_threshold"]:
                if rank <= quota:
                    rating = "A"
                else:
                    rating, status = "B", "cutline"
            elif r["score"] < cfg["c_threshold"]:
                rating = "C"
            ratings[r["id"]] = {"personId": r["id"], "score": r["score"], "rank": rank,
                                "deptCount": n, "quota": quota, "rating": rating,
                                "status": status, "detail": r["detail"]}
    if mgr_rows:
        mgr_rows.sort(key=lambda r: -r["score"])
        n = len(mgr_rows)
        quota = max(1, int(round(n * cfg["quota_ratio"])))
        for i, r in enumerate(mgr_rows):
            rank = i + 1
            rating, status = "B", "ok"
            if r["score"] >= cfg["a_threshold"]:
                if rank <= quota:
                    rating = "A"
                else:
                    rating, status = "B", "cutline"
            elif r["score"] < cfg["c_threshold"]:
                rating = "C"
            ratings[r["id"]] = {"personId": r["id"], "score": r["score"], "rank": rank,
                                "deptCount": n, "quota": quota, "rating": rating,
                                "status": status, "detail": r["detail"], "scope": "manager"}
    for pid, nleft in pending.items():
        ratings[pid] = {"personId": pid, "score": None, "rank": None, "deptCount": None,
                        "quota": None, "rating": "待打分", "status": "pending",
                        "pendingGoals": nleft, "detail": []}
    STATE["ratings"] = ratings
    save_state()
    return ratings

# ---------------- 模型 ----------------
def call_model(messages, model, temperature=0.2, max_tokens=None):
    payload = {"model": model, "messages": messages, "temperature": temperature}
    if max_tokens:
        payload["max_tokens"] = max_tokens
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE_URL.rstrip("/") + "/chat/completions", data=data,
                                 headers={"Authorization": "Bearer " + API_KEY,
                                          "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=150) as r:
        return json.loads(r.read().decode("utf-8"))

def extract_json(text):
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("```")[1]
        if t.startswith("json"):
            t = t[4:]
        t = t.strip()
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        t = t[i:j + 1]
    return json.loads(t)

def ask_json(system, user, max_tokens=None):
    last = None
    for m in [MODEL] + [x for x in MODEL_FALLBACK if x != MODEL]:
        mt = max_tokens or 800
        if "deepseek" in m.lower():
            mt = max(mt * 3, 1500)      # 思考型模型：思考会吃掉 token
        try:
            resp = call_model([{"role": "system", "content": system},
                               {"role": "user", "content": user}], m, max_tokens=mt)
            return extract_json(resp["choices"][0]["message"]["content"]), m, resp.get("usage", {})
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore")[:200]
            last = f"HTTP {e.code} {body}"
            if "model_not_available" in body:
                continue
            raise RuntimeError(last)
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            continue
    raise RuntimeError("所有候选模型均失败: " + str(last))

# ---------------- 锚点 ----------------
ANCHOR_SYS = (
    "你是绩效目标编译助手。把员工的定性目标编译成可打分的行为锚点。\n"
    "硬性要求：\n"
    "1. 每一档都必须包含可量化口径（数字、比例，或 ≥/≤/不低于/不超过 等比较词）；\n"
    "2. 三档单调：优秀 > 达标 > 待改进，同一指标上阈值必须递进；\n"
    "3. 每档都必须写清「证据要求」，指向具体系统、报表或记录；\n"
    "3b. 三档直接对应公司评级区间：level 4 = 优秀（总分 95 以上）、level 3 = 达标（80–94.9）、level 2 = 待改进（80 以下）；描述里的阈值必须落在这个区间内；\n"
    "4. 禁止出现没有依据的评价性形容词（如「积极」「优秀地」）；\n"
    "5. 如果目标里已经有数字（如「从 820ms 降到 300ms」），锚点必须直接沿用这个数字，不得另造指标（如「天数占比」）；\n"
    "6. 只输出 JSON，不要 Markdown 代码块，不要解释。"
)
ANCHOR_USER = (
    "岗位：{role}\n目标原文：「{goal}」（权重 {weight}%）\n\n"
    "同时给出判定：verdict = ok（有指标有阈值，可直接评分）/ warn（有指标但缺周期或范围，可评但要补口径）/ block（没有可验证指标，无法评分）。\n"
    "输出 JSON：{{\"verdict\":\"ok|warn|block\",\"problem\":\"判定理由（一句话，不超过 40 字）\","
    "\"suggestions\":[\"可直接替换的量化改写 1\",\"量化改写 2\"],"
    "\"anchors\":[{{\"level\":4,\"name\":\"优秀\",\"desc\":\"...\",\"evidence\":\"...\"}},"
    "{{\"level\":3,\"name\":\"达标\",\"desc\":\"...\",\"evidence\":\"...\"}},"
    "{{\"level\":2,\"name\":\"待改进\",\"desc\":\"...\",\"evidence\":\"...\"}}]}}"
)
ANCHOR_FIX = "上一版不合格，问题如下：\n{issues}\n请重写，逐条修正以上问题，只输出同样结构的 JSON。"
QUANT_RE = re.compile(r"[0-9]|≥|≤|以上|以内|不低于|不超过|持平|上升|下降|低于|高于|超过|达到|完成")

def check_anchor(obj):
    if obj.get("verdict") == "block":
        return []          # 不可评：不要求三档
    issues = []
    anchors = obj.get("anchors") or []
    if len(anchors) != 3:
        return ["锚点必须正好 3 档"]
    levels = [a.get("level") for a in anchors]
    if len(set(levels)) != 3:
        issues.append("三档 level 有重复")
    if levels != sorted(levels, reverse=True):
        issues.append("档位未按从高到低排列")
    for a in anchors:
        d = (a.get("desc") or "").strip()
        e = (a.get("evidence") or "").strip()
        lv = a.get("level")
        if len(d) < 12:
            issues.append(f"{lv} 分描述过短（<12 字），信息量不足")
        if not QUANT_RE.search(d):
            issues.append(f"{lv} 分描述里没有任何可量化口径（数字或比较词）")
        if len(e) < 4:
            issues.append(f"{lv} 分缺少证据要求")
        for w in ("积极", "优秀地", "努力", "认真负责"):
            if w in d:
                issues.append(f"{lv} 分出现无依据的评价性词「{w}」")
    if not obj.get("problem"):
        issues.append("缺少 problem 字段（为什么不可直接评分）")
    return issues

def gen_anchor_checked(role, goal, weight, max_attempts=3):
    user = ANCHOR_USER.format(role=role, goal=goal, weight=weight)
    attempts, issues = [], []
    t0 = time.time()
    for i in range(max_attempts):
        obj, model, usage = ask_json(ANCHOR_SYS, user)
        issues = check_anchor(obj)
        attempts.append({"try": i + 1, "model": model, "issues": issues,
                         "tokens": usage.get("total_tokens")})
        if not issues:
            obj["_model"] = model
            obj["_usage"] = usage
            return obj, {"passed": True, "attempts": attempts, "elapsed": round(time.time() - t0, 1)}
        user = ANCHOR_USER.format(role=role, goal=goal, weight=weight) + "\n\n" + \
            ANCHOR_FIX.format(issues="\n".join(f"- {x}" for x in issues))
    obj["_model"] = attempts[-1]["model"] if attempts else "unknown"
    obj["_usage"] = {}
    return obj, {"passed": False, "attempts": attempts, "elapsed": round(time.time() - t0, 1)}

def fallback_anchors():
    return {"problem": "该目标缺少可验证的量化口径，年底只能凭印象打分",
            "anchors": [
                {"level": 4, "name": "优秀", "desc": "2 项以上可量化成果，指标超出岗位基线 20% 以上",
                 "evidence": "数据报表、项目记录、上下游书面确认"},
                {"level": 3, "name": "达标", "desc": "完成年初约定的主要交付，核心指标达到基线",
                 "evidence": "月度/季度数据、验收记录"},
                {"level": 2, "name": "待改进", "desc": "未完成主要交付，或指标低于基线且无客观原因",
                 "evidence": "缺陷/延期记录、复盘结论"}],
            "_model": "template-fallback", "_usage": {}}

class Blocked(Exception):
    def __init__(self, payload):
        self.payload = payload


JOBS = {}


def start_anchor_job(p, g, force=False):
    jid = uuid.uuid4().hex[:8]
    JOBS[jid] = {"id": jid, "status": "running", "personId": p["id"], "goalId": g["id"],
                 "startedAt": time.strftime("%Y-%m-%d %H:%M:%S")}

    def work():
        try:
            # 口径齐全：按规则直接生成，毫秒级，不调模型
            if goal_fields(g) and g.get("kind") != "qualitative" and not goal_missing_fields(g):
                res = build_anchor_from_fields(g)
                meta = {"passed": True,
                        "attempts": [{"try": 0, "issues": [], "model": "rule"}], "elapsed": 0}
                src = "rule"
            else:
                key = f'{p["role"]}|{g["text"]}'
                if key in CACHE and not force:
                    res = CACHE[key]
                    meta = {"passed": True,
                            "attempts": [{"try": 0, "issues": [], "model": "cache"}], "elapsed": 0}
                    src = "cache"
                else:
                    res, meta = gen_anchor_checked(p["role"], g["text"], g["weight"])
                    CACHE[key] = res
                    save_cache()
                    src = "model"

            if res.get("verdict") == "block":
                g["anchor"] = None
                g["suggestions"] = res.get("suggestions") or []
                g["verdict"] = "block"
                g["selfcheck"] = meta
                JOBS[jid].update({"status": "done", "source": src, "selfcheck": meta,
                                  "blocked": True,
                                  "finishedAt": time.strftime("%Y-%m-%d %H:%M:%S")})
                return

            g["anchor"] = res
            g["verdict"] = res.get("verdict") or "ok"
            g["suggestions"] = res.get("suggestions") or []
            g["selfcheck"] = meta
            JOBS[jid].update({"status": "done", "source": src, "selfcheck": meta,
                              "finishedAt": time.strftime("%Y-%m-%d %H:%M:%S")})
        except Exception as e:
            JOBS[jid].update({"status": "error", "error": f"{type(e).__name__}: {e}"[:200],
                              "finishedAt": time.strftime("%Y-%m-%d %H:%M:%S")})

    threading.Thread(target=work, daemon=True).start()
    return jid


def goal_of(pid, gid):
    p = BY_ID.get(pid)
    if not p:
        return None, None
    return p, next((g for g in p["goals"] if g["id"] == gid), None)

# ---------------- 评级理由 ----------------
REASON_SYS = (
    "你在为一次绩效评级撰写「评级理由」。读者是员工本人和 HR，他们要看的是「凭什么」，不是分数复述。\n"
    "硬性要求：\n"
    "1. 输出 3–5 句，客观陈述，不出现没有依据的评价性形容词；\n"
    "2. 禁止复述算分过程：不要出现「权重为 X%」「得分为 Y」这类句子；\n"
    "3. 每句必须带 cites 数组，引用给定证据编号（只能用给定的编号）；\n"
    "4. 至少一句必须引用 E 类证据（具体事件），说明「凭什么这样评」；\n"
    "5. 至少一句点明与上一档的差距所在；评级为 A 时点明达到 A 的关键事实；\n"
    "6. 只输出 JSON。"
)
REASON_USER = (
    "员工：{name}（{role}，{dept}）\n"
    "最终得分 {score}，评级 {rating}，部门排名 {rank}/{deptCount}，本部门 A 级名额 {quota} 人。\n"
    "与 A 线的关系：{delta}\n\n可用证据（编号只能用这些）：\n{facts}\n\n"
    "输出 JSON：{{\"sentences\":[{{\"text\":\"...\",\"cites\":[\"G1\"]}}]}}"
)

def level_of(score):
    return 4 if score >= 95 else (3 if score >= 85 else 2)

def build_facts(p, rec):
    facts, mapping = [], {}
    for i, d in enumerate(rec["detail"], 1):
        key = f"G{i}"
        lv = level_of(d["score"])
        mapping[key] = {"type": "goal", "text": d["text"], "score": d["score"],
                        "weight": d["weight"], "level": lv}
        facts.append(f"{key}｜目标「{d['text']}」，锚点达成：{lv} 分档")
    for k, ev in enumerate(p.get("events", []), 1):
        key = f"E{k}"
        mapping[key] = {"type": "event", "text": ev["text"], "date": ev.get("date")}
        facts.append(f"{key}｜具体事件：{ev['text']}（{ev.get('date','2026')}）")
    mapping["RANK"] = {"type": "rank", "rank": rec["rank"], "of": rec["deptCount"]}
    facts.append(f"RANK｜部门排名 {rec['rank']}/{rec['deptCount']}")
    mapping["QUOTA"] = {"type": "quota", "quota": rec["quota"], "rating": rec["rating"]}
    facts.append(f"QUOTA｜本部门 A 级名额 {rec['quota']} 人，本次评级 {rec['rating']}")
    delta = round(STATE["config"]["a_threshold"] - rec["score"], 1)
    mapping["DELTA"] = {"type": "delta", "delta": delta}
    dtxt = (f"低于 A 线（{STATE['config']['a_threshold']}）{delta} 分" if delta > 0
            else f"高出 A 线（{STATE['config']['a_threshold']}）{abs(delta)} 分")
    facts.append(f"DELTA｜{dtxt}" + ("；因名额限制未能评为 A" if rec.get("status") == "cutline" else ""))
    return "\n".join(facts), mapping

def check_reason(obj, mapping):
    issues = []
    sents = obj.get("sentences") or []
    if not (3 <= len(sents) <= 6):
        issues.append(f"句子数量为 {len(sents)}，应为 3–5 句")
    for i, s in enumerate(sents, 1):
        t = (s.get("text") or "").strip()
        c = s.get("cites") or []
        if len(t) < 8:
            issues.append(f"第 {i} 句过短")
        if len(t) > 70:
            issues.append(f"第 {i} 句过长（{len(t)} 字），请拆成短句，一句只说一件事")
        if not c:
            issues.append(f"第 {i} 句没有引用任何证据")
        bad = [x for x in c if x not in mapping]
        if bad:
            issues.append(f"第 {i} 句引用了不存在的证据 {bad}")
        if re.search(r"积极|认真负责|十分优秀|非常努力", t):
            issues.append(f"第 {i} 句出现无依据的评价性词")
        if re.search(r"(权重为|得分为|权重 ?\d+%|得分 ?\d)", t):
            issues.append(f"第 {i} 句复述了算分过程（权重/得分），请改为事实描述")
    if not any((c or "").startswith("E") for s in sents for c in (s.get("cites") or [])):
        issues.append("没有引用任何具体事件（E 类证据），理由会显得空泛")
    return issues

def reason_for(pid):
    p = BY_ID.get(pid)
    rec = STATE["ratings"].get(pid)
    if not p or not rec:
        return None
    facts, mapping = build_facts(p, rec)
    user = REASON_USER.format(name=p["name"], role=p["role"], dept=p["dept"], score=rec["score"],
                              rating=rec["rating"], rank=rec["rank"], deptCount=rec["deptCount"],
                              quota=rec["quota"],
                              delta=round(STATE["config"]["a_threshold"] - rec["score"], 1),
                              facts=facts)
    attempts = []
    for i in range(2):
        try:
            obj, model, usage = ask_json(REASON_SYS, user)
            issues = check_reason(obj, mapping)
            attempts.append({"try": i + 1, "issues": issues, "model": model})
            if not issues:
                return {"sentences": obj["sentences"], "mapping": mapping, "model": model,
                        "passed": True, "attempts": attempts,
                        "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
            user = user + "\n\n上一版问题：\n" + "\n".join(f"- {x}" for x in issues) + "\n请修正后重新输出。"
        except Exception as e:
            attempts.append({"try": i + 1, "error": str(e)[:200]})
    sents = [{"text": f"「{d['text']}」达到 {level_of(d['score'])} 分档。",
              "cites": [f"G{i}"]} for i, d in enumerate(rec["detail"], 1)]
    sents.append({"text": f"部门内排名 {rec['rank']}/{rec['deptCount']}，A 级名额 {rec['quota']} 人。",
                  "cites": ["RANK", "QUOTA"]})
    return {"sentences": sents, "mapping": mapping, "model": "template-fallback",
            "passed": False, "attempts": attempts, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}

# ---------------- 视图辅助 ----------------
# ---------------- 年度自评 ----------------
SELF_SYS = (
    "你在帮员工起草年度绩效自评。读者是直属领导与 HR。\n"
    "硬性要求：\n"
    "1. 输出 3–4 节，标题固定为：目标达成、关键贡献、不足与改进、明年重点（可少不可多，按顺序）；\n"
    "2. 每节 60–180 字，只写事实，不写空话套话，不出现没有依据的评价性形容词；\n"
    "3. 每节必须带 cites，引用给定证据编号（只能用给定编号）；\n"
    "4. 禁止编造数据；没有证据的方面就写「暂无」；\n"
    "5. 只输出 JSON。"
)
SELF_USER = (
    "员工：{name}（{role}，{dept}）\n"
    "最终得分 {score}，评级 {rating}，部门排名 {rank}/{deptCount}，A 名额 {quota} 人。\n"
    "可用证据（编号只能用这些）：\n{facts}\n\n"
    "输出 JSON：{{\"sections\":[{{\"title\":\"目标达成\",\"text\":\"...\",\"cites\":[\"G1\"]}}]}}"
)

def check_self(obj, mapping):
    issues = []
    secs = obj.get("sections") or []
    if not (3 <= len(secs) <= 4):
        issues.append(f"节数 {len(secs)}，应为 3–4 节")
    for i, s in enumerate(secs, 1):
        t = (s.get("text") or "").strip()
        if len(t) < 40:
            issues.append(f"第 {i} 节过短（{len(t)} 字）")
        if len(t) > 260:
            issues.append(f"第 {i} 节过长（{len(t)} 字）")
        cs = s.get("cites") or []
        if not cs:
            issues.append(f"第 {i} 节没有引用证据")
        bad = [x for x in cs if x not in mapping]
        if bad:
            issues.append(f"第 {i} 节引用了不存在的证据 {bad}")
        if re.search(r"积极|认真负责|十分优秀|非常努力", t):
            issues.append(f"第 {i} 节出现无依据的评价性词")
    return issues

def self_summary_for(pid):
    p = BY_ID.get(pid)
    rec = STATE["ratings"].get(pid)
    if not p or not rec:
        return None
    facts, mapping = build_facts(p, rec)
    user = SELF_USER.format(name=p["name"], role=p["role"], dept=p["dept"], score=rec["score"],
                            rating=rec["rating"], rank=rec["rank"], deptCount=rec["deptCount"],
                            quota=rec["quota"], facts=facts)
    attempts = []
    for i in range(2):
        try:
            obj, model, usage = ask_json(SELF_SYS, user)
            issues = check_self(obj, mapping)
            attempts.append({"try": i + 1, "issues": issues, "model": model})
            if not issues:
                text = "\n\n".join(f"【{s['title']}】\n{s['text']}" for s in obj["sections"])
                return {"sections": obj["sections"], "text": text, "mapping": mapping,
                        "model": model, "passed": True, "attempts": attempts,
                        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "source": "model", "edited": False}
            user = user + "\n\n上一版问题：\n" + "\n".join(f"- {x}" for x in issues) + "\n请修正后重新输出。"
        except Exception as e:
            attempts.append({"try": i + 1, "error": str(e)[:200]})
    # 兜底：结构化事实陈述
    secs = []
    for i, d in enumerate(rec["detail"], 1):
        secs.append({"title": "目标达成" if i == 1 else "关键贡献",
                     "text": f"{d['text']}（权重 {d['weight']}%）本年度得分为 {d['score']}，"
                             f"落在 {level_of(d['score'])} 分档。",
                     "cites": [f"G{i}"]})
    secs.append({"title": "不足与改进",
                 "text": "证据材料中未体现明显不足项，建议补充上下半年的偏差分析与改进动作。",
                 "cites": ["RANK"]})
    text = "\n\n".join(f"【{s['title']}】\n{s['text']}" for s in secs)
    return {"sections": secs, "text": text, "mapping": mapping, "model": "template-fallback",
            "passed": False, "attempts": attempts, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source": "template", "edited": False}

def rec_of(pid):
    return STATE["ratings"].get(pid)

def year_start_done(p):
    return bool(p["goals"]) and all(g["status"] == "confirmed" for g in p["goals"])


def scores_visible(ps):
    """分数与评级只在年终可见；HR 任何时候都能看"""
    return stage() == "end" or ps.role == "hr"


def base_card(p, ps=None):
    show = (scores_visible(ps) if ps else True) and year_start_done(p)
    total = person_total(p)[0] if show else None
    rec = (rec_of(p["id"]) or {}) if show else {}
    return {"id": p["id"], "name": p["name"], "role": p["role"], "dept": p["dept"],
            "manager": p["manager"], "director": p["director"], "goals": len(p["goals"]),
            "isManager": bool(p.get("isManager")),
            "confirmed": sum(1 for g in p["goals"] if g["status"] == "confirmed"),
            "total": total, "rating": rec.get("rating", "-") if show else "",
            "status": rec.get("status", "") if show else "",
            "todo": todo_of(p)}

def todo_of(p):
    if not p["goals"]:
        return "目标未提交" if stage() == "start" else "年初未完成"
    st = [g["status"] for g in p["goals"]]
    phase = stage()
    if phase == "start":
        if any(s in ("draft", "rejected") for s in st):
            return "目标未提交"
        if any(s == "submitted" for s in st):
            return "目标待审批"
        if any(s == "approved" and not g.get("anchor") for g, s in zip(p["goals"], st)):
            return "锚点待生成"
        if any(s != "confirmed" for s in st):
            return "锚点待确认"
        return "年初已完成"
    if phase == "mid":
        if any(c["personId"] == p["id"] and c["status"] == "待审批" for c in STATE["changes"]):
            return "变更待审批"
        return "年中无待办"
    if any(s != "confirmed" for s in st):
        return "年初未完成"
    rater = "隔级领导" if p.get("isManager") else "直属领导"
    has_all_scores = all(f'{p["id"]}|{g["id"]}|{rater}' in STATE["scores"] for g in p["goals"])
    if not has_all_scores:
        return "待打分"
    if p["id"] not in STATE["reasons"]:
        return "待确认理由"
    return "已完成"

# ---------------- HTTP ----------------
class Handler(BaseHTTPRequestHandler):
    server_version = "Yardstick/0.3"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

    def persona(self):
        return Persona(self.headers.get("X-As") or "hr")

    def forbidden(self, msg="当前身份无权访问该数据"):
        return self._send(403, {"error": msg})

    def do_GET(self):
        path = self.path.split("?")[0]
        ps = self.persona()
        try:
            if path == "/api/health":
                return self._send(200, {"ok": True, "model": MODEL, "hasKey": bool(API_KEY),
                                        "cached": len(CACHE), "seeded": STATE["seeded"],
                                        "config": STATE["config"], "stage": stage(), "stageName": STAGE_NAMES.get(stage(), ""), "audit": len(STATE["audit"]),
                                        "persona": {"raw": ps.raw, "label": ps.label, "role": ps.role,
                                "selfId": (ps.self_person or {}).get("id") or (ps.person or {}).get("id")}})
            if path == "/api/people":
                if ps.role == "emp":
                    return self._send(200, [base_card(ps.person, ps)])
                return self._send(200, [base_card(p, ps) for p in ps.scope()])
            if path.startswith("/api/person/"):
                pid = path.rsplit("/", 1)[-1]
                p = BY_ID.get(pid)
                if not p:
                    return self._send(404, {"error": "not found"})
                if not ps.can("view", p):
                    return self.forbidden()
                show = scores_visible(ps) and year_start_done(p)
                total, detail = person_total(p) if show else (None, [])
                return self._send(200, dict(
                    p, total=total, detail=detail,
                    rating=rec_of(pid) if show else None,
                    reason=STATE["reasons"].get(pid), todo=todo_of(p),
                    scores=({g["id"]: {n: STATE["scores"].get(f"{pid}|{g['id']}|{n}")
                                       for n, _ in raters()} for g in p["goals"]} if show else {}),
                    changes=[c for c in STATE["changes"] if c["personId"] == pid],
                    appeals=[a for a in STATE["appeals"] if a["personId"] == pid],
                    selfsummary=STATE["selfsummaries"].get(pid),
                    metrics={g["id"]: [dict(m, **{
                        "actual": (STATE["actuals"].get(actual_key(pid, g["id"], m["id"])) or {}),
                        "score": (score_metric(STATE["actuals"][actual_key(pid, g["id"], m["id"])]["value"], m)
                                  if STATE["actuals"].get(actual_key(pid, g["id"], m["id"]), {}).get("status") == "verified" else None),
                        "state": (STATE["actuals"].get(actual_key(pid, g["id"], m["id"])) or {}).get("status", "missing"),
                    }) for m in (g.get("metrics") or [])]
                        for g in p["goals"] if g.get("metrics")},
                    permissions={"confirm": ps.can("confirm_anchor", p),
                                 "score": ps.can("score_direct", p)
                                          or ps.can("score_skip", p)
                                          or (p.get("isManager", False) and ps.role == "skip"),
                                 "reason": ps.can("reason", p),
                                 "appeal": ps.can("appeal", p),
                                 "approve_change": ps.can("approve_change", p)}))
            if path == "/api/progress":
                if ps.role in ("emp", "invalid"):
                    return self.forbidden()
                return self._send(200, self.progress(ps))
            if path == "/api/overview":
                if ps.role in ("emp", "invalid"):
                    return self.forbidden()
                return self._send(200, self.overview(ps))
            if path.startswith("/api/job/"):
                jid = path.rsplit("/", 1)[-1]
                job = JOBS.get(jid)
                if not job:
                    return self._send(404, {"error": "job not found"})
                return self._send(200, job)
            if path == "/api/audit":
                if ps.role != "hr":
                    return self.forbidden("审计日志仅 HR 可见")
                return self._send(200, STATE["audit"][-100:][::-1])
            if path == "/api/appeals":
                if ps.role != "hr":
                    return self.forbidden("申诉清单仅 HR 可见")
                return self._send(200, STATE["appeals"][::-1])
            if path.startswith("/api/export/"):
                pid = path.rsplit("/", 1)[-1]
                p = BY_ID.get(pid)
                if not p or not ps.can("view", p):
                    return self.forbidden()
                md = self.export_md(pid)
                if md is None:
                    return self._send(404, {"error": "not found"})
                return self._send(200, md, "text/markdown; charset=utf-8")
        except Exception as e:
            return self._send(500, {"error": str(e)})
        return self._static(path)

    def _static(self, path):
        if path.startswith("/preview/"):
            name = path.split("/", 2)[-1]
            if "/" in name or ".." in name:
                return self._send(404, {"error": "not found"})
            page = preview_html(name)
            if page is None:
                return self._send(404, {"error": "no such file"})
            return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
        if path.startswith("/uploads/"):
            fn = (UPLOAD_DIR / path.split("/", 2)[-1]).resolve()
            if not str(fn).startswith(str(UPLOAD_DIR.resolve())) or not fn.is_file():
                return self._send(404, {"error": "no such file"})
            ctype = {".pdf": "application/pdf", ".png": "image/png",
                     ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
                     ".txt": "text/plain; charset=utf-8", ".csv": "text/csv; charset=utf-8",
                     ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                     ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     ".doc": "application/msword", ".xls": "application/vnd.ms-excel"}.get(
                fn.suffix.lower(), "application/octet-stream")
            return self._send(200, fn.read_bytes(), ctype)
        if path == "/":
            path = "/index.html"
        f = (STATIC / path.lstrip("/")).resolve()
        if not str(f).startswith(str(STATIC.resolve())) or not f.is_file():
            return self._send(404, {"error": "no such file"})
        ctype = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}.get(
            f.suffix, "application/octet-stream")
        return self._send(200, f.read_bytes(), ctype)

    def do_POST(self):
        global STATE
        path = self.path.split("?")[0]
        ps = self.persona()
        try:
            body = self._body()
        except Exception as e:
            return self._send(400, {"error": "bad json: %s" % e})
        try:
            if path == "/api/anchors":
                return self._send(200, self.api_anchors(body, ps))
            if path == "/api/anchors/batch":
                return self._send(200, self.api_anchors_batch(body, ps))
            if path == "/api/goals/update":
                need_stage("start")
                p, g = goal_of(body.get("personId"), body.get("goalId"))
                if not g:
                    raise ValueError("找不到这条目标")
                if g.get("status") != "draft":
                    raise ValueError("只有草稿状态的目标能直接改；已提交的请走变更申请")
                if not is_self(ps, p):
                    raise PermissionError      # 领导也不能直接改下属目标
                if body.get("text"):
                    t = str(body["text"]).strip()
                    if len(t) < 6:
                        raise ValueError("目标描述至少 6 个字")
                    g["text"] = t[:200]
                if body.get("fields"):
                    nf = normalize_fields({**(g.get("fields") or {}), **(body.get("fields") or {})})
                    bad2 = numeric_conflict(nf)
                    if bad2:
                        raise ValueError(bad2)
                    g["fields"] = nf
                audit(ps.label, "修改草稿目标", f'{p["name"]}·{g["id"]}', None,
                      (body.get("why") or "")[:40], "")
                save_state()
                return self._send(200, {"ok": True, "goalId": g["id"],
                                        "text": g["text"], "fields": g.get("fields")})
            if path == "/api/goals/add":
                need_stage("start")
                p = BY_ID.get(body.get("personId"))
                if not p:
                    raise ValueError("not found")
                if not is_self(ps, p):
                    raise PermissionError
                text = (body.get("text") or "").strip()
                if len(text) < 6:
                    raise ValueError("目标描述至少 6 个字")
                fields = normalize_fields(body.get("fields") or {})
                miss = [lbl for k, lbl in FIELD_REQUIRED if not str(fields.get(k) or "").strip()]
                if miss:
                    raise ValueError("口径还缺：" + "、".join(miss))
                try:
                    w = float(body.get("weight") or 0)
                except (TypeError, ValueError):
                    raise ValueError("权重必须是数字")
                if not (0 < w <= 100):
                    raise ValueError("权重需在 1–100 之间")
                bad = numeric_conflict(fields)
                if bad:
                    raise ValueError(bad)
                used = sum(g["weight"] for g in p["goals"])
                if used + w > 100.5:
                    raise ValueError(f"权重会超过 100%（现在已用 {round(used)}%）")
                gid = f"g{int(time.time() * 1000) % 1000000}"
                p["goals"].append({"id": gid, "text": text, "weight": round(w),
                                   "fields": fields, "kind": "metric",
                                   "anchor": None, "status": "draft", "version": "v1"})
                audit(ps.label, "对话新增目标", f'{p["name"]}·{text[:16]}', None,
                      f'{round(w)}%', (body.get("why") or "")[:40])
                save_state()
                return self._send(200, {"ok": True, "goalId": gid,
                                        "totalWeight": round(used + w)})
            if path == "/api/goals/save":
                return self._send(200, self.api_goals_save(body, ps))
            if path == "/api/goals/parse":
                return self._send(200, self.api_goals_parse(body, ps))
            if path == "/api/goals/precheck":
                return self._send(200, self.api_goals_precheck(body, ps))
            if path == "/api/goals/submit":
                return self._send(200, self.api_goals_submit(body, ps))
            if path == "/api/goals/withdraw":
                return self._send(200, self.api_goals_withdraw(body, ps))
            if path == "/api/goals/qualitative":
                return self._send(200, self.api_goals_qualitative(body, ps))
            if path == "/api/goals/reopen":
                return self._send(200, self.api_goals_reopen(body, ps))
            if path == "/api/goals/review":
                return self._send(200, self.api_goals_review(body, ps))
            if path == "/api/anchors/async":
                return self._send(200, self.api_anchors_async(body, ps))
            if path == "/api/confirm/batch":
                return self._send(200, self.api_confirm_batch(body, ps))
            if path == "/api/anchors/edit":
                need_stage("start")
                p, g = goal_of(body.get("personId"), body.get("goalId"))
                if not g or not g.get("anchor"):
                    raise ValueError("这条目标还没有锚点")
                if not ps.can("confirm_anchor", p):
                    raise PermissionError
                try:
                    lvl = int(body.get("level"))
                except (TypeError, ValueError):
                    raise ValueError("档位必须是 4/3/2")
                desc = (body.get("desc") or "").strip()
                if len(desc) < 6:
                    raise ValueError("锚点描述至少 6 个字")
                for t in g["anchor"].get("anchors") or []:
                    if int(t.get("level")) == lvl:
                        old = t.get("desc")
                        t["desc"] = desc
                        t["edited"] = True
                        g["anchor"]["edited"] = True
                        audit(ps.label, "人工调整锚点", f'{p["name"]}·{g["text"][:12]}', old, desc, "")
                        save_state()
                        return self._send(200, {"ok": True, "anchor": g["anchor"]})
                raise ValueError("没有这一档")
            if path == "/api/anchors/polish":
                need_stage("start")
                p, g = goal_of(body.get("personId"), body.get("goalId"))
                if not g or not g.get("anchor"):
                    raise ValueError("先有锚点才能润色")
                if not ps.can("confirm_anchor", p):
                    raise PermissionError
                g["anchor"] = polish_anchor(p["role"], g["anchor"], g.get("fields") or {})
                save_state()
                audit(ps.label, "润色锚点措辞", f'{p["name"]}·{g["text"][:12]}', None, "polished", "")
                return self._send(200, {"ok": True, "anchor": g["anchor"]})
            if path == "/api/confirm":
                return self._send(200, self.api_confirm(body, ps))
            if path == "/api/score":
                return self._send(200, self.api_score(body, ps))
            if path == "/api/compute":
                if ps.role != "hr":
                    return self.forbidden("只有 HR 可以重算")
                r = compute_all()
                audit("HR", "重算评级", "全部人员", None, f"{len(r)} 人", body.get("reason", ""))
                return self._send(200, {"ok": True, "count": len(r)})
            if path == "/api/metric/report":
                return self._send(200, self.api_metric_report(body, ps))
            if path == "/api/metric/verify":
                return self._send(200, self.api_metric_verify(body, ps))
            if path == "/api/selfsummary":
                return self._send(200, self.api_selfsummary(body, ps))
            if path == "/api/reason":
                need_stage("end")
                pid = body.get("personId")
                p = BY_ID.get(pid)
                if not p:
                    return self._send(404, {"error": "not found"})
                if not ps.can("reason", p):
                    return self.forbidden()
                if not STATE["ratings"]:
                    compute_all()
                res = reason_for(pid)
                if not res:
                    return self._send(404, {"error": "no rating"})
                STATE["reasons"][pid] = res
                audit(ps.label, "生成评级理由", pid, None, res["model"], "")
                save_state()
                return self._send(200, res)
            if path == "/api/appeal":
                need_stage("end")
                pid = body.get("personId")
                p = BY_ID.get(pid)
                if not p or not ps.can("appeal", p):
                    return self.forbidden("只能为自己提交申诉")
                txt = (body.get("text") or "").strip()
                if len(txt) < 5:
                    return self._send(400, {"error": "申诉内容太短"})
                item = {"id": f"a{int(time.time())}", "personId": pid, "name": p["name"],
                        "text": txt[:300], "status": "待处理",
                        "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
                STATE["appeals"].append(item)
                audit(ps.label, "提交申诉", pid, None, txt[:60])
                return self._send(200, {"ok": True, "appeal": item})
            if path == "/api/appeal/resolve":
                if ps.role != "hr":
                    return self.forbidden()
                aid = body.get("id")
                for a in STATE["appeals"]:
                    if a["id"] == aid:
                        a["status"] = body.get("status", "已处理")
                        a["reply"] = (body.get("reply") or "")[:200]
                        audit("HR", "处理申诉", a["personId"], "待处理", a["status"], a["reply"])
                        return self._send(200, {"ok": True})
                return self._send(404, {"error": "not found"})
            if path == "/api/chat":
                msg = (body.get("message") or "").strip()
                if not msg:
                    raise ValueError("说点什么")
                hist = body.get("history") or []
                msgs = [{"role": "system", "content": CHAT_SYS},
                        {"role": "system", "content": "以下是当前数据：\n" + chat_context(ps)}]
                for h in hist[-8:]:
                    if h.get("role") in ("user", "assistant") and h.get("content"):
                        msgs.append({"role": h["role"], "content": clean_reply(str(h["content"]))[:800]})
                msgs.append({"role": "user", "content": msg[:800]})
                try:
                    raw, used_model = chat_reply(msgs, max_tokens=1200)
                    raw = clean_reply(raw)
                except Exception as e:
                    return self._send(200, {"reply": "后台模型暂时连不上（" + str(e)[:60] +
                                            "），你的数据没变，过一会儿再问。",
                                            "action": None, "page": ""})
                reply, action = raw, None
                try:
                    obj = extract_json(raw)
                    if not isinstance(obj, dict):
                        raise ValueError("not json")
                    if isinstance(obj, dict):
                        reply = str(obj.get("reply") or "").strip() or raw
                        act = obj.get("action")
                        if isinstance(act, dict) and act.get("type") in (
                                "submit_goals", "approve_goals", "gen_anchor",
                                "confirm_anchor", "score", "open_page", "edit_anchor", "add_goal", "update_goal", "withdraw_goals", "change_request"):
                            scope = {x["id"]: x for x in ps.scope()}
                            self_id = ((ps.self_person or ps.person or {}) or {}).get("id")
                            pid = act.get("personId") or ""
                            if pid not in scope:
                                pid = self_id if self_id in scope else (list(scope)[0] if scope else "")
                            act["personId"] = pid
                            if pid in scope:
                                act["personName"] = scope[pid]["name"]
                            gid = act.get("goalId") or ""
                            if gid and pid in scope:
                                valid = [g["id"] for g in scope[pid].get("goals") or []]
                                act["goalId"] = gid if gid in valid else (valid[0] if valid else "")
                            if act.get("type") == "edit_anchor":
                                # 模型常给半截描述：用现有锚点/口径补成完整一句
                                if not act.get("level"):
                                    _blob = str(act.get("detail") or "") + str(act.get("desc") or "")
                                    if "A档" in _blob or "A 档" in _blob:
                                        act["level"] = 4
                                    elif "B档" in _blob or "B 档" in _blob:
                                        act["level"] = 3
                                    elif "C档" in _blob or "C 档" in _blob:
                                        act["level"] = 2
                                    else:
                                        act["level"] = 4
                                _desc = str(act.get("desc") or "").strip()
                                if len(_desc) < 6:
                                    _per = scope.get(pid) or {}
                                    _g = next((x for x in (_per.get("goals") or [])
                                               if x["id"] == act.get("goalId")), None)
                                    if _g:
                                        _lvl = act.get("level")
                                        _built = build_anchor_from_fields(_g).get("anchors") or []
                                        _hit = next((t for t in _built
                                                     if str(t.get("level")) == str(_lvl)), None)
                                        if _hit:
                                            _desc = _hit.get("desc") or ""
                                    if len(_desc) < 6:
                                        _desc = str(act.get("detail") or "").strip()
                                act["desc"] = _desc
                            if act.get("type") == "change_request":
                                f2 = act.get("fields") if isinstance(act.get("fields"), dict) else {}
                                for k in ("name", "target", "baseline", "unit", "direction",
                                          "cycle", "source", "scope"):
                                    if not f2.get(k) and act.get(k):
                                        f2[k] = act[k]
                                if f2:
                                    f2 = normalize_fields(f2)
                                if not f2.get("name") or not f2.get("target"):
                                    # 从描述里抽一次口径
                                    try:
                                        pr = parse_goal_text(ps.role, str(act.get("detail") or msg))
                                        f2 = normalize_fields(pr.get("fields") or {})
                                    except Exception:
                                        pass
                                act["fields"] = f2
                                if not (act.get("newText") or "").strip() and f2.get("target"):
                                    act["newText"] = compose_goal_text(f2)
                                if not (act.get("evidence") or "").strip():
                                    ev = re.search(r"https?://\S+", str(act.get("detail") or msg))
                                    act["evidence"] = ev.group(0) if ev else ""
                                if len(str(act.get("newText") or "")) < 6:
                                    act = None
                            if act and act.get("type") == "add_goal":
                                f = act.get("fields") if isinstance(act.get("fields"), dict) else {}
                                for k in ("name", "target", "baseline", "unit", "direction",
                                          "cycle", "source", "scope"):
                                    if not f.get(k) and act.get(k):
                                        f[k] = act[k]
                                # 模型漏了 text：用 detail 或用户最后一句话兜底
                                if not act.get("text"):
                                    act["text"] = (act.get("detail") or msg or "").strip()[:200]
                                # 模型漏了口径：服务端自动补抽一次
                                need = [k for k, _ in FIELD_REQUIRED if not str(f.get(k) or "").strip()]
                                if need and act.get("text"):
                                    try:
                                        pr = parse_goal_text(ps.role, act["text"])
                                        for k, v in (pr.get("fields") or {}).items():
                                            if v and not f.get(k):
                                                f[k] = v
                                    except Exception:
                                        pass
                                act["fields"] = f
                                if not act.get("weight"):
                                    blob = " ".join([str(act.get("detail") or ""),
                                                     str(act.get("text") or ""),
                                                     str(msg or "")])
                                    mm = re.search(r"(\d{1,3})\s*%", blob)
                                    act["weight"] = int(mm.group(1)) if mm else 100
                            if act.get("type") in ("add_goal", "update_goal") and act.get("fields"):
                                _nf = normalize_fields(act["fields"])
                                _bad = numeric_conflict(_nf)
                                if _bad:
                                    act = None
                                    reply = (reply + "\n（这条数字对不上：" + _bad +
                                             "，口径需要重定）").strip()
                            ok_perm, why = action_allowed(ps, act) if act else (False, "")
                            if act.get("type") in ("gen_anchor", "confirm_anchor", "score") and not act.get("goalId"):
                                act, ok_perm = None, False
                            if act and ok_perm:
                                action = act
                            elif act and not ok_perm:
                                reply = (reply + "\n（这个操作现在做不了：" + why + "）").strip()
                except Exception:
                    pass
                page = ""
                _kw = [(("团队", "下属", "谁没交", "谁还没交", "谁没提交"), "team"),
                       (("审批", "批了", "批准", "退回"), "approve"),
                       (("变更申请", "改目标"), "change"),
                       (("条线", "分布", "名额", "卡线"), "org"),
                       (("申诉",), "appeals"),
                       (("我的目标", "我的评分", "理由", "我的锚点", "多少分"), "me")]
                for _words, _pg in _kw:
                    if any(w in msg for w in _words):
                        page = _pg
                        break
                try:
                    pg = str((obj or {}).get("page") or "").strip()
                    allowed = {"me": ("emp", "mgr"),
                               "team": ("mgr", "skip"),
                               "approve": ("mgr", "skip"),
                               "changes": ("mgr", "skip"),
                               "change": ("emp", "mgr", "skip"),
                               "org": ("mgr", "skip"),
                               "appeals": ("emp",)}
                    if pg in allowed and ps.role in allowed[pg]:
                        page = pg
                except Exception:
                    page = ""
                choices = []
                # 模型没给选项时，按问题类型补一组（常见追问）
                # 取出最后一句问句，只对它做判断
                question = ""
                for seg in re.split(r"[。！!\n]", reply or ""):
                    seg = seg.strip()
                    if "？" in seg or "?" in seg:
                        question = seg      # 只要句子里有问号就算提问
                if not question:
                    question = (reply or "").strip().split("\n")[0][:80]
                if not choices and ("？" in (question or "") or "?" in (question or "")):
                    choices = gen_choices(question)
                try:
                    for c in (obj or {}).get("choices") or []:
                        t = str(c).strip()
                        if 1 <= len(t) <= 14 and len(choices) < 4:
                            choices.append(t)
                except Exception:
                    pass
                reply = re.sub(r"\*\*|__|`", "", reply or "")
                _seen_c, _uniq = set(), []
                for _c in choices:
                    _k = str(_c).strip()
                    if _k and _k not in _seen_c:
                        _seen_c.add(_k)
                        _uniq.append(_k)
                choices = _uniq[:4]
                audit(ps.label, "对话提问", msg[:30], None, reply[:40], "")
                return self._send(200, {"reply": reply or "我没想好怎么答，你换个说法？",
                                        "action": action, "page": page, "choices": choices,
                                        "question": question, "model": used_model})
            if path == "/api/upload":
                need_stage("mid")
                name = (body.get("filename") or "file").strip()
                raw = body.get("content") or ""
                if "," in raw:
                    raw = raw.split(",", 1)[1]
                try:
                    data = base64.b64decode(raw, validate=False)
                except Exception:
                    raise ValueError("文件内容无法解析")
                if len(data) > 5 * 1024 * 1024:
                    raise ValueError("文件不能超过 5MB")
                ext = Path(name).suffix.lower()[:8]
                safe = f"{uuid.uuid4().hex[:10]}{ext}"
                (UPLOAD_DIR / safe).write_bytes(data)
                return self._send(200, {"ok": True, "url": "/uploads/" + safe,
                                        "name": name, "size": len(data)})
            if path == "/api/change":
                need_stage("mid")
                pid = body.get("personId")
                p = BY_ID.get(pid)
                if not p or not ps.can("view", p):
                    return self.forbidden()
                new_text = (body.get("newText") or "").strip()
                if len(new_text) < 6:
                    raise ValueError("请填写变更后的目标（至少 6 个字）")
                item = {"id": f"c{int(time.time())}", "personId": pid, "name": p["name"],
                        "goalId": body.get("goalId"), "text": (body.get("text") or "")[:200],
                        "newText": new_text[:200], "fields": body.get("fields") or {},
                        "evidence": (body.get("evidence") or "")[:200], "status": "待审批",
                        "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
                # 直属领导（或 HR）代下属发起：等于上级决定，直接生效，不再自己批自己
                by_manager = (ps.role == "mgr" and p.get("manager") == ps.who
                              and ps.role != "emp")
                if by_manager or ps.role == "hr":
                    apply_change(item, ps.label)
                    item["status"] = "已生效（上级发起）"
                STATE["changes"].append(item)
                audit(ps.label, "提交目标变更", pid, None, item["text"][:40])
                return self._send(200, {"ok": True, "change": item,
                                        "autoApplied": bool(by_manager or ps.role == "hr")})
            if path == "/api/change/approve":
                need_stage("mid")
                cid = body.get("id")
                for c in STATE["changes"]:
                    if c["id"] != cid:
                        continue
                    p = BY_ID.get(c["personId"])
                    if not ps.can("approve_change", p):
                        return self.forbidden()
                    ok = bool(body.get("ok"))
                    c["status"] = "已批准" if ok else "已驳回"
                    if ok:
                        apply_change(c, ps.label)
                    return self._send(200, {"ok": True, "change": c})
                return self._send(404, {"error": "not found"})
            if path == "/api/config":
                if ps.role != "hr":
                    return self.forbidden("只有 HR 可以修改规则")
                cfg = STATE["config"]
                before = dict(cfg)
                for k in ("mode", "quota_ratio", "a_threshold", "c_threshold", "stage"):
                    if k in body:
                        cfg[k] = body[k]
                changed = {k: (before.get(k), cfg[k]) for k in cfg if before.get(k) != cfg.get(k)}
                b = "; ".join(f"{k}={v[0]}" for k, v in changed.items()) or "-"
                a = "; ".join(f"{k}={v[1]}" for k, v in changed.items()) or "-"
                audit("HR", "修改配置", "规则配置", b, a, body.get("reason", ""))
                compute_all()
                return self._send(200, {"ok": True, "config": cfg})
            if path == "/api/simulate":
                if ps.role != "hr":
                    return self.forbidden()
                STATE["seeded"] = False
                STATE["scores"] = {}
                ensure_scores()
                compute_all()
                audit("HR", "生成演示分数", "全部人员", None, "重新模拟", "")
                return self._send(200, {"ok": True})
            if path == "/api/demo/load":
                if ps.role != "hr":
                    return self.forbidden()
                return self._send(200, load_demo())
            if path == "/api/reset":
                if ps.role != "hr":
                    return self.forbidden()
                STATE = fresh_state()
                # 只保留账号：所有人的目标全部清空（含口径、锚点、审批状态）
                for _p in PEOPLE:
                    _p["goals"] = []
                STATE["goals"] = {}
                STATE["scores"] = {}
                STATE["actuals"] = {}
                STATE["actuals_seeded"] = True
                STATE["ratings"] = {}
                STATE["reasons"] = {}
                STATE["selfsummaries"] = {}
                STATE["appeals"] = []
                STATE["changes"] = []
                STATE["audit"] = []
                STATE["cleared"] = True
                STATE["config"]["stage"] = "start"
                save_state()
                compute_all()
                return self._send(200, {"ok": True})
        except urllib.error.HTTPError as e:
            return self._send(502, {"error": e.read().decode("utf-8", "ignore")[:300]})
        except Blocked as e:
            return self._send(400, e.payload)
        except PermissionError:
            return self.forbidden()
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})
        return self._send(404, {"error": "no route"})

    def api_anchors(self, body, ps):
        need_stage("start")
        p, g = goal_of(body.get("personId"), body.get("goalId"))
        if not p or not g:
            raise ValueError("goal not found")
        if ps.role == "emp" and ps.person["id"] != p["id"]:
            raise PermissionError
        if g.get("status") == "confirmed" and g.get("anchor"):
            raise ValueError("锚点已确认锁定，不能再生成；如需调整请走变更申请")
        if g.get("status") not in ("approved", "confirmed"):
            raise ValueError("目标还没被批准，不能生成锚点；请先在「目标审批」里通过")
        if goal_fields(g) and g.get("kind") != "qualitative" and not goal_missing_fields(g):
            g["anchor"] = build_anchor_from_fields(g)
            g["verdict"] = "ok"
            g["selfcheck"] = {"passed": True, "attempts": [{"try": 0, "issues": [], "model": "rule"}],
                              "elapsed": 0}
            save_state()
            return {"ok": True, "result": dict(g["anchor"], goalId=g["id"]), "source": "rule"}
        key = f'{p["role"]}|{g["text"]}'
        if key in CACHE and not body.get("force"):
            res = CACHE[key]
            meta = {"passed": True, "attempts": [{"try": 0, "issues": [], "model": "cache"}], "elapsed": 0}
            src = "cache"
        else:
            try:
                res, meta = gen_anchor_checked(p["role"], g["text"], g["weight"])
                CACHE[key] = res
                save_cache()
                src = "model"
            except Exception as e:
                res = fallback_anchors()
                meta = {"passed": False, "attempts": [{"error": str(e)[:200]}], "elapsed": 0}
                src = "fallback"
        g["anchor"] = res
        g["selfcheck"] = meta
        return {"result": res, "source": src, "selfcheck": meta}

    def api_anchors_batch(self, body, ps):
        p = BY_ID.get(body.get("personId"))
        if not p:
            raise ValueError("not found")
        if ps.role == "emp" and ps.person["id"] != p["id"]:
            raise PermissionError
        todo = [g for g in p["goals"] if not g.get("anchor") or body.get("force")]
        if not todo:
            return {"results": [], "note": "all cached"}
        with ThreadPoolExecutor(max_workers=3) as ex:
            futures = {ex.submit(self.api_anchors, {"personId": p["id"], "goalId": g["id"]}, ps): g
                       for g in todo}
            out = []
            for f in futures:
                r = f.result()
                out.append({"goalId": futures[f]["id"], "ok": "result" in r,
                            "source": r.get("source"), "passed": r.get("selfcheck", {}).get("passed")})
        return {"results": out}

    # ---------- 年初：员工填目标 → 领导审批 ----------
    def api_goals_save(self, body, ps):
        need_stage("start")
        p = BY_ID.get(body.get("personId"))
        if not p:
            raise ValueError("not found")
        if not is_self(ps, p):
            raise PermissionError
        if any(g["status"] == "confirmed" for g in p["goals"]):
            raise ValueError("已有确认的目标，不能再改；如需调整请走变更申请")
        rows = body.get("goals") or []
        if not (1 <= len(rows) <= 6):
            raise ValueError("目标数量需在 1–6 条之间")
        total_w = 0
        new_goals = []
        for i, r in enumerate(rows):
            text = (r.get("text") or "").strip()
            if len(text) < 6:
                raise ValueError(f"第 {i+1} 条目标太短（至少 6 个字）")
            try:
                w = float(r.get("weight"))
            except (TypeError, ValueError):
                raise ValueError(f"第 {i+1} 条权重必须是数字")
            if not (0 < w <= 100):
                raise ValueError(f"第 {i+1} 条权重需在 1–100 之间")
            total_w += w
            new_goals.append({"id": r.get("id") or f'g{p["id"][1:]}_{i}',
                              "text": text, "weight": round(w), "anchor": None,
                              "status": "draft", "version": "v1",
                              "fields": normalize_fields(r.get("fields") or {}),
                              "kind": r.get("kind") or "metric",
                              "check": r.get("check") or {}})
        p["goals"] = new_goals
        audit(ps.label, "保存目标草稿", p["name"], None, f"{len(new_goals)} 条，合计 {round(total_w)}%")
        return {"ok": True, "count": len(new_goals), "totalWeight": round(total_w)}

    def api_goals_parse(self, body, ps):
        need_stage("start")
        p = BY_ID.get(body.get("personId"))
        if not p:
            raise ValueError("not found")
        if not (ps.role in ("emp", "mgr") or ps.role == "hr"):
            raise PermissionError
        text = (body.get("text") or "").strip()
        if len(text) < 4:
            raise ValueError("先用一句话把目标说清楚")
        return parse_goal_text(p.get("role"), text)

    def api_goals_precheck(self, body, ps):
        need_stage("start")
        p = BY_ID.get(body.get("personId"))
        if not p:
            raise ValueError("not found")
        if not (ps.role == "emp" and ps.person["id"] == p["id"]) and ps.role != "hr":
            raise PermissionError
        rows = body.get("goals") or [{"id": g["id"], "text": g["text"]} for g in p["goals"]]
        out = []
        for r in rows:
            g = next((x for x in p["goals"] if x["id"] == r.get("id")), None)
            if not g:
                continue
            if r.get("text"):
                g["text"] = r["text"]
            try:
                res = precheck_goal(p["role"], g["text"])
            except Exception as e:
                raise ValueError("预检服务暂时不可用：" + str(e)[:60])
            g["precheck"] = res
            out.append({"goalId": g["id"], "text": g["text"], **res})
        audit(ps.label, "AI 预检目标", p["name"], None, f'{len(out)} 条')
        return {"results": out}

    def api_goals_submit(self, body, ps):
        need_stage("start")
        p = BY_ID.get(body.get("personId"))
        if not p:
            raise ValueError("not found")
        if not is_self(ps, p):
            raise PermissionError
        if not p["goals"]:
            raise ValueError("还没有目标，请至少填写 1 条再提交")
        total_w = sum(g["weight"] for g in p["goals"])
        if abs(total_w - 100) > 0.5:
            raise ValueError(f"权重合计为 {total_w}%，必须等于 100%")
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        # 已确认的目标锁定，不再参与提交
        active = [g for g in p["goals"] if g.get("status") != "confirmed"]
        if not active:
            raise ValueError("目标已确认锁定，不能再提交；如需调整请走变更申请")
        # 提交时校验口径字段（确定性，不再调模型）
        details = []
        for g in active:
            miss = [m for m in goal_missing_fields(g) if m in ("指标", "目标值", "统计周期", "数据来源")]
            if miss:
                details.append({"goalId": g["id"], "text": g["text"], "verdict": "missing",
                                "missing": miss, "suggestion": ""})
                continue
            chk = g.get("check") or {}
            stale = (chk.get("sig") != fields_sig(g.get("fields") or {})) or \
                    (chk.get("forText") != g["text"])
            if stale:
                fresh = judge_goal(p["role"], g)
                g["check"] = dict(fresh, forText=g["text"])
                chk = g["check"]
            if chk.get("verdict") == "invalid":
                details.append({"goalId": g["id"], "text": g["text"], "verdict": "logic",
                                "missing": [chk.get("reason") or "这个目标年底无法验证"],
                                "suggestion": chk.get("rewrite") or ""})
        if details:
            n_block = len([d for d in details if d["verdict"] == "block"])
            msg = "有 %d 条目标缺少口径字段（指标/目标值/统计周期/数据来源），请补齐后再提交" % len(details)

            raise Blocked({"error": msg, "details": details})
        vague = [g["text"] for g in p["goals"] if not g.get("qualitative")
                 and not re.search(r"[0-9]|[一二三四五六七八九十]\s*(个|项|次|天|周|成|倍)", g["text"])]
        if vague:
            raise ValueError("这些目标缺少可验证的数字口径，请补充后再提交："
                             + "；".join(v[:20] for v in vague[:2])
                             + "（例：把 P99 从 820ms 降到 300ms 以内 / 覆盖率 ≥80%）")
        for g in p["goals"]:
            if g["status"] in ("submitted", "approved", "confirmed"):
                continue
            g["status"] = "submitted"
            g["submittedAt"] = now
            g["reviewNote"] = ""
            g["reviewedBy"] = ""
            g["reviewedAt"] = ""
        audit(ps.label, "提交目标审批", p["name"], None, f"{len(p['goals'])} 条")
        return {"ok": True, "count": len(p["goals"])}

    def api_goals_qualitative(self, body, ps):
        need_stage("start")
        p, g = goal_of(body.get("personId"), body.get("goalId"))
        if not g:
            raise ValueError("goal not found")
        if not ps.can("approve_change", p):
            raise PermissionError
        reason = (body.get("reason") or "").strip()
        if len(reason) < 6:
            raise ValueError("标记为定性目标必须写清理由")
        cap = float(STATE["config"].get("qualitative_cap", 0.2))
        total_q = sum(x["weight"] for x in p["goals"] if x.get("qualitative")) + g["weight"]
        if total_q > round(100 * cap):
            raise ValueError(f"定性目标权重合计会到 {total_q}%，超过上限 {round(100 * cap)}%")
        g["qualitative"] = True
        g["qualReason"] = reason
        g["status"] = "approved"
        audit(ps.label, "标记定性目标", f'{p["name"]}·{g["text"][:12]}', None, reason[:40])
        return {"ok": True, "qualitative": True, "capUsed": total_q}

    def api_goals_reopen(self, body, ps):
        need_stage("start")
        p, g = goal_of(body.get("personId"), body.get("goalId"))
        if not g:
            raise ValueError("goal not found")
        if not ps.can("approve_change", p):
            raise PermissionError
        reason = (body.get("reason") or "").strip()
        if len(reason) < 4:
            raise ValueError("退回必须写理由")
        g["status"] = "rejected"
        g["reviewNote"] = reason
        g["reviewedBy"] = ps.who or ps.label
        g["reviewedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
        g["anchor"] = None
        audit(ps.label, "退回已批准目标", f'{p["name"]}·{g["text"][:12]}', "approved", "rejected", reason[:40])
        return {"ok": True, "status": "rejected"}

    def api_goals_withdraw(self, body, ps):
        need_stage("start")
        p = BY_ID.get(body.get("personId"))
        if not p:
            raise ValueError("not found")
        if not is_self(ps, p):
            raise PermissionError
        n = 0
        for g in p["goals"]:
            if g["status"] == "submitted":
                g["status"] = "draft"
                g["submittedAt"] = ""
                n += 1
        if not n:
            raise ValueError("没有可撤回的提交")
        audit(ps.label, "撤回提交", p["name"], "submitted", "draft", "")
        return {"ok": True, "count": n}

    def api_goals_review(self, body, ps):
        need_stage("start")
        p = BY_ID.get(body.get("personId"))
        if not p:
            raise ValueError("not found")
        if not (ps.can("approve_change", p) or
                (p.get("isManager") and ps.role == "skip" and p["manager"] == ps.who)):
            raise PermissionError
        ok = bool(body.get("ok"))
        note = (body.get("reason") or "")[:120]
        if ok:
            bad = []
            for g in p["goals"]:
                if g["status"] != "submitted" or g.get("qualitative"):
                    continue
                miss = [m for m in goal_missing_fields(g) if m in ("指标", "目标值", "统计周期", "数据来源")]
                if miss:
                    bad.append({"goalId": g["id"], "text": g["text"], "verdict": "missing",
                                "missing": miss, "suggestion": ""})
                    continue
                chk = g.get("check") or {}
                if (chk.get("sig") != fields_sig(g.get("fields") or {})) or (chk.get("forText") != g["text"]):
                    g["check"] = dict(judge_goal(p["role"], g), forText=g["text"])
                    chk = g["check"]
                if chk.get("verdict") == "invalid":
                    bad.append({"goalId": g["id"], "text": g["text"], "verdict": "logic",
                                "missing": [chk.get("reason") or "这个目标年底无法验证"],
                                "suggestion": chk.get("rewrite") or ""})
            if bad:
                raise Blocked({"error": "这些目标缺少口径字段，不能批准；请退回让员工补齐",
                               "details": bad})
        n = 0
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        for g in p["goals"]:
            if g["status"] != "submitted":
                continue
            g["status"] = "approved" if ok else "rejected"
            g["reviewNote"] = note
            g["reviewedBy"] = ps.who or ps.label
            g["reviewedAt"] = now
            n += 1
        if not n:
            raise ValueError("没有待审批的目标")
        audit(ps.label, "审批目标", p["name"], "submitted", "approved" if ok else "rejected", note)
        return {"ok": True, "count": n, "status": "approved" if ok else "rejected"}

    def _metric_ctx(self, body, ps):
        p, g = goal_of(body.get("personId"), body.get("goalId"))
        if not g:
            raise ValueError("goal not found")
        m = next((x for x in (g.get("metrics") or []) if x["id"] == body.get("metricId")), None)
        if not m:
            raise ValueError("metric not found")
        return p, g, m

    def api_metric_report(self, body, ps):
        need_stage("end")
        p, g, m = self._metric_ctx(body, ps)
        if not (ps.role == "emp" and ps.person["id"] == p["id"]) and ps.role != "hr":
            raise PermissionError
        try:
            val = float(body.get("value"))
        except (TypeError, ValueError):
            raise ValueError("实际值必须是数字")
        ev = (body.get("evidence") or "").strip()
        if len(ev) < 4:
            raise ValueError("必须附佐证（链接或说明）")
        k = actual_key(p["id"], g["id"], m["id"])
        old = STATE["actuals"].get(k, {})
        STATE["actuals"][k] = {
            "value": val, "reported": val, "evidence": ev,
            "reportedBy": p["name"], "reportedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "pending", "changedFrom": old.get("changedFrom"),
            "reason": "", "verifiedBy": None, "verifiedAt": None,
        }
        save_state()
        audit(ps.label, "上报指标", f'{p["name"]}·{m["name"]}', old.get("value"), val, ev[:40])
        return {"ok": True, "status": "pending", "value": val}

    def api_metric_verify(self, body, ps):
        need_stage("end")
        p, g, m = self._metric_ctx(body, ps)
        if not ps.can("score_direct", p) and ps.role != "hr":
            raise PermissionError
        k = actual_key(p["id"], g["id"], m["id"])
        cur = STATE["actuals"].get(k)
        if not cur:
            raise ValueError("员工还没有上报该指标")
        if cur.get("status") == "verified" and not body.get("force"):
            raise ValueError("该指标已核验，如需修改请用改值")
        ok = bool(body.get("ok"))
        if ok:
            cur["status"] = "verified"
            cur["verifiedBy"] = p["manager"]
            cur["verifiedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
            audit(ps.label, "核验指标", f'{p["name"]}·{m["name"]}', cur.get("reported"), cur["value"], "通过")
        else:
            try:
                nv = float(body.get("value"))
            except (TypeError, ValueError):
                raise ValueError("改值必须填数字")
            reason = (body.get("reason") or "").strip()
            if len(reason) < 4:
                raise ValueError("改值必须写理由")
            cur["changedFrom"] = cur.get("reported")
            cur["value"] = nv
            cur["status"] = "verified"
            cur["reason"] = reason
            cur["verifiedBy"] = p["manager"]
            cur["verifiedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
            audit(ps.label, "改值核验", f'{p["name"]}·{m["name"]}', cur.get("reported"), nv, reason[:60])
        save_state()
        total, _ = person_total(p)
        return {"ok": True, "status": "verified", "value": cur["value"], "total": total}

    def api_selfsummary(self, body, ps):
        need_stage("end")
        p = BY_ID.get(body.get("personId"))
        if not p:
            raise ValueError("not found")
        if not (ps.role == "hr" or (ps.role == "emp" and ps.person["id"] == p["id"])):
            raise PermissionError
        if body.get("text"):
            txt = body["text"].strip()
            if len(txt) < 20:
                raise ValueError("自评太短")
            cur = STATE["selfsummaries"].get(p["id"], {})
            cur.update({"text": txt, "edited": True,
                        "min_words": STATE["config"].get("summary_min_words", 0),
                        "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
            cur.setdefault("sections", [])
            cur.setdefault("mapping", {})
            cur.setdefault("model", "manual")
            STATE["selfsummaries"][p["id"]] = cur
            audit(ps.label, "保存年度自评", p["id"], None, str(len(txt)) + " 字")
            return {"ok": True, "words": len(txt), "item": cur}
        res = self_summary_for(p["id"])
        if not res:
            raise ValueError("没有评级数据")
        res["min_words"] = STATE["config"].get("summary_min_words", 0)
        STATE["selfsummaries"][p["id"]] = res
        audit(ps.label, "生成年度自评", p["id"], None, str(len(res["text"])) + " 字")
        save_state()
        return res

    def api_confirm(self, body, ps):
        need_stage("start")
        p, g = goal_of(body.get("personId"), body.get("goalId"))
        if not g or not g.get("anchor"):
            raise ValueError("no anchor to confirm")
        if g.get("status") not in ("approved", "confirmed"):
            raise ValueError("目标还没被批准，不能确认锚点")
        if not ps.can("confirm_anchor", p):
            raise PermissionError
        before = g["status"]
        g["status"] = "confirmed"
        audit(ps.label, "确认锚点", f'{p["name"]}·{g["text"][:12]}', before, "confirmed",
              body.get("reason", ""))
        return {"ok": True, "status": "confirmed"}

    def api_anchors_async(self, body, ps):
        need_stage("start")
        p, g = goal_of(body.get("personId"), body.get("goalId"))
        if not p or not g:
            raise ValueError("goal not found")
        if not ps.can("confirm_anchor", p) and ps.role != "hr":
            raise PermissionError
        if g.get("status") not in ("approved", "confirmed"):
            raise ValueError("目标还没被批准，不能生成锚点")
        jid = start_anchor_job(p, g, bool(body.get("force")))
        audit(ps.label, "启动锚点生成", f'{p["name"]}·{g["text"][:12]}', None, jid, "")
        return {"ok": True, "jobId": jid, "status": "running"}

    def api_confirm_batch(self, body, ps):
        need_stage("start")
        p = BY_ID.get(body.get("personId"))
        if not p:
            raise ValueError("not found")
        if not ps.can("confirm_anchor", p):
            raise PermissionError
        n = 0
        for g in p["goals"]:
            if g.get("anchor") and g["status"] != "confirmed":
                g["status"] = "confirmed"
                n += 1
        if n:
            audit(ps.label, "批量确认锚点", p["name"], None, f"{n} 项")
        return {"ok": True, "confirmed": n}

    def api_score(self, body, ps):
        need_stage("end")
        p, g = goal_of(body.get("personId"), body.get("goalId"))
        if not g:
            raise ValueError("goal not found")
        if p.get("isManager") and ps.can("score_skip", p):
            rater = "隔级领导"          # 经理由隔级领导直接评，不受双评模式限制
        elif ps.can("score_direct", p):
            rater = "直属领导"
        elif ps.can("score_skip", p):
            if STATE["config"]["mode"] == "single":
                raise ValueError("当前是单评模式，隔级领导不参与打分")
            rater = "隔级领导"
        else:
            raise PermissionError
        try:
            val = float(body.get("value"))
        except (TypeError, ValueError):
            raise ValueError("分数必须是数字")
        if not (0 <= val <= 100):
            raise ValueError("分数需在 0–100")
        k = f'{p["id"]}|{g["id"]}|{rater}'
        before = STATE["scores"].get(k)
        STATE["scores"][k] = round(val, 1)
        save_state()
        audit(f'{ps.who or ps.label}·{rater}', "打分", f'{p["name"]}·{g["text"][:12]}',
              before, round(val, 1), body.get("reason", ""))
        compute_all()
        total, _ = person_total(p)
        return {"ok": True, "total": total}

    def progress(self, ps):
        scope = ps.scope()
        by_status = {"draft": 0, "submitted": 0, "approved": 0, "confirmed": 0, "rejected": 0}
        depts = {}
        for p in scope:
            if p.get("isManager"):
                continue          # 部门负责人单独考核，不计入员工填报进度
            d = depts.setdefault(p["dept"], {"dept": p["dept"], "n": 0, "draft": 0,
                                             "submitted": 0, "approved": 0, "confirmed": 0})
            d["n"] += 1
            sts = [g["status"] for g in p["goals"]]
            if not sts:
                d["draft"] += 1          # 没有目标 = 未提交，不能算完成
            elif all(s == "confirmed" for s in sts):
                d["confirmed"] += 1
            elif any(s == "submitted" for s in sts):
                d["submitted"] += 1
            elif any(s == "approved" for s in sts):
                d["approved"] += 1
            else:
                d["draft"] += 1
            for st in sts:
                by_status[st] = by_status.get(st, 0) + 1
        pending_changes = len([c for c in STATE["changes"] if c["status"] == "待审批"])
        emp_total = sum(d["n"] for d in depts.values())
        mgr_count = len([x for x in scope if x.get("isManager")])
        return {"stage": stage(), "total": emp_total, "managers": mgr_count,
                "byStatus": by_status,
                "depts": sorted(depts.values(), key=lambda x: -x["n"]),
                "pendingChanges": pending_changes,
                "pendingAppeals": len([a for a in STATE["appeals"] if a["status"] == "待处理"])}

    def overview(self, ps):
        if not STATE["ratings"]:
            compute_all()
        scope = ps.scope()
        rows = []
        for p in scope:
            if p.get("isManager"):
                continue          # 经理不占员工名额，单独排名
            r = rec_of(p["id"])
            if not r:
                continue
            rows.append({"id": p["id"], "name": p["name"], "dept": p["dept"],
                         "score": r["score"], "rating": r["rating"], "status": r["status"],
                         "rank": r["rank"], "quota": r["quota"], "manager": p["manager"]})
        depts = {}
        for r in rows:
            d = depts.setdefault(r["dept"], {"dept": r["dept"], "n": 0, "A": 0, "B": 0, "C": 0,
                                             "cutline": 0, "quota": 0, "scores": []})
            d["n"] += 1
            d[r["rating"]] = d.get(r["rating"], 0) + 1
            if r["status"] == "cutline":
                d["cutline"] += 1
            d["quota"] = r["quota"]
            d["scores"].append(r["score"])
        for d in depts.values():
            sc = sorted(d.pop("scores"))
            d["min"], d["max"] = sc[0], sc[-1]
            d["over_quota"] = max(0, d["A"] + d["cutline"] - d["quota"])
        visible = scores_visible(ps)
        cutline = sorted([r for r in rows if r["status"] == "cutline"],
                         key=lambda x: -x["score"]) if visible else []
        hist = {"A": 0, "B": 0, "C": 0}
        if visible:
            for r in rows:
                hist[r["rating"]] = hist.get(r["rating"], 0) + 1
        else:
            for d in depts.values():
                d.update({"A": 0, "B": 0, "C": 0, "cutline": 0, "quota": 0,
                          "min": None, "max": None, "over_quota": 0})
        mgr_stats = []
        mgr_progress = []
        mgr_self = {x["name"]: x for x in PEOPLE if x.get("isManager")}
        groups = {}
        for p_ in scope:
            if p_.get("isManager"):
                continue
            groups.setdefault(p_["manager"], []).append(p_)
        for m, plist in groups.items():
            me = mgr_self.get(m)
            plist2 = list(plist) + ([me] if me else [])
            done = len([x for x in plist2 if x.get("goals") and all(
                g["status"] == "confirmed" for g in x["goals"])])
            sub = len([x for x in plist2 if any(g["status"] == "submitted" for g in x["goals"])])
            appr = len([x for x in plist2 if not (x.get("goals") and all(
                g["status"] == "confirmed" for g in x["goals"]))
                and any(g["status"] == "approved" for g in x["goals"])])
            mgr_progress.append({"manager": m, "director": MGR_DIRECTOR.get(m, "-"),
                                 "n": len(plist2), "done": done, "submitted": sub,
                                 "approved": appr, "hasSelf": bool(me),
                                 "draft": len(plist2) - done - sub - appr})
        mgr_progress.sort(key=lambda x: -x["n"])
        if visible and ps.role in ("hr", "skip"):
            groups = {}
            for p in scope:
                groups.setdefault(p["manager"], []).append(p)
            for m, plist in groups.items():
                plist = [x for x in plist if x["id"] in STATE["ratings"]]
                if not plist:
                    continue
                totals = [person_total(x)[0] for x in plist]
                mgr_stats.append({"manager": m, "director": MGR_DIRECTOR.get(m, "-"),
                                  "n": len(plist),
                                  "avg": round(sum(totals) / len(totals), 1),
                                  "max": max(totals), "min": min(totals),
                                  "spread": round(max(totals) - min(totals), 1),
                                  "a": sum(1 for x in plist if (rec_of(x["id"]) or {}).get("rating") == "A"),
                                  "quota": (rec_of(plist[0]["id"]) or {}).get("quota", 0)})
            mgr_stats.sort(key=lambda x: -x["avg"])
        return {"config": STATE["config"], "scoresVisible": visible,
                "persona": {"role": ps.role, "label": ps.label}, "mgrProgress": mgr_progress,
                "depts": sorted(depts.values(), key=lambda x: -x["n"]), "cutline": cutline[:20],
                "hist": hist, "total": len(rows), "mgr_stats": mgr_stats,
                "pending_appeals": len([a for a in STATE["appeals"] if a["status"] == "待处理"]),
                "pending_changes": len([c for c in STATE["changes"] if c["status"] == "待审批"])}

    def export_md(self, pid):
        p = BY_ID.get(pid)
        rec = rec_of(pid)
        if not p or not rec:
            return None
        reason = STATE["reasons"].get(pid)
        L = [f"# {p['name']} · {p['dept']} · {p['role']}", "",
             f"- 综合得分：**{rec['score']}**",
             f"- 评级：**{rec['rating']}**（部门排名 {rec['rank']}/{rec['deptCount']}，A 名额 {rec['quota']}）",
             f"- 直属领导：{p['manager']}　隔级领导：{p['director']}", "",
             "## 目标与得分", "", "| 目标 | 权重 | 得分 | 版本 | 状态 |", "|---|---|---|---|---|"]
        for g, d in zip(p["goals"], rec["detail"]):
            L.append(f"| {d['text']} | {d['weight']}% | {d['score']} | {g.get('version','v1')} | "
                     f"{'已锁' if g['status']=='confirmed' else '未锁定'} |")
        L += ["", "## 评级理由", ""]
        if reason:
            for s in reason["sentences"]:
                L.append(f"- {s['text']} `{','.join(s['cites'])}`")
            L += ["", f"（理由由 {reason['model']} 生成，引用编号见证据表）"]
        else:
            L.append("（尚未生成）")
        L += ["", "## 审计摘要", ""]
        for a in [x for x in STATE["audit"] if x["target"] == pid][:10]:
            L.append(f"- {a['ts']} {a['actor']} {a['action']} {a['before']} → {a['after']}")
        L += ["", "---", "本简报由系统聚合生成，结论附原文出处；最终评价由直属领导与 HR 作出。"]
        return "\n".join(L)

def main():
    # 端口优先级：命令行 --port > 环境变量 PORT > 8899
    port = 8899
    if os.environ.get("PORT"):
        try:
            port = int(os.environ["PORT"])
        except ValueError:
            pass
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    host = os.environ.get("HOST", "0.0.0.0")
    if "--host" in sys.argv:
        host = sys.argv[sys.argv.index("--host") + 1]
    restore_goals()
    try:
        sanitize_all_goals()
    except Exception:
        pass
    ensure_scores()
    ensure_actuals()
    # 云端首次启动：自动铺演示数据，避免评委看到空系统
    if os.environ.get("SEED_DEMO") in ("1", "true", "yes"):
        try:
            if not any(p_["goals"] for p_ in PEOPLE):
                load_demo()
                print("[seed] 已载入演示数据")
        except Exception as e:
            print("[seed] 跳过:", e)
    compute_all()
    if not API_KEY:
        print("[warn] 未找到 GENESIS_API_KEY，将使用模板兜底")
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"[ready] http://{host}:{port}  model={MODEL} cache={len(CACHE)}")
    srv.serve_forever()

if __name__ == "__main__":
    main()

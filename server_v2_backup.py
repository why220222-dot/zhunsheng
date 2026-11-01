# -*- coding: utf-8 -*-
"""
准绳 · 绩效评审智能体 —— MVP 后端（零依赖，仅标准库）
启动： python server.py [--port 8899]
"""
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent
STATIC = BASE / "static"
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)
CACHE_FILE = DATA / "anchors_cache.json"
STATE_FILE = DATA / "state.json"

# ---------------- .env ----------------
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
MODEL_FALLBACK = ["qwen3.8-flash", "glm-5.3", "deepseek-v4.1-flash", "qwen3.8-max"]

# ---------------- 演示数据 ----------------
SURNAME = list("王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗")
GIVEN = list("伟芳娜秀英敏静丽强磊洋艳勇军杰娟涛明超霞平刚桂")
ROLES = [
    ("后端开发", "研发二部", ["提高代码质量，减少线上问题", "按期交付需求，减少返工", "参与系统稳定性建设"]),
    ("前端开发", "研发二部", ["提升页面性能与稳定性", "推动组件库复用，减少重复开发", "与设计、后端高效协作"]),
    ("测试工程师", "质量部", ["提升自动化测试覆盖率", "缩短版本回归周期", "线上问题复盘闭环率"]),
    ("产品经理", "产品部", ["需求文档质量与验收通过率", "推动关键需求上线并量化效果", "跨部门协同与信息同步"]),
    ("销售", "销售部", ["完成年度回款目标", "新客户开拓数量", "老客户续约率"]),
    ("客服", "客户成功部", ["客户满意度与响应时效", "知识库沉淀与自助解决率", "投诉闭环率"]),
]
MANAGERS = ["李明", "王芳", "张磊", "刘洋", "陈静", "赵强", "孙丽", "周军", "吴敏", "郑涛"]
ROLE_EVENTS = {
    "后端开发": ["Q2 主导订单服务拆分，接口 P99 从 820ms 降到 210ms", "全年处理 P1 故障 2 起，均在 30 分钟内恢复"],
    "前端开发": ["Q3 完成首屏优化，LCP 从 3.4s 降到 1.9s", "推动组件库复用，重复开发工时减少约 120 小时"],
    "测试工程师": ["搭建自动化回归流水线，回归周期从 3 天缩短到 6 小时", "全年拦截线上缺陷 37 个，其中高危 6 个"],
    "产品经理": ["主导会员体系改版上线，次月留存提升 4.2 个百分点", "完成 3 个季度需求验收，验收通过率 96%"],
    "销售": ["新签客户 23 家，年度回款完成率 108%", "老客户续约率 91%，同比提升 6 个百分点"],
    "客服": ["推动知识库自助解决率从 41% 提升到 63%", "全年投诉闭环率 98%，平均首响 28 秒"],
}
# 每个领导的宽严系数：决定分数通胀的形态（演示用）
BIAS = {m: round(random.Random(i * 7).uniform(-4.5, 5.5), 1) for i, m in enumerate(MANAGERS)}

def gen_people(n=200, seed=42):
    rnd = random.Random(seed)
    people = []
    for i in range(n):
        role, dept, goals = ROLES[i % len(ROLES)]
        name = rnd.choice(SURNAME) + rnd.choice(GIVEN)
        manager = MANAGERS[(i // 10) % len(MANAGERS)]
        gs = [{
            "id": f"g{i}_{j}",
            "text": g,
            "weight": [40, 35, 25][j],
            "anchor": None,
            "status": "draft",
        } for j, g in enumerate(goals)]
        ev_pool = ROLE_EVENTS.get(role, [])
        events = [{"id": f"e{i}_{k}", "text": t, "date": "2026"} for k, t in enumerate(ev_pool)]
        people.append({
            "id": f"p{i}", "name": name, "role": role, "dept": dept,
            "manager": manager, "goals": gs, "events": events,
        })
    return people

PEOPLE = gen_people()
BY_ID = {p["id"]: p for p in PEOPLE}

DEPT_NAMES = sorted({p["dept"] for p in PEOPLE})

# ---------------- 状态 ----------------
DEFAULT_CONFIG = {
    "mode": "single",           # single | dual
    "quota_ratio": 0.15,        # A 级名额比例
    "a_threshold": 95.0,        # 评 A 的分数线
    "c_threshold": 80.0,        # 评 C 的分数线
    "rater_weights": {"直属领导": 60, "隔级领导": 40},
}

def fresh_state():
    return {
        "config": dict(DEFAULT_CONFIG),
        "scores": {},        # "p0|g0_0|直属领导" -> 88
        "ratings": {},       # personId -> {...}
        "reasons": {},       # personId -> {...}
        "audit": [],
        "seeded": False,
    }

def load_state():
    if STATE_FILE.exists():
        try:
            s = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            s.setdefault("config", dict(DEFAULT_CONFIG))
            return s
        except Exception:
            pass
    return fresh_state()

STATE = load_state()

def save_state():
    STATE_FILE.write_text(json.dumps(STATE, ensure_ascii=False, indent=1), encoding="utf-8")

def audit(actor, action, target, before=None, after=None, reason=""):
    STATE["audit"].append({
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "actor": actor, "action": action, "target": target,
        "before": before, "after": after, "reason": reason,
    })
    save_state()

# ---------------- 锚点缓存 ----------------
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

# ---------------- 模型调用 ----------------
def call_model(messages, model, temperature=0.2, max_tokens=None):
    payload = {"model": model, "messages": messages, "temperature": temperature}
    if max_tokens:
        payload["max_tokens"] = max_tokens
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BASE_URL.rstrip("/") + "/chat/completions",
        data=data,
        headers={"Authorization": "Bearer " + API_KEY, "Content-Type": "application/json"},
        method="POST",
    )
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
    """按白名单回退调用，返回 (obj, model, usage)"""
    last = None
    for m in [MODEL] + [x for x in MODEL_FALLBACK if x != MODEL]:
        try:
            resp = call_model(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                m, max_tokens=max_tokens)
            obj = extract_json(resp["choices"][0]["message"]["content"])
            return obj, m, resp.get("usage", {})
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

# ---------------- 锚点：生成 + 自检 + 重写 ----------------
ANCHOR_SYS = (
    "你是绩效目标编译助手。把员工的定性目标编译成可打分的行为锚点。\n"
    "硬性要求：\n"
    "1. 每一档都必须包含可量化口径（数字、比例，或 ≥/≤/不低于/不超过 等比较词）；\n"
    "2. 三档单调：优秀 > 达标 > 待改进，同一指标上阈值必须递进；\n"
    "3. 每档都必须写清「证据要求」，指向具体系统、报表或记录；\n"
    "4. 禁止出现没有依据的评价性形容词（如「积极」「优秀地」）；\n"
    "5. 只输出 JSON，不要 Markdown 代码块，不要解释。"
)

ANCHOR_USER = (
    "岗位：{role}\n目标原文：「{goal}」（权重 {weight}%）\n\n"
    "输出 JSON：{{\"problem\":\"这个目标为什么不可直接评分（一句话，不超过 40 字）\","
    "\"anchors\":[{{\"level\":4,\"name\":\"优秀\",\"desc\":\"...\",\"evidence\":\"...\"}},"
    "{{\"level\":3,\"name\":\"达标\",\"desc\":\"...\",\"evidence\":\"...\"}},"
    "{{\"level\":2,\"name\":\"待改进\",\"desc\":\"...\",\"evidence\":\"...\"}}]}}"
)

ANCHOR_FIX = (
    "上一版不合格，问题如下：\n{issues}\n"
    "请重写，逐条修正以上问题，只输出同样结构的 JSON。"
)

QUANT_RE = re.compile(r"[0-9]|≥|≤|以上|以内|不低于|不超过|持平|上升|下降|低于|高于|超过|达到|完成")

def check_anchor(obj):
    """结构化自检：返回问题列表（空 = 通过）"""
    issues = []
    anchors = obj.get("anchors") or []
    if len(anchors) != 3:
        issues.append("锚点必须正好 3 档")
        return issues
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
    """生成 → 自检 → 不合格带着问题重写，返回 (obj, meta)"""
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
            return obj, {"passed": True, "attempts": attempts,
                         "elapsed": round(time.time() - t0, 1)}
        user = ANCHOR_USER.format(role=role, goal=goal, weight=weight) + "\n\n" + \
            ANCHOR_FIX.format(issues="\n".join(f"- {x}" for x in issues))
    # 三次都不过：返回最后一版，但标注未通过
    obj["_model"] = attempts[-1]["model"] if attempts else "unknown"
    obj["_usage"] = {}
    return obj, {"passed": False, "attempts": attempts,
                 "elapsed": round(time.time() - t0, 1)}

def fallback_anchors():
    return {
        "problem": "该目标缺少可验证的量化口径，年底只能凭印象打分",
        "anchors": [
            {"level": 4, "name": "优秀", "desc": "2 项以上可量化成果，指标超出岗位基线 20% 以上",
             "evidence": "数据报表、项目记录、上下游书面确认"},
            {"level": 3, "name": "达标", "desc": "完成年初约定的主要交付，核心指标达到基线",
             "evidence": "月度/季度数据、验收记录"},
            {"level": 2, "name": "待改进", "desc": "未完成主要交付，或指标低于基线且无客观原因",
             "evidence": "缺陷/延期记录、复盘结论"},
        ],
        "_model": "template-fallback", "_usage": {},
    }

def goal_of(pid, gid):
    p = BY_ID.get(pid)
    if not p:
        return None, None
    return p, next((g for g in p["goals"] if g["id"] == gid), None)

# ---------------- 计算：分数 → 评级 → 名额 ----------------
def raters():
    cfg = STATE["config"]
    if cfg["mode"] == "single":
        return [("直属领导", 100)]
    w = cfg["rater_weights"]
    return [("直属领导", w.get("直属领导", 60)), ("隔级领导", w.get("隔级领导", 40))]

def sim_score(person, goal):
    """演示用：确定性伪随机，含领导宽严系数与少量低绩效个案"""
    rnd = random.Random(hash((person["id"], goal["id"])) & 0xffffffff)
    base = 89 + rnd.random() * 7            # 89–96：模拟普遍的分数通胀
    base += BIAS.get(person["manager"], 0)  # 领导宽严
    n = int(str(person["id"])[1:])
    if n % 37 == 0:                          # 埋几个低绩效，用于「连续 C」演示
        base -= 14
    return max(55, min(100, round(base, 1)))

def ensure_scores():
    """首次运行时生成演示分数（单评模式）"""
    if STATE["seeded"]:
        return
    # 两种模式都预置分数，切换模式时无需重新模拟
    for p in PEOPLE:
        for g in p["goals"]:
            base = sim_score(p, g)
            for name in ("直属领导", "隔级领导"):
                k = f'{p["id"]}|{g["id"]}|{name}'
                if k in STATE["scores"]:
                    continue
                if name == "直属领导":
                    v = base
                else:   # 隔级领导普遍更严：这是双评模式下分数下移的原因
                    v = max(50, min(100, round(base - random.Random(hash(k) & 0xffff).uniform(0.5, 4.5), 1)))
                STATE["scores"][k] = v
    STATE["seeded"] = True
    save_state()

def person_total(p):
    cfg = STATE["config"]
    rw = raters()
    total, detail = 0.0, []
    for g in p["goals"]:
        s, wsum = 0.0, 0
        for name, w in rw:
            v = STATE["scores"].get(f'{p["id"]}|{g["id"]}|{name}')
            if v is None:
                continue
            s += v * w
            wsum += w
        gs = round(s / wsum, 1) if wsum else 0.0
        total += gs * g["weight"] / 100.0
        detail.append({"goalId": g["id"], "text": g["text"], "weight": g["weight"], "score": gs})
    return round(total, 1), detail

def compute_all():
    """重算全部人员的总分、部门排名、名额、评级"""
    cfg = STATE["config"]
    by_dept = {}
    rows = []
    for p in PEOPLE:
        total, detail = person_total(p)
        rows.append({"id": p["id"], "name": p["name"], "dept": p["dept"],
                     "manager": p["manager"], "score": total, "detail": detail})
        by_dept.setdefault(p["dept"], []).append(rows[-1])

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
                    rating = "B"
                    status = "cutline"      # 够线但没名额
            elif r["score"] < cfg["c_threshold"]:
                rating = "C"
            ratings[r["id"]] = {
                "personId": r["id"], "score": r["score"], "rank": rank,
                "deptCount": n, "quota": quota, "rating": rating, "status": status,
                "detail": r["detail"],
            }
    STATE["ratings"] = ratings
    save_state()
    return ratings

# ---------------- 评级理由：生成 + 引用校验 ----------------
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
    "与 A 线的关系：{delta}\n\n"
    "可用证据（编号只能用这些）：\n{facts}\n\n"
    "输出 JSON：{{\"sentences\":[{{\"text\":\"...\",\"cites\":[\"G1\"]}}]}}"
)

def level_of(score):
    if score >= 95:
        return 4
    if score >= 85:
        return 3
    return 2

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
    if delta > 0:
        dtxt = f"低于 A 线（{STATE['config']['a_threshold']}）{delta} 分"
    else:
        dtxt = f"高出 A 线（{STATE['config']['a_threshold']}）{abs(delta)} 分"
    facts.append(f"DELTA｜{dtxt}；因名额限制未能评为 A" if rec.get("status") == "cutline" else f"DELTA｜{dtxt}")
    if rec.get("manager"):
        mapping["MGR"] = {"type": "manager", "name": rec["manager"]}
        facts.append(f"MGR｜直属领导 {rec['manager']}")
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
    user = REASON_USER.format(
        name=p["name"], role=p["role"], dept=p["dept"],
        score=rec["score"], rating=rec["rating"], rank=rec["rank"],
        deptCount=rec["deptCount"], quota=rec["quota"],
        delta=round(STATE["config"]["a_threshold"] - rec["score"], 1),
        facts=facts)
    attempts = []
    for i in range(2):
        try:
            obj, model, usage = ask_json(REASON_SYS, user)
            issues = check_reason(obj, mapping)
            attempts.append({"try": i + 1, "issues": issues, "model": model})
            if not issues:
                return {"sentences": obj["sentences"], "mapping": mapping,
                        "model": model, "passed": True, "attempts": attempts,
                        "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
            user = user + "\n\n上一版问题：\n" + "\n".join(f"- {x}" for x in issues) + "\n请修正后重新输出。"
        except Exception as e:
            attempts.append({"try": i + 1, "error": str(e)[:200]})
    # 兜底：模板理由（仍然带引用）
    sents = [{"text": f"「{d['text']}」达到 {level_of(d['score'])} 分档。",
              "cites": [f"G{i}"]} for i, d in enumerate(rec["detail"], 1)]
    sents.append({"text": f"部门内排名 {rec['rank']}/{rec['deptCount']}，A 级名额 {rec['quota']} 人。",
                  "cites": ["RANK", "QUOTA"]})
    return {"sentences": sents, "mapping": mapping, "model": "template-fallback",
            "passed": False, "attempts": attempts,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S")}

# ---------------- HTTP ----------------
class Handler(BaseHTTPRequestHandler):
    server_version = "Yardstick/0.2"

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

    # ---------- GET ----------
    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path == "/api/health":
                return self._send(200, {
                    "ok": True, "model": MODEL, "hasKey": bool(API_KEY),
                    "cached": len(CACHE), "seeded": STATE["seeded"],
                    "config": STATE["config"], "audit": len(STATE["audit"]),
                })
            if path == "/api/people":
                return self._send(200, [self.summary(p) for p in PEOPLE])
            if path.startswith("/api/person/"):
                pid = path.rsplit("/", 1)[-1]
                p = BY_ID.get(pid)
                if not p:
                    return self._send(404, {"error": "not found"})
                total, detail = person_total(p)
                return self._send(200, dict(p, total=total, detail=detail,
                                           rating=STATE["ratings"].get(pid),
                                           reason=STATE["reasons"].get(pid),
                                           scores={g["id"]: {n: STATE["scores"].get(f"{pid}|{g['id']}|{n}") for n, _ in raters()} for g in p["goals"]}))
            if path == "/api/overview":
                return self._send(200, self.overview())
            if path == "/api/audit":
                return self._send(200, STATE["audit"][-100:][::-1])
            if path.startswith("/api/export/"):
                pid = path.rsplit("/", 1)[-1]
                md = self.export_md(pid)
                if md is None:
                    return self._send(404, {"error": "not found"})
                return self._send(200, md, "text/markdown; charset=utf-8")
        except Exception as e:
            return self._send(500, {"error": str(e)})
        return self._static(path)

    def _static(self, path):
        if path == "/":
            path = "/index.html"
        f = (STATIC / path.lstrip("/")).resolve()
        if not str(f).startswith(str(STATIC.resolve())) or not f.is_file():
            return self._send(404, {"error": "no such file"})
        ctype = {".html": "text/html; charset=utf-8",
                 ".js": "application/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8",
                 ".svg": "image/svg+xml"}.get(f.suffix, "application/octet-stream")
        return self._send(200, f.read_bytes(), ctype)

    # ---------- POST ----------
    def do_POST(self):
        global STATE
        path = self.path.split("?")[0]
        try:
            body = self._body()
        except Exception as e:
            return self._send(400, {"error": "bad json: %s" % e})
        try:
            if path == "/api/anchors":
                return self._send(200, self.api_anchors(body))
            if path == "/api/anchors/batch":
                return self._send(200, self.api_anchors_batch(body))
            if path == "/api/confirm":
                return self._send(200, self.api_confirm(body))
            if path == "/api/score":
                return self._send(200, self.api_score(body))
            if path == "/api/compute":
                r = compute_all()
                audit("HR", "重算评级", "全部人员", None, f"{len(r)} 人", body.get("reason", ""))
                return self._send(200, {"ok": True, "count": len(r)})
            if path == "/api/reason":
                pid = body.get("personId")
                if not STATE["ratings"]:
                    compute_all()
                res = reason_for(pid)
                if not res:
                    return self._send(404, {"error": "no rating for " + str(pid)})
                STATE["reasons"][pid] = res
                audit("系统", "生成评级理由", pid, None, res["model"], "")
                save_state()
                return self._send(200, res)
            if path == "/api/appeal":
                pid = body.get("personId")
                txt = (body.get("text") or "").strip()
                if not txt:
                    return self._send(400, {"error": "申诉内容不能为空"})
                audit("员工", "提交申诉", pid, None, txt[:200])
                return self._send(200, {"ok": True})
            if path == "/api/config":
                cfg = STATE["config"]
                before = dict(cfg)
                for k in ("mode", "quota_ratio", "a_threshold", "c_threshold"):
                    if k in body:
                        cfg[k] = body[k]
                changed = {k: (before.get(k), cfg[k]) for k in cfg if before.get(k) != cfg.get(k)}
                b = "; ".join(f"{k}={v[0]}" for k, v in changed.items()) or "-"
                a = "; ".join(f"{k}={v[1]}" for k, v in changed.items()) or "-"
                audit("HR", "修改配置", "规则配置", b, a, body.get("reason", ""))
                compute_all()
                return self._send(200, {"ok": True, "config": cfg})
            if path == "/api/simulate":
                STATE["seeded"] = False
                STATE["scores"] = {}
                ensure_scores()
                compute_all()
                audit("HR", "生成演示分数", "全部人员", None, "重新模拟", "")
                return self._send(200, {"ok": True})
            if path == "/api/reset":
                STATE = fresh_state()
                CACHE.clear()
                save_cache()
                save_state()
                compute_all()
                return self._send(200, {"ok": True})
        except urllib.error.HTTPError as e:
            return self._send(502, {"error": e.read().decode("utf-8", "ignore")[:300]})
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})
        return self._send(404, {"error": "no route"})

    # ---------- handlers ----------
    def api_anchors(self, body):
        p, g = goal_of(body.get("personId"), body.get("goalId"))
        if not g:
            return {"error": "goal not found"}
        key = f'{p["role"]}|{g["text"]}'
        if key in CACHE and not body.get("force"):
            res, meta = CACHE[key], {"passed": True, "attempts": [{"try": 0, "issues": [], "model": "cache"}], "elapsed": 0}
            src = "cache"
        else:
            try:
                res, meta = gen_anchor_checked(p["role"], g["text"], g["weight"])
                CACHE[key] = res
                save_cache()
                src = "model"
            except Exception as e:
                res, meta, src = fallback_anchors(), {"passed": False, "attempts": [{"error": str(e)[:200]}], "elapsed": 0}, "fallback"
        g["anchor"] = res
        g["selfcheck"] = meta
        return {"result": res, "source": src, "selfcheck": meta}

    def api_anchors_batch(self, body):
        p = BY_ID.get(body.get("personId"))
        if not p:
            return {"error": "not found"}
        todo = [g for g in p["goals"] if not g.get("anchor") or body.get("force")]
        if not todo:
            return {"results": [], "note": "all cached"}
        with ThreadPoolExecutor(max_workers=3) as ex:
            futures = {ex.submit(self.api_anchors, {"personId": p["id"], "goalId": g["id"]}): g for g in todo}
            out = []
            for f in futures:
                r = f.result()
                out.append({"goalId": futures[f]["id"], "ok": "result" in r, "source": r.get("source"),
                            "passed": r.get("selfcheck", {}).get("passed")})
        return {"results": out}

    def api_confirm(self, body):
        p, g = goal_of(body.get("personId"), body.get("goalId"))
        if not g or not g.get("anchor"):
            return {"error": "no anchor to confirm"}
        before = g["status"]
        g["status"] = "confirmed"
        audit("直属领导", "确认锚点", g["id"], before, "confirmed", body.get("reason", ""))
        return {"ok": True, "status": "confirmed"}

    def api_score(self, body):
        p, g = goal_of(body.get("personId"), body.get("goalId"))
        if not g:
            return {"error": "goal not found"}
        rater = body.get("rater") or raters()[0][0]
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
        audit(rater, "打分", f'{p["name"]}·{g["text"][:12]}', before, round(val, 1), body.get("reason", ""))
        total, _ = person_total(p)
        return {"ok": True, "total": total}

    # ---------- 视图数据 ----------
    def summary(self, p):
        total, _ = person_total(p)
        rec = STATE["ratings"].get(p["id"], {})
        return {"id": p["id"], "name": p["name"], "role": p["role"], "dept": p["dept"],
                "manager": p["manager"], "goals": len(p["goals"]),
                "confirmed": sum(1 for g in p["goals"] if g["status"] == "confirmed"),
                "total": total, "rating": rec.get("rating", "-"), "status": rec.get("status", "")}

    def overview(self):
        if not STATE["ratings"]:
            compute_all()
        rows = []
        for p in PEOPLE:
            r = STATE["ratings"].get(p["id"])
            if not r:
                continue
            rows.append({"id": p["id"], "name": p["name"], "dept": p["dept"],
                         "score": r["score"], "rating": r["rating"],
                         "status": r["status"], "rank": r["rank"], "quota": r["quota"]})
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
        cutline = sorted([r for r in rows if r["status"] == "cutline"], key=lambda x: -x["score"])
        hist = {"A": 0, "B": 0, "C": 0}
        for r in rows:
            hist[r["rating"]] = hist.get(r["rating"], 0) + 1
        return {"config": STATE["config"], "depts": sorted(depts.values(), key=lambda x: -x["n"]),
                "cutline": cutline[:20], "hist": hist, "total": len(rows)}

    def export_md(self, pid):
        p = BY_ID.get(pid)
        rec = STATE["ratings"].get(pid)
        if not p or not rec:
            return None
        reason = STATE["reasons"].get(pid)
        L = [f"# {p['name']} · {p['dept']} · {p['role']}", "",
             f"- 综合得分：**{rec['score']}**",
             f"- 评级：**{rec['rating']}**（部门排名 {rec['rank']}/{rec['deptCount']}，A 名额 {rec['quota']}）",
             f"- 直属领导：{p['manager']}", "", "## 目标与得分", "",
             "| 目标 | 权重 | 得分 | 锚点 |", "|---|---|---|---|"]
        for g, d in zip(p["goals"], rec["detail"]):
            lvl = (g.get("anchor") or {}).get("anchors") or []
            L.append(f"| {d['text']} | {d['weight']}% | {d['score']} | {'已锁 v1' if g['status']=='confirmed' else '未锁定'} |")
        L += ["", "## 评级理由", ""]
        if reason:
            for s in reason["sentences"]:
                L.append(f"- {s['text']} `{','.join(s['cites'])}`")
            L.append("")
            L.append(f"（理由由 {reason['model']} 生成，引用编号见证据表）")
        else:
            L.append("（尚未生成）")
        L += ["", "## 审计摘要", ""]
        for a in [x for x in STATE["audit"] if x["target"] == pid or pid in str(x["target"])][:10]:
            L.append(f"- {a['ts']} {a['actor']} {a['action']} {a['before']} → {a['after']}")
        L += ["", "---", "本简报由系统聚合生成，结论附原文出处；最终评价由直属领导与 HR 作出。"]
        return "\n".join(L)


def main():
    port = 8899
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    ensure_scores()
    compute_all()
    if not API_KEY:
        print("[warn] 未找到 GENESIS_API_KEY，将使用模板兜底")
    host = os.environ.get("HOST", "127.0.0.1")
    if "--host" in sys.argv:
        host = sys.argv[sys.argv.index("--host") + 1]
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"[ready] http://{host}:{port}  model={MODEL} cache={len(CACHE)}")
    srv.serve_forever()


if __name__ == "__main__":
    main()

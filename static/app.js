/* 准绳 · 绩效评审智能体（四身份 × 三阶段） */
const $ = (s, r = document) => r.querySelector(s);
const el = (tag, cls, txt) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (txt != null) e.textContent = txt;
  return e;
};
const fmt = n => (n == null ? "-" : (Math.round(n * 10) / 10).toFixed(1));
const S = { persona: localStorage.getItem("ys_persona") || "hr", health: null, cur: null };

/* 进行中的锚点生成任务：goalId -> jobId（存 sessionStorage，刷新后续上） */
const JOBS = JSON.parse(sessionStorage.getItem("ys_jobs") || "{}");
function saveJobs() { sessionStorage.setItem("ys_jobs", JSON.stringify(JOBS)); }

async function pollJob(jobId, onDone) {
  for (let i = 0; i < 240; i++) {
    await new Promise(r => setTimeout(r, 1500));
    try {
      const j = await api("/api/job/" + jobId);
      if (j.status === "done") return onDone(j);
      if (j.status === "error") return onDone(j);
    } catch (e) { /* 网络抖动继续轮询 */ }
  }
  return onDone({ status: "error", error: "超时" });
}

const stage = () => (S.health && S.health.config && S.health.config.stage) || "end";
const STAGE_LABEL = { start: "年初 · 绩效填报", mid: "年中 · 目标回顾与变更", end: "年终 · 考核与结果" };

const BANDS = [
  { lv: 4, key: "A", name: "优秀", min: 95, max: 100, hint: "≥95" },
  { lv: 3, key: "B", name: "达标", min: 80, max: 94.9, hint: "80–94.9" },
  { lv: 2, key: "C", name: "待改进", min: 0, max: 79.9, hint: "<80" },
];
const bandOf = s => BANDS.find(b => s >= b.min && s <= b.max) || BANDS[1];
const bandOfLevel = lv => BANDS.find(b => b.lv === lv) || BANDS[1];

let PERSONAS = [];


const STATUS_TXT = {
  draft: ["草稿 · 未提交", "grey"], submitted: ["待领导审批", "amber"],
  approved: ["已批准 · 待生成锚点", ""], rejected: ["已退回，请修改", "red"],
  confirmed: ["已锁定", ""],
};

/* 导航：角色 × 阶段 */
function navFor(role, st) {
  if (role === "emp") {
    const items = [["me", "我的绩效"]];
    if (st === "mid") items.push(["change", "申请目标变更"]);
    if (st === "end") items.push(["appeals", "我的申诉"]);
    return items;
  }
  if (role === "mgr") {
    const items = [["me", "我的绩效"], ["team", "我的团队"]];
    if (st === "start") items.push(["approve", "目标审批与锚点"]);
    if (st === "mid") { items.push(["change", "发起变更"]); items.push(["changes", "变更审批"]); }
    items.push(["org", "团队分布"]);
    return items;
  }
  if (role === "skip") {
    const items = [["org", "我的条线"], ["team", "细看团队"]];
    if (st === "start") items.push(["approve", "目标审批与锚点"]);
    return items;
  }
  return [["hr", "全公司"], ["appeals", "申诉"], ["audit", "审计"]];
}

async function api(path, opt = {}) {
  const asHeader = raw => {
    const i = raw.indexOf(":");
    return i < 0 ? raw : raw.slice(0, i + 1) + encodeURIComponent(raw.slice(i + 1));
  };
  opt.headers = Object.assign({ "X-As": asHeader(S.persona) }, opt.headers || {});
  const r = await fetch(path, opt);
  const t = await r.text();
  if (!r.ok) throw new Error(t);
  try { return JSON.parse(t); } catch { return t; }
}
const post = (p, b) => api(p, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b || {}),
});

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("on");
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove("on"), 2400);
}

/* ---------------- 路由 ---------------- */
async function route() {
  const role = S.health ? S.health.persona.role : "hr";
  const st = stage();
  const items = navFor(role, st);
  let view = (location.hash || "").replace("#/", "").split("/")[0];
  if (!items.some(x => x[0] === view)) view = items[0][0];
  const nav = $("#nav");
  nav.innerHTML = "";
  for (const [k, label] of items) {
    const a = el("a", k === view ? "on" : "", label);
    a.href = "#/" + k;
    nav.append(a);
  }
  const app = $("#app");
  app.innerHTML = '<div class="empty">加载中…</div>';
  try {
    if (view === "me") await viewMe(app);
    else if (view === "change") await viewChange(app);
    else if (view === "appeals" && role === "emp") await viewMyAppeals(app);
    else if (view === "team") await viewTeam(app);
    else if (view === "approve") await viewApprove(app);
    else if (view === "changes") await viewChanges(app);
    else if (view === "org") await viewOrg(app);
    else if (view === "hr") await viewHR(app);
    else if (view === "appeals") await viewHRAppeals(app);
    else if (view === "audit") await viewAudit(app);
  } catch (e) {
    app.innerHTML = "";
    app.append(el("div", "err", "出错了：" + e.message));
  }
}
window.addEventListener("hashchange", route);

function stageBar(app, extra) {
  const c = el("div", "card");
  c.style.background = "#FAFCFB";
  const row = el("div", "kv");
  row.append(el("span", "tag", STAGE_LABEL[stage()]));
  if (extra) row.append(el("span", "muted", extra));
  c.append(row);
  app.append(c);
}

/* ---------------- 员工：我的绩效 ---------------- */
async function viewMe(app) {
  const role = (S.health && S.health.persona && S.health.persona.role) || "";
  let pid = (S.health && S.health.persona && S.health.persona.selfId) || null;
  if (!pid) {
    const people = await api("/api/people");
    pid = people[0].id;
  }
  const p = await api("/api/person/" + pid);
  const st = stage();
  app.innerHTML = "";
  app.append(el("div", "crumb", `2026 年度绩效 · ${p.name} · ${p.dept} · ${p.role}`));
  app.append(el("h1", null, "我的绩效"));
  stageBar(app, st === "start" ? "当前阶段只能填目标、提交审批" :
    st === "mid" ? "目标已锁定，可在「申请目标变更」里提交调整" :
      "结果已出：看分数构成、评级理由；有异议去「我的申诉」");

  if (st === "start") app.append(goalEditor(p));
  else if (st === "mid") app.append(goalReadonly(p));
  else {
    app.append(resultCard(p));
    app.append(goalReadonly(p, true));
    app.append(reasonCard(p, p.id, null));
    app.append(metricsCard(p, "emp"));
    app.append(selfSummaryCard(p));
  }
}

function resultCard(p) {
  const card = el("div", "card");
  const head = el("div", "head");
  head.append(el("h2", null, "我的结果"));
  head.append(el("span", "tag grey", "上级 " + p.manager));
  head.append(el("span", "tag grey", "隔级 " + p.director));
  card.append(head);
  if (!p.rating) return card;
  if (p.rating.rating === "待打分" || p.rating.score == null) {
    const w = el("div", "big");
    w.append(el("div", "n", "—"));
    w.append(el("div", "meta", "还没有打分，打完后自动算总分和评级"));
    card.append(w);
    return card;
  }
  const b = el("div", "big");
  b.append(el("div", "n", fmt(p.rating.score)));
  const meta = el("div", "meta");
  meta.append(el("div", null, `部门排名 ${p.rating.rank}/${p.rating.deptCount} · A 名额 ${p.rating.quota}`));
  const line = el("div");
  line.append(document.createTextNode("评级 "));
  line.append(el("span", "badge " + p.rating.rating, p.rating.rating));
  if (p.rating.status === "cutline") line.append(el("span", "tag amber", " 卡线：够线但没名额"));
  meta.append(line);
  b.append(meta);
  card.append(b);

  const t = el("table");
  const th = el("tr");
  for (const h of ["目标", "权重", "得分", "落在哪档"]) th.append(el("th", null, h));
  t.append(th);
  p.rating.detail.forEach(d => {
    const bd = d.score == null ? { key: "", name: "未打分", hint: "" } : bandOf(d.score);
    const tr = el("tr");
    tr.append(el("td", null, d.text), el("td", null, d.weight + "%"),
      el("td", null, fmt(d.score)), el("td", null, `${bd.key} 档 · ${bd.name}（${bd.hint}）`));
    t.append(tr);
  });
  card.append(t);
  card.append(el("div", "src", "分数由规则引擎按权重计算，模型不参与算分。"));
  return card;
}

/* ---------------- 员工：目标填报（年初） ---------------- */
function goalEditor(p) {
  const card = el("div", "card");
  card.append(el("h2", null, "年初 · 填报绩效目标"));

  const sts = p.goals.map(function (g) { return g.status; });
  const has = function (x) { return sts.indexOf(x) >= 0; };
  let state = "draft";
  if (!sts.length) state = "draft";
  else if (sts.every(function (x) { return x === "confirmed"; })) state = "confirmed";
  else if (has("submitted")) state = "submitted";
  else if (has("rejected")) state = "rejected";
  else if (has("approved")) state = "approved";
  const editable = (state === "draft" || state === "rejected");

  const map0 = {
    draft: ["草稿 · 未提交", "grey", "先用一句话说清楚要做什么，剩下的系统帮你整理"],
    submitted: ["已提交 · 等待领导审批", "amber", "如需修改可先撤回"],
    approved: ["领导已批准 · 待生成锚点", "", "锚点由口径直接生成，无需再等"],
    rejected: ["被退回 · 请按意见修改后重新提交", "red", "退回意见见每条目标"],
    confirmed: ["已锁定 v1", "", "年内如需调整走「申请目标变更」"],
  };
  const bar = el("div", "goal");
  bar.style.padding = "12px 14px";
  const m0 = map0[state];
  bar.append(el("span", "tag " + m0[1], m0[0]), el("span", "muted", m0[2]));
  card.append(bar);

  if (!editable) {
    p.goals.forEach(function (g) {
      const box = el("div", "goal");
      const top = el("div", "top");
      top.append(el("div", "text", g.text), el("div", "w", "权重 " + g.weight + "%"));
      box.append(top);
      box.append(fieldLine(g));
      const ft = fieldsTable(g);
      if (ft) box.append(ft);
      if (g.reviewNote) box.append(el("div", "src", "审批意见：" + g.reviewNote));
      if (g.anchor) box.append(anchorBlock(g.anchor, g.selfcheck));
      card.append(box);
    });
    // 状态条：把"按钮"换成不可点的状态按钮，明确告诉用户已经提交
    const stBtnRow = el("div", "acts");
    const stText = state === "submitted" ? "已提交，等待审批"
      : (state === "confirmed" ? "已确认，目标锁定"
        : (state === "approved" ? "已批准，待生成锚点" : "待修改"));
    const stBtn = el("button", "ghost", stText);
    stBtn.disabled = true;
    stBtnRow.append(stBtn);
    card.append(stBtnRow);
    return card;
  }

  const rows = p.goals.map(function (g) {
    return { id: g.id, text: g.text, weight: g.weight,
             fields: Object.assign({ direction: "target", cycle: "" }, g.fields || {}),
             missing: [], issues: [], options: null, check: null,
             status: g.status, note: g.reviewNote || "", reviewedBy: g.reviewedBy || "",
             reviewedAt: g.reviewedAt || "",
             // 已存目标只要口径齐全，就算"已整理"，不必再点整理
             parsed: !!(g.check && g.check.verdict) ||
                     ["name", "target", "cycle", "source"].every(function (k) {
                       return String((g.fields || {})[k] || "").trim();
                     }) };
  });

  // 被退回时，在最上面单独列出领导的意见
  if (state === "rejected") {
    const notes = rows.filter(function (r) { return r.status === "rejected" && r.note; });
    if (notes.length) {
      const tip = el("div", "card");
      tip.style.background = "#FEF8F4";
      tip.style.border = "1px solid #EBC8B6";
      tip.style.padding = "12px 14px";
      const h = el("div", "head");
      h.append(el("h2", null, "领导退回意见"));
      tip.append(h);
      notes.forEach(function (r) {
        const line = el("div");
        line.style.marginTop = "8px";
        const q = el("div", "quote");
        q.style.borderLeftColor = "#C9734E";
        q.append(el("span", "tag red", "退回"), document.createTextNode(r.note));
        line.append(q);
        const meta = [r.reviewedBy, r.reviewedAt].filter(Boolean).join(" · ");
        if (meta) {
          const m = el("div", "src");
          m.textContent = "「" + (r.text || "").slice(0, 20) + "」　" + meta;
          line.append(m);
        }
        tip.append(line);
      });
      card.append(tip);
    }
  }

  const list = el("div");
  card.append(list);
  const sumBadge = el("span", "tag grey", "合计 0%");
  const weightHint = el("span", "muted");
  const problem = el("div");
  card.append(problem);

  const mkText = function (value, ph, oninput) {
    const i = el("input");
    i.type = "text"; i.value = value || ""; i.placeholder = ph || "";
    i.oninput = function () { oninput(i.value); };
    return i;
  };

  const recomputeMissing = function (r) {
    const need = [["name", "指标"], ["target", "目标值"], ["cycle", "统计周期"], ["source", "数据来源"]];
    r.missing = need.filter(function (x) {
      return !String((r.fields || {})[x[0]] || "").trim();
    }).map(function (x) { return x[1]; });
  };

  const maybeCompose = function (r) {
    recomputeMissing(r);
    const composed = composeGoalText(r.fields);
    if (composed) { r.text = composed; r.auto = true; }
  };

  const draw = function () {
    list.innerHTML = "";

    if (!rows.length) {
      const empty = el("div", "quote");
      empty.style.borderLeftColor = "#C9D6CF";
      empty.textContent = "还没有目标。点下面「+ 添加一条」，用一句话写，例如：把订单接口 P99 从 820ms 降到 300ms 以内。";
      list.append(empty);
    }

    rows.forEach(function (r, idx) {
      const box = el("div", "goal");
      box.style.padding = "16px 16px 12px";

      const tip = el("div", "src");
      tip.style.color = "#A6AFAA";
      tip.style.marginBottom = "8px";
      tip.textContent = "建议包含：做什么 ＋ 指标 ＋ 目标值 ＋ 统计周期 ＋ 数据来源（如：季度、APM 日报）";
      box.append(tip);

      const top = el("div", "top");
      const t = mkText(r.text, "用人话说：把订单接口 P99 从 820ms 降到 300ms 以内", function (v) {
        r.text = v; r.auto = false; r.check = null;
      });
      t.style.flex = "1";
      t.style.fontSize = "15px";
      const w = el("input");
      w.type = "number"; w.min = 1; w.max = 100; w.step = 5;
      w.value = r.weight; w.style.width = "64px";
      w.oninput = function () { r.weight = Number(w.value) || 0; sync(); };
      const del = el("button", "ghost", "删除");
      del.onclick = function () { rows.splice(idx, 1); draw(); };
      top.append(t, el("span", "muted", "权重"), w, el("span", "muted", "%"), del);
      box.append(top);

      // 判定结果：紧贴输入框下方
      if (r.parsed) {
        const rejected = (r.check && r.check.verdict === "invalid");
        const incomplete = !rejected &&
          (((r.missing && r.missing.length) > 0) || (r.issues && r.issues.length));
        const q = el("div", "quote");
        q.style.marginTop = "8px";
        if (rejected) {
          q.style.borderLeftColor = "#C9734E";
          const tg = el("span", "tag red", "没通过");
          tg.style.marginRight = "8px";
          q.append(tg);
          let msgs = [];
          if (r.check && r.check.reason) msgs.push(r.check.reason);
          (r.issues || []).forEach(function (it) { if (it && it.message) msgs.push(it.message); });
          msgs = msgs.filter(function (m, i) { return msgs.indexOf(m) === i; });
          q.append(document.createTextNode(msgs.join("；") || "这个目标年底无法验证"));
          box.append(q);

          const rw = (r.check && r.check.rewrite) || "";
          if (rw) {
            const line = el("div", "anchor");
            line.style.marginTop = "6px";
            line.append(el("div", null, "建议改成：" + rw));
            const use = el("button", "p", "采用并重新整理");
            use.style.marginTop = "6px";
            use.onclick = async function () {
              r.text = rw;
              // 同时按新文字重算口径，避免「文字改了表没改」
              try {
                const res = await post("/api/goals/parse", { personId: p.id, text: rw });
                r.fields = Object.assign({}, r.fields, res.fields || {});
                r.missing = res.missing || [];
                r.issues = res.issues || [];
                r.questions = res.questions || [];
                r.check = { verdict: res.aiVerdict || "", reason: res.aiReason || "",
                            rewrite: res.aiRewrite || "", forText: rw };
                r.parsed = true;
                toast("已采用建议，口径同步更新");
              } catch (e) {
                r.parsed = false; r.check = null; r.issues = [];
                toast("文字已替换，请点「整理成表格」重算口径");
              }
              draw();
            };
            line.append(use);
            box.append(line);
          }
        } else if (incomplete) {
          q.style.borderLeftColor = "#D9C07A";
          const tg = el("span", "tag amber", "待补充");
          tg.style.marginRight = "8px";
          q.append(tg, document.createTextNode(
            "还缺：" + (r.missing || []).join("、") + "，回答下面的问题就能补齐"));
          box.append(q);
        } else {
          q.style.borderLeftColor = "#C9D6CF";
          const ok = el("span", "tag", "通过");
          ok.style.marginRight = "8px";
          q.append(ok, document.createTextNode("这个目标年底能验证，可以提交"));
          box.append(q);
        }
      }

      const actsRow = el("div", "acts");
      actsRow.style.marginTop = "10px";
      const parseBtn = el("button", "p", r.parsed ? "重新整理" : "整理成表格");
      parseBtn.onclick = async function () {
        if ((r.text || "").trim().length < 4) { toast("先用一句话把目标说清楚"); return; }
        parseBtn.disabled = true;
        parseBtn.innerHTML = '<span class="spin"></span>整理中…';
        try {
          const res = await post("/api/goals/parse", { personId: p.id, text: r.text });
          r.fields = Object.assign({}, res.fields);
          r.missing = res.missing || [];
          r.issues = res.issues || [];
          r.options = res.options || null;
          r.check = { verdict: res.aiVerdict || "", reason: res.aiReason || "",
                      rewrite: res.aiRewrite || "", forText: r.text };
          r.questions = res.questions || [];
          r.parsed = true;
          maybeCompose(r);
          draw();
        } catch (e) { toast("整理失败：" + e.message); }
        parseBtn.disabled = false;
      };
      actsRow.append(parseBtn);

      const filled = ["name", "target", "source", "cycle"].filter(function (k) {
        return String(r.fields[k] || "").trim();
      }).length;
      const badge = el("span", "tag " + (filled === 4 ? "" : "amber"), "口径 " + filled + "/4");
      actsRow.append(badge);
      if (r.auto && filled === 4) {
        const note = el("span", "muted");
        note.style.marginLeft = "8px";
        note.textContent = "已按表格重写这句话";
        actsRow.append(note);
      }
      box.append(actsRow);

      // 局部刷新：只更新句子与徽章，不重建 DOM（否则输入框失焦）
      const refreshRow = function () {
        recomputeMissing(r);
        if (t) t.value = r.text;
        let n = 0;
        ["name", "target", "source", "cycle"].forEach(function (k) {
          if (String(r.fields[k] || "").trim()) n += 1;
        });
        badge.textContent = "口径 " + n + "/4";
        badge.className = "tag " + (n === 4 ? "" : "amber");
      };

      const FIELD_LABEL = { name: "指标", target: "目标值", cycle: "统计周期", source: "数据来源",
                            scope: "统计范围", baseline: "基线", unit: "单位", direction: "方向" };

      const applyField = function (key, val) {
        r.answered = r.answered || {};
        r.answered[key] = val;
        if (key === "baseline" || key === "target") {
          const m = String(val).match(/[0-9]+(?:\.[0-9]+)?/);
          if (m) r.fields[key] = m[0];
          const um = String(val).match(/(ms|毫秒|%|次|个|条|单|天|分)/);
          if (um && !r.fields.unit) r.fields.unit = um[1];
          r.missing = r.missing.filter(function (lbl) { return lbl !== "指标" && lbl !== ""; });
          maybeCompose(r);
          draw();
          return;
        }
        if (key === "direction") {
          r.fields.direction = (val === "越低越好") ? "lower" : (val === "越高越好" ? "higher" : "target");
        } else {
          r.fields[key] = val;
        }
        r.missing = r.missing.filter(function (lbl) {
          const k = Object.keys(FIELD_LABEL).filter(function (x) { return FIELD_LABEL[x] === lbl; })[0];
          return String(r.fields[k] || "").trim() === "";
        });
        maybeCompose(r);
        draw();
      };

      // AI 反问：缺的信息做成选择题
      if (r.parsed && r.check && r.check.verdict === "ok" &&
          r.questions && r.questions.length && r.missing && r.missing.length) {
        const ask = el("div");
        ask.style.marginTop = "12px";
        const _seen = {};
        r.questions.forEach(function (q) {
          if (_seen[q.field]) return;          // 同一字段只问一次
          _seen[q.field] = 1;
          const filledNow = String((r.fields || {})[q.field] || "").trim();
          if (filledNow) return;
          const line = el("div");
          line.style.marginTop = "10px";
          const qtext = el("div", "muted");
          qtext.style.marginBottom = "6px";
          qtext.textContent = q.question;
          line.append(qtext);

          const chips = el("div", "kv");
          (q.options || []).forEach(function (o) {
            const chip = el("button", "ghost", o);
            chip.style.padding = "3px 12px";
            chip.onclick = function () { applyField(q.field, o); };
            chips.append(chip);
          });
          line.append(chips);

          const custom = el("div", "kv");
          custom.style.marginTop = "6px";
          const inp = mkText("", "自己补充…", function () {});
          inp.style.width = "240px";
          const ok = el("button", "ghost", "填这个");
          ok.style.padding = "3px 10px";
          ok.onclick = function () {
            const v = (inp.value || "").trim();
            if (!v) { toast("先写内容"); return; }
            applyField(q.field, v);
          };
          custom.append(inp, ok);
          line.append(custom);
          ask.append(line);
        });
        if (ask.childNodes.length) box.append(ask);
      }

      if (r.parsed && r.check && r.check.verdict === "ok" &&
          (!r.missing || !r.missing.length) && r.answered) {
        const done = el("div", "kv");
        done.style.marginTop = "10px";
        done.append(el("span", "tag", "口径已补齐"));
        const bits = Object.keys(r.answered).map(function (k) { return r.answered[k]; }).join(" / ");
        if (bits) done.append(el("span", "muted", bits));
        box.append(done);
      }

      if (r.parsed) {
        const det = el("details");
        det.open = (r.missing || []).length === 0;
        const sum = el("summary");
        sum.style.cssText = "cursor:pointer;font-size:12px;color:#6F7A74;margin-top:10px";
        sum.textContent = "口径表（可展开修改）";
        det.append(sum);

        const grid = el("div", "fieldgrid");
        const F = r.fields;
        const setF = function (k) {
          return function (v) { F[k] = v.replace(/^\s+|\s+$/g, ""); maybeCompose(r); refreshRow(); };
        };
        grid.append(lab("指标", mkText(F.name, "如 订单接口 P99", setF("name"))));
        grid.append(lab("目标值", mkText(F.target, "如 300", setF("target"))));
        const dirS = el("select");
        [["lower", "越低越好"], ["higher", "越高越好"], ["target", "达标型"]].forEach(function (x) {
          const o = el("option", null, x[1]); o.value = x[0];
          if ((F.direction || "target") === x[0]) o.selected = true;
          dirS.append(o);
        });
        dirS.onchange = function () { F.direction = dirS.value; maybeCompose(r); refreshRow(); };
        grid.append(lab("方向", dirS));
        const cycS = el("select");
        ["", "月度", "季度", "半年", "年度"].forEach(function (x) {
          const o = el("option", null, x || "（未定）"); o.value = x;
          if ((F.cycle || "") === x) o.selected = true;
          cycS.append(o);
        });
        cycS.onchange = function () { F.cycle = cycS.value; maybeCompose(r); refreshRow(); };
        grid.append(lab("统计周期", cycS));
        grid.append(lab("数据来源", mkText(F.source, "如 APM 日报", setF("source"))));

        const optDefs = [["baseline", "基线", "如 820"],
                         ["unit", "单位", "如 ms / % / 次"],
                         ["scope", "统计范围", "如 生产环境 /order/* 接口"]];
        const shown = r.showOpt || {};
        optDefs.forEach(function (o) {
          if (String(F[o[0]] || "").trim() || shown[o[0]]) {
            grid.append(lab(o[1] + "（可选）", mkText(F[o[0]], o[2], setF(o[0]))));
          }
        });
        det.append(grid);

        const addRow = el("div", "acts");
        addRow.style.marginTop = "8px";
        const missOpts = optDefs.filter(function (o) {
          return !String(F[o[0]] || "").trim() && !shown[o[0]];
        });
        missOpts.forEach(function (o) {
          const b = el("button", "ghost", "＋ " + o[1]);
          b.style.padding = "2px 10px";
          b.onclick = function () {
            r.showOpt = r.showOpt || {};
            r.showOpt[o[0]] = true;
            draw();
          };
          addRow.append(b);
        });
        if (missOpts.length) {
          addRow.append(el("span", "muted", "这些不是必填，不需要就别加"));
          det.append(addRow);
        }
        box.append(det);
      }

      list.append(box);
    });

    const total = rows.reduce(function (a, b) { return a + (Number(b.weight) || 0); }, 0);
    sumBadge.textContent = "合计 " + total + "%";
    sumBadge.className = "tag " + (Math.abs(total - 100) < 0.5 ? "" : "amber");
    if (weightHint) {
      if (Math.abs(total - 100) < 0.5) {
        weightHint.textContent = "权重没问题，可以提交";
        weightHint.style.color = "#6F7A74";
      } else {
        weightHint.textContent = "还差 " + (100 - total) + "%，调到 100% 才能提交";
        weightHint.style.color = "#B4552B";
      }
    }
  };

  function lab(text, node) {
    const d = el("div", "field");
    d.append(el("span", "muted", text), node);
    return d;
  }
  // 对话模式下 route() 只渲染隐藏的后台容器，这里要刷新右侧面板
  const refreshView = function () {
    const shell = document.getElementById("shell");
    const chatMode = shell && !shell.classList.contains("hidden");
    if (chatMode && window.renderPanel) return window.renderPanel("me");
    route();
  };
  var autosaveTimer = null;
  window.__panelDirty = false;
  var sync = function () {
    draw();
    // 草稿自动保存：400ms 内无新改动就写回服务器，界面与服务器保持一致
    window.__panelDirty = true;
    if (autosaveTimer) clearTimeout(autosaveTimer);
    autosaveTimer = setTimeout(async function () {
      if (state !== "draft" && state !== "rejected") { window.__panelDirty = false; return; }
      const empty = rows.filter(function (r) { return !String(r.text || "").trim(); });
      if (empty.length || !rows.length) { return; }
      try {
        await post("/api/goals/save", { personId: p.id, goals: payload() });
        window.__panelDirty = false;
      } catch (e) { /* 自动保存失败不打扰用户 */ }
    }, 400);
  };
  draw();

  const dirty = (function () {
    const cur = rows.map(function (r) { return (r.text || "") + "|" + r.weight; }).join(";");
    const srv = (p.goals || []).map(function (g) { return (g.text || "") + "|" + g.weight; }).join(";");
    return cur !== srv;
  })();
  if (dirty) {
    const warn = el("div", "src");
    warn.style.color = "#B4762B";
    warn.textContent = "有未保存的改动，提交时会自动保存";
    card.append(warn);
  }
  const acts = el("div", "acts");
  const add = el("button", "ghost", "+ 添加一条");
  const save = el("button", "ghost", "保存草稿");
  const submit = el("button", "p", "提交给领导审批");
  weightHint.style.marginLeft = "6px";
  acts.append(add, save, submit, sumBadge, weightHint);
  card.append(acts);
  card.append(el("div", "src", "提交后不可直接修改；年中如需调整，走「申请目标变更」并附佐证。"));

  add.onclick = function () {
    rows.push({ id: "", text: "", weight: 0, fields: { direction: "target", cycle: "" },
                missing: [], issues: [], options: null, check: null });
    draw();
  };

  const payload = function () {
    return rows.map(function (r, i) {
      return { id: r.id || ("gnew" + i), text: r.text, weight: r.weight,
               fields: r.fields, kind: "metric", check: r.check || null };
    });
  };

  save.onclick = async function () {
    try {
      const r = await post("/api/goals/save", { personId: p.id, goals: payload() });
      toast("已保存 " + r.count + " 条，合计 " + r.totalWeight + "%");
      window.__panelDirty = false;
      refreshView();
    } catch (e) { toast("保存失败：" + e.message); }
  };

  submit.onclick = async function () {
    const total = rows.reduce(function (a, b) { return a + (Number(b.weight) || 0); }, 0);
    if (Math.abs(total - 100) > 0.5) {
      toast("权重合计 " + total + "%，要调到 100% 才能提交");
      const wIn = document.querySelector(".goal input[type=number]");
      if (wIn) { wIn.focus(); wIn.style.outline = "2px solid #C9734E"; }
      return;
    }
    const unparsed = rows.filter(function (r) { return !r.parsed; }).length;
    if (unparsed) { toast("还有 " + unparsed + " 条没整理成表格，先点「整理成表格」"); return; }
    submit.disabled = true;
    submit.innerHTML = '<span class="spin"></span>检查中…';
    problem.innerHTML = "";
    try {
      await post("/api/goals/save", { personId: p.id, goals: payload() });
      await post("/api/goals/submit", { personId: p.id });
      window.__panelDirty = false;
      submit.disabled = true;
      submit.textContent = "已提交，等待审批";
      toast("已提交，等待领导审批");
      await refreshView();
    } catch (e) {
      let pay = null;
      try { pay = JSON.parse(e.message); } catch (x) { pay = null; }
      if (pay && pay.details) {
        const head = el("div", "quote");
        head.style.borderLeftColor = "#C9734E";
        head.append(el("span", "tag red", "未提交"), document.createTextNode(pay.error || ""));
        problem.append(head);
        pay.details.forEach(function (d) {
          const q = el("div", "src");
          q.style.color = "#B4552B";
          q.textContent = "「" + (d.text || "").slice(0, 18) + "」缺：" + (d.missing || []).join("、");
          problem.append(q);
        });
      } else {
        toast("提交失败：" + e.message);
      }
    }
    submit.disabled = false;
    submit.textContent = "提交给领导审批";
  };

  return card;
}

function composeGoalText(f) {
  if (!f || !f.name || !f.target || !f.source || !f.cycle) return "";
  const unit = f.unit || "";
  const base = f.baseline ? (f.baseline + unit + " ") : "";
  const verb = f.direction === "higher" ? "提升到" : (f.direction === "lower" ? "降到" : "达到");
  let t = "按" + f.source + "每" + f.cycle + "统计，" + f.name;
  t += base ? ("从 " + f.baseline + unit + " " + verb + " " + f.target + unit)
            : (verb + " " + f.target + unit);
  if (f.direction === "lower") t += " 以内";
  if (f.direction === "higher") t += " 以上";
  if (f.scope) t += "（范围：" + f.scope + "）";
  return t;
}

function fieldsTable(g) {
  const f = g.fields || {};
  const rows = [
    ["指标", f.name], ["目标值", f.target], ["基线", f.baseline], ["单位", f.unit],
    ["方向", f.direction === "lower" ? "越低越好" : (f.direction === "higher" ? "越高越好" : "达标型")],
    ["统计周期", f.cycle], ["数据来源", f.source], ["统计范围", f.scope],
  ].filter(function (x) { return String(x[1] || "").trim(); });
  if (!rows.length) return null;

  const det = el("details");
  const sum = el("summary");
  sum.style.cssText = "cursor:pointer;font-size:12px;color:#6F7A74;margin-top:6px";
  sum.textContent = "口径表（展开看）";
  det.append(sum);

  const grid = el("div", "fieldgrid");
  rows.forEach(function (r) {
    const d = el("div", "field");
    d.append(el("span", "muted", r[0]), el("div", null, String(r[1])));
    grid.append(d);
  });
  det.append(grid);
  return det;
}

function linkify(text) {
  const wrap = el("span");
  const raw = String(text || "");
  const re = /(https?:\/\/\S+|\/uploads\/\S+)/g;
  let last = 0, m;
  while ((m = re.exec(raw)) !== null) {
    if (m.index > last) wrap.append(document.createTextNode(raw.slice(last, m.index)));
    let href = m[1];
    // 上传附件默认走网页预览，避免下载后打不开
    const pm = href.match(/^(?:https?:\/\/127\.0\.0\.1:\d+)?\/uploads\/(.+)$/);
    if (pm) href = "/preview/" + pm[1];
    const a = el("a", null, href.replace(/^https?:\/\/127\.0\.0\.1:\d+/, ""));
    a.href = href;
    a.target = "_blank";
    a.style.color = "var(--accent)";
    a.style.textDecoration = "underline";
    wrap.append(a);
    last = m.index + m[1].length;
  }
  if (last < raw.length) wrap.append(document.createTextNode(raw.slice(last)));
  return wrap;
}

function fieldLine(g) {
  const f = g.fields || {};
  if (!f.name) return el("div", "src", "");
  const bits = [f.name];
  if (f.target) {
    bits.push((f.direction === "lower" ? "目标 ≤ " : f.direction === "higher" ? "目标 ≥ " : "目标 ")
      + f.target + (f.unit || ""));
  }
  if (f.baseline) bits.push("基线 " + f.baseline + (f.unit || ""));
  if (f.cycle) bits.push(f.cycle);
  if (f.source) bits.push(f.source);
  if (f.scope) bits.push(f.scope);
  return el("div", "src", bits.join(" · "));
}

function goalReadonly(p, compact) {
  const card = el("div", "card");
  card.append(el("h2", null, compact ? "我的目标（依据）" : "我的目标与锚点"));
  for (const g of p.goals) {
    const box = el("div", "goal");
    const top = el("div", "top");
    top.append(el("div", "text", g.text), el("div", "w", `权重 ${g.weight}% · ${g.version || "v1"}`));
    const stx = STATUS_TXT[g.status] || ["—", "grey"];
    top.append(el("span", "tag " + stx[1], stx[0]));
    box.append(top);
    if (!compact) {
      box.append(fieldLine(g));
      const ftm2 = fieldsTable(g);
      if (ftm2) box.append(ftm2);
    }
    if (compact) {
      const t = JOBS[g.id] ? "锚点正在生成中…" : (g.status === "confirmed" ? "锚点已锁定 v1" : "锚点待生成");
      box.append(el("div", "src", t));
      box.append(fieldLine(g));
      const ftc = fieldsTable(g);
      if (ftc) box.append(ftc);
    } else if (g.verdict === "block" || (g.suggestions && g.suggestions.length)) {
      const q = el("div", "quote");
      q.style.borderLeftColor = "#C9734E";
      q.append(el("span", "tag red", "待改写"), document.createTextNode(g.reviewNote || "这条目标无法量化"));
      box.append(q);
      (g.suggestions || []).forEach(function (sg) {
        const row = el("div", "anchor");
        row.append(el("div", null, sg));
        box.append(row);
      });
    }
    else if (g.anchor) box.append(anchorBlock(g.anchor, g.selfcheck));
    else box.append(el("div", "src", "锚点尚未生成"));
    card.append(box);
  }
  return card;
}

/* ---------------- 员工：申请目标变更（年中） ---------------- */
async function viewChange(app) {
  const role = (S.health && S.health.persona && S.health.persona.role) || "";
  const people = await api("/api/people");
  let target = people[0];
  if (role !== "emp") {
    // 领导/HR：先选人
    const pick = el("div", "card");
    pick.append(el("h2", null, "给谁改目标"));
    const chips = el("div", "kv");
    people.forEach(function (x, i) {
      const b = el("button", i === 0 ? "p" : "ghost", x.name + "（" + x.dept + "）");
      b.style.padding = "3px 12px";
      b.onclick = async function () {
        target = x;
        Array.prototype.forEach.call(chips.querySelectorAll("button"), function (y) { y.className = "ghost"; });
        b.className = "p";
        app.innerHTML = "";
        await renderChange(app, target, role);
        app.insertBefore(pick, app.firstChild.nextSibling);
      };
      chips.append(b);
    });
    pick.append(chips);
    app.innerHTML = "";
    app.append(el("div", "crumb", S.health.persona.label + " · 年中"));
    app.append(el("h1", null, "发起目标变更"));
    stageBar(app, "领导可以给下属调整目标；提交后需要审批，版本 +1，锚点重算。");
    app.append(pick);
  }
  if (role === "emp") {
    app.innerHTML = "";
    await renderChange(app, target, role);
  }
}

async function renderChange(app, target, role) {
  const p = await api("/api/person/" + target.id);
  app.innerHTML = "";
  app.append(el("div", "crumb", `${p.name} · ${p.dept}` + (role === "emp" ? "" : " · 由 " + S.health.persona.label + " 代填")));
  app.append(el("h1", null, role === "emp" ? "申请目标变更" : "变更 " + p.name + " 的目标"));
  stageBar(app, "目标已锁定。确因业务变化需要调整，写清原因并附佐证，直属领导审批后版本 +1。");

  const card = el("div", "card");
  let curGoalId = p.goals.length ? p.goals[0].id : "";
  // 变更后的新目标（可手填）
  const ntLab = el("div", "sub");
  ntLab.textContent = "变更后的目标（可以直接改）";
  ntLab.style.marginTop = "12px";
  const nt = el("input");
  nt.type = "text";
  nt.style.width = "100%";
  nt.placeholder = "例如：按 APM 系统每季度统计，接口平均响应时间从 800ms 降到 600ms 以内";

  // 口径字段
  const fg = el("div", "fieldgrid");
  const fields = { name: "", target: "", baseline: "", unit: "", direction: "lower",
                   cycle: "", source: "", scope: "" };
  const mkF = function (label, key, ph) {
    const d = el("div", "field");
    const i = el("input");
    i.type = "text"; i.placeholder = ph || ""; i.value = "";
    i.oninput = function () { fields[key] = i.value; };
    d.append(el("span", "muted", label), i);
    fg.append(d);
    return i;
  };
  const iName = mkF("指标", "name", "如 接口平均响应时间");
  const iTarget = mkF("目标值", "target", "如 600");
  const iBase = mkF("基线", "baseline", "如 800");
  const iUnit = mkF("单位", "unit", "如 ms");
  const iCycle = mkF("统计周期", "cycle", "如 季度");
  const iSource = mkF("数据来源", "source", "如 APM 系统");
  const iScope = mkF("统计范围", "scope", "如 核心交易接口");

  const fillFrom = function (gid) {
    const g = (p.goals || []).filter(function (x) { return x.id === gid; })[0];
    if (!g) return;
    nt.value = g.text || "";
    const f = g.fields || {};
    fields.name = f.name || ""; fields.target = f.target || ""; fields.baseline = f.baseline || "";
    fields.unit = f.unit || ""; fields.direction = f.direction || "lower";
    fields.cycle = f.cycle || ""; fields.source = f.source || ""; fields.scope = f.scope || "";
    iName.value = fields.name; iTarget.value = fields.target; iBase.value = fields.baseline;
    iUnit.value = fields.unit; iCycle.value = fields.cycle; iSource.value = fields.source;
    iScope.value = fields.scope;
  };
  fillFrom(curGoalId);

  const ta = el("textarea");
  ta.placeholder = "变更原因：例如上半年客户需求变化，原目标的指标已不适用";
  const ev = el("input"); ev.type = "text";
  ev.placeholder = "佐证：可粘贴文件、上传附件，或直接写说明";
  ev.style.width = "100%"; ev.style.marginTop = "8px";

  const picker = el("input");
  picker.type = "file";
  picker.style.display = "none";
  const bFile = el("button", "ghost", "上传附件");
  bFile.style.marginTop = "6px";
  const upTip = el("span", "muted");
  upTip.style.marginLeft = "8px";

  const uploadFile = function (file) {
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) { toast("文件不能超过 5MB"); return; }
    const fr = new FileReader();
    fr.onload = async function () {
      upTip.textContent = "上传中…";
      try {
        const r = await post("/api/upload", { filename: file.name, content: String(fr.result) });
        const mark = ev.value.trim() ? "；" : "";
        ev.value = ev.value.trim() + mark + file.name + " " + location.origin + r.url;
        upTip.textContent = "已上传：" + file.name;
      } catch (e) { upTip.textContent = ""; toast("上传失败：" + e.message); }
    };
    fr.readAsDataURL(file);
  };

  bFile.onclick = function () { picker.click(); };
  picker.onchange = function () { uploadFile(picker.files && picker.files[0]); };
  ev.onpaste = function (e) {
    const items = (e.clipboardData && e.clipboardData.files) || [];
    if (items.length) { e.preventDefault(); uploadFile(items[0]); }
  };
  ev.ondragover = function (e) { e.preventDefault(); };
  ev.ondrop = function (e) {
    e.preventDefault();
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) uploadFile(f);
  };
  const row = el("div", "acts");
  const btn = el("button", "p", "提交变更申请");
  row.append(btn);
  if (p.goals.length > 1) {
    const chips = el("div", "kv");
    chips.style.marginBottom = "8px";
    p.goals.forEach(function (g, i) {
      const b = el("button", i === 0 ? "p" : "ghost", "目标 " + (i + 1));
      b.style.padding = "3px 10px";
      b.onclick = function () {
        curGoalId = g.id;
        Array.prototype.forEach.call(chips.querySelectorAll("button"), function (x) {
          x.className = "ghost";
        });
        b.className = "p";
        fillFrom(curGoalId);
      };
      chips.append(b);
    });
    card.append(chips);
  }

  const upRow = el("div", "acts");
  upRow.append(bFile, upTip, picker);
  card.append(ntLab, nt, fg, ta, ev, upRow, row);
  btn.onclick = async () => {
    if (!curGoalId) { toast("还没有可变更的目标"); return; }
    if (!ev.value.trim()) { toast("佐证不能为空"); return; }
    if (ta.value.trim().length < 5) { toast("写清变更原因"); return; }
    if (!nt.value.trim()) { toast("填写变更后的目标"); return; }
    try {
      await post("/api/change", { personId: p.id, goalId: curGoalId, text: ta.value,
        evidence: ev.value, newText: nt.value, fields: fields });
      toast("已提交，等待领导审批"); route();
    } catch (e) { toast("提交失败：" + e.message); }
  };
  app.append(card);

  const hc = el("div", "card");
  hc.append(el("h2", null, "我的变更记录"));
  if (!p.changes.length) hc.append(el("div", "empty", "还没有提交过变更"));
  for (const c of p.changes) {
    const r = el("div", "audit-row");
    r.append(el("span", "t", c.ts), el("span", "tag " + (c.status === "待审批" ? "amber" : "grey"), c.status),
      el("span", null, (c.newText || "") + "（原因：" + c.text + "）"),
      (function () {
        const box = el("span", "muted");
        box.append(document.createTextNode("佐证："));
        box.append(linkify(c.evidence));
        return box;
      })());
    hc.append(r);
  }
  app.append(hc);
}

/* ---------------- 员工：我的申诉（年终） ---------------- */
async function viewMyAppeals(app) {
  const people = await api("/api/people");
  const p = await api("/api/person/" + people[0].id);
  app.innerHTML = "";
  app.append(el("div", "crumb", `${p.name} · 年终`));
  app.append(el("h1", null, "我的申诉"));
  stageBar(app, "对评分或评级有异议，写清哪一项不对以及依据；HR 会处理并回复。");

  const card = el("div", "card");
  const ta = el("textarea");
  ta.placeholder = "例如：第三项目标『跨部门协同』我参与了两次联合项目，但评分未体现，依据见…";
  const row = el("div", "acts");
  const btn = el("button", "p", "提交申诉");
  const ex = el("a");
  ex.href = "/api/export/" + p.id; ex.target = "_blank";
  ex.className = "muted"; ex.textContent = "导出我的绩效简报"; ex.style.marginLeft = "auto";
  row.append(btn, ex);
  card.append(ta, row);
  btn.onclick = async () => {
    if (ta.value.trim().length < 5) { toast("写清楚再提交"); return; }
    try { await post("/api/appeal", { personId: p.id, text: ta.value }); ta.value = ""; toast("已提交"); route(); }
    catch (e) { toast("失败：" + e.message); }
  };
  app.append(card);

  const hist = el("div", "card");
  hist.append(el("h2", null, "处理进度"));
  if (!p.appeals.length) hist.append(el("div", "empty", "没有申诉记录"));
  for (const a of p.appeals) {
    const r = el("div", "audit-row");
    r.append(el("span", "t", a.ts), el("span", "tag " + (a.status === "待处理" ? "amber" : "grey"), a.status),
      el("span", null, a.text));
    if (a.reply) r.append(el("span", "muted", "回复：" + a.reply));
    hist.append(r);
  }
  app.append(hist);
}

/* ---------------- 领导：我的团队 ---------------- */
async function viewTeam(app) {
  const people = await api("/api/people");
  const only = localStorage.getItem("ys_mgr");
  if (only) {
    const keep = people.filter(x => x.manager === only);
    if (keep.length) { people.length = 0; people.push(...keep); }
  }
  app.innerHTML = "";
  app.append(el("div", "crumb", `${S.health.persona.label} · 可见 ${people.length} 人`));
  app.append(el("h1", null, "我的团队"));
  if (only) {
    const chip = el("span", "tag amber", `只看 ${only} 的团队（点击清除）`);
    chip.style.cursor = "pointer";
    chip.onclick = () => { localStorage.removeItem("ys_mgr"); route(); };
    app.append(chip);
  }
  stageBar(app, stage() === "start" ? "年初：审批目标、生成并确认锚点" :
    stage() === "mid" ? "年中：只处理目标变更" : "年终：打分、生成评级理由");

  const ORDER = { "目标未提交": 0, "目标待审批": 1, "锚点待生成": 2, "锚点待确认": 3,
    "变更待审批": 4, "待打分": 5, "待确认理由": 6, "年初已完成": 7, "年中无待办": 8, "已完成": 9 };
  people.sort((a, b) => (ORDER[a.todo] ?? 99) - (ORDER[b.todo] ?? 99));

  const sum = el("div", "card");
  const todo = {};
  for (const p of people) todo[p.todo || "—"] = (todo[p.todo || "—"] || 0) + 1;
  sum.append(el("h2", null, "待办"));
  const kv = el("div", "kv");
  for (const k of Object.keys(todo)) {
    const s = el("span", null, `${k} `);
    s.append(el("b", null, String(todo[k])));
    kv.append(s);
  }
  sum.append(kv);
  app.append(sum);

  const g = el("div", "grid2");
  const listCard = el("aside", "card");
  const box = el("div", "people");
  listCard.append(box);
  const detail = el("section", "card");
  g.append(listCard, detail);
  app.append(g);

  let cur = S.cur && people.some(x => x.id === S.cur) ? S.cur : (people[0] && people[0].id);
  const render = () => {
    box.innerHTML = "";
    for (const p of people) {
      const row = el("div", "person" + (cur === p.id ? " on" : ""));
      row.append(el("div", "av", p.name.slice(-1)));
      const b = el("div");
      const meta = (p.total == null)
        ? (p.role + " · " + (p.todo || ""))
        : (p.role + " · " + fmt(p.total) + " 分 " + p.rating);
      b.append(el("div", "nm", p.name), el("div", "meta", meta));
      row.append(b);
      const st = el("div", "st " + (p.todo === "已完成" ? "done" : ""), p.todo || "");
      st.style.fontSize = "10.5px";
      row.append(st);
      row.onclick = () => { cur = p.id; S.cur = p.id; render(); open(p.id); };
      box.append(row);
    }
  };
  const open = async (pid) => {
    detail.innerHTML = '<div class="empty">加载中…</div>';
    const p = await api("/api/person/" + pid);
    await renderPerson(detail, p, () => { render(); open(pid); });
    const bar = el("div", "acts");
    bar.style.marginTop = "14px";
    const bNext = el("button", "ghost", "下一个待办 →");
    bar.append(bNext);
    detail.append(bar);
    bNext.onclick = () => {
      const idx = people.findIndex(x => x.id === pid);
      const next = people.slice(idx + 1).find(x => x.todo !== "已完成") || people.find(x => x.todo !== "已完成");
      if (next && next.id !== pid) { S.cur = next.id; render(); open(next.id); } else toast("没有待办了");
    };
  };
  render();
  if (cur) open(cur);
}

/* ---------------- 领导：目标审批（年初） ---------------- */
async function viewApprove(app) {
  const people = await api("/api/people");
  const pending = [];
  for (const x of people) {
    if (!["目标待审批", "锚点待生成", "锚点待确认"].includes(x.todo)) continue;
    pending.push(await api("/api/person/" + x.id));
  }

  app.innerHTML = "";
  app.append(el("div", "crumb", `${S.health.persona.label} · ${pending.length} 人待处理`));
  app.append(el("h1", null, "目标审批与锚点"));
  stageBar(app, "一条流程走完：看目标 → 批准/退回 → 生成锚点 → 确认锁定。退回必须写原因。");

  if (!pending.length) {
    const done = el("div", "card");
    done.append(el("div", "empty", "没有待处理的。员工提交后会出现在这里。"));
    app.append(done);
    return;
  }

  pending.forEach(function (p) {
    const card = el("div", "card");
    const head = el("div", "head");
    head.append(el("h2", null, p.name), el("span", "tag grey", p.dept), el("span", "tag grey", p.role));
    head.append(el("span", "tag amber", p.todo));
    card.append(head);

    const submitted = p.goals.filter(function (g) { return g.status === "submitted"; });
    const approved = p.goals.filter(function (g) { return g.status === "approved"; });

    p.goals.forEach(function (g) {
      const box = el("div", "goal");
      const top = el("div", "top");
      top.append(el("div", "text", g.text), el("div", "w", "权重 " + g.weight + "% · " + (g.version || "v1")));
      const stx = STATUS_TXT[g.status] || ["—", "grey"];
      top.append(el("span", "tag " + stx[1], stx[0]));
      box.append(top);
      box.append(fieldLine(g));

      if (g.reviewNote && g.status === "rejected") {
        const q = el("div", "quote");
        q.style.borderLeftColor = "#C9734E";
        q.append(el("span", "tag red", "退回"), document.createTextNode(g.reviewNote));
        box.append(q);
      }

      // 已批准还没锚点
      if (g.status === "approved") {
        const acts = el("div", "acts");
        const bGen = el("button", "ghost", g.anchor ? "重新生成锚点" : "生成锚点");
        const bOk = el("button", "p", "确认锚点");
        bOk.disabled = !g.anchor;
        acts.append(bGen, bOk);
        box.append(acts);

        bGen.onclick = async function () {
          bGen.disabled = true; bGen.innerHTML = '<span class="spin"></span>生成中…';
          try {
            await post("/api/anchors", { personId: p.id, goalId: g.id, force: true });
            toast("锚点已生成"); route();
          } catch (e) { toast("失败：" + e.message); bGen.disabled = false; bGen.textContent = "生成锚点"; }
        };
        bOk.onclick = async function () {
          try {
            await post("/api/confirm", { personId: p.id, goalId: g.id });
            toast("已确认，目标锁定"); route();
          } catch (e) { toast("失败：" + e.message); }
        };
      }

      if (g.anchor) {
        if (g.anchor.polished) {
          box.append(el("span", "tag", "措辞已润色"));
        } else {
          const pol = el("button", "ghost", "AI 润色措辞");
          pol.style.padding = "2px 10px";
          pol.onclick = async function () {
            pol.disabled = true; pol.textContent = "润色中…";
            try { await post("/api/anchors/polish", { personId: p.id, goalId: g.id }); toast("措辞已润色"); route(); }
            catch (e) { toast("失败：" + e.message); pol.disabled = false; pol.textContent = "AI 润色措辞"; }
          };
          box.append(pol);
        }
        box.append(anchorBlock(g.anchor, g.selfcheck));
      }
      card.append(box);
    });

    if (submitted.length) {
      const note = el("input");
      note.type = "text";
      note.placeholder = "退回原因（退回时必填，会显示给员工）";
      note.style.width = "100%";
      note.style.marginTop = "10px";
      const acts = el("div", "acts");
      const ok = el("button", "p", `批准 ${submitted.length} 条`);
      const no = el("button", "ghost", "退回修改");
      acts.append(ok, no);
      card.append(note, acts);
      ok.onclick = async function () {
        ok.disabled = true;
        try {
          const r = await post("/api/goals/review", { personId: p.id, ok: true });
          toast(`已批准 ${r.count} 条，接下来生成锚点`); route();
        } catch (e) { toast("失败：" + e.message); ok.disabled = false; }
      };
      no.onclick = async function () {
        if (!note.value.trim()) { toast("先写退回原因"); note.focus(); return; }
        try {
          await post("/api/goals/review", { personId: p.id, ok: false, reason: note.value });
          toast("已退回"); route();
        } catch (e) { toast("失败：" + e.message); }
      };
    }

    app.append(card);
  });
}

/* ---------------- 领导：变更审批（年中） ---------------- */
async function viewChanges(app) {
  const people = await api("/api/people");
  app.innerHTML = "";
  app.append(el("div", "crumb", S.health.persona.label));
  app.append(el("h1", null, "目标变更审批"));
  stageBar(app, "批准后目标版本 +1，锚点清空需重新生成；驳回要写原因。");

  let n = 0;
  for (const p of people) {
    const d = await api("/api/person/" + p.id);
    const pend = (d.changes || []).filter(c => c.status === "待审批");
    if (!pend.length) continue;
    n += pend.length;
    const card = el("div", "card");
    const head = el("div", "head");
    head.append(el("h2", null, d.name), el("span", "tag grey", d.dept));
    card.append(head);
    for (const c of pend) {
      const row = el("div", "audit-row");
      row.append(el("span", "t", c.ts));
      const body = el("span");
      if (c.newText) {
        body.append(el("b", null, "新目标：" + c.newText), el("br"));
      }
      body.append(el("span", "muted", "原因：" + c.text));
      row.append(body);
      const evBox = el("span", "muted");
      evBox.append(document.createTextNode("佐证："));
      evBox.append(linkify(c.evidence));
      row.append(evBox);
      const ok = el("button", "p", "批准");
      ok.style.marginLeft = "auto";
      ok.onclick = async () => {
        try { await post("/api/change/approve", { id: c.id, ok: true }); toast("已批准，版本 +1"); route(); }
        catch (e) { toast("失败：" + e.message); }
      };
      const no = el("button", "ghost", "驳回");
      no.onclick = async () => {
        try { await post("/api/change/approve", { id: c.id, ok: false }); toast("已驳回"); route(); }
        catch (e) { toast("失败：" + e.message); }
      };
      const box = el("span");
      box.append(ok, no);
      row.append(box);
      card.append(row);
    }
    app.append(card);
  }
  if (!n) app.append(el("div", "card", "没有待审批的变更"));
}

/* ---------------- 隔级/领导：分布 ---------------- */
async function viewOrg(app) {
  const ov = await api("/api/overview");
  app.innerHTML = "";
  app.append(el("div", "crumb", `${S.health.persona.label} · 可见 ${ov.total} 人 · 模式：${ov.config.mode === "single" ? "单评" : "双评 60/40"}`));
  app.append(el("h1", null, "我的条线"));
  stageBar(app, "经理之间的宽严差异、团队内的名额压力，是校准会上真正要谈的东西");

  if (!ov.scoresVisible) {
    const pc = el("div", "card");
    pc.append(el("h2", null, "各经理的目标填报进度"));
    const t0 = el("table");
    const tr0 = el("tr");
    for (const h of ["直属领导", "隔级", "人数", "已确认", "已批准", "待审批", "未提交"]) tr0.append(el("th", null, h));
    t0.append(tr0);
    (ov.mgrProgress || []).forEach(function (m) {
      const r = el("tr");
      r.append(el("td", null, m.manager), el("td", null, m.director),
        el("td", null, m.n + (m.hasSelf ? "（含本人）" : "")),
        el("td", null, m.done), el("td", null, m.approved || 0),
        el("td", null, m.submitted), el("td", null, m.draft));
      t0.append(r);
    });
    pc.append(t0);
    pc.append(el("div", "src", "年终结果、名额与宽严对比将在「年终」阶段开放。"));
    app.append(pc);
    return;
  }

  if (ov.mgr_stats.length) {
    const mg = el("div", "card");
    mg.append(el("h2", null, "经理宽严对比"));
    const t = el("table");
    const tr = el("tr");
    for (const h of ["直属领导", "人数", "团队均分", "最高", "最低", "极差", "评 A 人数", ""])
      tr.append(el("th", null, h));
    t.append(tr);
    const avgAll = ov.mgr_stats.reduce((a, b) => a + b.avg, 0) / ov.mgr_stats.length;
    for (const m of ov.mgr_stats) {
      const r = el("tr");
      const nm = el("td", null, m.manager);
      if (Math.abs(m.avg - avgAll) > 2) {
        nm.append(el("span", "tag " + (m.avg > avgAll ? "amber" : "grey"), m.avg > avgAll ? " 偏松" : " 偏严"));
      }
      const link = el("td");
      const a = el("a", null, "查看");
      a.href = "#/team"; a.style.color = "var(--accent)";
      a.onclick = () => localStorage.setItem("ys_mgr", m.manager);
      link.append(a);
      r.append(nm, el("td", null, m.n), el("td", null, fmt(m.avg)), el("td", null, fmt(m.max)),
        el("td", null, fmt(m.min)), el("td", null, fmt(m.spread)),
        el("td", null, `${m.a}/${m.n}`), link);
      t.append(r);
    }
    mg.append(t);
    app.append(mg);
  }

  const dc = el("div", "card");
  dc.append(el("h2", null, "团队分布与名额"));
  const t2 = el("table");
  const tr2 = el("tr");
  for (const h of ["团队", "人数", "A 名额", "实际 A", "卡线", "区间", "提示"]) tr2.append(el("th", null, h));
  t2.append(tr2);
  for (const d of ov.depts) {
    const r = el("tr");
    r.append(el("td", null, d.dept), el("td", null, d.n), el("td", null, d.quota),
      el("td", null, d.A), el("td", null, d.cutline), el("td", null, `${fmt(d.min)}–${fmt(d.max)}`));
    const td = el("td");
    td.append(el("span", "tag " + (d.over_quota > 0 ? "amber" : d.max - d.min < 3 ? "red" : "grey"),
      d.over_quota > 0 ? `超名额 ${d.over_quota} 人` : d.max - d.min < 3 ? "无区分度" : "正常"));
    r.append(td);
    t2.append(r);
  }
  dc.append(t2);
  app.append(dc);

  const cc = el("div", "card");
  cc.append(el("h2", null, `卡线名单（够 ${ov.config.a_threshold} 分但没名额）：${ov.cutline.length} 人`));
  if (!ov.cutline.length) cc.append(el("div", "empty", "没有卡线人员"));
  else {
    const t3 = el("table");
    const tr3 = el("tr");
    for (const h of ["姓名", "团队", "得分", "排名", "名额"]) tr3.append(el("th", null, h));
    t3.append(tr3);
    for (const c of ov.cutline.slice(0, 12)) {
      const r = el("tr");
      r.append(el("td", null, c.name), el("td", null, c.dept), el("td", null, fmt(c.score)),
        el("td", null, c.rank), el("td", null, c.quota));
      t3.append(r);
    }
    cc.append(t3);
  }
  app.append(cc);
}

/* ---------------- HR ---------------- */
async function viewHR(app) {
  const st = stage();
  const prog = await api("/api/progress");
  app.innerHTML = "";
  app.append(el("div", "crumb", "HR · " + (STAGE_LABEL[st] || "") + " · 共 " + prog.total + " 人"));
  app.append(el("h1", null, st === "start" ? "年初：谁的目标交了、谁还没交"
    : st === "mid" ? "年中：变更与目标回顾" : "年终：谁该是 A"));

  // 阶段切换
  const sc = el("div", "card");
  sc.style.background = "#FAFCFB";
  sc.append(el("h2", null, "当前阶段（决定所有人看到什么）"));
  const srow = el("div", "acts");
  const stNow = st;
  for (const [k, label] of [["start", "年初 · 绩效填报"], ["mid", "年中 · 目标回顾与变更"], ["end", "年终 · 考核与结果"]]) {
    const b = el("button", stNow === k ? "p" : "ghost", label);
    b.onclick = async () => {
      if (!confirm(`切换到「${label}」？所有人界面只保留该阶段的内容。`)) return;
      await post("/api/config", { stage: k, reason: "HR 切换阶段：" + label });
      toast("已切换到 " + label);
      await loadHealth(); route();
    };
    srow.append(b);
  }
  sc.append(srow);
  sc.append(el("div", "src", "员工与领导的导航会随阶段变化；HR 也只看本阶段该看的东西。"));
  app.append(sc);

  // 数据管理：清空演示数据
  const danger = el("div", "card");
  danger.append(el("h2", null, "数据管理"));
  const bReset = el("button", "ghost", "清空所有数据");
  bReset.style.borderColor = "#D9A08A";
  bReset.style.color = "#B4552B";
  bReset.onclick = async function () {
    if (!confirm("确认清空？\n\n将删除：所有员工的目标与口径、锚点、打分、评级、理由、申诉、变更申请、审计记录、年度自评。\n\n保留：账号（HR / 领导 / 员工）与规则配置。\n\n此操作不可恢复。")) return;
    bReset.disabled = true; bReset.textContent = "清空中…";
    try {
      await post("/api/reset", { reason: "HR 清空数据" });
      // 聊天记录存在浏览器本地，服务端清不到，这里一并清掉
      try {
        Object.keys(localStorage).filter(function (k) { return k.indexOf("ys_chat_") === 0; })
          .forEach(function (k) { localStorage.removeItem(k); });
      } catch (e) {}
      toast("已清空：目标、打分、评级、理由、申诉、审计、对话记录都已删除");
      await loadHealth();
      route();
    } catch (e) { toast("失败：" + e.message); }
    bReset.disabled = false; bReset.textContent = "清空所有数据";
  };
  const bLoad = el("button", "p", "载入演示数据");
  bLoad.onclick = async function () {
    if (!confirm("载入演示数据？会重建三人的目标、锚点与评分。")) return;
    bLoad.disabled = true; bLoad.textContent = "载入中…";
    try {
      await post("/api/demo/load", {});
      toast("演示数据已载入");
      await loadHealth(); route();
    } catch (e) { toast("失败：" + e.message); }
    bLoad.disabled = false; bLoad.textContent = "载入演示数据";
  };
  const row2 = el("div", "acts");
  row2.append(bLoad, bReset);
  danger.append(row2);
  danger.append(el("div", "src", "清空后保留账号与规则配置；目标、口径、锚点、打分、评级、理由、申诉、变更、审计、年度自评全部删除。「载入演示数据」可一键恢复。"));
  app.append(danger);

  if (st === "start") { renderStart(st, prog); return; }
  if (st === "mid") { await renderMid(st, prog); return; }
  await renderEnd(st, prog);

  async function renderStart(stageKey, prog) {
    const c1 = el("div", "card");
    c1.append(el("h2", null, "填报进度"));
    const kv = el("div", "kv");
    const done = prog.depts.reduce(function (a, d) { return a + d.confirmed; }, 0);
    const submitted = prog.depts.reduce(function (a, d) { return a + d.submitted; }, 0);
    const draft = prog.depts.reduce(function (a, d) { return a + d.draft; }, 0);
    const approved = prog.depts.reduce(function (a, d) { return a + (d.approved || 0); }, 0);
    [["已确认目标", done], ["已批准待锚点", approved], ["待审批", submitted], ["未提交", draft]].forEach(function (x) {
      const s = el("span", null, x[0] + " ");
      s.append(el("b", null, String(x[1])));
      kv.append(s);
    });
    c1.append(kv);
    const bar = el("div", "scorebar");
    bar.style.marginTop = "10px";
    const i = el("i");
    i.style.width = (prog.total ? Math.round(done / prog.total * 100) : 0) + "%";
    bar.append(i);
    c1.append(bar);
    c1.append(el("div", "src", "完成率 " + (prog.total ? Math.round(done / prog.total * 100) : 0) + "%"));
    app.append(c1);

    const c2 = el("div", "card");
    c2.append(el("h2", null, "按部门看进度"));
    const t = el("table");
    const tr = el("tr");
    for (const h of ["部门", "人数", "已确认", "已批准", "待审批", "未提交", "进度"]) tr.append(el("th", null, h));
    t.append(tr);
    for (const d of prog.depts) {
      const r = el("tr");
      const pct = d.n ? Math.round(d.confirmed / d.n * 100) : 0;
      r.append(el("td", null, d.dept), el("td", null, d.n), el("td", null, d.confirmed),
        el("td", null, d.approved || 0), el("td", null, d.submitted), el("td", null, d.draft));
      const td = el("td");
      const b = el("div", "scorebar");
      const ii = el("i");
      ii.style.width = pct + "%";
      b.append(ii);
      td.append(b, el("span", "muted", " " + pct + "%"));
      r.append(td);
      t.append(r);
    }
    c2.append(t);
    app.append(c2);
  }

  async function renderMid(stageKey, prog) {
    const c1 = el("div", "card");
    c1.append(el("h2", null, "变更概况"));
    const kv = el("div", "kv");
    const s1 = el("span", null, "待审批变更 "); s1.append(el("b", null, String(prog.pendingChanges)));
    const s2 = el("span", null, "参与人数 "); s2.append(el("b", null, String(prog.total)));
    kv.append(s1, s2);
    c1.append(kv);
    app.append(c1);

    const c2 = el("div", "card");
    c2.append(el("h2", null, "目标与锚点（明面可见）"));
    const people = await api("/api/people");
    const t = el("table");
    const tr = el("tr");
    for (const h of ["姓名", "部门", "目标数", "状态"]) tr.append(el("th", null, h));
    t.append(tr);
    for (const x of people.slice(0, 12)) {
      const r = el("tr");
      r.append(el("td", null, x.name), el("td", null, x.dept), el("td", null, x.goals),
        el("td", null, x.todo || "-"));
      t.append(r);
    }
    c2.append(t);
    if (people.length > 12) c2.append(el("div", "src", "仅显示前 12 人"));
    app.append(c2);
  }

  async function renderEnd(stageKey, prog) {
    const ov = await api("/api/overview");

    const guide = el("div", "card");
    guide.style.background = "#FAFCFB";
    guide.append(el("h2", null, "3 分钟演示路径"));
    const ol = el("div");
    ol.style.cssText = "font-size:12.5px;line-height:2;color:#4A544F";
    ol.innerHTML = "① 切「年初」→ 员工写目标并提交 → 领导「目标审批」通过 → 生成并确认锚点<br>" +
      "② 切「年终」→ 下方看部门分布：有人够 95 却因名额拿不到 A<br>" +
      "③ 切到「刘刚 · 员工」→ 我的绩效 / 我的申诉，逐句引用可点开";
    guide.append(ol);
    app.append(guide);

    const cfg = el("div", "card");
    cfg.append(el("h2", null, "规则配置"));
    const row = el("div", "kv");
    const mk = function (label, node) { const s = el("span", null, label + " "); s.append(node); return s; };
    const selMode = el("select");
    [["single", "直属领导单评"], ["dual", "直属领导 60% + 隔级领导 40%"]].forEach(function (x) {
      const o = el("option", null, x[1]); o.value = x[0];
      if (ov.config.mode === x[0]) o.selected = true;
      selMode.append(o);
    });
    const inQ = el("input"); inQ.type = "number"; inQ.step = "0.01"; inQ.min = "0"; inQ.max = "1";
    inQ.value = ov.config.quota_ratio; inQ.style.width = "80px";
    const inA = el("input"); inA.type = "number"; inA.step = "0.5"; inA.value = ov.config.a_threshold; inA.style.width = "80px";
    const inC = el("input"); inC.type = "number"; inC.step = "0.5"; inC.value = ov.config.c_threshold; inC.style.width = "80px";
    const bSave = el("button", "p", "保存并重算");
    row.append(mk("模式", selMode), mk("A 名额比例", inQ), mk("A 线", inA), mk("C 线", inC), bSave);
    cfg.append(row);
    bSave.onclick = async function () {
      try {
        await post("/api/config", { mode: selMode.value, quota_ratio: parseFloat(inQ.value),
          a_threshold: parseFloat(inA.value), c_threshold: parseFloat(inC.value), reason: "HR 调整" });
        toast("已保存并重算"); route();
      } catch (e) { toast("失败：" + e.message); }
    };
    app.append(cfg);

    const dist = el("div", "card");
    dist.append(el("h2", null, "评级分布（" + ov.total + " 人）"));
    const kv2 = el("div", "kv");
    ["A", "B", "C"].forEach(function (k) { kv2.append(el("span", "badge " + k, k + " " + (ov.hist[k] || 0))); });
    dist.append(kv2);
    app.append(dist);

    const dc = el("div", "card");
    dc.append(el("h2", null, "部门分布与名额"));
    const t = el("table");
    const tr = el("tr");
    for (const h of ["部门", "人数", "A 名额", "实际 A", "卡线", "区间", "提示"]) tr.append(el("th", null, h));
    t.append(tr);
    for (const d of ov.depts) {
      const r = el("tr");
      r.append(el("td", null, d.dept), el("td", null, d.n), el("td", null, d.quota),
        el("td", null, d.A), el("td", null, d.cutline),
        el("td", null, fmt(d.min) + "–" + fmt(d.max)));
      const td = el("td");
      td.append(el("span", "tag " + (d.over_quota > 0 ? "amber" : d.max - d.min < 3 ? "red" : "grey"),
        d.over_quota > 0 ? ("超名额 " + d.over_quota + " 人") : ((d.n >= 3 && d.max - d.min < 3) ? "无区分度" : "正常")));
      r.append(td);
      t.append(r);
    }
    dc.append(t);
    app.append(dc);

    if (ov.mgr_stats.length) {
      const mc = el("div", "card");
      mc.append(el("h2", null, "经理宽严对比（校准会材料）"));
      const t2 = el("table");
      const tr2 = el("tr");
      for (const h of ["直属领导", "隔级", "人数", "均分", "极差", "评 A 人数"]) tr2.append(el("th", null, h));
      t2.append(tr2);
      for (const m of ov.mgr_stats) {
        const r = el("tr");
        r.append(el("td", null, m.manager), el("td", null, m.director), el("td", null, m.n),
          el("td", null, fmt(m.avg)), el("td", null, fmt(m.spread)),
          el("td", null, m.a + "/" + m.n));
        t2.append(r);
      }
      mc.append(t2);
      app.append(mc);
    }
  }
}

async function viewHRAppeals(app) {
  const list = await api("/api/appeals");
  app.innerHTML = "";
  app.append(el("div", "crumb", "申诉清单 · 仅 HR 可见"));
  app.append(el("h1", null, "谁在质疑，质疑什么"));
  const card = el("div", "card");
  if (!list.length) card.append(el("div", "empty", "还没有申诉"));
  for (const a of list) {
    const row = el("div", "audit-row");
    row.append(el("span", "t", a.ts), el("span", "a", a.name),
      el("span", "tag " + (a.status === "待处理" ? "amber" : "grey"), a.status),
      el("span", null, a.text));
    const btn = el("button", "ghost", "标记已处理");
    btn.style.marginLeft = "auto";
    btn.disabled = a.status !== "待处理";
    btn.onclick = async () => {
      await post("/api/appeal/resolve", { id: a.id, status: "已处理", reply: "HR 已沟通并解释依据" });
      toast("已处理"); route();
    };
    row.append(btn);
    card.append(row);
  }
  app.append(card);
}

async function viewAudit(app) {
  const logs = await api("/api/audit");
  app.innerHTML = "";
  app.append(el("div", "crumb", "append-only 事件日志 · 最近 100 条 · 仅 HR 可见"));
  app.append(el("h1", null, "谁在哪一步改了什么"));
  const card = el("div", "card");
  if (!logs.length) card.append(el("div", "empty", "还没有记录"));
  for (const l of logs) {
    const row = el("div", "audit-row");
    row.append(el("span", "t", l.ts), el("span", "a", l.actor), el("span", null, l.action),
      el("span", "muted", String(l.target)));
    if (l.before != null || l.after != null) {
      const cut = s => { s = String(s); return s.length > 80 ? s.slice(0, 80) + "…" : s; };
      row.append(el("span", "muted", `${cut(l.before)} → ${cut(l.after)}`));
    }
    card.append(row);
  }
  app.append(card);
}

/* ---------------- 人员详情（领导端） ---------------- */
async function renderPerson(root, p, refresh) {
  root.innerHTML = "";
  const st = stage();
  const head = el("div", "head");
  head.append(el("h2", null, p.name), el("span", "tag", p.role), el("span", "tag grey", p.dept));
  if (p.total != null) {
    head.append(el("span", "tag", fmt(p.total) + " 分 " + (p.rating ? p.rating.rating : "")));
  }
  head.append(el("span", "tag " + (p.todo === "已完成" ? "" : "grey"), p.todo || ""));
  root.append(head);
  if (p.rating) {
    const kv = el("div", "kv");
    if (p.rating.rank == null) {
      kv.append(el("span", null, "还没打分，打完再算排名和名额"));
    } else {
      kv.append(el("span", null, `排名 ${p.rating.rank}/${p.rating.deptCount}`));
      kv.append(el("span", null, `A 名额 ${p.rating.quota}`));
    }
    if (p.rating.status === "cutline") kv.append(el("span", "tag amber", "卡线"));
    root.append(kv);
  }

  const readOnly = (st === "start");
  if (!readOnly && p.permissions.approve_change && st === "start" && p.goals.some(g => g.status === "submitted")) {
    const rc = el("div", "card");
    rc.style.background = "#FBF3DF";
    rc.append(el("h2", null, "待审批的目标"));
    for (const g of p.goals.filter(x => x.status === "submitted")) {
      rc.append(el("div", "audit-row", `${g.text}（权重 ${g.weight}%）`));
    }
    const acts = el("div", "acts");
    const ok = el("button", "p", "批准");
    const no = el("button", "ghost", "退回");
    acts.append(ok, no);
    rc.append(acts);
    ok.onclick = async () => {
      try { await post("/api/goals/review", { personId: p.id, ok: true }); toast("已批准"); refresh(); }
      catch (e) { toast("失败：" + e.message); }
    };
    no.onclick = async () => {
      try { await post("/api/goals/review", { personId: p.id, ok: false, reason: "请补充可量化口径" }); toast("已退回"); refresh(); }
      catch (e) { toast("失败：" + e.message); }
    };
    root.append(rc);
  }

  for (const g of p.goals) {
    const card = el("div", "goal");
    const top = el("div", "top");
    top.append(el("div", "text", g.text), el("div", "w", `权重 ${g.weight}% · ${g.version || "v1"}`));
    const stx = STATUS_TXT[g.status] || ["—", "grey"];
    top.append(el("span", "tag " + stx[1], stx[0]));
    card.append(top);

    const canAnchor = (g.status === "approved");
    if (st === "start" && canAnchor && !readOnly) {
      const acts = el("div", "acts");
      const bGen = el("button", "ghost", g.anchor ? "重新生成锚点" : "生成锚点");
      const bOk = el("button", "p", "确认锚点");
      bOk.disabled = !p.permissions.confirm || !g.anchor || g.status === "confirmed";
      acts.append(bGen);
      if (g.anchor) acts.append(bOk);
      card.append(acts);
      bGen.onclick = async () => {
        bGen.disabled = true; bGen.innerHTML = '<span class="spin"></span>生成中…';
        try {
          const r = await post("/api/anchors/async", { personId: p.id, goalId: g.id, force: true });
          JOBS[g.id] = r.jobId; saveJobs();
          pollJob(r.jobId, function (j) {
            delete JOBS[g.id]; saveJobs();
            if (j.status === "done") { toast("锚点已生成"); refresh(); }
            else { toast("生成失败：" + (j.error || "")); refresh(); }
          });
        } catch (e) {
          toast("失败：" + e.message); bGen.disabled = false; bGen.textContent = "生成锚点";
        }
      };
      bOk.onclick = async () => {
        try { await post("/api/confirm", { personId: p.id, goalId: g.id }); toast("已锁定"); refresh(); }
        catch (e) { toast("失败：" + e.message); }
      };
    }
    if (g.verdict === "block" && (!g.anchor)) {
      const box = el("div", "quote");
      box.style.borderLeftColor = "#C9734E";
      const tg = el("span", "tag red", "不可评");
      tg.style.marginRight = "8px";
      box.append(tg, document.createTextNode(g.problem || "该目标没有可验证的指标"));
      card.append(box);
      if (g.suggestions && g.suggestions.length) {
        const sl = el("div", "anchors");
        g.suggestions.forEach(function (sg, k) {
          const row = el("div", "anchor");
          row.append(el("div", null, (k + 1) + ". " + sg));
          sl.append(row);
        });
        card.append(el("div", "src", "建议改写（可发给员工替换）"));
        card.append(sl);
      }
      const actsB = el("div", "acts");
      const bReopen = el("button", "ghost", "退回让员工改写");
      bReopen.onclick = async function () {
        try {
          await post("/api/goals/reopen", { personId: p.id, goalId: g.id, reason: "目标无法量化，请按建议改写" });
          toast("已退回"); refresh();
        } catch (e) { toast("失败：" + e.message); }
      };
      actsB.append(bReopen);
      if (!g.qualitative) {
        const rq = el("input");
        rq.type = "text"; rq.placeholder = "定性理由（必填，≥6 字）"; rq.style.width = "220px";
        const bQ = el("button", "p", "标记为定性目标");
        bQ.onclick = async function () {
          try {
            const r = await post("/api/goals/qualitative", { personId: p.id, goalId: g.id, reason: rq.value });
            toast("已标记定性，定性权重已用 " + r.capUsed + "%");
            refresh();
          } catch (e) { toast("失败：" + e.message); }
        };
        actsB.append(rq, bQ);
      } else {
        actsB.append(el("span", "tag amber", "定性目标：" + (g.qualReason || "")));
      }
      card.append(actsB);
      root.append(card);
      continue;
    }

    if (g.anchor) {
      if (g.status === "confirmed") {
        const line = el("div", "kv");
        line.style.marginTop = "8px";
        line.append(el("span", "tag", "已确认"),
                    el("span", "muted", "锚点已锁定，年底按这三档打分"));
        card.append(line);
      }
      const pol = el("div", "acts");
      pol.style.marginBottom = "6px";
      if (!g.anchor.polished && !readOnly) {
        const bPol = el("button", "ghost", "AI 润色措辞");
        bPol.onclick = async function () {
          bPol.disabled = true; bPol.innerHTML = '<span class="spin"></span>润色中…';
          try {
            await post("/api/anchors/polish", { personId: p.id, goalId: g.id });
            toast("措辞已润色，数字没变"); refresh();
          } catch (e) { toast("失败：" + e.message); bPol.disabled = false; bPol.textContent = "AI 润色措辞"; }
        };
        pol.append(bPol);
        pol.append(el("span", "muted", "不改数字，只改说法"));
      } else {
        pol.append(el("span", "tag", "措辞已润色"), el("span", "muted", "数字仍由口径计算"));
      }
      card.append(pol);
      card.append(anchorBlock(g.anchor, g.selfcheck));
    }
    else if (st === "start" && !canAnchor) {
      card.append(el("div", "src", g.status === "submitted"
        ? "等待你审批目标；通过后才能生成锚点"
        : "目标还是草稿，等员工提交后再审批"));
    } else if (st !== "start") card.append(el("div", "src", "锚点未生成"));

    if (!g.anchor && JOBS[g.id]) {
      const wait = el("div", "quote");
      wait.innerHTML = '<span class="spin"></span>锚点正在后台生成，切走也不会中断；生成完会自动出现。';
      card.append(wait);
      pollJob(JOBS[g.id], function (j) {
        delete JOBS[g.id]; saveJobs();
        if (j.status === "done") toast("锚点已生成");
        else toast("生成失败：" + (j.error || ""));
        refresh();
      });
    }

    if (p.permissions.score && st === "end" && !(g.metrics && g.metrics.length)) {
      const line = el("div", "kv");
      line.style.marginTop = "8px";
      for (const rater of Object.keys(p.scores[g.id] || {})) {
        line.append(el("span", null, rater));
        const inp = el("input");
        inp.type = "number"; inp.min = 0; inp.max = 100; inp.step = 0.5;
        inp.style.width = "86px";
        inp.value = p.scores[g.id][rater] == null ? "" : p.scores[g.id][rater];
        const tag = el("span", "tag grey", "—");
        const sync = v => {
          const bd = bandOf(Number(v));
          tag.textContent = v === "" ? "—" : `${bd.key} 档 · ${bd.name}（${bd.hint}）`;
          tag.className = "tag " + (bd.key === "A" ? "" : bd.key === "C" ? "red" : "grey");
        };
        sync(inp.value);
        inp.oninput = () => sync(inp.value);
        inp.onchange = async () => {
          try {
            const r = await post("/api/score", { personId: p.id, goalId: g.id, value: inp.value });
            toast(`已保存，总分 ${fmt(r.total)}`); refresh();
          } catch (e) { toast("失败：" + e.message); }
        };
        line.append(inp, tag);
      }
      card.append(line);
    }
    root.append(card);
  }

  if (st === "end" && p.permissions.score) root.append(metricsCard(p, "mgr", refresh));
  if (p.permissions.reason && st === "end") root.append(reasonCard(p, p.id, refresh));

  if (p.permissions.approve_change && st === "mid" && p.changes && p.changes.length) {
    const cc = el("div", "card");
    cc.append(el("h2", null, "目标变更审批"));
    for (const c of p.changes) {
      const row = el("div", "audit-row");
      row.append(el("span", "t", c.ts), el("span", "tag " + (c.status === "待审批" ? "amber" : "grey"), c.status),
        (function () {
          const box = el("span");
          if (c.newText) box.append(el("b", null, c.newText), el("br"));
          box.append(el("span", "muted", "原因：" + c.text + "　佐证："));
          box.append(linkify(c.evidence));
          return box;
        })());
      if (c.status === "待审批") {
        const ok = el("button", "p", "批准");
        ok.style.marginLeft = "auto";
        ok.onclick = async () => {
          await post("/api/change/approve", { id: c.id, ok: true });
          toast("已批准，版本 +1"); refresh();
        };
        const no = el("button", "ghost", "驳回");
        no.onclick = async () => {
          await post("/api/change/approve", { id: c.id, ok: false });
          toast("已驳回"); refresh();
        };
        const box = el("span");
        box.append(ok, no);
        row.append(box);
      }
      cc.append(row);
    }
    root.append(cc);
  }
}

function selfSummaryCard(p) {
  const card = el("div", "card");
  const head = el("div", "head");
  head.append(el("h2", null, "年度自评"));
  const item = p.selfsummary;
  const minW = (S.health && S.health.config && S.health.config.summary_min_words) || 0;
  const ta = el("textarea");
  ta.style.minHeight = "220px";
  ta.value = item ? item.text : "";
  const counter = el("span", "muted", "");
  const sync = () => {
    const n = ta.value.trim().length;
    counter.textContent = (minW > 0)
      ? ("当前 " + n + " 字 / 要求不低于 " + minW + " 字")
      : ("当前 " + n + " 字（未设字数要求）");
    counter.style.color = (minW > 0 && n < minW) ? "#B4552B" : "#8A938E";
  };
  ta.oninput = sync;
  const bGen = el("button", "p", item ? "重新生成草稿" : "生成草稿");
  const bSave = el("button", "ghost", "保存");
  head.append(bGen, bSave);
  card.append(head);
  card.append(el("div", "sub", "从目标达成与具体事件起草，每句挂证据；改完再交。公司若要求字数，这里会显示还差多少。"));
  card.append(ta);
  const row = el("div", "acts");
  row.append(counter);
  card.append(row);
  card.append(el("div", "src", item ? ("来源：" + (item.model || "手动") + " · " + (item.ts || "")) : "尚未生成"));

  bGen.onclick = async () => {
    bGen.disabled = true; bGen.innerHTML = '<span class="spin"></span>生成中…';
    try {
      const r = await post("/api/selfsummary", { personId: p.id });
      ta.value = r.text; sync(); toast("草稿已生成，可继续修改");
    } catch (e) { toast("失败：" + e.message); }
    bGen.disabled = false; bGen.textContent = "重新生成草稿";
  };
  bSave.onclick = async () => {
    try {
      const r = await post("/api/selfsummary", { personId: p.id, text: ta.value });
      toast("已保存，" + r.words + " 字");
      if (minW > 0 && r.words < minW) toast("还差 " + (minW - r.words) + " 字才到公司要求");
    } catch (e) { toast("保存失败：" + e.message); }
  };
  sync();
  return card;
}

function reasonCard(p, pid, refresh) {
  const rc = el("div", "card");
  const rh = el("div", "head");
  rh.append(el("h2", null, "评级理由"));
  let bGen = null;
  if (p.permissions && p.permissions.reason) {
    bGen = el("button", "p", p.reason ? "重新生成" : "生成评级理由");
    rh.append(bGen);
  } else {
    rh.append(el("span", "muted", "理由由直属领导生成后可见"));
  }
  rc.append(rh);
  const rbox = el("div");
  rc.append(rbox);
  const draw = reason => {
    rbox.innerHTML = "";
    if (!reason) { rbox.append(el("div", "empty", "还没有生成理由")); return; }
    for (const s of reason.sentences) {
      const row = el("div", "reason-sent");
      const txt = el("div", "txt");
      txt.append(document.createTextNode(s.text));
      for (const c of s.cites) {
        const chip = el("span", "ref", c);
        chip.onclick = () => {
          const old = row.querySelector(".ev-box");
          if (old) { old.remove(); return; }
          row.append(el("div", "ev-box", describeCite(c, reason.mapping[c] || {})));
        };
        txt.append(chip);
      }
      row.append(txt);
      rbox.append(row);
    }
    rbox.append(el("div", "src", `来源：${reason.model} · ${reason.passed ? "自检通过" : "兜底模板"} · ${reason.ts} · 点编号看原文`));
  };
  draw(p.reason);
  if (bGen) bGen.onclick = async () => {
    bGen.disabled = true; bGen.innerHTML = '<span class="spin"></span>生成中…';
    try { const r = await post("/api/reason", { personId: pid }); draw(r); toast("已生成"); }
    catch (e) { toast("失败：" + e.message); }
    bGen.disabled = false; bGen.textContent = "重新生成";
  };
  return rc;
}

function anchorBlock(a, sc) {
  const box = el("div");
  if (a.problem) {
    const vmap = { ok: ["可评", ""], warn: ["口径待确认", "amber"], block: ["不可评", "red"] };
    const v = vmap[a.verdict] || ["口径检查", "grey"];
    const q = el("div", "quote");
    q.style.borderLeftColor = a.verdict === "block" ? "#C9734E" : (a.verdict === "warn" ? "#D9C07A" : "#C9D6CF");
    const tag = el("span", "tag " + v[1], v[0]);
    tag.style.marginRight = "8px";
    q.append(tag, document.createTextNode(a.problem));
    box.append(q);
  }
  const list = el("div", "anchors");
  for (const x of (a.anchors || []).slice().sort((m, n) => n.level - m.level)) {
    const bd = bandOfLevel(x.level);
    const c = el("div", "anchor");
    const lv = el("div", "lv");
    lv.append(el("span", "dot" + (x.level === 3 ? " d3" : x.level === 2 ? " d2" : "")),
      el("b", null, `${bd.key} 档 · ${bd.name}（${bd.hint} 分）`));
    const evText = String(x.evidence || "");
    c.append(lv, el("div", null, x.desc),
      el("div", "ev", evText.indexOf("证据") === 0 ? evText : ("证据：" + evText)));
    list.append(c);
  }
  box.append(list);
  if (sc) {
    const line = el("div", "selfcheck");
    line.append(el("span", sc.passed ? "sc-ok" : "sc-bad",
      sc.passed ? `✓ 自检通过 · 尝试 ${sc.attempts.length} 次 · ${sc.elapsed}s`
                : `× 自检未通过 · ${sc.attempts.length} 次`));
    const last = sc.attempts && sc.attempts.length ? sc.attempts[sc.attempts.length - 1] : null;
    if (last && last.issues && last.issues.length) {
      line.append(el("span", "muted", "问题：" + last.issues.slice(0, 2).join("；")));
    }
    box.append(line);
  }
  return box;
}

function describeCite(key, m) {
  if (m.type === "goal") {
    const bd = bandOfLevel(m.level);
    return `${key}｜目标「${m.text}」权重 ${m.weight}%，本次得分 ${m.score}，落在 ${bd.key} 档（${bd.hint}）。`;
  }
  if (m.type === "event") return `${key}｜具体事件：${m.text}（${m.date || "2026"}）。`;
  if (m.type === "rank") return `${key}｜部门排名 ${m.rank}/${m.of}（同部门同口径排序）。`;
  if (m.type === "quota") return `${key}｜本部门 A 级名额 ${m.quota} 人，本次评级 ${m.rating}。`;
  if (m.type === "delta") return `${key}｜与 A 线的差距：${m.delta} 分。`;
  return key;
}

/* ---------------- 启动 ---------------- */
async function buildPersonas() {
  const list = [];
  list.push({ raw: "hr", label: "HR · 管理员" });
  let people = [];
  try {
    const r = await fetch("/api/people", { headers: { "X-As": "hr" } });
    people = await r.json();
  } catch (e) { people = []; }
  const mgrs = [];
  const dirs = [];
  people.filter(function (p) { return !p.isManager; }).forEach(function (p) {
    if (mgrs.indexOf(p.manager) < 0) mgrs.push(p.manager);
    if (p.director && dirs.indexOf(p.director) < 0) dirs.push(p.director);
  });
  dirs.forEach(function (d) { list.push({ raw: "skip:" + d, label: d + " · 隔级领导" }); });
  mgrs.forEach(function (m) {
    const n = people.filter(function (x) { return x.manager === m; }).length;
    const self = people.filter(function (x) { return x.isManager && x.name === m; })[0];
    const deptName = self ? self.dept : "";
    list.push({ raw: "mgr:" + m,
      label: m + (deptName ? " · " + deptName + "领导" : " · 直属领导") + "（管 " + n + " 人）" });
  });
  people.filter(function (p) { return !p.isManager; }).slice(0, 3).forEach(function (p) {
    list.push({ raw: "emp:" + p.id, label: p.name + " · 员工（" + p.dept + "）" });
  });
  const valid = list.filter(function (x) { return x.raw === S.persona; }).length > 0;
  if (!valid) { S.persona = "hr"; localStorage.setItem("ys_persona", "hr"); }
  PERSONAS = list;
}

function setMode(chatMode) {
  const shell = document.getElementById("shell");
  const appEl = document.getElementById("app");
  const nav = document.getElementById("nav");
  if (!shell || !appEl) return;
  if (chatMode) {
    shell.classList.remove("hidden");
    appEl.style.display = "none";
    if (nav) nav.style.display = "none";
  } else {
    shell.classList.add("hidden");
    appEl.style.display = "";
    if (nav) nav.style.display = "";
  }
}
window.setMode = setMode;

async function boot() {
  await buildPersonas();
  const sel = $("#persona");
  for (const pr of PERSONAS) {
    const o = el("option", null, pr.label);
    o.value = pr.raw;
    if (pr.raw === S.persona) o.selected = true;
    sel.append(o);
  }
  sel.onchange = async () => {
    S.persona = sel.value;
    localStorage.setItem("ys_persona", S.persona);
    location.hash = "";
    await loadHealth();
    await afterHealth();
  };
  await loadHealth();
  await afterHealth();
}

async function afterHealth() {
  const role = (S.health && S.health.persona && S.health.persona.role) || "";
  if (role === "hr" || role === "invalid") {
    setMode(false);
    route();
    return;
  }
  setMode(true);
  const who = document.getElementById("chatWho");
  if (who && S.health) who.textContent = "（" + S.health.persona.label + " · " +
    (STAGE_LABEL[S.health.config.stage] || "") + "）";
  if (window.bootChat) await window.bootChat();
}

async function loadHealth() {
  try {
    S.health = await api("/api/health");
    const p = $("#health");
    p.textContent = S.health.persona.label + " · " + (STAGE_LABEL[S.health.config.stage] || "");
    p.className = "pill";
  } catch (e) {
    $("#health").textContent = "后端未启动";
    $("#health").className = "pill bad";
  }
}
boot();

function metricsCard(p, mode, refresh) {
  const goals = p.goals.filter(function (g) { return g.metrics && g.metrics.length; });
  if (!goals.length) return el("div");
  const card = el("div", "card");
  card.append(el("h2", null, mode === "emp" ? "指标填报（可计算的部分）" : "指标核验"));
  card.append(el("div", "sub", mode === "emp"
    ? "指标达标情况由数据决定：填实际值并附佐证，领导核验后系统自动算分，不需要人打分。"
    : "员工报的值在这里核验：通过就用员工的值；改值必须写理由，全程留痕。"));

  goals.forEach(function (g) {
    const head = el("div", "head");
    head.style.marginTop = "12px";
    head.append(el("h3", null, g.text), el("span", "tag grey", "权重 " + g.weight + "%"));
    card.append(head);

    p.metrics[g.id].forEach(function (m) {
      const box = el("div", "goal");
      box.style.padding = "10px 12px";
      const top = el("div", "top");
      const rule = [];
      rule.push(m.unit + " · 目标 " + m.target);
      if (m.a_line != null && m.a_line !== m.target) rule.push("优秀 " + m.a_line);
      if (m.c_line != null) rule.push("不合格 " + m.c_line);
      if (m.type === "gate") rule.push("守门型");
      if (m.proxy) rule.push("代理指标（最高 90 分）");
      top.append(el("div", "text", m.name), el("div", "w", rule.join(" · ")));
      box.append(top);
      box.append(el("div", "src", "口径来源：" + (m.source || "—") + "　方向："
        + (m.type === "higher" ? "越高越好" : (m.type === "gate" ? "不达标即封顶" : "越低越好"))));

      const act = m.actual || {};
      const stMap = { missing: ["未上报", "grey"], pending: ["待核验", "amber"],
        verified: ["已核验", ""], rejected: ["已退回", "red"] };
      const stx = stMap[m.state] || ["—", "grey"];
      const line = el("div", "kv");
      line.append(el("span", "tag " + stx[1], stx[0]));

      if (mode === "emp") {
        const inp = el("input");
        inp.type = "number"; inp.step = "0.01"; inp.style.width = "100px";
        inp.value = act.value != null ? act.value : "";
        inp.placeholder = "实际值";
        const ev = el("input");
        ev.type = "text"; ev.style.flex = "1"; ev.placeholder = "佐证：报表链接或说明";
        ev.value = act.evidence || "";
        const b = el("button", "p", "上报核验");
        b.onclick = async function () {
          try {
            await post("/api/metric/report", {
              personId: p.id, goalId: g.id, metricId: m.id,
              value: inp.value, evidence: ev.value
            });
            toast(m.name + " 已上报，等领导核验");
            if (refresh) refresh();
          } catch (e) { toast("失败：" + e.message); }
        };
        line.append(inp, ev, b);
      } else {
        line.append(el("span", null, "员工报 " + (act.reported != null ? act.reported : "—") + m.unit));
        if (act.changedFrom != null) line.append(el("span", "tag amber", "改为 " + act.value + m.unit));
        if (m.state === "pending") {
          const okB = el("button", "p", "通过");
          const nv = el("input");
          nv.type = "number"; nv.step = "0.01"; nv.style.width = "90px"; nv.placeholder = "改值";
          const rs = el("input");
          rs.type = "text"; rs.style.width = "180px"; rs.placeholder = "改值理由（必填）";
          const noB = el("button", "ghost", "改值并核验");
          okB.onclick = async function () {
            try {
              await post("/api/metric/verify", { personId: p.id, goalId: g.id, metricId: m.id, ok: true });
              toast("已通过"); if (refresh) refresh();
            } catch (e) { toast("失败：" + e.message); }
          };
          noB.onclick = async function () {
            try {
              await post("/api/metric/verify", { personId: p.id, goalId: g.id, metricId: m.id,
                ok: false, value: nv.value, reason: rs.value });
              toast("已改值"); if (refresh) refresh();
            } catch (e) { toast("失败：" + e.message); }
          };
          line.append(okB, nv, rs, noB);
        } else if (m.score != null) {
          line.append(el("span", "tag", "自动得分 " + fmt(m.score)));
        }
      }
      box.append(line);
      if (act.reason) box.append(el("div", "src", "改值理由：" + act.reason));
      card.append(box);
    });

    const det = (p.rating && p.rating.detail ? p.rating.detail : []).filter(function (x) {
      return x.goalId === g.id;
    })[0];
    if (det) {
      card.append(el("div", "src", "本目标得分：" + fmt(det.score)
        + (det.source === "auto" ? "（由指标自动计算）" : "（人工评分）")));
    }
  });
  return card;
}

/* 对话模式需要用到这些（const 声明不会挂到 window 上，显式导出） */
window.S = S;
window.post = post;
window.api = api;
window.stage = stage;
window.navFor = navFor;
window.fmt = fmt;
window.STAGE_LABEL = STAGE_LABEL;
window.STATUS_TXT = STATUS_TXT;

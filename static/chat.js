/* 准绳 · 对话逻辑（Left chat / Right panel） */
(function () {
  const qs = (s) => document.querySelector(s);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };

  let history = [];
  let curPersona = null;
  let curPage = "me";
  let renderToken = 0;
  let booting = false;

  function storeKey() { return "ys_chat_" + curPersona; }

  function loadHistory() {
    try { history = JSON.parse(localStorage.getItem(storeKey()) || "[]"); }
    catch (e) { history = []; }
    if (!Array.isArray(history)) history = [];
  }
  function saveHistory() {
    try { localStorage.setItem(storeKey(), JSON.stringify(history.slice(-40))); } catch (e) {}
  }

  function bubble(role, text) {
    const log = qs("#chatlog");
    const b = el("div", "bubble " + role);
    b.textContent = text;
    log.append(b);
    log.scrollTop = log.scrollHeight;
    return b;
  }

  function actionCard(action, refreshPanel) {
    const log = qs("#chatlog");
    const card = el("div", "actioncard");
    const names = {
      submit_goals: "提交目标审批", approve_goals: "批准目标",
      gen_anchor: "生成锚点", confirm_anchor: "确认锚点",
      score: "打分", open_page: "打开页面", edit_anchor: "修改锚点", add_goal: "新增目标", update_goal: "修改目标", withdraw_goals: "撤回目标", change_request: "提交变更申请",
    };
    card.append(el("div", "t", "建议操作：" + (names[action.type] || action.type)));
    card.append(el("div", "d", action.detail || "确认后立即执行"));
    const acts = el("div", "acts");
    const ok = el("button", "p", action.type === "open_page" ? "打开" : "确认执行");
    const no = el("button", "ghost", "不用了");
    acts.append(ok, no);
    card.append(acts);
    log.append(card);
    log.scrollTop = log.scrollHeight;

    no.onclick = function () {
      card.remove();
      bubble("sys", "已取消");
    };
    let busy = false;
    ok.onclick = async function () {
      if (busy) return;
      busy = true;
      ok.disabled = true;
      no.disabled = true;
      try {
        const S = window.S;
        if (action.type === "open_page") {
          await renderPanel(action.page || "me");
        } else if (action.type === "submit_goals") {
          const pid = action.personId || (S.health.persona.selfId);
          await post("/api/goals/submit", { personId: pid });
          bubble("sys", "已提交给领导审批");
        } else if (action.type === "approve_goals") {
          await post("/api/goals/review", { personId: action.personId, ok: true });
          bubble("sys", "已批准 " + (action.personName || "") + " 的目标");
        } else if (action.type === "gen_anchor") {
          await post("/api/anchors", { personId: action.personId, goalId: action.goalId, force: true });
          bubble("sys", "锚点已生成");
        } else if (action.type === "confirm_anchor") {
          await post("/api/confirm", { personId: action.personId, goalId: action.goalId });
          bubble("sys", "锚点已确认，目标锁定");
        } else if (action.type === "change_request") {
          await post("/api/change", {
            personId: action.personId, goalId: action.goalId,
            text: action.text || action.detail || "年中调整",
            evidence: action.evidence || "",
            newText: action.newText || "", fields: action.fields || {} });
          bubble("sys", "变更申请已提交，等领导审批");
        } else if (action.type === "withdraw_goals") {
          await post("/api/goals/withdraw", { personId: action.personId });
          bubble("sys", "已撤回，可以重新修改后再提交");
        } else if (action.type === "update_goal") {
          await post("/api/goals/update", {
            personId: action.personId, goalId: action.goalId,
            text: action.text || "", fields: action.fields || {}, why: action.detail || "" });
          bubble("sys", "目标已更新");
        } else if (action.type === "add_goal") {
          await post("/api/goals/add", {
            personId: action.personId, text: action.text,
            fields: action.fields || {}, weight: action.weight || 100 });
          bubble("sys", "目标已添加，右侧能看到，填完口径就能提交");
        } else if (action.type === "edit_anchor") {
          await post("/api/anchors/edit", {
            personId: action.personId, goalId: action.goalId,
            level: action.level, desc: action.desc });
          bubble("sys", "锚点已修改");
        } else if (action.type === "score") {
          await post("/api/score", {
            personId: action.personId, goalId: action.goalId, value: action.value });
          bubble("sys", "已打分 " + action.value);
        }
        card.remove();
        window.__panelDirty = false;
        await renderPanel(curPage);
        // 右侧切换后，把最新的待办回灌给界面提示
        const st2 = window.stage();
        const role2 = (window.S.health.persona || {}).role || "";
        buildChips(role2, st2, refreshPanel);
        syncPanelChips();
      } catch (e) {
        // 失败只报一次，卡片保留但没有按钮可再点
        let msg = e.message || "执行失败";
        try { msg = JSON.parse(msg).error || msg; } catch (x) {}
        bubble("sys", "没能完成：" + msg);
        ok.remove();
        no.remove();
        card.querySelectorAll(".feet button").forEach(function (b) { b.remove(); });
        const tip = el("div", "d", "这条操作没生效，数据没有变化。");
        tip.style.marginTop = "8px";
        card.append(tip);
      }
    };
  }

  function choiceRow(choices, refreshPanel, anchor, question) {
    const log = qs("#chatlog");
    const card = el("div", "choicecard");

    card.append(el("div", "ck", "选择题"));
    card.append(el("div", "cq", question || "选一个，或者自己补充"));
    if (!choices.length) {
      card.append(el("div", "od", "这道题没有标准选项，直接写下你的答案："));
    }

    let picked = -1;
    const rows = [];
    choices.forEach(function (c, i) {
      const row = el("div", "opt");
      const num = el("div", "num", String(i + 1));
      const body = el("div");
      let title = c, desc = "";
      if (typeof c === "object" && c) { title = c.title || ""; desc = c.desc || ""; }
      else if (String(c).indexOf("｜") > 0) {
        const parts = String(c).split("｜");
        title = parts[0]; desc = parts[1] || "";
      }
      body.append(el("div", "ot", title));
      if (desc) body.append(el("div", "od", desc));
      row.append(num, body);
      row.onclick = function () {
        picked = i;
        rows.forEach(function (r, k) { r.className = "opt" + (k === i ? " on" : ""); });
        customInput.value = "";
      };
      rows.push(row);
      card.append(row);
    });

    const custom = el("div", "custom");
    custom.append(el("span", null, "✎"));
    const customInput = document.createElement("input");
    customInput.placeholder = "输入你的答案";
    customInput.oninput = function () {
      if (customInput.value.trim()) {
        picked = -1;
        rows.forEach(function (r) { r.className = "opt"; });
      }
    };
    custom.append(customInput);
    card.append(custom);

    const feet = el("div", "feet");
    const skip = el("button", null, "跳过");
    const ok = el("button", "p", "提交");
    ok.disabled = true;
    feet.append(skip, ok);
    card.append(feet);

    const syncOk = function () {
      ok.disabled = !(picked >= 0 || customInput.value.trim());
    };
    customInput.oninput = function () {
      if (customInput.value.trim()) {
        picked = -1;
        rows.forEach(function (r) { r.className = "opt"; });
      }
      syncOk();
    };
    rows.forEach(function (r) {
      r.addEventListener("click", syncOk);
    });

    skip.onclick = function () {
      card.remove();
      bubble("sys", "已跳过");
    };
    ok.onclick = function () {
      const answer = customInput.value.trim() ||
        (picked >= 0 ? (rows[picked].querySelector(".ot") || {}).textContent : "");
      if (!answer) return;
      card.remove();
      send(answer, refreshPanel);
    };

    log.append(card);
    log.scrollTop = log.scrollHeight;
  }

  async function send(text, refreshPanel) {
    const log = qs("#chatlog");
    bubble("user", text);
    history.push({ role: "user", content: text });
    const think = bubble("ai", "");
    think.innerHTML = '<span class="spin"></span>想一想…';
    try {
      const r = await post("/api/chat", { message: text, history: history.slice(-8) });
      const reply = r.reply || "（没听清）";
      think.textContent = reply;
      if (r.model) {
        const mtag = el("div", "bubble sys");
        mtag.style.alignSelf = "flex-start";
        mtag.style.fontSize = "10.5px";
        mtag.style.opacity = ".7";
        mtag.textContent = "· " + r.model;
        qs("#chatlog").append(mtag);
      }
      history.push({ role: "assistant", content: reply });
      saveHistory();
      const _q = (r.question || "").trim();
      const _hasQ = /[？?]/.test(_q) || /[？?]/.test((r.reply || ""));
      if ((r.choices && r.choices.length) || (_hasQ && _q)) {
        choiceRow(r.choices || [], refreshPanel, think, _q || r.reply);
      }
      // 每轮对话后静默刷新右侧，保证界面和服务端数据一致
      if (!r.action && !window.__panelDirty) { try { await renderPanel(curPage); } catch (e) {} }
      if (r.page && PAGES[r.page] && r.page !== curPage) {
        await renderPanel(r.page);
        buildChips((window.S.health.persona || {}).role || "", window.stage(), refreshPanel);
      }
      if (r.action) actionCard(r.action, refreshPanel);
    } catch (e) {
      think.textContent = "连不上后台了：" + e.message;
    }
    log.scrollTop = log.scrollHeight;
  }

  // 右侧面板：复用现有页面
  const PAGES = {
    me: "viewMe", team: "viewTeam", approve: "viewApprove", changes: "viewChanges",
    change: "viewChange", org: "viewOrg", appeals: "viewMyAppeals",
    hrapp: "viewHRAppeals", audit: "viewAudit",
  };

  async function renderPanel(page) {
    const panel = qs("#panel");
    if (!panel) return;
    curPage = page;
    const myToken = ++renderToken;
    panel.innerHTML = '<div class="empty">加载中…</div>';
    const fn = PAGES[page] || PAGES.me;
    try {
      const f = window[fn];
      if (typeof f !== "function") { panel.innerHTML = '<div class="empty">页面不存在：' + page + '</div>'; return; }
      if (myToken !== renderToken) return;   // 有更新的渲染，丢弃这次
      panel.innerHTML = "";
      await f(panel);
      if (myToken !== renderToken) return;
      document.querySelectorAll("#panelChips button").forEach(function (b) {
        b.className = b.dataset.page === page ? "p" : "ghost";
      });
    } catch (e) {
      panel.innerHTML = '<div class="empty">加载失败：' + e.message + '</div>';
    }
  }

  function buildChips(role, st, refreshPanel) {
    const box = qs("#panelChips");
    box.innerHTML = "";
    const items = (window.navFor ? window.navFor(role, st) : [["me", "我的绩效"]])
      .filter(function (x) { return x[0] !== "hr"; });
    items.forEach(function (x, i) {
      const b = el("button", i === 0 ? "p" : "ghost", x[1]);
      b.dataset.page = x[0];
      b.onclick = function () { renderPanel(x[0]); };
      box.append(b);
    });
    syncPanelChips(items);
    return items;
  }

  function syncPanelChips(items) {
    const box = qs("#panelChips");
    if (!box) return;
    Array.prototype.forEach.call(box.querySelectorAll("button"), function (b) {
      b.className = (b.dataset.page === curPage) ? "p" : "ghost";
    });
  }

  async function bootChat() {
    if (booting) return;
    booting = true;
    try {
    const S = window.S;
    curPersona = S.persona;
    loadHistory();

    const log = qs("#chatlog");
    log.innerHTML = "";
    history.forEach(function (h) { bubble(h.role === "user" ? "user" : "ai", h.content); });
    if (!history.length) {
      bubble("ai", "我是准绳的助手。你的目标、锚点和分数我都能查，也能帮你提交或调整。\n\n不确定从哪开始的话，可以直接问「我现在该干什么」。");
    }

    const refreshPanel = async function () {
      const st = window.stage();
      const role = S.health.persona.role;
      const items = buildChips(role, st, refreshPanel);
      await renderPanel((items && items[0] && items[0][0]) || "me");
    };

    const first = buildChips(S.health.persona.role, window.stage(), refreshPanel);
    await renderPanel((first && first[0] && first[0][0]) || "me");

    const sendBtn = qs("#sendBtn");
    const ta = qs("#chatInput");
    const doSend = function () {
      const v = (ta.value || "").trim();
      if (!v) return;
      ta.value = "";
      send(v, refreshPanel);
    };
    sendBtn.onclick = doSend;
    ta.onkeydown = function (e) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); doSend(); }
    };
    } finally { booting = false; }
  }

  window.bootChat = bootChat;
  window.renderPanel = renderPanel;
})();

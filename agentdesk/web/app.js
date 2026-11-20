// agentdesk 界面：拉 /api/state 整体重绘，监听 /api/events；实时输出单独追加，不整体重绘。
const token = new URLSearchParams(location.search).get("t");
let S = null;          // 最近一次 /api/state
let sel = null;        // {kind: "req" | "task", id}
let tab = "log";       // 任务详情的页签
let dirty = false;     // 用户正在输入时推迟刷新

const $ = (q) => document.querySelector(q);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const split = (s) => (s || "").split(/[,，\s]+/).filter(Boolean);

const STATUS = { draft: "草稿", queued: "排队", running: "运行中", done: "待审查", reviewing: "审查中", passed: "合格", rejected: "不合格", failed: "失败", stopped: "已停止", interrupted: "中断", discarded: "已丢弃", merged: "已合并" };
const ICON = { draft: "✎", queued: "⏳", running: "▶", done: "👀", reviewing: "🔍", passed: "✅", rejected: "❌", failed: "⚠", stopped: "■", interrupted: "⚡", discarded: "🗑", merged: "🔀" };
const REQ = { planning: "Claude 正在拆任务…", confirm_plan: "⏸ 等你确认：任务拆分", running: "执行中", confirm_merge: "⏸ 等你确认：合并", merged: "已合并" };
const MERGE = { ok: "合并完成", test_failed: "已合并，但全量测试没通过", conflict: "合并冲突，已恢复原状", refused: "没有合并", undone: "已撤销合并", kept: "已保留合并" };

async function api(path, body) {
  const opt = body === undefined
    ? { headers: { "X-Token": token } }
    : { method: "POST", headers: { "X-Token": token, "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const data = (r.headers.get("Content-Type") || "").includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error(data.error || data);
  return data;
}
async function act(path, body) {
  try { await api(path, body ?? {}); await refresh(); } catch (e) { alert(e.message); }
}
async function refresh() { S = await api("/api/state"); render(); }

const agentOf = (key) => (S.agents || []).find((a) => a.key === key);
const task = (id) => S.tasks.find((t) => t.id === id);
const req = (id) => S.requirements.find((r) => r.id === id);

function agentSelect(value) {
  return `<select name="agent">${S.agents.map((a) => `<option value="${a.key}" ${a.key === value ? "selected" : ""} ${a.ok === false ? "disabled" : ""}>${esc(a.label)}${a.ok === false ? "（不可用：" + esc(a.reason) + "）" : a.ok === null ? "（检测中）" : ""}</option>`).join("")}</select>`;
}
function modelSelect(agentKey, value) {
  return `<select name="model">${(agentOf(agentKey)?.models || []).map((m) => `<option ${m === value ? "selected" : ""}>${esc(m)}</option>`).join("")}</select>`;
}
function levelSelect(agentKey, value) {
  const a = agentOf(agentKey);
  return `<select name="level">${Object.entries(S.levels).map(([k, v]) => `<option value="${k}" ${k === value ? "selected" : ""} ${a && !a.levels.includes(k) ? "disabled" : ""}>${v}</option>`).join("")}</select>`;
}
function taskFields(t) {
  const usable = S.agents.find((a) => a.ok !== false) || S.agents[0];
  const agent = t.agent || usable.key;
  return `<label>执行者 ${agentSelect(agent)}</label>
    <label>模型 ${modelSelect(agent, t.model || agentOf(agent).default_model)}</label>
    <label>权限 ${levelSelect(agent, t.level || "edit")}</label>
    <label>说明<textarea name="prompt" rows="10">${esc(t.prompt || "")}</textarea></label>`;
}

function render() {
  $("#project").value = S.project || $("#project").value;
  $("#recent").innerHTML = S.recent.map((p) => `<option value="${esc(p)}">`).join("");
  $("#branch").textContent = S.project ? `分支：${S.branch}` : "请先打开项目";
  $("#running").textContent = S.project ? `正在跑 ${S.tasks.filter((t) => t.status === "running").length} / 最多同时 ${S.max_parallel}` : "";
  renderList();
  renderDetail();
}

function renderList() {
  if (!S.project) { $("#list").innerHTML = ""; return; }
  const item = (t) => `<div class="item ${sel?.id === t.id ? "sel" : ""}" data-task="${t.id}">${ICON[t.status]} ${esc(t.title)} <small>${esc(agentOf(t.agent)?.label || t.agent)}</small></div>`;
  let html = S.requirements.slice().reverse().map((r) => `<div class="req">
      <div class="item ${sel?.id === r.id ? "sel" : ""}" data-req="${r.id}"><b>${esc(r.text.slice(0, 40))}</b><br><small>${REQ[r.status]}</small></div>
      ${r.tasks.map(task).filter(Boolean).map(item).join("")}</div>`).join("");
  const manual = S.tasks.filter((t) => !t.requirement);
  if (manual.length) html += `<div class="req"><div class="head">手动任务</div>${manual.slice().reverse().map(item).join("")}</div>`;
  $("#list").innerHTML = html;
}

function renderDetail() {
  const box = $("#detail");
  if (!S.project) { box.innerHTML = "<p>在上方输入或选择一个 git 仓库目录，点“打开”。</p>"; return; }
  if (sel?.kind === "req" && req(sel.id)) { box.innerHTML = reqView(req(sel.id)); return; }
  if (sel?.kind === "task" && task(sel.id)) { box.innerHTML = taskView(task(sel.id)); loadTab(); return; }
  box.innerHTML = "<p>左侧选择需求或任务，或点上方按钮新建。</p>";
}

function mergeView(m, kind) {
  if (!m) return "";
  let h = `<h3>${MERGE[m.state] || esc(m.state)}</h3>`;
  if (m.message) h += `<pre>${esc(m.message)}</pre>`;
  if (m.conflicts?.length) h += `<p>冲突文件：${m.conflicts.map(esc).join("、")}（任务 ${esc(m.conflict_task)}，可以“从头重做”后再合并）</p>`;
  if (m.test_output) h += `<details ${m.state === "test_failed" ? "open" : ""}><summary>全量测试输出（退出码 ${m.test_code}）</summary><pre>${esc(m.test_output)}</pre></details>`;
  if (m.state === "test_failed") h += `<button data-undo="${kind}">撤销这次合并</button> <button data-keep="${kind}">保留合并</button>`;
  return h;
}

function reqView(r) {
  let h = `<h2>${esc(r.text)}</h2><p class="state">${REQ[r.status]}</p>`;
  if (r.error) h += `<p class="err">${esc(r.error)}</p>`;
  if (r.plan_raw) h += `<details><summary>Claude 原文</summary><pre>${esc(r.plan_raw)}</pre></details>`;
  const tasks = r.tasks.map(task).filter(Boolean);
  if (r.status === "confirm_plan") {
    h += `<table class="plan"><tr><th>任务号</th><th>标题</th><th>执行者</th><th>模型</th><th>权限</th><th>依赖</th><th>验收命令</th><th></th></tr>`;
    for (const t of tasks) {
      h += `<tr data-row data-id="${t.id}"><td>${t.id}</td><td><input name="title" value="${esc(t.title)}"></td>
        <td>${agentSelect(t.agent)}</td><td>${modelSelect(t.agent, t.model)}</td><td>${levelSelect(t.agent, t.level)}</td>
        <td><input name="after" value="${esc(t.after.join(","))}" size="10"></td><td><input name="accept" value="${esc(t.accept)}"></td>
        <td><button data-del="${t.id}">删除</button></td></tr>
        <tr data-prompt="${t.id}"><td colspan="8"><textarea name="prompt" rows="4">${esc(t.prompt)}</textarea>
        ${t.problems.length ? `<div class="err">${t.problems.map(esc).join("；")}</div>` : ""}</td></tr>`;
    }
    h += `</table><p><button id="add-draft">＋ 加一个任务</button> <button id="start" class="primary">开始</button></p>`;
  } else {
    h += `<ul>${tasks.map((t) => `<li>${ICON[t.status]} ${t.id} ${esc(t.title)} — ${STATUS[t.status]}${t.status === "queued" && t.after.length ? `（等待 ${t.after.join("、")}）` : ""}</li>`).join("")}</ul>`;
  }
  if (r.status === "confirm_merge") h += `<h3>Claude 的总结</h3><pre>${esc(r.summary)}</pre><button id="merge-req" class="primary">合并</button>`;
  return h + mergeView(r.merge, "req");
}

function taskView(t) {
  const a = agentOf(t.agent);
  const st = t.status;
  const btn = (id, text, show) => (show ? `<button id="${id}">${text}</button> ` : "");
  let h = `<h2>${t.id} ${esc(t.title)}</h2><p class="state">${ICON[st]} ${STATUS[st]} · ${esc(a?.label || t.agent)} / ${esc(t.model)} / ${S.levels[t.level]} · 第 ${t.attempts} 次</p>`;
  if (t.error) h += `<p class="err">${esc(t.error)}</p>`;
  if (st === "rejected" && t.attempts >= 3) h += `<p class="err">已经重派 2 次还不合格，建议你自己处理。</p>`;
  h += `<div class="actions">
    ${btn("stop", "停止", ["running", "queued"].includes(st))}
    ${btn("retry", "重派…", ["failed", "rejected", "stopped", "interrupted", "done", "passed"].includes(st))}
    ${btn("review", "交给 Claude 审查", !t.requirement && st === "done")}
    ${btn("approve", "我确认合格", ["done", "rejected"].includes(st))}
    ${btn("merge-task", "合并", !t.requirement && ["done", "passed"].includes(st))}
    ${btn("discard", "丢弃", !["running", "reviewing", "merged", "discarded", "draft"].includes(st))}</div>`;
  h += `<div class="tabs">${[["prompt", "说明"], ["log", "实时输出"], ["diff", "改动"], ["review", "Claude 审查"]].map(([k, v]) => `<button data-tab="${k}" class="${tab === k ? "on" : ""}">${v}</button>`).join("")}</div><pre id="tabbody"></pre>`;
  return h + (t.requirement ? "" : mergeView(t.merge, "task"));
}

async function loadTab() {
  const t = task(sel.id);
  const body = $("#tabbody");
  if (tab === "prompt") {
    body.textContent = t.prompt + (t.accept ? `\n\n验收命令：${t.accept}` : "") + (t.accept_code !== null ? `\n验收退出码：${t.accept_code}\n${t.accept_output}` : "");
  } else if (tab === "review") {
    const r = t.review;
    body.textContent = r ? `${r.verdict === "pass" ? "合格" : "不合格"}\n${r.reasons.join("\n")}${r.retry_prompt ? "\n\n建议的新说明：\n" + r.retry_prompt : ""}` : "还没有审查";
  } else {
    const text = await api(`/api/tasks/${t.id}/${tab}`);
    body.textContent = text || (tab === "diff" ? "没有改动" : "还没有输出");
    if (tab === "log") body.scrollTop = body.scrollHeight;
  }
}

function dialog(title, inner, onOk) {
  const d = $("#dlg");
  d.innerHTML = `<form method="dialog" data-row><h3>${title}</h3>${inner}<menu><button value="cancel">取消</button><button value="ok" class="primary">确定</button></menu></form>`;
  d.returnValue = "";
  d.showModal();
  d.onclose = async () => {
    if (d.returnValue !== "ok") return;
    const f = Object.fromEntries(new FormData(d.querySelector("form")));
    try { await onOk(f); await refresh(); } catch (e) { alert(e.message); }
  };
}

const accept = (v = "") => `<label>验收命令（可空）<input name="accept" value="${esc(v)}" size="60"></label>`;

document.addEventListener("click", async (e) => {
  const el = e.target.closest("button, .item");
  if (!el || el.closest("dialog")) return;
  if (el.dataset.req) { sel = { kind: "req", id: el.dataset.req }; return render(); }
  if (el.dataset.task) { sel = { kind: "task", id: el.dataset.task }; tab = "log"; return render(); }
  if (el.dataset.tab) { tab = el.dataset.tab; return render(); }
  if (el.dataset.del) return act(`/api/tasks/${el.dataset.del}/delete`);
  const owner = sel?.kind === "req" ? `/api/requirements/${sel.id}` : `/api/tasks/${sel?.id}`;
  if (el.dataset.undo) return confirm("回到合并前的提交？只回退这次合并产生的提交。") && act(`${owner}/undo`);
  if (el.dataset.keep) return act(`${owner}/keep`);
  const t = sel?.kind === "task" ? task(sel.id) : null;
  switch (el.id) {
    case "open": return act("/api/open", { path: $("#project").value.trim() });
    case "new-req":
      if (!S.project) return alert("请先打开项目");
      return dialog("新需求给 Claude", `<textarea name="text" rows="8" placeholder="描述你要做的事"></textarea>`,
        async (f) => { const r = await api("/api/requirements", f); sel = { kind: "req", id: r.id }; });
    case "new-task":
      if (!S.project) return alert("请先打开项目");
      return dialog("手动派活", `<label>标题（可空）<input name="title" size="40"></label>${taskFields({})}${accept()}`,
        async (f) => { const r = await api("/api/tasks", f); sel = { kind: "task", id: r.id }; tab = "log"; });
    case "add-draft":
      return dialog("加一个任务", `<label>标题<input name="title" size="40"></label>${taskFields({})}<label>依赖（任务号，逗号分隔）<input name="after"></label>${accept()}`,
        (f) => api(`/api/requirements/${sel.id}/tasks`, { ...f, after: split(f.after) }));
    case "start": return act(`/api/requirements/${sel.id}/start`);
    case "merge-req": return confirm("把合格的任务合并进当前分支？") && act(`/api/requirements/${sel.id}/merge`);
    case "stop": return act(`/api/tasks/${t.id}/stop`);
    case "review": return act(`/api/tasks/${t.id}/review`);
    case "approve": return act(`/api/tasks/${t.id}/approve`);
    case "merge-task": return confirm("把这个任务的改动合并进当前分支？") && act(`/api/tasks/${t.id}/merge`);
    case "discard": return confirm("丢弃这个任务和它的改动？") && act(`/api/tasks/${t.id}/discard`);
    case "retry":
      return dialog("重派", `${taskFields({ ...t, prompt: t.review?.retry_prompt || t.prompt })}
        <label><input type="checkbox" name="keep" checked> 在已有改动上继续（不勾选则从头重做）</label>`,
        (f) => api(`/api/tasks/${t.id}/retry`, { ...f, keep: f.keep === "on" }));
  }
});

document.addEventListener("change", (e) => {
  const row = e.target.closest("[data-row]");
  if (e.target.name === "agent" && row) {
    const a = agentOf(e.target.value);
    row.querySelector("[name=model]").outerHTML = modelSelect(a.key, a.default_model);
    const lv = row.querySelector("[name=level]");
    lv.outerHTML = levelSelect(a.key, a.levels.includes(lv.value) ? lv.value : a.levels[0]);
  }
  if (e.target.closest("dialog")) return;
  const id = e.target.closest("tr[data-id]")?.dataset.id || e.target.closest("tr[data-prompt]")?.dataset.prompt;
  if (!id) return;
  const tr = document.querySelector(`tr[data-id="${id}"]`);
  const v = (name) => tr.querySelector(`[name=${name}]`).value;
  act(`/api/tasks/${id}/update`, {
    title: v("title"), agent: v("agent"), model: v("model"), level: v("level"), accept: v("accept"),
    after: split(v("after")), prompt: document.querySelector(`tr[data-prompt="${id}"] textarea`).value,
  });
});

const editing = () => !!document.activeElement?.closest("#detail input, #detail textarea, #detail select");
document.addEventListener("focusout", () => { if (dirty) { dirty = false; setTimeout(refresh, 0); } });

function connect() {
  const es = new EventSource(`/api/events?t=${encodeURIComponent(token)}`);
  let timer = null;
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (ev.project && S && ev.project !== S.project) return;
    if (ev.type === "log") {
      const body = $("#tabbody");
      if (body && sel?.id === ev.task && tab === "log") {
        const stick = body.scrollTop + body.clientHeight >= body.scrollHeight - 20;
        body.textContent += ev.line + "\n";
        if (stick) body.scrollTop = body.scrollHeight;
      }
      return;
    }
    if (ev.type === "error") console.warn(ev.message);
    clearTimeout(timer);
    timer = setTimeout(() => (editing() ? (dirty = true) : refresh()), 200);
  };
  es.onerror = () => { es.close(); setTimeout(() => refresh().then(connect).catch(connect), 2000); };
}

refresh().then(connect).catch((e) => { document.body.textContent = e.message; });

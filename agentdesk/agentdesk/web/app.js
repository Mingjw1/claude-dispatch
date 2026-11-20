// agentdesk 界面：拉 /api/state 整体重绘，监听 /api/events；实时输出单独追加，不整体重绘。
const token = new URLSearchParams(location.search).get("t");
let S = null;          // 最近一次 /api/state
let sel = null;        // {kind: "req" | "task", id}
let tab = "log";       // 任务详情的页签
let dirty = false;     // 用户正在输入时推迟刷新

const $ = (q) => document.querySelector(q);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// 状态 → [文字, 颜色]
const STATUS = {
  draft: ["草稿", ""], queued: ["排队", ""], running: ["运行中", "blue running"], done: ["待审查", "amber"],
  reviewing: ["审查中", "purple running"], passed: ["合格", "green"], rejected: ["不合格", "red"], failed: ["失败", "red"],
  stopped: ["已停止", ""], interrupted: ["中断", "amber"], discarded: ["已丢弃", ""], merged: ["已合并", "green"],
};
const REQ = {
  planning: ["Claude 拆任务中", "purple running"], confirm_plan: ["等你确认拆分", "amber"], running: ["执行中", "blue running"],
  confirm_merge: ["等你确认合并", "amber"], merged: ["已合并", "green"],
};
const MERGE = { ok: "已合并进当前分支", test_failed: "已合并，但全量测试没通过", conflict: "合并冲突", refused: "没有合并",
  failed: "合并没有完成", undone: "已撤销这次合并", kept: "已保留合并" };
const badge = ([text, cls]) => `<span class="badge ${cls}">${text}</span>`;

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
  // 合并、跑测试可能要几分钟：请求期间禁用所有按钮，防止连点
  document.querySelectorAll("button").forEach((b) => { b.disabled = true; });
  document.body.style.cursor = "progress";
  try { await api(path, body ?? {}); } catch (e) { alert(e.message); }
  document.body.style.cursor = "";
  document.querySelectorAll("header button").forEach((b) => { b.disabled = false; });   // 顶栏不会重绘，要手动恢复
  await refresh().catch((e) => alert(e.message));
}
async function refresh() { S = await api("/api/state"); render(); }

const agentOf = (key) => (S.agents || []).find((a) => a.key === key);
const label = (key) => agentOf(key)?.label || key;
const task = (id) => S.tasks.find((t) => t.id === id);
const req = (id) => S.requirements.find((r) => r.id === id);
const baseName = (p) => p.replace(/[\\/]+$/, "").split(/[\\/]/).pop();

function elapsed(since) {
  const s = Math.max(0, Math.floor(Date.now() / 1000 - since));
  return s < 60 ? `${s} 秒` : s < 3600 ? `${Math.floor(s / 60)} 分 ${s % 60} 秒` : `${Math.floor(s / 3600)} 小时 ${Math.floor(s / 60) % 60} 分`;
}
const timer = (t) => (t.status === "running" && t.started ? `<span data-started="${t.started}">已运行 ${elapsed(t.started)}</span>` : "");
setInterval(() => document.querySelectorAll("[data-started]").forEach((el) => { el.textContent = `已运行 ${elapsed(+el.dataset.started)}`; }), 1000);

// ---------- 什么事在等用户
function waiting() {
  const out = [];
  for (const r of S.requirements) {
    if (r.status === "confirm_plan") out.push({ kind: "req", id: r.id, why: "确认拆分" });
    else if (r.status === "confirm_merge") out.push({ kind: "req", id: r.id, why: "确认合并" });
    else if (r.merge?.state === "test_failed") out.push({ kind: "req", id: r.id, why: "测试没通过：撤销或保留" });
  }
  for (const t of S.tasks) {
    if (["rejected", "failed", "interrupted"].includes(t.status)) out.push({ kind: "task", id: t.id, why: STATUS[t.status][0] + "：重派或处理" });
    else if (!t.requirement && t.status === "done") out.push({ kind: "task", id: t.id, why: "看改动后合并或丢弃" });
    else if (!t.requirement && t.merge?.state === "test_failed") out.push({ kind: "task", id: t.id, why: "测试没通过：撤销或保留" });
    else if (t.requirement && t.status === "done" && t.error) out.push({ kind: "task", id: t.id, why: "审查没完成：重新审查或确认" });
  }
  return out;
}

function render() {
  $("#project-name").textContent = S.project ? baseName(S.project) : "选择项目";
  $("#project-btn").title = S.project || "选择项目";
  $("#branch").textContent = S.project ? `· ${S.branch}` : "";
  const running = S.project ? S.tasks.filter((t) => t.status === "running").length : 0;
  $("#running").textContent = S.project ? `运行中 ${running} / ${S.max_parallel}` : "";
  $("#running").className = "pill" + (running ? " on" : "");
  if (S.project && !(sel && (sel.kind === "req" ? req(sel.id) : task(sel.id)))) {
    const first = waiting()[0];
    const latest = [...S.requirements.map((r) => ({ kind: "req", id: r.id })), ...S.tasks.filter((t) => !t.requirement).map((t) => ({ kind: "task", id: t.id }))].pop();
    sel = first || latest || null;
    tab = "log";
  }
  renderList();
  renderDetail();
}

function taskItem(t, child) {
  const sub = [label(t.agent), t.model, timer(t)].filter(Boolean).join(" · ");
  return `<div class="item ${child ? "child" : ""} ${sel?.id === t.id ? "sel" : ""}" data-task="${esc(t.id)}">
    <div class="title"><span>${esc(t.title)}</span>${badge(STATUS[t.status])}</div><div class="sub">${sub}</div></div>`;
}

function renderList() {
  if (!S.project) { $("#list").innerHTML = ""; return; }
  const w = waiting();
  let h = "";
  if (w.length) {
    h += `<div class="group">待你处理<span class="count">${w.length}</span></div>`;
    h += w.map((x) => {
      const o = x.kind === "req" ? req(x.id) : task(x.id);
      return `<div class="item ${sel?.id === x.id ? "sel" : ""}" data-${x.kind}="${esc(x.id)}">
        <div class="title"><span>${esc(o.text || o.title)}</span></div><div class="sub">${esc(x.why)}</div></div>`;
    }).join("");
  }
  h += `<div class="group">需求</div>`;
  h += S.requirements.length ? S.requirements.slice().reverse().map((r) => `
    <div class="item req ${sel?.id === r.id ? "sel" : ""}" data-req="${esc(r.id)}">
      <div class="title"><span>${esc(r.text)}</span>${badge(REQ[r.status])}</div></div>
    ${r.tasks.map(task).filter(Boolean).map((t) => taskItem(t, true)).join("")}`).join("")
    : `<div class="empty">点右上角“新需求给 Claude”，Claude 会拆成任务分给各个执行者。</div>`;
  const manual = S.tasks.filter((t) => !t.requirement);
  h += `<div class="group">手动任务</div>`;
  h += manual.length ? manual.slice().reverse().map((t) => taskItem(t, false)).join("")
    : `<div class="empty">点“手动派活”，自己选执行者和模型。</div>`;
  $("#list").innerHTML = h;
}

function renderDetail() {
  const box = $("#detail");
  if (!S.project) {
    box.innerHTML = `<div class="welcome"><h2>先选一个项目</h2><p>任意本地 git 仓库都可以。每个任务会在仓库旁边单独建一个 worktree 执行，合并前都会问你。</p>
      <button class="primary" id="pick">选择文件夹…</button></div>`;
    return;
  }
  if (sel?.kind === "req" && req(sel.id)) { box.innerHTML = reqView(req(sel.id)); return; }
  if (sel?.kind === "task" && task(sel.id)) { box.innerHTML = taskView(task(sel.id)); loadTab(); return; }
  box.innerHTML = `<div class="welcome"><h2>还没有任务</h2><p>新需求交给 Claude 拆分，或者自己手动派活。</p></div>`;
}

function mergeView(m, owner) {
  if (!m) return "";
  const color = { ok: "green", kept: "green", undone: "blue", test_failed: "red", conflict: "red", failed: "red", refused: "amber" }[m.state] || "blue";
  let h = `<div class="alert ${color}"><b>${MERGE[m.state] || esc(m.state)}</b>`;
  if (m.message) h += `\n${esc(m.message)}`;
  if (m.conflicts?.length) h += `\n冲突文件：${m.conflicts.map(esc).join("、")}`;
  h += `</div>`;
  if (m.test_output) h += `<details ${m.state === "test_failed" ? "open" : ""}><summary>全量测试输出（退出码 ${m.test_code}）</summary><pre class="panel code">${esc(m.test_output)}</pre></details>`;
  if (m.state === "test_failed") h += `<p><button data-undo="${owner}">撤销这次合并</button> <button data-keep="${owner}">保留合并</button></p>`;
  return h;
}

function reqView(r) {
  const tasks = r.tasks.map(task).filter(Boolean);
  const actions = r.status === "confirm_plan" ? `<button id="add-draft">＋ 加一个任务</button><button id="start" class="primary">开始执行 ${tasks.length} 个任务</button>`
    : r.status === "confirm_merge" ? `<button id="merge-req" class="primary">合并进 ${esc(S.branch)}</button>` : "";
  let h = `<div class="head"><h2>${esc(r.text)}</h2><div class="actions">${actions}</div></div>
    <div class="meta">${badge(REQ[r.status])}<span>${tasks.length} 个任务</span></div>`;
  if (r.error) h += `<div class="alert red">${esc(r.error)}</div>`;
  if (r.plan_raw) h += `<details><summary>Claude 的原始回答</summary><pre class="panel code">${esc(r.plan_raw)}</pre></details>`;
  if (r.status === "planning") h += `<div class="alert blue">Claude 正在读代码、拆任务，完成后会在这里列出来让你确认。</div>`;
  if (r.status === "confirm_plan") {
    h += `<div class="alert amber">确认每个任务的执行者、模型和权限，可以改说明、删任务、加任务。改完点“开始执行”。</div>`;
    h += tasks.map((t, i) => draftCard(t, i, tasks)).join("");
  } else {
    if (r.status === "confirm_merge") h += `<h3>Claude 的总结</h3><div class="card summary">${esc(r.summary)}</div><h3>任务</h3>`;
    h += `<div class="tasklist">${tasks.map((t) => `<div class="card" data-task="${esc(t.id)}">
      <div class="name"><b>${esc(t.title)}</b><div class="sub">${esc(label(t.agent))} · ${esc(t.model)} · ${S.levels[t.level]}
      ${t.after.length ? ` · 依赖 ${t.after.map((a) => esc(task(a)?.title || a)).join("、")}` : ""}
      ${t.review?.reasons?.length ? ` · ${esc(t.review.reasons[0])}` : ""} ${timer(t)}</div></div>
      ${badge(STATUS[t.status])}<button class="link">查看 ›</button></div>`).join("")}</div>`;
  }
  return h + mergeView(r.merge, "req");
}

function draftCard(t, i, all) {
  const others = all.filter((o) => o.id !== t.id);
  const a = agentOf(t.agent);
  return `<div class="card ${t.problems.length ? "bad" : ""}" data-draft="${esc(t.id)}">
    <div class="row"><span class="num">任务 ${i + 1}</span><input name="title" value="${esc(t.title)}"><button class="danger" data-del="${esc(t.id)}">删除</button></div>
    <div class="fields">
      <label>执行者${agentSelect(t.agent)}</label>
      <label>模型${modelSelect(t.agent, a?.models.includes(t.model) ? t.model : t.model)}</label>
      <label>权限${levelSelect(t.agent, t.level)}</label>
      <label>验收命令<input name="accept" value="${esc(t.accept)}" placeholder="可空"></label>
    </div>
    ${others.length ? `<div class="meta">先完成：${others.map((o) => `<label><input type="checkbox" name="after" value="${esc(o.id)}" ${t.after.includes(o.id) ? "checked" : ""}> ${esc(o.title)}</label>`).join(" ")}</div>` : ""}
    <textarea name="prompt" rows="4">${esc(t.prompt)}</textarea>
    ${t.problems.length ? `<div class="problems">${t.problems.map(esc).join("；")}</div>` : ""}
  </div>`;
}

function agentSelect(value) {
  return `<select name="agent">${S.agents.map((a) => `<option value="${esc(a.key)}" ${a.key === value ? "selected" : ""} ${a.ok === false ? "disabled" : ""}>${esc(a.label)}${a.ok === false ? "（不可用：" + esc(a.reason) + "）" : a.ok === null ? "（检测中）" : ""}</option>`).join("")}</select>`;
}
function modelSelect(agentKey, value) {
  const models = agentOf(agentKey)?.models || [];
  const list = value && !models.includes(value) ? [value, ...models] : models;   // 配置里没有的模型也显示出来，方便看出问题
  return `<select name="model">${list.map((m) => `<option ${m === value ? "selected" : ""}>${esc(m)}</option>`).join("")}</select>`;
}
function levelSelect(agentKey, value) {
  const a = agentOf(agentKey);
  return `<select name="level">${Object.entries(S.levels).map(([k, v]) => `<option value="${k}" ${k === value ? "selected" : ""} ${a && !a.levels.includes(k) ? "disabled" : ""}>${v}</option>`).join("")}</select>`;
}

function taskView(t) {
  const st = t.status;
  const btn = (id, text, show, cls = "") => (show ? `<button id="${id}" class="${cls}">${text}</button>` : "");
  const actions = [
    btn("stop", "停止", ["running", "queued"].includes(st)),
    btn("discard", "丢弃", !["running", "reviewing", "merged", "discarded", "draft"].includes(st), "danger"),
    btn("approve", "我确认合格", ["done", "rejected"].includes(st)),
    btn("review", "交给 Claude 审查", st === "done"),
    btn("retry", "重派…", ["failed", "rejected", "stopped", "interrupted", "done", "passed"].includes(st), ["failed", "rejected", "interrupted", "stopped"].includes(st) ? "primary" : ""),
    btn("merge-task", `合并进 ${esc(S.branch)}`, !t.requirement && ["done", "passed"].includes(st), "primary"),
  ].join("");
  const r = t.requirement && req(t.requirement);
  let h = `<div class="head"><h2>${esc(t.title)}</h2><div class="actions">${actions}</div></div>
    <div class="meta">${badge(STATUS[st])}<span class="chip">${esc(label(t.agent))}</span><span class="chip">${esc(t.model)}</span>
    <span class="chip">${S.levels[t.level]}</span><span>第 ${t.attempts} 次</span>${timer(t)}
    ${r ? `<span>· 属于需求 <button class="link" data-req="${esc(r.id)}">${esc(r.text.slice(0, 30))}</button></span>` : ""}</div>`;
  if (t.error) h += `<div class="alert red">${esc(t.error)}</div>`;
  if (st === "rejected" && t.attempts >= 3) h += `<div class="alert amber">已经重派 2 次还不合格，建议你自己处理。</div>`;
  if (t.review && ["passed", "rejected"].includes(st)) {
    h += `<div class="alert ${st === "passed" ? "green" : "red"}"><b>Claude 审查：${st === "passed" ? "合格" : "不合格"}</b>\n${t.review.reasons.map(esc).join("\n")}</div>`;
  }
  if (t.accept_code !== null) h += `<div class="meta">验收命令 <code class="cmd" title="${esc(t.accept)}">${esc(t.accept)}</code> ${t.accept_code === 0 ? badge(["通过", "green"]) : badge([`退出码 ${t.accept_code}`, "red"])}</div>`;
  h += `<div class="tabs">${[["log", "实时输出"], ["diff", "改动"], ["prompt", "任务说明"], ["review", "审查详情"]].map(([k, v]) => `<button data-tab="${k}" class="${tab === k ? "on" : ""}">${v}</button>`).join("")}</div>
    <div id="tabbody" class="panel"></div>`;
  return h + (t.requirement ? "" : mergeView(t.merge, "task"));
}

function logLine(line) {
  const cls = line.startsWith("=====") ? "sys" : line.startsWith("> ") ? "echo" : "";
  return cls ? `<span class="${cls}">${esc(line)}</span>` : esc(line);
}
function diffHtml(text) {
  return text.split("\n").map((l) => {
    const cls = l.startsWith("diff --git") ? "file" : l.startsWith("@@") ? "hunk" : l.startsWith("+") && !l.startsWith("+++") ? "add"
      : l.startsWith("-") && !l.startsWith("---") ? "del" : "";
    return cls ? `<span class="${cls}">${esc(l)}</span>` : esc(l) + "\n";
  }).join("");
}

async function loadTab() {
  const t = task(sel.id);
  const body = $("#tabbody");
  body.className = "panel" + (tab === "log" ? " term" : tab === "diff" ? " code" : "");
  if (tab === "prompt") {
    body.textContent = t.prompt + (t.accept ? `\n\n验收命令：${t.accept}` : "") + (t.accept_code !== null ? `\n\n验收输出：\n${t.accept_output}` : "");
  } else if (tab === "review") {
    const r = t.review;
    body.textContent = r ? `${r.verdict === "pass" ? "合格" : "不合格"}\n\n${r.reasons.join("\n")}${r.retry_prompt ? "\n\n建议的新说明：\n" + r.retry_prompt : ""}` : "还没有审查";
  } else {
    const text = await api(`/api/tasks/${t.id}/${tab}`);
    if (tab === "log") {
      body.innerHTML = text ? text.replace(/\n$/, "").split("\n").map(logLine).join("\n") + "\n" : `<span class="echo">还没有输出</span>`;
      body.scrollTop = body.scrollHeight;
    } else {
      body.innerHTML = text ? diffHtml(text) : `<span class="muted">没有改动</span>`;
    }
  }
}

// ---------- 弹窗
function dialog(title, inner, okText, onOk) {
  const d = $("#dlg");
  d.innerHTML = `<form method="dialog" data-form><h3>${title}</h3><div class="form">${inner}</div>
    <menu><span class="hint">Ctrl+Enter 确定</span><button value="cancel">取消</button><button value="ok" class="primary">${okText}</button></menu></form>`;
  d.returnValue = "";
  d.showModal();
  d.querySelector("textarea, input")?.focus();
  d.onclose = async () => {
    if (d.returnValue !== "ok") return;
    const form = d.querySelector("form");
    const f = Object.fromEntries(new FormData(form));
    f.after = [...form.querySelectorAll("[name=after]:checked")].map((c) => c.value);
    try { await onOk(f); await refresh(); } catch (e) { alert(e.message); }
  };
}
document.addEventListener("keydown", (e) => {
  const d = $("#dlg");
  if (d.open && e.key === "Enter" && e.ctrlKey) { e.preventDefault(); d.close("ok"); }
  if (e.key === "Escape") $("#popover").hidden = true;
});

function taskFields(t) {
  const usable = S.agents.find((a) => a.ok !== false) || S.agents[0];
  const agent = t.agent || usable.key;
  return `<div class="three"><label>执行者${agentSelect(agent)}</label><label>模型${modelSelect(agent, t.model || agentOf(agent).default_model)}</label>
    <label>权限${levelSelect(agent, t.level || "edit")}</label></div>
    <label>任务说明（执行者看不到我们的对话，写清目标、涉及文件和约束）<textarea name="prompt" rows="9">${esc(t.prompt || "")}</textarea></label>`;
}
const acceptField = (v = "") => `<label>验收命令（可空，在 worktree 根目录执行，退出码 0 算通过）<input name="accept" value="${esc(v)}"></label>`;

// ---------- 项目切换
async function openProject(path) {
  if (!path) return;
  $("#popover").hidden = true;
  sel = null;
  await act("/api/open", { path });
}
async function pickFolder() {
  try { const r = await api("/api/pick-folder", {}); if (r.path) await openProject(r.path); } catch (e) { alert(e.message); }
}
function showProjects() {
  const p = $("#popover");
  p.innerHTML = `${S.recent.map((x) => `<div class="recent" data-open="${esc(x)}"><b>${esc(baseName(x))}</b><small>${esc(x)}</small></div>`).join("") || `<div class="empty">还没有打开过项目</div>`}
    <div class="row"><input id="path" placeholder="或者粘贴路径，例如 D:\\code\\my-repo"><button id="open-path">打开</button><button class="primary" id="pick">选择文件夹…</button></div>`;
  p.hidden = !p.hidden;
}

// ---------- 事件
document.addEventListener("click", async (e) => {
  const pop = $("#popover");
  if (!pop.hidden && !e.target.closest("#popover, #project-btn")) pop.hidden = true;
  const el = e.target.closest("button, .item, .tasklist .card, .recent");
  if (!el || el.closest("dialog")) return;
  if (el.dataset.open) return openProject(el.dataset.open);
  if (el.dataset.req) { sel = { kind: "req", id: el.dataset.req }; return render(); }
  if (el.dataset.task) { sel = { kind: "task", id: el.dataset.task }; tab = "log"; return render(); }
  if (el.dataset.tab) { tab = el.dataset.tab; return render(); }
  if (el.dataset.del) return act(`/api/tasks/${el.dataset.del}/delete`);
  const owner = sel?.kind === "req" ? `/api/requirements/${sel.id}` : `/api/tasks/${sel?.id}`;
  if (el.dataset.undo) return confirm("回到合并前的提交？只回退这次合并产生的提交。") && act(`${owner}/undo`);
  if (el.dataset.keep) return act(`${owner}/keep`);
  const t = sel?.kind === "task" ? task(sel.id) : null;
  switch (el.id) {
    case "project-btn": return showProjects();
    case "pick": return pickFolder();
    case "open-path": return openProject($("#path").value.trim().replace(/^"|"$/g, ""));
    case "new-req":
      if (!S.project) return pickFolder();
      return dialog("新需求给 Claude", `<label>要做什么（Claude 会读代码，拆成任务分给各执行者，拆完先给你确认）
        <textarea name="text" rows="9" placeholder="例如：给 calc.py 加 mul 和 div，div 除数为 0 时抛 ValueError，并各写一个测试文件"></textarea></label>`,
        "交给 Claude 拆任务", async (f) => { const r = await api("/api/requirements", f); sel = { kind: "req", id: r.id }; });
    case "new-task":
      if (!S.project) return pickFolder();
      return dialog("手动派活", `<label>标题（可空，默认取说明第一行）<input name="title"></label>${taskFields({})}${acceptField()}`,
        "派出", async (f) => { const r = await api("/api/tasks", f); sel = { kind: "task", id: r.id }; tab = "log"; });
    case "add-draft": {
      const others = req(sel.id).tasks.map(task).filter(Boolean);
      return dialog("加一个任务", `<label>标题<input name="title"></label>${taskFields({})}
        ${others.length ? `<label>先完成<span>${others.map((o) => `<label class="check"><input type="checkbox" name="after" value="${esc(o.id)}"> ${esc(o.title)}</label>`).join("")}</span></label>` : ""}${acceptField()}`,
        "加入", (f) => api(`/api/requirements/${sel.id}/tasks`, f));
    }
    case "start": return act(`/api/requirements/${sel.id}/start`);
    case "merge-req": return confirm(`把合格的任务合并进 ${S.branch}？`) && act(`/api/requirements/${sel.id}/merge`);
    case "stop": return act(`/api/tasks/${t.id}/stop`);
    case "review": return act(`/api/tasks/${t.id}/review`);
    case "approve": return act(`/api/tasks/${t.id}/approve`);
    case "merge-task": return confirm(`把这个任务的改动合并进 ${S.branch}？`) && act(`/api/tasks/${t.id}/merge`);
    case "discard": return confirm("丢弃这个任务和它的改动？") && act(`/api/tasks/${t.id}/discard`);
    case "retry":
      return dialog("重派", `${taskFields({ ...t, prompt: t.review?.retry_prompt || t.prompt })}
        <label class="check"><input type="checkbox" name="keep" checked> 在已有改动上继续（不勾选则丢掉改动、从最新代码重做）</label>`,
        "重派", (f) => api(`/api/tasks/${t.id}/retry`, { ...f, keep: f.keep === "on" }));
  }
});

document.addEventListener("change", (e) => {
  const scope = e.target.closest("[data-draft], [data-form]");
  if (!scope) return;
  if (e.target.name === "agent") {   // 换执行者时，模型和权限跟着换成它支持的
    const a = agentOf(e.target.value);
    scope.querySelector("[name=model]").outerHTML = modelSelect(a.key, a.default_model);
    const lv = scope.querySelector("[name=level]");
    lv.outerHTML = levelSelect(a.key, a.levels.includes(lv.value) ? lv.value : a.levels[0]);
  }
  if (!scope.dataset.draft) return;
  const v = (name) => scope.querySelector(`[name=${name}]`).value;
  act(`/api/tasks/${scope.dataset.draft}/update`, {
    title: v("title"), agent: v("agent"), model: v("model"), level: v("level"), accept: v("accept"), prompt: v("prompt"),
    after: [...scope.querySelectorAll("[name=after]:checked")].map((c) => c.value),
  });
});

const editing = () => !!document.activeElement?.closest("#detail input, #detail textarea, #detail select");
document.addEventListener("focusout", () => { if (dirty) { dirty = false; setTimeout(refresh, 0); } });

function connect() {
  const es = new EventSource(`/api/events?t=${encodeURIComponent(token)}`);
  let pending = null;
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (ev.project && S && ev.project !== S.project) return;
    if (ev.type === "log") {
      const body = $("#tabbody");
      if (body && sel?.id === ev.task && tab === "log") {
        const stick = body.scrollTop + body.clientHeight >= body.scrollHeight - 20;
        body.insertAdjacentHTML("beforeend", logLine(ev.line) + "\n");
        if (stick) body.scrollTop = body.scrollHeight;
      }
      return;
    }
    if (ev.type === "error") console.warn(ev.message);
    clearTimeout(pending);
    pending = setTimeout(() => (editing() ? (dirty = true) : refresh()), 200);
  };
  es.onerror = () => { es.close(); sleep(2000).then(() => refresh().then(connect).catch(connect)); };
}

refresh().then(connect).catch((e) => { document.body.textContent = e.message; });

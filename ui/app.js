// RocoAgent · 战术台
// 原生 JS，无框架。所有轮询都带"当前视图 / 页面可见性"判断，
// 不在后台空转；日志批量插入，避免几千个节点逐条 append。
"use strict";

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const MAX_LINES = 1500;

const S = {
  view: "log",
  offset: 0,
  lines: [],          // { t, m, cls }
  follow: true,
  filter: "",
  status: null,
  results: null,
  turns: null,
  portraits: { total: 0, items: [] },
  portraitOffset: 0,
  portraitFilter: "",
};

// ───────────────────────── 工具 ─────────────────────────

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}

async function postJSON(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return r.json();
}

const pct = (rate) => (rate == null ? "—" : (rate * 100).toFixed(1) + "%");
const clamp = (v) => (v == null ? 0 : Math.max(0, Math.min(100, v)));

// ───────────────────────── 视图切换 ─────────────────────────

const VIEW_META = {
  log:       ["实时日志", "机器人在做什么，逐行看"],
  results:   ["战绩", "胜负账本与段位变化"],
  turns:     ["回合", "每一手打了什么、为什么这么打"],
  portraits: ["头像标注", "认不出的对手，给它们名字"],
  control:   ["控制", "启停机器人与运行环境"],
};

function switchView(name) {
  S.view = name;
  $$(".nav-item").forEach((b) => b.classList.toggle("is-on", b.dataset.view === name));
  $$(".view").forEach((v) => v.classList.toggle("is-on", v.id === "view-" + name));
  const [t, sub] = VIEW_META[name] || ["", ""];
  $("#view-title").textContent = t;
  $("#view-sub").textContent = sub;

  if (name === "results") loadResults();
  if (name === "turns") loadTurns();
  if (name === "portraits") loadPortraits(true);
  if (name === "log") { pollLogs(); if (S.follow) scrollLog(true); }
}

$$(".nav-item").forEach((b) => b.addEventListener("click", () => switchView(b.dataset.view)));

// ───────────────────────── 状态 ─────────────────────────

function renderStatus() {
  const s = S.status;
  if (!s) return;
  const t = s.today || {};

  $("#v-state").textContent = s.running ? "运行中" : "已停止";
  $("#v-state").style.color = s.running ? "var(--win)" : "var(--muted)";
  $("#v-pid").textContent = s.pids && s.pids.length ? s.pids.join(", ") : "—";
  $("#v-last").textContent = s.last_log_time || "—";
  $("#v-today").textContent = (t.win || 0) + "W " + (t.loss || 0) + "L";
  $("#v-rate").textContent = t.decided ? pct(t.rate) : "—";

  const led = $("#led");
  led.className = "led" + (s.running ? " is-run" : "");
  $("#led-text").textContent = s.running ? "运行中" : "已停止";

  const ctl = $("#ctl-led");
  if (ctl) ctl.className = "led" + (s.running ? " is-run" : "");
  const ct = $("#ctl-title");
  if (ct) ct.textContent = s.running ? "机器人运行中" : "机器人已停止";
  const cs = $("#ctl-sub");
  if (cs) {
    cs.textContent = s.running
      ? "PID " + (s.pids || []).join(", ") + "　最后输出 " + (s.last_log_time || "—")
      : "点击启动，开始接管对局";
  }

  $("#nv-rate").textContent = t.decided ? pct(t.rate) : "—";
  const ago = s.seconds_ago;
  $("#v-last").title = ago == null ? "" : "距今 " + ago + " 秒";
  if ($("#ctl-cwd") && s.src_dir) $("#ctl-cwd").textContent = s.src_dir;
}

async function pollStatus() {
  if (document.hidden) return;
  try {
    S.status = await getJSON("/api/status");
    renderStatus();
  } catch (e) { /* 服务重载中，静默 */ }
}

// ───────────────────────── 日志 ─────────────────────────

const KW = [
  ["PERCEPTION ERROR", "err"],
  ["RESULT win", "win"],
  ["RESULT loss", "loss"],
  ["LEADER", "warn"],
  ["FAINT", "warn"],
  ["PET ->", "info"],
  ["ERROR", "err"],
];

const KW_RE = new RegExp(
  "(" + KW.map(([k]) => k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|") + ")", "gi"
);
const KW_CLS = Object.fromEntries(KW.map(([k, c]) => [k.toLowerCase(), c]));

function classify(msg) {
  const up = msg.toUpperCase();
  for (const [k, c] of KW) {
    if (up.includes(k.toUpperCase())) return c;
  }
  return "";
}

function decorate(msg) {
  return esc(msg).replace(KW_RE, (m) => {
    const cls = KW_CLS[m.toLowerCase()] || "";
    return '<mark class="' + cls + '">' + m + "</mark>";
  });
}

function parseLine(raw) {
  const m = /^(\d{2}:\d{2}:\d{2})\s+(.*)$/.exec(raw);
  const t = m ? m[1] : "";
  const msg = m ? m[2] : raw;
  return { t, m: msg, cls: classify(msg) };
}

function lineHTML(ln) {
  return '<div class="log-line ' + (ln.cls ? "l-" + ln.cls : "") + '">' +
    '<span class="t">' + esc(ln.t) + "</span>" +
    '<span class="m">' + decorate(ln.m) + "</span></div>";
}

function matchesFilter(ln) {
  if (!S.filter) return true;
  return (ln.t + " " + ln.m).toLowerCase().includes(S.filter);
}

function renderLog() {
  const view = $("#log-view");
  const html = S.lines.filter(matchesFilter).map(lineHTML).join("");
  view.innerHTML = html;
  const shown = S.lines.filter(matchesFilter).length;
  $("#log-hint").textContent =
    (S.filter ? "筛选 " + shown + " / " : "") + S.lines.length + " 行";
  $("#nv-log").textContent = S.lines.length;
  if (S.follow) scrollLog(true);
}

function appendLog(lines) {
  const view = $("#log-view");
  for (const ln of lines) S.lines.push(ln);
  if (S.lines.length > MAX_LINES) S.lines = S.lines.slice(-MAX_LINES);

  if (!S.filter) {
    view.insertAdjacentHTML("beforeend", lines.map(lineHTML).join(""));
    while (view.childElementCount > MAX_LINES) view.removeChild(view.firstChild);
  } else {
    renderLog();
    return;
  }
  $("#log-hint").textContent = S.lines.length + " 行";
  $("#nv-log").textContent = S.lines.length;
  if (S.follow) scrollLog(false);
}

function scrollLog(force) {
  const v = $("#log-view");
  if (!v) return;
  const nearBottom = v.scrollHeight - v.scrollTop - v.clientHeight < 120;
  if (force || nearBottom) v.scrollTop = v.scrollHeight;
}

async function pollLogs() {
  if (document.hidden || S.view !== "log") return;
  try {
    const d = await getJSON("/api/logs?offset=" + S.offset);
    S.offset = d.offset;
    if (d.lines && d.lines.length) {
      appendLog(d.lines.map(parseLine));
      if (d.truncated) $("#log-hint").textContent += "（仅末尾）";
    }
  } catch (e) { /* 静默 */ }
}

$("#log-follow").addEventListener("change", (e) => {
  S.follow = e.target.checked;
  if (S.follow) scrollLog(true);
});

let filterTimer = 0;
$("#log-filter").addEventListener("input", (e) => {
  clearTimeout(filterTimer);
  const v = e.target.value.trim().toLowerCase();
  filterTimer = setTimeout(() => { S.filter = v; renderLog(); }, 120);
});

$("#log-clear").addEventListener("click", () => {
  S.lines = [];
  $("#log-view").innerHTML = "";
  $("#log-hint").textContent = "0 行";
  $("#nv-log").textContent = "0";
});

// 用户手动往上滚，就取消跟随；滚回底部再恢复
$("#log-view").addEventListener("scroll", () => {
  const v = $("#log-view");
  const atBottom = v.scrollHeight - v.scrollTop - v.clientHeight < 40;
  if (atBottom !== S.follow) {
    S.follow = atBottom;
    $("#log-follow").checked = atBottom;
  }
});

// ───────────────────────── 战绩 ─────────────────────────

function statCard(k, v, note, accent) {
  return '<div class="stat" style="--accent:' + (accent || "transparent") + '">' +
    '<span class="stat-k">' + esc(k) + "</span>" +
    '<div class="stat-v">' + v + "</div>" +
    '<div class="stat-note">' + esc(note || "") + "</div></div>";
}

function renderResults() {
  const d = S.results;
  if (!d) return;
  const t = d.today || {};
  const r = d.recent20 || {};

  const flatPct = t.count ? t.flat / t.count : 0;
  $("#result-stats").innerHTML =
    statCard("累计战绩",
      t.win + '<span class="unit"> W</span> ' + t.loss + '<span class="unit"> L</span>',
      t.flat + " 场未判定　共 " + t.count + " 场", "var(--amber)") +
    statCard("累计胜率", t.decided ? pct(t.rate) : "—",
      t.decided + " 场有判定", t.decided && t.rate >= 0.5 ? "var(--win)" : "var(--loss)") +
    statCard("近 20 场胜率", r.decided ? pct(r.rate) : "—",
      r.win + " 胜 " + r.loss + " 负", r.decided && r.rate >= 0.5 ? "var(--win)" : "var(--loss)") +
    statCard("未判定占比", (flatPct * 100).toFixed(0) + '<span class="unit">%</span>',
      "识别失败的对局不算输", "var(--flat)");

  const strip = $("#result-strip");
  strip.innerHTML = (d.recent30 || []).map((it) =>
    '<i class="blk ' + esc(it.result || "flat") + '" title="' +
    esc((it.ts || "") + " " + (it.result || "flat")) + '"></i>'
  ).join("") || '<span class="hint">暂无数据</span>';

  drawSpark(d.recent30 || []);

  const tb = $("#result-table tbody");
  tb.innerHTML = (d.recent20_table || []).map((row) => {
    const delta = row.delta;
    const dcls = delta == null || delta === 0 ? "zero" : delta > 0 ? "up" : "down";
    const dtxt = delta == null ? "—" : (delta > 0 ? "+" + delta : delta < 0 ? String(delta) : "±0");
    const rank = (row.before == null ? "?" : row.before) + " → " +
      (row.after == null ? "?" : row.after);
    return "<tr><td class=\"mono\">" + esc(row.ts) + "</td>" +
      '<td><span class="tag ' + esc(row.result || "flat") + '">' + esc(row.result || "flat") + "</span></td>" +
      "<td>" + esc(rank) + ' <span class="delta ' + dcls + '">' + esc(dtxt) + "</span></td></tr>";
  }).join("") || '<tr><td colspan="3" class="dim">暂无记录</td></tr>';

  $("#result-hint").textContent = (d.recent20_table || []).length + " 条";
}

function drawSpark(items) {
  const svg = $("#rate-spark");
  const W = 600, H = 88, P = 4;
  if (!items.length) { svg.innerHTML = ""; return; }

  let w = 0, l = 0;
  const pts = [];
  items.forEach((it, i) => {
    if (it.result === "win") w++;
    else if (it.result === "loss") l++;
    const decided = w + l;
    if (!decided) return;
    const x = P + (i / Math.max(1, items.length - 1)) * (W - P * 2);
    const y = H - P - (w / decided) * (H - P * 2);
    pts.push([x, y]);
  });

  if (pts.length < 2) {
    svg.style.height = "56px";
    svg.innerHTML =
      '<line x1="0" y1="' + (H - P) + '" x2="' + W + '" y2="' + (H - P) +
      '" stroke="rgba(255,255,255,.08)" stroke-width="1"/>' +
      '<text x="' + (W / 2) + '" y="' + (H / 2 - 10) +
      '" text-anchor="middle" fill="#56616f" font-size="12" font-family="' +
      'Bahnschrift, Microsoft YaHei UI, sans-serif">近 30 场没有可判定的对局</text>';
    return;
  }
  svg.style.height = "88px";

  const line = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
  const area = line + " L" + pts[pts.length - 1][0].toFixed(1) + " " + (H - P) +
    " L" + pts[0][0].toFixed(1) + " " + (H - P) + " Z";

  svg.innerHTML =
    '<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">' +
    '<stop offset="0" stop-color="rgba(242,179,74,.28)"/>' +
    '<stop offset="1" stop-color="rgba(242,179,74,0)"/></linearGradient></defs>' +
    '<line x1="0" y1="' + (H - P) + '" x2="' + W + '" y2="' + (H - P) +
    '" stroke="rgba(255,255,255,.08)" stroke-width="1"/>' +
    '<path d="' + area + '" fill="url(#g)"/>' +
    '<path d="' + line + '" fill="none" stroke="var(--amber)" stroke-width="1.6" ' +
    'stroke-linejoin="round" stroke-linecap="round"/>';
}

async function loadResults() {
  try {
    S.results = await getJSON("/api/results");
    renderResults();
  } catch (e) {
    $("#result-stats").innerHTML = statCard("战绩", "读取失败", "", "var(--loss)");
  }
}

// ───────────────────────── 回合 ─────────────────────────

function hpCell(my, foe) {
  const mv = my == null ? 0 : my;
  const fv = foe == null ? 0 : foe;
  return '<div class="duo">' +
    '<span class="mini"><i style="width:' + Math.max(0, Math.min(100, mv)) + '%"></i></span>' +
    '<span class="val">' + (my == null ? "—" : Math.round(mv)) + "</span>" +
    '<span class="mini foe"><i style="width:' + Math.max(0, Math.min(100, fv)) + '%"></i></span>' +
    '<span class="val">' + (foe == null ? "—" : Math.round(fv)) + "</span>" +
    "</div>";
}

function actChip(action) {
  const txt = String(action || "—");
  const kind = txt.split(/[\s,(]/)[0];
  const cls = /skill|energy|switch/.test(kind) ? " " + kind : "";
  return '<span class="act' + cls + '">' + esc(txt) + "</span>";
}

function renderTurns() {
  const d = S.turns;
  if (!d) return;
  const c = d.counts || {};
  const avgMy = d.avg_my, avgFoe = d.avg_foe;

  $("#turn-chips").innerHTML =
    '<div class="chip"><span class="k">技能</span><span class="v">' + (c.skill || 0) + "</span></div>" +
    '<div class="chip"><span class="k">蓄能</span><span class="v">' + (c.energy || 0) + "</span></div>" +
    '<div class="chip"><span class="k">换宠</span><span class="v">' + (c.switch || 0) + "</span></div>" +
    '<div class="chip"><span class="k">其他</span><span class="v">' + (c.other || 0) + "</span></div>" +
    '<div class="chip hp-chip"><span class="k">平均血量</span>' +
      '<span class="bar"><i style="width:' + clamp(avgMy) + '%"></i></span>' +
      '<span class="v">' + (avgMy == null ? "—" : Math.round(avgMy)) + "</span>" +
      '<span class="bar foe"><i style="width:' + clamp(avgFoe) + '%"></i></span>' +
      '<span class="v">' + (avgFoe == null ? "—" : Math.round(avgFoe)) + "</span>" +
    "</div>";

  const rows = (d.rows || []).slice(0, 120);
  $("#turn-table tbody").innerHTML = rows.map((r) =>
    '<tr><td class="mono">' + esc(r.ts) + "</td>" +
    '<td class="num">' + esc(r.turn == null ? "—" : r.turn) + "</td>" +
    '<td class="hp-cell">' + hpCell(r.my_pct, r.foe_pct) + "</td>" +
    "<td>" + actChip(r.action) + "</td>" +
    '<td><div class="why" title="' + esc(r.why || "") + '">' + esc(r.why || "—") + "</div></td></tr>"
  ).join("") || '<tr><td colspan="5" class="dim">暂无出招记录</td></tr>';

  $("#turn-hint").textContent = rows.length + " 条";
  $("#nv-turns").textContent = rows.length;
}

async function loadTurns() {
  try {
    S.turns = await getJSON("/api/turns");
    renderTurns();
  } catch (e) { /* 静默 */ }
}

// ───────────────────────── 头像 ─────────────────────────

function portraitCard(it) {
  const f = encodeURIComponent(it.file);
  const nf = encodeURIComponent(it.name_file);
  return '<div class="portrait-card" data-stamp="' + esc(it.stamp) + '">' +
    '<div class="pc-media">' +
      '<figure data-src="/img/portraits/' + f + '"><img loading="lazy" decoding="async" src="/thumb/' + f + '?w=140" alt="" /></figure>' +
      (it.has_name
        ? '<figure data-src="/img/portraits/' + nf + '"><img loading="lazy" decoding="async" src="/thumb/' + nf + '?w=140" alt="" /></figure>'
        : '<figure class="none"><span class="none">无名称宽图</span></figure>') +
    "</div>" +
    '<div class="pc-body">' +
      '<span class="pc-stamp">#' + esc(it.stamp) + "</span>" +
      '<div class="pc-row">' +
        '<input type="text" placeholder="物种名 a-z0-9_" data-stamp="' + esc(it.stamp) + '" />' +
        '<button class="btn btn-sm submit-btn" data-stamp="' + esc(it.stamp) + '">标注</button>' +
      "</div>" +
      '<div class="pc-msg" data-stamp="' + esc(it.stamp) + '"></div>' +
    "</div></div>";
}

function renderPortraits() {
  const list = $("#portrait-list");
  const items = S.portraits.items;
  list.innerHTML = items.map(portraitCard).join("") ||
    '<div class="hint">没有待标注的头像</div>';

  $("#portrait-hint").textContent =
    "已显示 " + items.length + " / " + S.portraits.total;
  $("#nv-portraits").textContent = S.portraits.total;
  $("#portrait-more").style.display =
    items.length < S.portraits.total ? "" : "none";

  list.querySelectorAll("figure[data-src]").forEach((fig) => {
    fig.addEventListener("click", () => openModal(fig.dataset.src));
  });
  list.querySelectorAll(".submit-btn").forEach((btn) => {
    btn.addEventListener("click", () => submitLabel(btn.dataset.stamp));
  });
  list.querySelectorAll("input[data-stamp]").forEach((inp) => {
    inp.addEventListener("keydown", (e) => {
      if (e.key === "Enter") submitLabel(inp.dataset.stamp);
    });
  });
}

async function submitLabel(stamp) {
  const input = document.querySelector('input[data-stamp="' + stamp + '"]');
  const msg = document.querySelector('.pc-msg[data-stamp="' + stamp + '"]');
  const name = (input.value || "").trim().toLowerCase();
  if (!/^[a-z0-9_]{1,24}$/.test(name)) {
    msg.textContent = "只允许小写字母/数字/下划线";
    msg.className = "pc-msg bad";
    return;
  }
  msg.textContent = "提交中…";
  msg.className = "pc-msg";
  const r = await postJSON("/api/portraits/label", { stamp, name });
  msg.textContent = r.msg || (r.ok ? "已标注" : "失败");
  msg.className = "pc-msg " + (r.ok ? "ok" : "bad");
  if (r.ok) {
    setTimeout(() => {
      S.portraits.items = S.portraits.items.filter((it) => it.stamp !== stamp);
      S.portraits.total = Math.max(0, S.portraits.total - 1);
      renderPortraits();
    }, 600);
  }
}

async function loadPortraits(reset) {
  if (reset) {
    S.portraitOffset = 0;
    S.portraits = { total: 0, items: [] };
  }
  try {
    const q = encodeURIComponent(S.portraitFilter);
    const d = await getJSON(
      "/api/portraits?offset=" + S.portraitOffset + "&limit=" + PORTRAIT_PAGE + "&q=" + q);
    S.portraits.total = d.total;
    S.portraits.items = S.portraitOffset
      ? S.portraits.items.concat(d.items)
      : d.items;
    S.portraitOffset += (d.items || []).length;
    renderPortraits();
  } catch (e) {
    $("#portrait-hint").textContent = "读取失败";
  }
}

const PORTRAIT_PAGE = 60;

$("#portrait-more").addEventListener("click", () => loadPortraits(false));

$("#portrait-filter").addEventListener("input", (e) => {
  S.portraitFilter = e.target.value.trim();
  clearTimeout(S._pf);
  S._pf = setTimeout(() => loadPortraits(true), 200);
});

// ───────────────────────── 控制 ─────────────────────────

async function startBot() {
  $("#ctl-msg").textContent = "启动中…";
  const r = await postJSON("/api/robot/start");
  $("#ctl-msg").textContent = r.msg;
  pollStatus();
}
async function stopBot() {
  $("#ctl-msg").textContent = "停止中…";
  const r = await postJSON("/api/robot/stop");
  $("#ctl-msg").textContent = r.msg;
  pollStatus();
}

["#start-btn", "#rail-start"].forEach((s) => $(s).addEventListener("click", startBot));
["#stop-btn", "#rail-stop"].forEach((s) => $(s).addEventListener("click", stopBot));
$("#openlogs-btn").addEventListener("click", async () => {
  const r = await postJSON("/api/open-logs");
  $("#ctl-msg").textContent = r.msg;
});

// ───────────────────────── 弹层 ─────────────────────────

const modal = $("#modal");
function openModal(src) {
  $("#modal-img").src = src;
  modal.classList.remove("is-hidden");
}
modal.addEventListener("click", () => modal.classList.add("is-hidden"));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") modal.classList.add("is-hidden");
});

// ───────────────────────── 轮询 ─────────────────────────

// 首屏一次拿全，之后各视图按需刷新
(async function boot() {
  try {
    const d = await getJSON("/api/full");
    S.status = d.status;
    S.results = d.results;
    S.turns = d.turns;
    renderStatus();
    renderResults();
    renderTurns();
    if (d.portraits) {
      S.portraits.total = d.portraits.total || 0;
      $("#nv-portraits").textContent = S.portraits.total;
    }
    if (d.status && d.status.today) {
      $("#nv-rate").textContent = d.status.today.decided ? pct(d.status.today.rate) : "—";
    }
  } catch (e) { /* 服务未起 */ }
  await pollLogs();
})();

setInterval(pollStatus, 5000);
setInterval(pollLogs, 1500);
setInterval(() => { if (!document.hidden && S.view === "results") loadResults(); }, 15000);
setInterval(() => { if (!document.hidden && S.view === "turns") loadTurns(); }, 10000);

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) { pollStatus(); pollLogs(); }
});

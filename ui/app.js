// RocoAgent 管理面板前端逻辑（原生 JS，无框架）
"use strict";

// ---------- 工具 ----------
const $ = (sel) => document.querySelector(sel);

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

async function getJSON(url) {
  const r = await fetch(url);
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

// ---------- 标签页 ----------
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $("#panel-" + btn.dataset.tab).classList.add("active");
    if (btn.dataset.tab === "results") loadResults();
    if (btn.dataset.tab === "turns") loadTurns();
    if (btn.dataset.tab === "portraits") loadPortraits();
    if (btn.dataset.tab === "log") scrollLogToBottom();
  });
});

// ---------- 顶部状态条 ----------
async function refreshStatus() {
  try {
    const s = await getJSON("/api/status");
    const dot = $("#sb-dot");
    dot.className = "dot " + (s.running ? "running" : "stopped");
    $("#sb-status-text").textContent = s.running ? "运行中" : "已停止";
    $("#sb-pid").textContent = "PID: " + (s.pids && s.pids.length ? s.pids.join(",") : "—");
    $("#sb-last").textContent = "最后日志: " + (s.last_log_time || "—");
    $("#sb-ago").textContent = "距今: " + (s.seconds_ago == null ? "—" : s.seconds_ago + "s");
    const t = s.today || {};
    // flat 多数是"对局没识别出来"，不能当输算进分母
    const rate = t.decided ? (t.rate * 100).toFixed(1) + "%" : "—";
    $("#sb-rate").textContent = "今日胜率: " + rate +
      (t.flat ? "（" + t.decided + " 场有判定，" + t.flat + " 场未判定）" : "");
  } catch (e) {
    // 服务刚起/重载时静默忽略
  }
}

// ---------- 实时日志 ----------
const KW = [
  ["PERCEPTION ERROR", "kw-perr"],
  ["RESULT win", "kw-win"],
  ["RESULT loss", "kw-loss"],
  ["LEADER", "kw-leader"],
  ["FAINT", "kw-faint"],
  ["PET ->", "kw-pet"],
  ["ERROR", "kw-error"],
];
const kwRegex = new RegExp(
  "(" + KW.map(([k]) => k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|") + ")",
  "gi"
);
const kwMap = {};
KW.forEach(([k, cls]) => (kwMap[k.toLowerCase()] = cls));

function highlight(line) {
  const safe = esc(line);
  return safe.replace(kwRegex, (m) => {
    const cls = kwMap[m.toLowerCase()];
    return '<span class="' + cls + '">' + m + "</span>";
  });
}

const logView = $("#log-view");
let logOffset = 0;
let paused = false;

function appendLogLines(lines) {
  for (const ln of lines) {
    const div = document.createElement("div");
    div.className = "log-line";
    div.innerHTML = highlight(ln) || "&nbsp;";
    logView.appendChild(div);
  }
  // 控制最大节点数，避免长时间运行内存膨胀
  while (logView.childElementCount > 4000) {
    logView.removeChild(logView.firstChild);
  }
}

function scrollLogToBottom() {
  logView.scrollTop = logView.scrollHeight;
}

async function pollLogs() {
  try {
    const data = await getJSON("/api/logs?offset=" + logOffset);
    logOffset = data.offset;
    if (data.lines.length) {
      appendLogLines(data.lines);
      if (!paused) scrollLogToBottom();
    }
    $("#log-hint").textContent = data.truncated
      ? "（仅显示末尾，更早内容被截断）"
      : "共 " + logOffset + " 行";
  } catch (e) {
    $("#log-hint").textContent = "读取日志失败";
  }
}

$("#pause-btn").addEventListener("click", () => {
  paused = !paused;
  $("#pause-btn").textContent = paused ? "继续跟随" : "暂停跟随";
  $("#follow-toggle").checked = !paused;
  if (!paused) scrollLogToBottom();
});
$("#follow-toggle").addEventListener("change", (e) => {
  paused = !e.target.checked;
  $("#pause-btn").textContent = paused ? "继续跟随" : "暂停跟随";
});

// ---------- 机器人控制 ----------
async function refreshControl() {
  try {
    const s = await getJSON("/api/status");
    $("#control-info").textContent = s.running
      ? "运行中，PID: " + s.pids.join(", ")
      : "已停止";
  } catch (e) {
    $("#control-info").textContent = "读取状态失败";
  }
}

$("#start-btn").addEventListener("click", async () => {
  $("#control-info").textContent = "启动中…";
  const r = await postJSON("/api/robot/start");
  $("#control-info").textContent = r.msg;
  refreshStatus();
});
$("#stop-btn").addEventListener("click", async () => {
  $("#control-info").textContent = "停止中…";
  const r = await postJSON("/api/robot/stop");
  $("#control-info").textContent = r.msg;
  refreshStatus();
});
$("#openlogs-btn").addEventListener("click", async () => {
  const r = await postJSON("/api/open-logs");
  $("#control-info").textContent = r.msg;
});

// ---------- 战绩 ----------
function tag(result) {
  return '<span class="tag ' + esc(result) + '">' + esc(result) + "</span>";
}

async function loadResults() {
  try {
    const d = await getJSON("/api/results");
    const t = d.today, r = d.recent20;
    const pct = (x) => (x.decided ? (x.rate * 100).toFixed(1) + "%" : "—");
    $("#result-cards").innerHTML =
      card("今日 胜/负/平", t.win + " / " + t.loss + " / " + t.flat, "gray") +
      card("总胜率", pct(t), t.rate > 0.5 ? "green" : "gray") +
      card("近 20 场胜率", pct(r), r.rate > 0.5 ? "green" : "gray");
    drawBarChart(d.recent30);
    const tb = $("#result-table tbody");
    tb.innerHTML = "";
    d.recent20_table.forEach((row) => {
      const delta = row.delta == null ? "—" : (row.delta > 0 ? "+" + row.delta : row.delta);
      const rank = (row.before == null ? "?" : row.before) + " → " +
        (row.after == null ? "?" : row.after) + " (" + delta + ")";
      const tr = document.createElement("tr");
      tr.innerHTML = "<td>" + esc(row.ts) + "</td><td>" + tag(row.result) +
        "</td><td>" + esc(rank) + "</td>";
      tb.appendChild(tr);
    });
  } catch (e) {
    $("#result-cards").innerHTML = card("战绩", "读取失败", "red");
  }
}

function card(k, v, color) {
  return '<div class="card"><div class="k">' + esc(k) +
    '</div><div class="v ' + color + '">' + esc(v) + "</div></div>";
}

function drawBarChart(items) {
  const canvas = $("#bar-chart");
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 900;
  const h = 180;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);
  const n = items.length;
  if (!n) {
    ctx.fillStyle = "#6e7681";
    ctx.font = "13px sans-serif";
    ctx.fillText("暂无数据", 10, 30);
    return;
  }
  const gap = 3;
  const bw = Math.max(4, (w - gap * (n - 1)) / n);
  const colors = { win: "#3fb950", loss: "#f85149", flat: "#6e7681" };
  items.forEach((it, i) => {
    const x = i * (bw + gap);
    ctx.fillStyle = colors[it.result] || colors.flat;
    const barH = 6 + Math.random() * 0; // 等高层叠，仅作颜色区分
    ctx.fillRect(x, h - 30, bw, 30);
    ctx.fillRect(x, h - 30, bw, -1 * (10 + (i % 7) * 3)); // 轻微高度变化
  });
  // 图例
  ctx.font = "12px sans-serif";
  let lx = 0;
  [["win", "胜"], ["loss", "负"], ["flat", "平"]].forEach(([k, label]) => {
    ctx.fillStyle = colors[k];
    ctx.fillRect(lx, h - 14, 10, 10);
    ctx.fillStyle = "#8b949e";
    ctx.fillText(label, lx + 14, h - 5);
    lx += 40;
  });
}

// ---------- 回合统计 ----------
async function loadTurns() {
  try {
    const d = await getJSON("/api/turns");
    const c = d.counts;
    const avg = (v) => (v == null ? "—" : v + "%");
    $("#turn-cards").innerHTML =
      card("技能 skill", c.skill, "gray") +
      card("蓄能 energy", c.energy, "gray") +
      card("换宠 switch", c.switch, "gray") +
      card("其他", c.other, "gray") +
      card("我方平均血量", avg(d.avg_my), "green") +
      card("对面平均血量", avg(d.avg_foe), "red");
    const tb = $("#turn-table tbody");
    tb.innerHTML = "";
    d.rows.forEach((row) => {
      const tr = document.createElement("tr");
      tr.innerHTML = "<td>" + esc(row.ts) + "</td><td>" + esc(row.turn) +
        "</td><td>" + esc(row.my_pct) + "</td><td>" + esc(row.foe_pct) +
        "</td><td>" + esc(row.action) + "</td><td>" + esc(row.why) + "</td>";
      tb.appendChild(tr);
    });
  } catch (e) {
    $("#turn-cards").innerHTML = card("回合", "读取失败", "red");
  }
}

// ---------- 头像标注 ----------
async function loadPortraits() {
  try {
    const d = await getJSON("/api/portraits");
    const list = $("#portrait-list");
    list.innerHTML = "";
    $("#portrait-hint").textContent = "共 " + d.items.length + " 个未标注头像";
    d.items.forEach((it) => {
      const item = document.createElement("div");
      item.className = "portrait-item";
      item.innerHTML =
        '<div class="row">' +
          '<img class="thumb" src="/img/portraits/' + esc(it.file) + '" alt="" />' +
          '<div class="meta">' +
            '<div class="stamp">' + esc(it.file) + "</div>" +
            (it.has_name
              ? '<img class="name-img" src="/img/portraits/' + esc(it.name_file) + '" alt="名称宽图" />'
              : '<div class="no-name">无对应名称宽图</div>') +
          "</div>" +
        "</div>" +
        '<input type="text" placeholder="输入名字 [a-z0-9_]" data-stamp="' + esc(it.stamp) + '" />' +
        '<button class="btn submit-btn" data-stamp="' + esc(it.stamp) + '">提交</button>' +
        '<div class="msg" data-stamp="' + esc(it.stamp) + '"></div>';
      list.appendChild(item);
    });
    // 缩略图点击放大（头像本身）
    list.querySelectorAll("img.thumb").forEach((img) => {
      img.addEventListener("click", () => openModal(img.src));
    });
    // 宽图点击放大
    list.querySelectorAll("img.name-img").forEach((img) => {
      img.addEventListener("click", () => openModal(img.src));
    });
    // 提交
    list.querySelectorAll(".submit-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const stamp = btn.dataset.stamp;
        const input = list.querySelector('input[data-stamp="' + stamp + '"]');
        const msg = list.querySelector('.msg[data-stamp="' + stamp + '"]');
        const name = input.value.trim().toLowerCase();
        if (!/^[a-z0-9_]{1,24}$/.test(name)) {
          msg.textContent = "名字非法（仅小写字母/数字/下划线，1-24 位）";
          return;
        }
        msg.textContent = "提交中…";
        const r = await postJSON("/api/portraits/label", { stamp, name });
        msg.textContent = r.msg;
        if (r.ok) loadPortraits();
      });
    });
    // 回车提交
    list.querySelectorAll("input[data-stamp]").forEach((input) => {
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          const stamp = input.dataset.stamp;
          list.querySelector('.submit-btn[data-stamp="' + stamp + '"]').click();
        }
      });
    });
  } catch (e) {
    $("#portrait-hint").textContent = "读取失败";
  }
}

// ---------- 放大遮罩 ----------
const modal = $("#modal");
function openModal(src) {
  $("#modal-img").src = src;
  modal.classList.remove("hidden");
}
modal.addEventListener("click", () => modal.classList.add("hidden"));

// ---------- 轮询 ----------
setInterval(refreshStatus, 3000);
setInterval(pollLogs, 2000);
setInterval(refreshControl, 5000);

// ---------- 初始化 ----------
refreshStatus();
pollLogs();
refreshControl();

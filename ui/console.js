/* ============================================================
   控制台逻辑
   ------------------------------------------------------------
   性能原则（来自旧 UI 的失败教训，docs/UI_TODO.md）：
     · 只刷当前可见页，切走就停
     · 日志按字节 offset 增量拉，不全量重读
     · 战绩/回合按 mtime 缓存（服务端做）
     · 不可见时（document.hidden）完全停止轮询
   ============================================================ */
'use strict';

const API = '';

const TYPE_COLOR = {
  普通: '#A8A878', 火: '#F08030', 水: '#6890F0', 草: '#78C850',
  电: '#F8D030', 冰: '#98D8D8', 武: '#C03028', 毒: '#A040A0',
  地: '#E0C068', 翼: '#A890F0', 萌: '#EE99AC', 幻: '#C080FF',
  虫: '#A8B820', 幽: '#705898', 机械: '#B8B8D0', 龙: '#7038F8',
  恶: '#705848', 光: '#FFF68F',
};

const $ = (id) => document.getElementById(id);

let page = 'overview';
let timers = {};
let logOffset = 0;
let logFollow = true;

/* ---------------------------------------------------------------- 请求 */
async function get(path) {
  const r = await fetch(API + path, { cache: 'no-store' });
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

async function post(path, body) {
  const r = await fetch(API + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  return r.json();
}

/* ---------------------------------------------------------------- 工具 */
function hexA(hex, a) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}

function typeChip(t) {
  const c = TYPE_COLOR[t] || '#8A8F98';
  return `<span class="type-chip" style="color:${c};background:${hexA(c, .15)};
          border:1px solid ${hexA(c, .3)}">${t}</span>`;
}

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function fmtPct(v) {
  return v == null ? '—' : Math.round(v) + '%';
}

/* ================================================================
   机器人状态
   ================================================================ */
const PHASE_TEXT = {
  live: '运行中',
  stale: '运行中（无数据）',
  stopped: '未运行',
};

async function refreshBot() {
  let b;
  try {
    b = await get('/api/bot');
  } catch (e) {
    $('botText').textContent = '服务未连接';
    $('botDot').className = 'dot off';
    return;
  }

  const dot = $('botDot');
  const text = $('botText');
  const pid = $('botPid');

  dot.className = 'dot ' + (b.phase === 'live' ? 'live'
    : b.phase === 'stale' ? 'stale' : 'off');
  text.textContent = PHASE_TEXT[b.phase] || b.phase;
  pid.textContent = b.pid ? 'PID ' + b.pid : '';

  $('btnStart').disabled = b.running;
  $('btnStop').disabled = !b.running;

  // 悬浮窗联动：机器人跑起来时提示
  return b;
}

/* ================================================================
   系统健康
   ----------------------------------------------------------------
   这一块是"让我们清楚系统运行状态"的核心。

   判据来自实测：fps_ms=745 且 bar=False 时机器人正在盲打
   （技能栏从没画出来）。所以这两项必须独立显示，不能合并成一个
   "总体健康"的绿灯 —— 那会掩盖具体哪一环坏了。
   ================================================================ */
function healthItem(label, value, level) {
  return `<div class="hitem ${level}">
    <i class="hdot"></i>
    <div class="hbody">
      <div class="hlabel">${label}</div>
      <div class="hvalue">${value}</div>
    </div>
  </div>`;
}

async function refreshHealth() {
  const grid = $('healthGrid');
  const sub = $('healthSub');

  let b, st;
  try { b = await get('/api/bot'); } catch (e) { b = null; }
  try { st = await get('/api/state'); } catch (e) { st = null; }

  const items = [];

  // 1. 机器人进程
  if (!b) {
    items.push(healthItem('机器人', '服务未连接', 'bad'));
  } else if (b.phase === 'stopped') {
    items.push(healthItem('机器人', '未运行', ''));
  } else {
    items.push(healthItem('机器人', `PID ${b.pid}`, 'ok'));
  }

  // 2. 状态推送新鲜度
  if (!b || b.state_age == null) {
    items.push(healthItem('状态推送', '无数据', b && b.running ? 'bad' : ''));
  } else if (b.state_age < 3) {
    items.push(healthItem('状态推送', `${b.state_age.toFixed(1)}s 前`, 'ok'));
  } else {
    items.push(healthItem('状态推送', `${b.state_age.toFixed(0)}s 前（过期）`, 'warn'));
  }

  // 3. 感知耗时
  const ms = st && st.system ? st.system.fps_ms : null;
  if (ms == null) {
    items.push(healthItem('感知耗时', '无数据', ''));
  } else {
    const lv = ms > 200 ? 'bad' : ms > 50 ? 'warn' : 'ok';
    items.push(healthItem('感知耗时', `${ms.toFixed(0)} ms`, lv));
  }

  // 4-6. 三个检测器（各自独立，出问题能定位到哪一环）
  const dets = (st && st.system && st.system.detectors) || {};
  const DET_LABEL = {
    bar: '血条',
    skillbar: '技能栏',
    countdown: '倒计时',
  };
  Object.keys(DET_LABEL).forEach((k) => {
    const on = !!dets[k];
    items.push(healthItem(DET_LABEL[k], on ? '正常' : '读不到', on ? 'ok' : 'bad'));
  });

  // 7. 决策门（被拦住时显示原因，这是排查卡住的关键）
  const gate = st && st.system ? st.system.gate : null;
  items.push(healthItem('决策门', gate || '畅通', gate ? 'warn' : 'ok'));

  grid.innerHTML = items.join('');

  // 汇总
  const bad = items.filter((x) => x.includes('class="hitem bad"')).length;
  const warn = items.filter((x) => x.includes('class="hitem warn"')).length;
  sub.textContent = bad ? `${bad} 项异常`
    : warn ? `${warn} 项警告`
    : b && b.running ? '全部正常' : '';
}

/* ================================================================
   实时状态（概览页）
   ================================================================ */
function hpClass(p) {
  if (p == null) return '';
  if (p <= 25) return 'low';
  if (p <= 55) return 'mid';
  return '';
}

async function refreshLive() {
  let st;
  try {
    st = await get('/api/state');
  } catch (e) {
    return;
  }

  const body = $('liveBody');
  const age = $('liveAge');

  if (!st) {
    age.textContent = '';
    body.innerHTML = `
      <div class="empty">
        <svg width="26" height="26" viewBox="0 0 26 26" fill="none">
          <rect x="3" y="3" width="20" height="20" rx="4" stroke="currentColor" stroke-width="1.6"/>
          <path d="M8 13h10" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>
        </svg>
        <b>机器人未在推送状态</b>
        <span>启动机器人后，这里会显示实时对局</span>
      </div>`;
    return;
  }

  age.textContent = st._stale
    ? `数据已过期 ${st._age}s`
    : '实时';

  const me = st.me || {};
  const foe = st.foe || {};
  const skills = st.skills || [];

  body.innerHTML = `
    <div class="live-grid">
      <div class="live-side">
        <div class="live-side-head">
          <span class="live-side-name">${esc(me.name || '未知')}</span>
          ${(me.types || []).map(typeChip).join('')}
          <span class="live-pct">${fmtPct(me.hp_pct)}</span>
        </div>
        <div class="bar-track">
          <div class="bar-fill ${hpClass(me.hp_pct)}"
               style="transform:scaleX(${((me.hp_pct || 0) / 100).toFixed(3)})"></div>
        </div>
        <div class="live-meta">
          <span>${me.hp_abs != null ? me.hp_abs + ' / ' + (me.hp_max || '?') : '血量未知'}</span>
          <span>能量 ${me.energy != null ? me.energy : '?'}/${me.energy_max || 10}</span>
        </div>
      </div>

      <div class="live-side">
        <div class="live-side-head">
          <span class="live-side-name">${esc(foe.name || '未知')}</span>
          ${(foe.types || []).map(typeChip).join('')}
          <span class="live-pct">${fmtPct(foe.hp_pct)}</span>
        </div>
        <div class="bar-track">
          <div class="bar-fill ${hpClass(foe.hp_pct)}"
               style="transform:scaleX(${((foe.hp_pct || 0) / 100).toFixed(3)})"></div>
        </div>
        <div class="live-meta">
          <span>回合 ${st.turn != null ? st.turn : '?'}</span>
          ${st.matchup ? `<span>我打 ×${st.matchup.out} / 打我 ×${st.matchup.in}</span>` : ''}
        </div>
      </div>
    </div>

    ${skills.length ? `
    <table class="tbl" style="margin-top:12px">
      <thead><tr><th>#</th><th>技能</th><th>属性</th><th>消耗</th>
        <th>伤害</th><th>占比</th></tr></thead>
      <tbody>
        ${skills.map((s) => `
          <tr>
            <td class="muted">${s.i}</td>
            <td>${esc(s.name || '—')}</td>
            <td class="muted">${esc(s.element || '—')}</td>
            <td class="num muted">${s.cost != null ? s.cost : '—'}</td>
            <td class="num">${s.dmg != null ? s.dmg : '—'}</td>
            <td class="num">${s.pct != null ? s.pct + '%' : (s.note || '—')}</td>
          </tr>`).join('')}
      </tbody>
    </table>` : ''}
  `;
}

/* ================================================================
   战绩
   ================================================================ */
function metricHtml(label, value, unit, note, cls) {
  return `<div class="metric">
    <div class="metric-label">${label}</div>
    <div class="metric-value ${cls || ''}">${value}${
      unit ? `<span class="metric-unit">${unit}</span>` : ''}</div>
    ${note ? `<div class="metric-note">${note}</div>` : ''}
  </div>`;
}

async function refreshStats() {
  let s;
  try {
    s = await get('/api/stats');
  } catch (e) {
    return;
  }

  const sub = s.reliable
    ? `${s.decided} 场已判定`
    : `样本不足（${s.decided}/20），胜率不可信`;

  // 概览页
  if ($('statSub')) $('statSub').textContent = sub;
  const rateCls = s.rate == null ? '' : (s.rate >= 55 ? 'win' : s.rate >= 45 ? 'warn' : 'lose');
  $('statMetrics').innerHTML =
    metricHtml('胜率', s.rate == null ? '—' : s.rate, '%',
      s.rank ? `段位 ${s.rank.now}/${s.rank.of}` : '', rateCls) +
    metricHtml('胜 / 负', `${s.wins} / ${s.losses}`, '', '已判定对局') +
    metricHtml('保分 / 未判定', s.flats, '', '不计入胜率') +
    metricHtml('近 20 场', s.rate20 == null ? '—' : s.rate20, '%',
      `${s.last20.length} 场`, '');

  // 走势
  const seq = s.last20 || [];
  $('spark').innerHTML = seq.length
    ? seq.map((v) => `<i class="${v === 1 ? 'w' : v === -1 ? 'l' : ''}"
        style="height:${v === 0 ? 18 : 100}%"></i>`).join('')
    : `<span style="color:var(--ink-3);font-size:11px">还没有已判定的对局</span>`;

  // 战绩页
  if ($('statsSub')) $('statsSub').textContent = sub;
  $('statsMetrics').innerHTML =
    metricHtml('胜率', s.rate == null ? '—' : s.rate, '%', '', rateCls) +
    metricHtml('胜', s.wins, '场', '', 'win') +
    metricHtml('负', s.losses, '场', '', 'lose') +
    metricHtml('保分', s.flats, '场', '未判定胜负') +
    metricHtml('总记录', s.samples, '条', 'results.jsonl');
}

async function refreshRounds() {
  let d;
  try {
    d = await get('/api/rounds?limit=40');
  } catch (e) {
    return;
  }
  const rows = (d.items || []).slice().reverse();
  const tb = $('roundsTbl').querySelector('tbody');
  if (!rows.length) {
    tb.innerHTML = `<tr><td colspan="5" class="muted">还没有对局记录</td></tr>`;
    return;
  }
  tb.innerHTML = rows.map((r) => `
    <tr>
      <td class="num muted">${r.match != null ? r.match : '—'}</td>
      <td class="num muted">${esc(r.start || '—')}</td>
      <td class="num muted">${esc(r.end || '—')}</td>
      <td class="num">${r.seconds != null ? Math.round(r.seconds) + 's' : '—'}</td>
      <td class="num">${r.actions != null ? r.actions : '—'}</td>
    </tr>`).join('');
}

/* ================================================================
   日志（增量）
   ================================================================ */
function classifyLine(ln) {
  if (/\b(ERROR|FAIL|Traceback|异常)\b/i.test(ln)) return 'err';
  if (/\b(WARN|STUCK|FOCUS LOST|GATE)\b/i.test(ln)) return 'warn';
  if (/\b(OK|WIN|RECOVER|recovered)\b/i.test(ln)) return 'ok';
  return '';
}

async function pollLog() {
  if (page !== 'logs') return;
  let d;
  try {
    d = await get('/api/log?offset=' + logOffset);
  } catch (e) {
    return;
  }
  if (d.reset) {
    $('logbox').innerHTML = '';
  }
  logOffset = d.offset;

  if (d.lines && d.lines.length) {
    const box = $('logbox');
    const frag = document.createDocumentFragment();
    d.lines.forEach((ln) => {
      const el = document.createElement('span');
      el.className = 'ln ' + classifyLine(ln);
      // 时间戳单独着色
      const m = ln.match(/^(\d{2}:\d{2}:\d{2})\s(.*)$/);
      if (m) {
        el.innerHTML = `<span class="t">${m[1]}</span> ${esc(m[2])}`;
      } else {
        el.textContent = ln;
      }
      frag.appendChild(el);
    });
    box.appendChild(frag);

    // 上限：超过 1200 行就砍掉前半
    while (box.childElementCount > 1200) {
      box.removeChild(box.firstElementChild);
    }
    if (logFollow) box.scrollTop = box.scrollHeight;
  }
  $('logSub').textContent =
    `bot_stdout.log · ${(d.size / 1024).toFixed(0)} KB`;
}

/* ================================================================
   头像加载器
   ----------------------------------------------------------------
   两个必须解决的问题：
     1. 120 张图同时请求会把 ThreadingHTTPServer 打满（默认 backlog 小），
        大部分静默失败 —— 实测只有 14/120 成功。
        解法：并发上限 6，排队加载。
     2. 两个目录都要试。第一次 onerror 换目录，第二次还失败就必须
        恢复成首字占位，不能留个破图。
   ================================================================ */
const IMG_QUEUE = [];
const IMG_ACTIVE = new Set();
const IMG_MAX = 6;

function loadAvatar(el, name) {
  if (!el || !name) return;
  IMG_QUEUE.push({ el, name });
  pumpImages();
}

function pumpImages() {
  while (IMG_ACTIVE.size < IMG_MAX && IMG_QUEUE.length) {
    const job = IMG_QUEUE.shift();
    runImage(job);
  }
}

function runImage({ el, name }) {
  const file = encodeURIComponent(name) + '.png';
  const dirs = ['portraits', 'portraits_foe'];
  let idx = 0;
  const img = new Image();
  IMG_ACTIVE.add(img);

  const done = () => {
    IMG_ACTIVE.delete(img);
    pumpImages();
  };

  img.onload = () => {
    el.textContent = '';
    el.replaceChildren(img);
    done();
  };
  img.onerror = () => {
    idx += 1;
    if (idx < dirs.length) {
      img.src = dirs[idx] + '/' + file;   // 换目录再试
      return;
    }
    // 两个目录都没有：恢复首字占位
    el.textContent = name.slice(0, 1);
    done();
  };
  img.src = dirs[0] + '/' + file;
}

/* ================================================================
   队伍
   ================================================================ */
async function refreshTeam() {
  let d;
  try {
    d = await get('/api/team');
  } catch (e) {
    return;
  }
  const slots = d.slots || [];
  const grid = $('teamGrid');

  if (!slots.length) {
    grid.innerHTML = `<div class="empty"><b>队伍是空的</b>
      <span>用 python src\\team_db.py --set 1 --species 音速犬 添加</span></div>`;
    return;
  }

  grid.innerHTML = slots.map((s) => `
    <div class="slot">
      <div class="slot-av" data-name="${esc(s.name || '')}">
        ${esc((s.name || '?').slice(0, 1))}
      </div>
      <div class="slot-body">
        <div class="slot-name">${esc(s.name || '未设置')}</div>
        <div class="dex-types">${(s.types || []).map(typeChip).join('')}</div>
        <div class="slot-stats">
          HP ${s.max_hp || '—'} · 攻 ${s.atk || '—'} ·
          防 ${s.def || '—'} · 速 ${s.spe || '—'}
        </div>
      </div>
    </div>`).join('');

  // 头像懒加载（限并发，见 loadAvatar）
  grid.querySelectorAll('.slot-av').forEach((el) => {
    loadAvatar(el, el.dataset.name);
  });
}

/* ================================================================
   图鉴
   ================================================================ */
let dexTimer = null;

async function refreshDex(q) {
  let d;
  try {
    d = await get('/api/dex?limit=120&q=' + encodeURIComponent(q || ''));
  } catch (e) {
    return;
  }
  const items = d.items || [];
  $('dexSub').textContent = `${items.length} / ${d.total || 0} 只`;

  const grid = $('dexGrid');
  if (!items.length) {
    grid.innerHTML = `<div class="empty"><b>没有匹配的精灵</b></div>`;
    return;
  }

  grid.innerHTML = items.map((p) => `
    <div class="dex-card">
      <div class="dex-av" data-name="${esc(p.name)}">${esc(p.name.slice(0, 1))}</div>
      <div class="dex-body">
        <div class="dex-name">${esc(p.name)}</div>
        <div class="dex-types">${(p.types || []).map(typeChip).join('')}</div>
        <div class="dex-stat">HP ${p.hp || '—'} · 攻 ${p.atk || '—'} ·
          速 ${p.spe || '—'} · 计 ${p.total || '—'}</div>
      </div>
    </div>`).join('');

  grid.querySelectorAll('.dex-av').forEach((el) => {
    loadAvatar(el, el.dataset.name);
  });
}

/* ================================================================
   回合明细
   ================================================================ */
function fmtAction(a) {
  if (!a) return '—';
  const NAMES = {
    skill: '出招', switch: '换人', heal: '治疗',
    charge: '聚能', energy: '聚能', wait: '等待',
  };
  if (Array.isArray(a)) {
    const kind = String(a[0] || '');
    const slot = a[1];
    const label = NAMES[kind] || kind;
    return label + (slot != null ? ' ' + slot : '');
  }
  const s = String(a);
  return NAMES[s] || s;
}

async function refreshTurns() {
  let d;
  try {
    d = await get('/api/turns?limit=100');
  } catch (e) {
    return;
  }
  const rows = (d.items || []).slice().reverse();
  const tb = $('turnsTbl').querySelector('tbody');
  if (!rows.length) {
    tb.innerHTML = `<tr><td colspan="7" class="muted">还没有回合记录</td></tr>`;
    return;
  }
  tb.innerHTML = rows.map((r) => `
    <tr>
      <td class="num muted">${esc(r.ts || '—')}</td>
      <td class="num">${r.turn != null ? r.turn : '—'}</td>
      <td class="num">${r.my_pct != null ? r.my_pct + '%' : '—'}</td>
      <td class="num">${r.foe_pct != null ? r.foe_pct + '%' : '—'}</td>
      <td class="num muted">${r.energy != null ? r.energy : '—'}</td>
      <td>${esc(fmtAction(r.action))}</td>
      <td class="muted" style="max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
          title="${esc(r.why || '')}">${esc(r.why || '—')}</td>
    </tr>`).join('');
}

/* ================================================================
   页面切换 + 定时器管理
   ================================================================ */
const REFRESH = {
  overview: [['health', refreshHealth, 1500], ['live', refreshLive, 1000],
             ['stats', refreshStats, 8000]],
  logs: [['log', pollLog, 700]],
  stats: [['stats', refreshStats, 8000], ['rounds', refreshRounds, 15000]],
  team: [['team', refreshTeam, 30000]],
  // 图鉴是静态数据，进页面拉一次就够，不轮询
  dex: [['dex', () => refreshDex($('dexSearch').value.trim()), 0]],
  turns: [['turns', refreshTurns, 10000]],
};

function stopTimers() {
  Object.values(timers).forEach(clearInterval);
  timers = {};
}

function startTimers() {
  stopTimers();
  if (document.hidden) return;
  const jobs = REFRESH[page] || [];
  jobs.forEach(([key, fn, ms]) => {
    fn();
    // ms=0 表示只拉一次（静态数据）
    if (ms > 0) timers[key] = setInterval(fn, ms);
  });
}

function goto(p) {
  page = p;
  document.querySelectorAll('.nav-item').forEach((b) => {
    b.classList.toggle('active', b.dataset.page === p);
  });
  document.querySelectorAll('.page').forEach((s) => {
    s.classList.toggle('active', s.id === 'page-' + p);
  });
  startTimers();
}

/* ================================================================
   事件绑定
   ================================================================ */
document.querySelectorAll('.nav-item').forEach((b) => {
  b.addEventListener('click', () => goto(b.dataset.page));
});

$('btnStart').addEventListener('click', async () => {
  const btn = $('btnStart');
  btn.disabled = true;
  btn.textContent = '启动中…';
  const r = await post('/api/bot/start', { supervisor: true });
  btn.textContent = '启动';
  if (!r.ok) alert(r.msg || '启动失败');
  setTimeout(() => { refreshBot(); startTimers(); }, 1500);
});

$('btnStop').addEventListener('click', async () => {
  if (!confirm('确定停止机器人？')) return;
  const btn = $('btnStop');
  btn.disabled = true;
  const r = await post('/api/bot/stop');
  if (!r.ok) alert(r.msg || '停止失败');
  setTimeout(refreshBot, 1200);
});

$('btnLogFollow').addEventListener('click', () => {
  logFollow = !logFollow;
  $('btnLogFollow').textContent = logFollow ? '跟随中' : '已暂停';
});

$('btnLogClear').addEventListener('click', () => {
  $('logbox').innerHTML = '';
});

$('dexSearch').addEventListener('input', (e) => {
  clearTimeout(dexTimer);
  dexTimer = setTimeout(() => refreshDex(e.target.value.trim()), 220);
});

// 页面隐藏时停掉所有轮询 —— 后台标签页不该烧 CPU
document.addEventListener('visibilitychange', () => {
  if (document.hidden) stopTimers();
  else startTimers();
});

/* ---------------------------------------------------------------- 启动 */
refreshBot();
setInterval(refreshBot, 3000);
startTimers();

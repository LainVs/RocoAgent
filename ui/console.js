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

/* ---------------------------------------------------------------- 反馈 */
// toast：右下角堆叠，可手动关。ms <= 0 表示不自动消失。
function toast(msg, kind = 'ok', ms = 4200) {
  const host = $('toasts');
  if (!host) return null;
  const el = document.createElement('div');
  el.className = 'toast ' + kind;
  el.innerHTML = `<i class="tdot"></i><span class="tmsg">${esc(msg)}</span>` +
                 `<button class="tclose" aria-label="关闭">×</button>`;
  const drop = () => { if (el.parentNode) el.parentNode.removeChild(el); };
  el.querySelector('.tclose').addEventListener('click', drop);
  host.appendChild(el);
  if (ms > 0) setTimeout(drop, ms);
  return el;
}

// 连接状态只在"变化"时提示一次 —— 每 3 秒弹一条"服务未连接"等于刷屏。
let connState = null;      // null=还没判断过 / 'ok' / 'down'
function setConn(ok) {
  if (ok) {
    if (connState === 'down') toast('已重新连上本地服务', 'ok', 2500);
    connState = 'ok';
  } else if (connState !== 'down') {
    // 首次就失败时不弹（可能页面刚打开、服务还没起）
    if (connState !== null) toast('与本地服务断开，正在重试…', 'err', 0);
    connState = 'down';
  }
}

/* ================================================================
   机器人状态
   ================================================================ */
const PHASE_TEXT = {
  live: '运行中',
  stale: '运行中（无数据）',
  stopped: '未运行',
  // 下面四种是"进程活着但卡住了"——不能再用绿灯。
  waiting_board: '等待板子',
  ble_failed: '板子连接失败',
  focus_lost: '焦点丢失',
  board_offline: '板子离线',
};

// 状态 -> 指示灯等级。bad 用红色，stale 用琥珀色。
const PHASE_LEVEL = {
  live: 'live',
  stale: 'stale',
  stopped: 'off',
  waiting_board: 'stale',
  ble_failed: 'bad',
  focus_lost: 'stale',
  board_offline: 'bad',
};

// 秒数 -> 人话。状态文案里要带"多久了"，否则用户不知道是新问题还是旧残留。
function fmtAge(s) {
  if (s == null) return '';
  if (s < 90) return Math.round(s) + 's';
  if (s < 5400) return Math.round(s / 60) + 'min';
  return Math.round(s / 3600) + 'h';
}

async function refreshBot() {
  let b;
  try {
    b = await get('/api/bot');
    setConn(true);
  } catch (e) {
    setConn(false);
    $('botText').textContent = '服务未连接';
    $('botDot').className = 'dot off';
    const link = $('botLink');
    if (link) link.hidden = true;
    return;
  }

  const dot = $('botDot');
  const text = $('botText');
  const pid = $('botPid');

  // 状态灯 + 文案。新增的四种"卡住"状态必须和 live 区分开 ——
  // 2026-11-21 16:06 那次板子掉线，界面上一直是绿色"运行中"。
  const phase = b.phase || 'stopped';
  dot.className = 'dot ' + (PHASE_LEVEL[phase] || 'off');
  const act = b.activity || {};
  let label = PHASE_TEXT[phase] || phase;
  if (phase !== 'live' && phase !== 'stopped' && act.age != null) {
    label += ' ' + fmtAge(act.age);
  }
  text.textContent = label;
  text.title = act.text || '';
  pid.textContent = b.pid ? 'PID ' + b.pid : '';

  // 输入链路的小标：这是"能不能打"的第一道门，放在顶栏一眼可见。
  const link = $('botLink');
  if (link) {
    const bd = b.board || {};
    // "connected" 也可能是很久以前的日志（espfast 停了不会写新行）。
    // 超过 10 分钟就降级成"最后已连"，不能拿旧记录当现在连上了。
    const stale = bd.age != null && bd.age > 600;
    if (!bd.state) {
      link.hidden = true;
    } else {
      link.hidden = false;
      link.className = 'top-link ' + (bd.state === 'offline' ? 'bad'
        : bd.state === 'connected' && !stale ? 'ok' : 'warn');
      link.textContent = bd.state === 'offline' ? '板子离线'
        : bd.state === 'connected'
          ? (stale ? `板子 ${fmtAge(bd.age)}前` : '板子已连')
          : '板子待连';
      link.title = [bd.transport, bd.text].filter(Boolean).join(' · ');
    }
  }

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
function healthItem(label, value, level, cls) {
  return `<div class="hitem ${level}">
    <i class="hdot"></i>
    <div class="hbody">
      <div class="hlabel">${label}</div>
      <div class="hvalue${cls ? ' ' + cls : ''}">${value}</div>
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

  // 0. 输入链路（ESP32）—— 能不能打的第一道门。
  //
  // 为什么放在第一位：2026-11-21 16:06 机器人挂掉的原因就是板子掉线
  // （`link down, reconnecting` → `Device not found`），而当时的健康
  // 面板只关心"血条/技能栏"，看起来一切正常。能不能出招，先看这条。
  const bd = b && b.board;
  if (!b) {
    items.push(healthItem('输入链路', '服务未连接', 'bad'));
  } else if (!bd || !bd.state) {
    items.push(healthItem('输入链路', '无日志', ''));
  } else if (bd.state === 'connected') {
    const stale = bd.age != null && bd.age > 600;
    items.push(healthItem('输入链路',
      `${stale ? '最后已连' : '已连'} <small>${bd.age != null ? fmtAge(bd.age) + '前' : ''}</small>`,
      stale ? 'warn' : 'ok', 'has-sub'));
  } else if (bd.state === 'offline') {
    items.push(healthItem('输入链路',
      `离线 <small>${bd.age != null ? fmtAge(bd.age) + '前' : '无时间'}</small>`,
      'bad', 'has-sub'));
  } else {
    items.push(healthItem('输入链路', '守护在、未连板', 'warn'));
  }

  // 1. 机器人进程
  if (!b) {
    items.push(healthItem('机器人', '服务未连接', 'bad'));
  } else if (b.phase === 'stopped') {
    items.push(healthItem('机器人', '未运行', ''));
  } else {
    // 非 live 时把"卡在哪"写进同一格，避免用户只看绿灯。
    const act = b.activity || {};
    const stuck = b.phase !== 'live';
    const sub = stuck
      ? ` <small>${PHASE_TEXT[b.phase] || b.phase}${act.age != null ? ' ' + fmtAge(act.age) : ''}</small>`
      : '';
    items.push(healthItem('机器人', `PID ${b.pid}${sub}`,
      stuck ? 'warn' : 'ok', sub ? 'has-sub' : ''));
  }

  // 2. 状态推送新鲜度
  // 机器人没跑时不该报"过期"—— 那是噪音，不是故障
  const running = b && b.running;
  if (!running) {
    items.push(healthItem('状态推送', '—', ''));
  } else if (b.state_age == null) {
    items.push(healthItem('状态推送', '无数据', 'bad'));
  } else if (b.state_age < 3) {
    items.push(healthItem('状态推送', `${b.state_age.toFixed(1)}s 前`, 'ok'));
  } else {
    items.push(healthItem('状态推送', `${b.state_age.toFixed(0)}s 前（过期）`, 'warn'));
  }

  // 3. 感知耗时
  //
  // 判据不能是一个魔法数字。主循环每轮 sleep 的下限是 config.POLL_S
  // （120ms），一轮的实际周期 ≈ 感知耗时 + 间隔，所以：
  //   <= 间隔       正常（周期 < 2×间隔）
  //   <= 2×间隔     偏慢（周期翻倍，出招会明显迟）
  //   >  2×间隔     异常
  // 改前实测 387ms -> 周期 507ms（2Hz）；现在 ~85ms -> 周期 205ms（5Hz）。
  const ms = st && st.system ? st.system.fps_ms : null;
  const budget = st && st.system && st.system.budget_ms
    ? st.system.budget_ms : 120;
  if (ms == null) {
    items.push(healthItem('感知耗时', '—', ''));
  } else {
    const lv = ms > budget * 2 ? 'bad' : ms > budget ? 'warn' : 'ok';
    const rate = (1000 / (ms + budget)).toFixed(1);
    items.push(healthItem('感知耗时',
      `${ms.toFixed(0)} ms <small>${budget} 间隔 · ${rate} Hz</small>`,
      lv, 'has-sub'));
  }

  // 4-6. 三个检测器（各自独立，出问题能定位到哪一环）
  //
  // 关键：**不在战斗时一律不判红**。技能卡只在轮到我方时才画出来，
  // 所以非我方回合 bar=False / countdown=False 是正常现象 ——
  // 把它们标红是过去控制台"一直红"的来源之一。
  // 真正代表"看不清局面"的是下面第 7 格的「盲打」次数。
  const dets = (st && st.system && st.system.detectors) || {};
  const inBattle = !!(st && st.mode === 'battle');
  const DET_LABEL = {
    bar: '血条',
    skillbar: '技能栏',
    countdown: '倒计时',
  };
  Object.keys(DET_LABEL).forEach((k) => {
    if (!st) {
      items.push(healthItem(DET_LABEL[k], '—', ''));
      return;
    }
    if (!inBattle) {
      items.push(healthItem(DET_LABEL[k], '不在战斗', ''));
      return;
    }
    const on = !!dets[k];
    if (!on) {
      items.push(healthItem(DET_LABEL[k], '未画', ''));
      return;
    }
    items.push(healthItem(DET_LABEL[k], '正常', 'ok'));
  });

  // 7. 盲打次数（本局累计）
  const blind = st && st.system ? st.system.blind : null;
  if (blind == null) {
    items.push(healthItem('盲打', '—', ''));
  } else if (blind === 0) {
    items.push(healthItem('盲打', '0 次 <small>每手都看清了</small>', 'ok'));
  } else {
    items.push(healthItem('盲打',
      `${blind} 次 <small>没看到技能栏就出招</small>`, 'warn', 'has-sub'));
  }

  // 8. 决策门（被拦住时显示原因，这是排查卡住的关键）
  const gate = st && st.system ? st.system.gate : null;
  if (!st) {
    items.push(healthItem('决策门', '—', ''));
  } else {
    items.push(healthItem('决策门', gate || '畅通', gate ? 'warn' : 'ok'));
  }

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

  // 过期数据必须明确标注 —— 否则用户会以为这是当前局面
  const stale = !!st._stale;
  age.innerHTML = stale
    ? `<span style="color:var(--warn)">数据已过期 ${Math.round(st._age)}s（对局可能已结束）</span>`
    : '实时';

  // 过期时整块降低不透明度，视觉上一眼看出"这不是现在"
  body.style.opacity = stale ? '0.55' : '1';

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
    <div class="tbl-wrap">
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
    </table>
    </div>` : ''}
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

  // 主口径：游戏自己记的累计胜负（logs/record_state.json）。
  // 为什么不用 results.jsonl 的 delta 当主口径：那份文件 92% 是 flat，
  // 而 flat 里 100 条是段位卡读数卡死在 2/3 造成的假平局。
  // 拿它算胜率会得出 66.7%，真实是 44.8%。
  const season = s.season || null;
  const stuck = !!s.badge_stuck;
  const rate = season ? season.rate : s.rate;
  const wins = season ? season.wins : s.wins;
  const losses = season ? season.losses : s.losses;
  const decided = season ? season.decided : s.decided;

  let sub;
  if (season) {
    sub = `赛季累计 ${decided} 场` + (season.at ? ` · ${season.at} 读取` : '');
  } else if (s.reliable) {
    sub = `${s.decided} 场已判定`;
  } else {
    sub = `样本不足（${s.decided}/20），胜率不可信`;
  }
  if (stuck) sub += ' · 段位卡读数卡死，已改用权威战绩';

  // 概览页
  if ($('statSub')) $('statSub').textContent = sub;
  const rateCls = rate == null ? '' : (rate >= 55 ? 'win' : rate >= 45 ? 'warn' : 'lose');
  $('statMetrics').innerHTML =
    metricHtml('胜率', rate == null ? '—' : rate, '%',
      season ? '游戏内累计战绩' : '', rateCls) +
    metricHtml('胜 / 负', `${wins} / ${losses}`, '', '', '') +
    metricHtml('段位', s.rank ? `${s.rank.now}/${s.rank.of}` : '—', '',
      s.rank ? '星 / 本段' : '', '') +
    metricHtml('近 20 场', s.rate20 == null ? '—' : s.rate20, '%',
      stuck ? '本文档内统计（不可信）' : `${s.last20.length} 场`, '');

  // 走势
  const seq = s.last20 || [];
  $('spark').innerHTML = seq.length
    ? seq.map((v) => `<i class="${v === 1 ? 'w' : v === -1 ? 'l' : ''}"
        style="height:${v === 0 ? 18 : 100}%"></i>`).join('')
    : `<span style="color:var(--ink-3);font-size:11px">还没有已判定的对局</span>`;

  // 战绩页
  if ($('statsSub')) $('statsSub').textContent = sub;
  $('statsMetrics').innerHTML =
    metricHtml('赛季胜率', rate == null ? '—' : rate, '%',
      season ? `${wins} 胜 / ${losses} 负` : '', rateCls) +
    metricHtml('胜', wins, '场', '', 'win') +
    metricHtml('负', losses, '场', '', 'lose') +
    metricHtml('段位卡推断', `${s.wins} / ${s.losses}`, '',
      stuck ? '读数卡死，不可用' : 'results.jsonl 的已判定项',
      stuck ? 'warn' : '') +
    metricHtml('保分记录', s.flats, '条',
      stuck ? '疑似假平局' : '未判定胜负');
}

async function refreshMatches() {
  let d;
  try {
    d = await get('/api/matches?limit=40');
  } catch (e) {
    return;
  }
  const rows = d.items || [];
  const tb = $('matchesTbl').querySelector('tbody');
  const sub = $('matchesSub');
  if (sub) {
    const linked = rows.filter((r) => !r.legacy).length;
    sub.textContent = rows.length
      ? `${rows.length} 场，其中 ${linked} 场可按 id 对齐（其余是没有场次 id 的老记录）`
      : '还没有对局记录';
  }
  if (!rows.length) {
    tb.innerHTML = `<tr><td colspan="8" class="muted">还没有对局记录</td></tr>`;
    return;
  }
  const RES = { win: ['胜', 'win'], loss: ['负', 'lose'],
                flat: ['平', 'flat'], unknown: ['—', 'muted'] };
  tb.innerHTML = rows.map((r) => {
    const [txt, cls] = RES[r.result] || RES.unknown;
    // 读数 = 可读槽位 / 总槽位；老记录没有回合数据就留空
    const rate = r.slots
      ? `${r.slots - r.slots_bad}/${r.slots}`
      : '—';
    // 备注：优先提示会直接影响出招质量的东西
    const notes = [];
    if (r.legacy) notes.push('老记录(无 id)');
    if (r.blind) notes.push(`盲打 ${r.blind}`);
    if (r.unreadable) notes.push(`无数字 ${r.unreadable}`);
    return `<tr>
      <td class="num" title="${esc(r.mid)}">${esc(r.short)}</td>
      <td class="num muted">${esc(r.start || '—')}</td>
      <td class="num">${r.seconds != null ? Math.round(r.seconds) + 's' : '—'}</td>
      <td class="num">${r.decisions != null ? r.decisions : '—'}</td>
      <td><span class="res ${cls}">${txt}</span>${
        r.result_src === 'badge' ? '<i class="res-src" title="来自段位卡推断，可信度低">*</i>' : ''}</td>
      <td class="muted">${esc(r.foe || '—')}</td>
      <td class="num">${rate}</td>
      <td class="muted">${esc(notes.join(' · ') || '—')}</td>
    </tr>`;
  }).join('');
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

  // 队伍应该 6 只。不足时提示，而不是让用户以为只有一只
  const missing = Math.max(0, 6 - slots.length);
  const hint = missing
    ? `<div class="team-hint">还有 ${missing} 个槽位没录
         <code>python src\\team_db.py --set ${slots.length + 1} --species 名称</code>
       </div>`
    : '';

  grid.innerHTML = hint + slots.map((s) => `
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
   头像标注
   ----------------------------------------------------------------
   旧 UI 移除后一直没有替代入口，积压了 1122 张待标注（已标注只有 115）。
   头像是识别对面精灵的唯一可靠信号，标注准确率直接影响识别率。

   交互设计：
     · 名字条截图和头像并排显示 —— 标注时可以直接对照名字
     · 输入名字回车即提交（不用点按钮）
     · 提交成功后卡片淡出，不用手动刷新
     · 支持"跳过"（删掉不要的截图）
   ================================================================ */
let labelSide = 'foe';
let labelBusy = false;

async function refreshLabel() {
  let d;
  try {
    d = await get('/api/portraits?side=' + labelSide + '&limit=60');
  } catch (e) {
    return;
  }
  const items = d.items || [];
  const groups = d.groups || {};
  $('labelSub').textContent =
    `${items.length} / ${d.total || 0} 张待标注` +
    (d.dup_count ? `（${d.dup_count} 组同款）` : '');

  const grid = $('labelGrid');
  if (!items.length) {
    grid.innerHTML = `<div class="empty"><b>没有待标注的头像</b>
      <span>跑几局机器人后，未识别的头像会自动存到这里</span></div>`;
    return;
  }

  grid.innerHTML = items.map((it, i) => {
    const av = `portraits${labelSide === 'foe' ? '_foe' : ''}/` +
               encodeURIComponent(it.file);
    const nc = it.name_crop
      ? `portraits${labelSide === 'foe' ? '_foe' : ''}/` +
        encodeURIComponent(it.name_crop)
      : null;
    // 同款数量：同一只精灵往往有 5~10 张截图，
    // 标注一张后其余的应该一起标掉，否则 1122 张标不完
    const same = (d.groups && d.groups[it.stem]) || [];
    const dupBadge = same.length > 1
      ? `<span class="dup-badge" title="同款 ${same.length} 张">×${same.length}</span>`
      : '';
    // OCR 预填的三种状态：
    //   匹配到库里  -> 绿底，直接回车就对
    //   有 OCR 但库里没有 -> 黄底，需要看一眼（可能是错字或库里真没有）
    //   没识别出来  -> 空，手输
    const ocrTxt = it.ocr || '';
    const ocrConf = it.ocr_conf || 0;
    const ocrCls = !ocrTxt ? ''
      : (it.ocr_matched ? ' ocr-hi' : ' ocr-lo');
    const ocrNote = !ocrTxt ? ''
      : (it.ocr_matched
         ? ` · OCR ${Math.round(ocrConf * 100)}%`
         : ` · OCR ${Math.round(ocrConf * 100)}%（库里没有，原文「${it.ocr_raw || ''}」）`);
    // 名字条其实是场景截图（采集时 ROI 抓偏了）—— 标出来，
    // 这种 OCR 永远读不出，人工也标不了，应该批量删掉
    const junkTag = it.junk
      ? `<span class="junk-tag" title="名字条区域其实是场景截图">垃圾</span>`
      : '';
    return `<div class="label-card${it.junk ? ' junk' : ''}"
                 data-stem="${it.stem}"
                 data-sig="${it.sig || ''}" data-i="${i}">
      ${junkTag}
      <div class="label-imgs">
        <img class="av" src="${av}" alt="">
        ${nc ? `<img class="nc" src="${nc}" alt="名字条">` : ''}
      </div>
      <div class="label-row">
        <input class="label-input${ocrCls}" placeholder="精灵名…"
               value="${ocrTxt}" autocomplete="off">
        <button class="label-btn ok">标注</button>
        <button class="label-btn skip">跳过</button>
        ${dupBadge}
      </div>
      <div class="label-hint">${it.stem}${ocrNote}</div>
    </div>`;
  }).join('');

  // 绑定：回车提交 / 按钮提交 / 跳过
  grid.querySelectorAll('.label-card').forEach((card) => {
    const stem = card.dataset.stem;
    const input = card.querySelector('.label-input');
    const btnOk = card.querySelector('.label-btn.ok');
    const btnSkip = card.querySelector('.label-btn.skip');

    const submit = async () => {
      const species = input.value.trim();
      if (!species || labelBusy) return;
      labelBusy = true;
      btnOk.textContent = '…';

      // 同款一起标：同一只精灵往往有 5~10 张截图，
      // 一张张标 1122 张会疯。有同款时问一次。
      const same = (groups[stem]) || [];
      const others = same.filter((s) => s !== stem);

      let r;
      if (others.length > 0) {
        const yes = confirm(
          `还有 ${others.length} 张同款（同一个名字条）。\n` +
          `一起标注为「${species}」吗？\n\n` +
          `选"取消"则只标这一张。`);
        r = yes
          ? await post('/api/portrait/label_group',
                       { side: labelSide, species, stems: same })
          : await post('/api/portrait/label',
                       { side: labelSide, stem, species });
      } else {
        r = await post('/api/portrait/label',
                       { side: labelSide, stem, species });
      }

      labelBusy = false;
      btnOk.textContent = '标注';

      if (r.ok) {
        // 把这一组（含同款）的卡片全部淡出
        const done = others.length > 0 ? same : [stem];
        done.forEach((s) => {
          const c = grid.querySelector(`.label-card[data-stem="${s}"]`);
          if (c) c.classList.add('done');
        });
        // 自动聚焦下一张未完成的
        const next = [...grid.querySelectorAll('.label-card:not(.done)')]
          .find((c) => {
            const r2 = c.getBoundingClientRect();
            return r2.top > card.getBoundingClientRect().top - 50;
          });
        if (next) {
          const ni = next.querySelector('.label-input');
          if (ni) ni.focus();
        }
        refreshLabelCount();
      } else {
        input.style.borderColor = 'var(--lose)';
        setTimeout(() => { input.style.borderColor = ''; }, 1500);
        toast(r.msg || '标注失败', 'err');
      }
    };

    btnOk.addEventListener('click', submit);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') submit();
    });

    btnSkip.addEventListener('click', async () => {
      const r = await post('/api/portrait/skip',
        { side: labelSide, stem });
      if (r.ok) {
        card.classList.add('done');
        refreshLabelCount();
      }
    });
  });

  // 自动聚焦第一个输入框
  const first = grid.querySelector('.label-input');
  if (first) first.focus();
}

async function refreshLabelCount() {
  try {
    const d = await get('/api/portraits?side=' + labelSide + '&limit=1');
    $('labelSub').textContent = `待标注 ${d.total || 0} 张`;
  } catch (e) { /* 忽略 */ }
}

function bindLabelSide() {
  document.querySelectorAll('#labelSide button').forEach((b) => {
    b.addEventListener('click', () => {
      document.querySelectorAll('#labelSide button')
        .forEach((x) => x.classList.remove('active'));
      b.classList.add('active');
      labelSide = b.dataset.side;
      refreshLabel();
    });
  });
}

// 批量确认「OCR 精确匹配 + 高置信度」的。
// 实测 630 张里约 30% 属于这类，几乎不可能错，应该一次提交掉，
// 把人工精力留给真正模糊的。
//
// 两段式：先 dry_run 报告会标哪些，用户确认后才真跑。
// 为什么不直接跑：这会改动真实文件（改名），不可撤销。
function bindAuto() {
  const btn = $('btnAuto');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    btn.textContent = '统计中…';

    const dry = await post('/api/portrait/auto_confirm',
      { side: labelSide, min_conf: 0.9, dry_run: true });
    btn.disabled = false;
    btn.textContent = '批量确认';

    if (!dry.ok) {
      toast(dry.msg || '统计失败', 'err');
      return;
    }
    if (!dry.n) {
      toast('没有可批量确认的（需要 OCR 精确匹配且置信度 ≥ 90%）', 'warn');
      return;
    }

    // 列出前 8 个样例，让人知道会发生什么
    const sample = (dry.candidates || []).slice(0, 8)
      .map((c) => `  ${c.stem} → ${c.species}  ${Math.round(c.conf * 100)}%`)
      .join('\n');
    const more = dry.n > 8 ? `\n  …还有 ${dry.n - 8} 张` : '';
    const msg = `将标注 ${dry.n} 张（OCR 精确匹配 + 置信度 ≥ 90%）\n\n` +
                `${sample}${more}\n\n` +
                `这是改名操作，不可撤销。继续吗？`;
    if (!confirm(msg)) return;

    btn.disabled = true;
    btn.textContent = '标注中…';
    const r = await post('/api/portrait/auto_confirm',
      { side: labelSide, min_conf: 0.9, dry_run: false });
    btn.disabled = false;
    btn.textContent = '批量确认';

    let extra = '';
    if (r.fails && r.fails.length) {
      extra = `\n失败 ${r.fails.length} 张：\n` +
        r.fails.slice(0, 5).map((f) => `  ${f.stem}: ${f.msg}`).join('\n');
    }
    toast((r.msg || '完成') + extra, r.ok ? 'ok' : 'err', r.ok ? 4200 : 8000);
    refreshLabel();
  });
}

// 批量清理"名字条其实是场景截图"的垃圾。
// 实测 36% 的待标注是这种（采集时 ROI 抓偏了），留着只是噪声。
function bindPurge() {
  const btn = $('btnPurge');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    const n = document.querySelectorAll('.label-card.junk').length;
    const msg = n > 0
      ? `当前页有 ${n} 张标记为「垃圾」（名字条区域其实是场景截图）。\n\n` +
        `清理会删除这些头像和对应的名字条截图，不可恢复。\n` +
        `有 OCR 结果的一律不碰。\n\n继续吗？`
      : `清理会扫描所有待标注，删除「名字条区域其实是场景截图」的那些。\n` +
        `有 OCR 结果的一律不碰。\n\n继续吗？`;
    if (!confirm(msg)) return;
    btn.disabled = true;
    btn.textContent = '清理中…';
    const r = await post('/api/portrait/purge_junk', { side: labelSide });
    btn.disabled = false;
    btn.textContent = '清理垃圾';
    toast(r.msg || '完成', r.ok ? 'ok' : 'err');
    refreshLabel();
  });
}

/* ================================================================
   页面切换 + 定时器管理
   ================================================================ */
const REFRESH = {
  overview: [['health', refreshHealth, 1500], ['live', refreshLive, 1000],
             ['stats', refreshStats, 8000]],
  logs: [['log', pollLog, 700]],
  stats: [['stats', refreshStats, 8000], ['matches', refreshMatches, 8000]],
  team: [['team', refreshTeam, 30000]],
  // 图鉴是静态数据，进页面拉一次就够，不轮询
  dex: [['dex', () => refreshDex($('dexSearch').value.trim()), 0]],
  turns: [['turns', refreshTurns, 10000]],
  // 标注页不轮询 —— 标注过程中刷新会把输入框清掉
  label: [['label', refreshLabel, 0]],
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
  let r;
  try {
    r = await post('/api/bot/start', { supervisor: true });
  } catch (e) {
    r = { ok: false, msg: '请求失败：' + e.message };
  }
  btn.textContent = '启动';
  if (r.ok) {
    toast('机器人已启动，正在等板子…', 'ok');
  } else {
    toast(r.msg || '启动失败', 'err', 7000);
  }
  setTimeout(() => { refreshBot(); startTimers(); }, 1500);
});

$('btnStop').addEventListener('click', async () => {
  if (!confirm('确定停止机器人？')) return;
  const btn = $('btnStop');
  btn.disabled = true;
  let r;
  try {
    r = await post('/api/bot/stop');
  } catch (e) {
    r = { ok: false, msg: '请求失败：' + e.message };
  }
  if (r.ok) toast('机器人已停止', 'ok');
  else toast(r.msg || '停止失败', 'err', 7000);
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

/* ---------------------------------------------------------------- 键盘
   1~7 切页 / `/` 聚焦图鉴搜索 / Esc 退出输入。
   只在没有输入焦点时生效 —— 头像标注页要打精灵名，不能被切页抢走。 */
const NAV_ORDER = ['overview', 'logs', 'stats', 'team', 'dex', 'turns', 'label'];
document.addEventListener('keydown', (e) => {
  const t = e.target;
  const typing = !!(t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA'
    || t.isContentEditable));
  if (e.key === 'Escape') {
    if (typing) t.blur();
    return;
  }
  if (typing || e.ctrlKey || e.altKey || e.metaKey) return;
  if (e.key === '/') {
    e.preventDefault();
    goto('dex');
    setTimeout(() => {
      const s = $('dexSearch');
      if (s) s.focus();
    }, 40);
    return;
  }
  const n = parseInt(e.key, 10);
  if (n >= 1 && n <= NAV_ORDER.length) {
    const target = NAV_ORDER[n - 1];
    goto(target);
    const btn = document.querySelector(`.nav-item[data-page="${target}"]`);
    if (btn) btn.focus();
  }
});

/* ---------------------------------------------------------------- 启动 */
refreshBot();
setInterval(refreshBot, 3000);
bindLabelSide();
bindAuto();
bindPurge();
startTimers();

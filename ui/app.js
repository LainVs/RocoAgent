/* ============================================================
   RocoAgent 对战悬浮窗
   ------------------------------------------------------------
   两种数据来源，自动降级：
     1. 机器人推送（ui_server 的 /api/state，新鲜 < 3s）→ 零额外开销
     2. 自己感知（独立运行，自己抓屏）→ 还没接，先显示空态

   性能约束：
     · 10Hz 轮询，只在数据变化时碰 DOM
     · 无变化时整帧跳过
     · document.hidden 时完全停止
   ============================================================ */
'use strict';

const API = '';

/* ---------------------------------------------------------------- 属性色 */
const TYPE_COLOR = {
  普通: '#A8A878', 火: '#F08030', 水: '#6890F0', 草: '#78C850',
  电: '#F8D030', 冰: '#98D8D8', 武: '#C03028', 毒: '#A040A0',
  地: '#E0C068', 翼: '#A890F0', 萌: '#EE99AC', 幻: '#C080FF',
  虫: '#A8B820', 幽: '#705898', 机械: '#B8B8D0', 龙: '#7038F8',
  恶: '#705848', 光: '#FFF68F',
};

// 克制倍率 → 强调色。面板整体色调本身就是情报。
const ACCENT = {
  strong: '#FF6B4A',
  even:   '#7FD3E8',
  weak:   '#5B7FD4',
};

const $ = (id) => document.getElementById(id);
const root = document.documentElement;

let folded = false;
let lastSig = '';
let pollTimer = null;

/* ---------------------------------------------------------------- 工具 */
function hexA(hex, a) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}

function setAccent(kind) {
  const c = ACCENT[kind] || ACCENT.even;
  root.style.setProperty('--accent', c);
  root.style.setProperty('--accent-soft', hexA(c, 0.55));
  root.style.setProperty('--accent-dim', hexA(c, 0.16));
}

function hpLevel(p) {
  if (p == null) return 'ok';
  if (p <= 25) return 'low';
  if (p <= 55) return 'mid';
  return 'ok';
}

function typeChips(el, types) {
  el.innerHTML = (types || []).map((t) => {
    const c = TYPE_COLOR[t] || '#8A8F98';
    return `<span class="tchip" style="
      --tc-text:${c};
      --tc-bg:${hexA(c, 0.16)};
      --tc-bd:${hexA(c, 0.34)};
    ">${t}</span>`;
  }).join('');
}

function energyPips(n, max) {
  let out = '';
  for (let i = 0; i < max; i++) out += `<i class="${i < n ? 'on' : ''}"></i>`;
  return out;
}

/* 头像限并发加载（120 张同时请求会把服务打满） */
const IMG_Q = [];
const IMG_ACTIVE = new Set();
const IMG_MAX = 4;

function loadAvatar(el, name, side) {
  if (!el || !name) return;
  IMG_Q.push({ el, name, side });
  pumpImages();
}

function pumpImages() {
  while (IMG_ACTIVE.size < IMG_MAX && IMG_Q.length) {
    runImage(IMG_Q.shift());
  }
}

function runImage({ el, name, side }) {
  const file = encodeURIComponent(name) + '.png';
  const dirs = side === 'foe'
    ? ['portraits_foe', 'portraits']
    : ['portraits', 'portraits_foe'];
  let idx = 0;
  const img = new Image();
  IMG_ACTIVE.add(img);

  const done = () => { IMG_ACTIVE.delete(img); pumpImages(); };

  img.onload = () => { el.textContent = ''; el.replaceChildren(img); done(); };
  img.onerror = () => {
    idx += 1;
    if (idx < dirs.length) { img.src = dirs[idx] + '/' + file; return; }
    el.textContent = name.slice(0, 1);
    done();
  };
  img.src = dirs[0] + '/' + file;
}

/* ================================================================
   渲染
   ================================================================ */
function skillTier(s) {
  if (s.dmg == null || s.pct == null) return '';
  if (s.pct >= 100) return ' kill hot';
  if (s.pct >= 40) return ' high hot';
  if (s.pct >= 20) return ' mid';
  return '';
}

function render(st) {
  // ── 系统栏
  const sys = st.system || {};
  const dets = sys.detectors || {};
  const detArr = [dets.bar, dets.skillbar, dets.countdown];

  $('sysTurn').textContent = st.turn != null ? 'T' + st.turn : 'T—';

  const clock = $('sysClock');
  if (st.clock != null) {
    clock.textContent = String(st.clock).padStart(2, '0') + 's';
    clock.classList.toggle('urgent', st.clock <= 5);
  } else if (dets.countdown) {
    // 机器人只判断"倒计时数字在不在"，不读具体秒数（那要 OCR）。
    // 不假装有数据，只表示"决策窗口开着"。
    clock.textContent = '●';
    clock.classList.remove('urgent');
  } else {
    clock.textContent = '';
    clock.classList.remove('urgent');
  }

  const ms = $('sysMs');
  if (sys.fps_ms != null) {
    ms.textContent = sys.fps_ms.toFixed(1) + 'ms';
    ms.className = 'hdr-ms' + (sys.fps_ms > 200 ? ' bad'
      : sys.fps_ms > 50 ? ' warn' : '');
  } else {
    ms.textContent = '';
    ms.className = 'hdr-ms';
  }

  $('sysDets').innerHTML = detArr
    .map((on) => `<i class="${on ? 'on' : ''}"></i>`).join('');

  const live = $('liveDot');
  const anyLive = detArr.some((x) => x);
  live.className = 'live-dot' + (st._stale ? ' warn' : anyLive ? '' : ' off');

  const stateText = st._stale ? '数据过期'
    : st.mode === 'battle' ? '战斗中'
    : st.mode === 'idle' ? '待机'
    : '未连接';
  $('sysState').textContent = stateText;

  // ── 我方
  const me = st.me || {};
  $('meName').textContent = me.name || '未知';
  loadAvatar($('meAvatar'), me.name, 'me');
  typeChips($('meTypes'), me.types);
  $('mePct').textContent = me.hp_pct == null ? '—' : Math.round(me.hp_pct);
  $('meAbs').textContent = (me.hp_abs != null && me.hp_max != null)
    ? `${me.hp_abs} / ${me.hp_max}` : '—';
  $('meEnergy').innerHTML = me.energy != null
    ? energyPips(me.energy, me.energy_max || 10) : '';
  const buff0 = (me.buffs || [])[0];
  $('meBuff').textContent = buff0
    ? (buff0.name || '') + (buff0.layers ? ' +' + buff0.layers : '') : '';
  $('meBuff').className = 'buff';

  $('meBar').dataset.lvl = hpLevel(me.hp_pct);
  const meP = me.hp_pct == null ? 0 : me.hp_pct / 100;
  root.style.setProperty('--me-p', meP.toFixed(4));
  root.style.setProperty('--me-g', Math.min(1, meP + 0.05).toFixed(4));

  // ── 克制
  if (st.matchup) {
    const m = st.matchup;
    $('muOut').textContent = '×' + Number(m.out).toFixed(1);
    $('muIn').textContent = '×' + Number(m.in).toFixed(1);
    $('muOut').parentElement.className =
      'mu-side' + (m.out > 1 ? ' good' : m.out < 1 ? ' bad' : '');
    $('muIn').parentElement.className =
      'mu-side' + (m.in > 1 ? ' bad' : m.in < 1 ? ' good' : '');
    const tag = $('muTag');
    tag.textContent = m.tag || '';
    tag.className = 'mu-tag' + (m.out > m.in ? ' good' : m.out < m.in ? ' bad' : '');
  } else {
    $('muOut').textContent = '×—';
    $('muIn').textContent = '×—';
    $('muTag').textContent = '属性未知';
    $('muTag').className = 'mu-tag';
  }

  // 强调色：由克制关系推出来
  if (st.matchup) {
    setAccent(st.matchup.out > st.matchup.in ? 'strong'
      : st.matchup.out < st.matchup.in ? 'weak' : 'even');
  } else {
    setAccent('even');
  }

  // ── 敌方
  const foe = st.foe || {};
  $('foeName').textContent = foe.name || '未知';
  loadAvatar($('foeAvatar'), foe.name, 'foe');
  typeChips($('foeTypes'), foe.types);
  $('foePct').textContent = foe.hp_pct == null ? '—' : Math.round(foe.hp_pct);
  $('foeHpRange').textContent = foe.hp_range
    ? `${foe.hp_range[0]}~${foe.hp_range[1]}` : '—';
  $('foeDefRange').textContent = foe.def_range
    ? `${foe.def_range[0]}~${foe.def_range[1]}` : '—';
  $('foeNature').textContent = foe.nature || '—';
  $('foeConf').textContent = foe.nature_conf != null
    ? Math.round(foe.nature_conf * 100) + '%' : '';

  $('foeBar').dataset.lvl = hpLevel(foe.hp_pct);
  const foeP = foe.hp_pct == null ? 0 : foe.hp_pct / 100;
  root.style.setProperty('--foe-p', foeP.toFixed(4));
  root.style.setProperty('--foe-g', Math.min(1, foeP + 0.05).toFixed(4));

  // 推测置信度点阵（0~5 格）
  const conf = foe.nature_conf == null ? 0 : Math.round(foe.nature_conf * 5);
  $('foeDots').innerHTML = Array.from({ length: 5 }, (_, i) =>
    `<i class="${i < conf ? 'on' : ''}"></i>`).join('');

  // ── 技能
  // 机器人只读卡面伤害，不读技能名。名字为空时显示"卡 N"，绝不编造。
  const wrap = $('skills');
  wrap.innerHTML = '';
  (st.skills || []).forEach((s, i) => {
    const na = s.dmg == null;
    const idx = s.i != null ? s.i : i + 1;
    const row = document.createElement('div');
    row.className = 'skill' + (na ? ' na' : '') + skillTier(s);
    row.style.setProperty('--w', na || s.pct == null ? 0
      : Math.min(1, s.pct / 100).toFixed(4));

    const meta = [s.element, s.cost != null ? s.cost : null]
      .filter((x) => x != null).join('·');
    const label = s.name || `卡 ${idx}`;

    row.innerHTML = `
      <span class="sk-idx">${idx}</span>
      <span class="sk-name${s.name ? '' : ' unnamed'}">${label}${
        meta ? `<span class="sk-meta">${meta}</span>` : ''}</span>
      <span class="sk-dmg">${na ? '—' : s.dmg}</span>
      <span class="sk-pct">${na ? (s.note || '—')
        : (s.pct != null ? Math.round(s.pct) + '%' : '—')}</span>
      <span class="sk-bar"><i></i></span>
    `;
    wrap.appendChild(row);
  });

  // 没有技能数据时给个占位
  if (!(st.skills || []).length) {
    wrap.innerHTML = `<div class="empty-skills">技能栏未识别</div>`;
  }

  // ── 结论
  const v = st.verdict;
  if (v) {
    $('vKill').textContent = v.kill_turns != null ? v.kill_turns : '—';
    $('vDie').textContent = v.die_turns != null ? v.die_turns : '—';
    const order = $('vOrder');
    order.textContent = v.order === 'first' ? '先手'
      : v.order === 'second' ? '后手' : '—';
    order.className = v.order === 'first' ? 'good' : 'bad';
  } else {
    $('vKill').textContent = '—';
    $('vDie').textContent = '—';
    $('vOrder').textContent = '—';
    $('vOrder').className = '';
  }
}

/* ================================================================
   数据轮询
   ================================================================ */
async function poll() {
  if (document.hidden) return;
  let st;
  try {
    const r = await fetch(API + '/api/state', { cache: 'no-store' });
    st = await r.json();
  } catch (e) {
    $('sysState').textContent = '服务未连接';
    $('liveDot').className = 'live-dot off';
    return;
  }

  if (!st) {
    // 机器人没跑：显示空态，但保留布局不跳动
    renderEmpty();
    return;
  }

  // 无变化就不碰 DOM —— 这是 10Hz 下不卡的关键
  const sig = [
    st.seq, st.turn, st.clock,
    st.me && st.me.hp_pct, st.me && st.me.energy,
    st.foe && st.foe.hp_pct, st.foe && st.foe.name,
    (st.skills || []).map((s) => `${s.dmg}|${s.pct}|${s.name}`).join(','),
    st._stale,
  ].join('|');
  if (sig === lastSig) return;
  lastSig = sig;

  render(st);
}

function renderEmpty() {
  const sig = 'empty';
  if (sig === lastSig) return;
  lastSig = sig;

  $('sysState').textContent = '未连接';
  $('liveDot').className = 'live-dot off';
  $('sysTurn').textContent = 'T—';
  $('sysClock').textContent = '';
  $('sysMs').textContent = '';
  $('sysDets').innerHTML = '<i></i><i></i><i></i>';

  $('meName').textContent = '等待连接';
  $('mePct').textContent = '—';
  $('meAbs').textContent = '—';
  $('meTypes').innerHTML = '';
  $('meEnergy').innerHTML = '';
  $('meBuff').textContent = '';
  root.style.setProperty('--me-p', '0');
  root.style.setProperty('--me-g', '0');

  $('foeName').textContent = '等待连接';
  $('foePct').textContent = '—';
  $('foeTypes').innerHTML = '';
  $('foeHpRange').textContent = '—';
  $('foeDefRange').textContent = '—';
  $('foeNature').textContent = '—';
  $('foeConf').textContent = '';
  $('foeDots').innerHTML = '<i></i><i></i><i></i><i></i><i></i>';
  root.style.setProperty('--foe-p', '0');
  root.style.setProperty('--foe-g', '0');

  $('muOut').textContent = '×—';
  $('muIn').textContent = '×—';
  $('muTag').textContent = '等待连接';
  $('muTag').className = 'mu-tag';

  $('skills').innerHTML =
    `<div class="empty-skills">启动机器人后这里显示四张技能卡</div>`;
  $('vKill').textContent = '—';
  $('vDie').textContent = '—';
  $('vOrder').textContent = '—';

  setAccent('even');
}

/* ================================================================
   交互
   ================================================================ */

// 透明度：只改面板底色 alpha。文字带描边，20% 仍可读。
// 图形元素（血条底槽/边框）有下限补偿 —— 它们没有描边，会跟着面板消失。
function bindAlpha() {
  const slider = $('alpha');
  const label = $('alphaVal');
  const apply = () => {
    const v = +slider.value;
    const a = v / 100;
    root.style.setProperty('--alpha', a.toFixed(2));
    label.textContent = v + '%';

    const boost = Math.min(1, 0.55 + (1 - a) * 0.55);
    root.style.setProperty('--well-a', (0.34 * boost).toFixed(3));
    root.style.setProperty('--line-a', (0.085 * boost).toFixed(3));
    root.style.setProperty('--line-soft-a', (0.045 * boost).toFixed(3));
    root.style.setProperty('--edge-a',
      (0.10 * boost + (1 - a) * 0.10).toFixed(3));
    root.style.setProperty('--edge-hi-a', (0.11 * boost).toFixed(3));

    try { localStorage.setItem('overlay.alpha', String(v)); } catch (_) {}
  };
  slider.addEventListener('input', apply);

  // 恢复上次的设置
  try {
    const saved = localStorage.getItem('overlay.alpha');
    if (saved) slider.value = saved;
  } catch (_) {}
  apply();
}

function bindDrawer() {
  const btn = $('btnSet');
  const drawer = $('drawer');
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const open = drawer.classList.toggle('open');
    btn.setAttribute('aria-expanded', String(open));
  });
}

function bindPass() {
  const btn = $('btnPass');
  btn.addEventListener('click', () => {
    const on = btn.getAttribute('aria-pressed') !== 'true';
    btn.setAttribute('aria-pressed', String(on));
    // 正式版：这里调用窗口壳切 WS_EX_TRANSPARENT
    window.__rocoPassThrough = on;
  });
}

function bindFold() {
  const overlay = $('overlay');
  const hdr = $('hdr');
  const btnFold = $('btnFold');

  const toggle = () => {
    folded = !folded;
    overlay.classList.toggle('folded', folded);
    btnFold.textContent = folded ? '展开' : '折叠';
    try { localStorage.setItem('overlay.folded', folded ? '1' : '0'); } catch (_) {}
  };
  hdr.addEventListener('dblclick', (e) => {
    if (e.target.closest('button')) return;
    toggle();
  });
  btnFold.addEventListener('click', toggle);

  try {
    if (localStorage.getItem('overlay.folded') === '1') toggle();
  } catch (_) {}
}

function bindDrag() {
  const overlay = $('overlay');
  const hdr = $('hdr');
  let sx = 0, sy = 0, ox = 0, oy = 0, dragging = false;

  hdr.addEventListener('pointerdown', (e) => {
    if (e.target.closest('button')) return;
    dragging = true;
    sx = e.clientX; sy = e.clientY;
    const r = overlay.getBoundingClientRect();
    ox = r.left; oy = r.top;
    overlay.classList.add('dragging');
    hdr.setPointerCapture(e.pointerId);
  });

  hdr.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    const nx = ox + (e.clientX - sx);
    const ny = oy + (e.clientY - sy);
    const maxX = window.innerWidth - overlay.offsetWidth;
    const maxY = window.innerHeight - overlay.offsetHeight;
    overlay.style.left = Math.max(0, Math.min(maxX, nx)) + 'px';
    overlay.style.top = Math.max(0, Math.min(maxY, ny)) + 'px';
  });

  const end = (e) => {
    if (!dragging) return;
    dragging = false;
    overlay.classList.remove('dragging');
    try { hdr.releasePointerCapture(e.pointerId); } catch (_) {}
    try {
      localStorage.setItem('overlay.pos',
        overlay.style.left + ',' + overlay.style.top);
    } catch (_) {}
  };
  hdr.addEventListener('pointerup', end);
  hdr.addEventListener('pointercancel', end);

  // 恢复上次位置
  try {
    const pos = localStorage.getItem('overlay.pos');
    if (pos) {
      const [l, t] = pos.split(',');
      overlay.style.left = l;
      overlay.style.top = t;
    }
  } catch (_) {}
}

/* ================================================================ */
document.addEventListener('DOMContentLoaded', () => {
  bindAlpha();
  bindDrawer();
  bindPass();
  bindFold();
  bindDrag();

  renderEmpty();
  poll();
  pollTimer = setInterval(poll, 100);

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      clearInterval(pollTimer);
      pollTimer = null;
    } else if (!pollTimer) {
      lastSig = '';
      poll();
      pollTimer = setInterval(poll, 100);
    }
  });
});

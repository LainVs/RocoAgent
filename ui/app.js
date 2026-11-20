/* ============================================================
   RocoAgent 对战悬浮窗 · 视觉稿逻辑
   ------------------------------------------------------------
   数据全是写死的，目的是先把「好看」和「交互手感」敲定。
   接后端时只需替换 MOCK 与 render()，其余（拖拽/折叠/滑杆）原样保留。

   真实版本里这些值从两处来：
     · 机器人推送（快照新鲜 < 1.5s）→ 零额外截屏开销
     · 自己感知（独立运行）          → mss 抓三块小区域
   ============================================================ */
'use strict';

// ── 属性 → 颜色（与 DB 的 types.color 同源）
const TYPE_COLOR = {
  普通: '#A8A878', 火: '#F08030', 水: '#6890F0', 草: '#78C850',
  电: '#F8D030', 冰: '#98D8D8', 武: '#C03028', 毒: '#A040A0',
  地: '#E0C068', 翼: '#A890F0', 萌: '#EE99AC', 幻: '#C080FF',
  虫: '#A8B820', 幽: '#705898', 机械: '#B8B8D0', 龙: '#7038F8',
  恶: '#705848', 光: '#FFF68F',
};

// 克制倍率 → 强调色。面板整体色调本身就是情报，不用读字。
const ACCENT = {
  strong: '#FF6B4A',   // 我打它克制
  even:   '#7FD3E8',   // 中性
  weak:   '#5B7FD4',   // 我被压
};

// ── 三个场景（覆盖真实会遇到的三种局面）
const MOCK = {
  A: {
    accent: 'strong',
    turn: 12, clock: 8, ms: 4.2,
    dets: [true, true, true],
    me:  { name: '寂灭骨龙', types: ['龙'], hp: 78, abs: 382, max: 490,
           energy: 6, maxEnergy: 10, buffs: [{ t: '物攻 +1', neg: false }] },
    foe: { name: '水灵', types: ['水'], hp: 45,
           hpRange: '349~419', defRange: '178~231',
           nature: '固执', conf: 62, confDots: 3, uncertainty: 0.30 },
    skills: [
      { name: '隼鳞',     dmg: 245, pct: 58, el: '龙', cost: 3 },
      { name: '龙之怒',   dmg: 186, pct: 44, el: '龙', cost: 2 },
      { name: '碎岩',     dmg: 92,  pct: 22, el: '地', cost: 1 },
      { name: '龙舞',     dmg: null, note: '强化', el: '龙', cost: 3 },
    ],
    verdict: { kill: 2, die: 4, order: '先手', orderGood: true },
    matchup: { out: 3.0, in: 0.5, tag: '双重克制' },
  },

  B: {
    accent: 'even',
    turn: 7, clock: 13, ms: 4.0,
    dets: [true, true, true],
    me:  { name: '音速犬', types: ['火'], hp: 100, abs: 366, max: 366,
           energy: 8, maxEnergy: 10, buffs: [] },
    foe: { name: '巨鼓象', types: ['地', '武'], hp: 92,
           hpRange: '510~612', defRange: '241~312',
           nature: '—', conf: 18, confDots: 1, uncertainty: 0.62 },
    skills: [
      { name: '火云车',   dmg: 121, pct: 21, el: '火', cost: 2 },
      { name: '力量增效', dmg: null, note: '强化', el: '普通', cost: 1 },
      { name: '闪击',     dmg: 96,  pct: 17, el: '普通', cost: 1 },
      { name: '热风',     dmg: 74,  pct: 13, el: '火', cost: 3 },
    ],
    verdict: { kill: 5, die: 3, order: '后手', orderGood: false },
    matchup: { out: 1.0, in: 1.0, tag: '互不克制' },
  },

  C: {
    accent: 'weak',
    turn: 19, clock: 4, ms: 41.7,
    dets: [true, false, true],
    me:  { name: '冰钻布鲁斯', types: ['冰'], hp: 23, abs: 79, max: 345,
           energy: 2, maxEnergy: 10, buffs: [{ t: '物防 −1', neg: true }] },
    foe: { name: '白金独角兽', types: ['光', '翼'], hp: 61,
           hpRange: '402~483', defRange: '196~254',
           nature: '胆小', conf: 84, confDots: 4, uncertainty: 0.14 },
    skills: [
      { name: '寒冰箭',   dmg: 48,  pct: 10, el: '冰', cost: 2 },
      { name: '冰之砾',   dmg: 31,  pct: 6,  el: '冰', cost: 1 },
      { name: '极寒风暴', dmg: null, note: '能量不足', el: '冰', cost: 4 },
      { name: '雪之守护', dmg: null, note: '强化', el: '冰', cost: 2 },
    ],
    verdict: { kill: 7, die: 1, order: '后手', orderGood: false },
    matchup: { out: 0.5, in: 3.0, tag: '被双重克制' },
  },
};

const $ = (id) => document.getElementById(id);
const root = document.documentElement;

let folded = false;
let current = 'A';

/* ============================================================
   工具
   ============================================================ */
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
  if (p <= 25) return 'low';
  if (p <= 55) return 'mid';
  return 'ok';
}

// 属性 chip：淡色底 + 属性色字，在游戏画面上也能一眼认出
function typeChips(el, types) {
  el.innerHTML = types.map((t) => {
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

// 头像：有图用图，没图用属性色方块 + 首字
// 文件在 assets/portraits{,_foe}/<精灵名>.png，通过 ui/ 下的 junction 暴露给 HTTP
// 两个目录只是采集来源不同（我方/敌方），头像本身通用，所以互相回退
function avatar(el, name, types, side) {
  const t = types[0];
  const c = TYPE_COLOR[t] || '#8A8F98';
  el.style.setProperty('--tc', c);
  el.style.background = hexA(c, 0.20);

  const first = side === 'foe' ? 'portraits_foe' : 'portraits';
  const second = side === 'foe' ? 'portraits' : 'portraits_foe';
  const file = encodeURIComponent(name) + '.png';

  const img = new Image();
  img.alt = '';
  img.decoding = 'async';

  let tried = 0;
  img.onload = () => {
    el.textContent = '';
    el.replaceChildren(img);
  };
  img.onerror = () => {
    tried += 1;
    if (tried === 1) {
      img.src = `${second}/${file}`;     // 换目录再试一次
      return;
    }
    // 两个目录都没有：保留首字占位，这不是错误
    if (!el.querySelector('img')) el.textContent = name.slice(0, 1);
  };
  img.src = `${first}/${file}`;

  if (!el.querySelector('img')) el.textContent = name.slice(0, 1);
}

function skillTier(s) {
  if (s.dmg == null) return '';
  if (s.pct >= 100) return ' kill hot';
  if (s.pct >= 40) return ' high hot';
  if (s.pct >= 20) return ' mid';
  return '';
}

/* ============================================================
   渲染
   ============================================================ */
function render(d) {
  setAccent(d.accent);

  // ── 系统栏
  $('sysTurn').textContent = 'T' + d.turn;
  const clock = $('sysClock');
  clock.textContent = String(d.clock).padStart(2, '0') + 's';
  clock.classList.toggle('urgent', d.clock <= 5);

  const ms = $('sysMs');
  ms.textContent = d.ms.toFixed(1) + 'ms';
  ms.className = 'hdr-ms' + (d.ms > 200 ? ' bad' : d.ms > 50 ? ' warn' : '');

  $('sysDets').innerHTML = d.dets
    .map((on) => `<i class="${on ? 'on' : ''}"></i>`).join('');

  const live = $('liveDot');
  live.className = 'live-dot' + (d.dets.some((x) => x) ? '' : ' off');

  // ── 我方
  const me = d.me;
  $('meName').textContent = me.name;
  avatar($('meAvatar'), me.name, me.types, 'me');
  typeChips($('meTypes'), me.types);
  $('mePct').textContent = Math.round(me.hp);
  $('meAbs').textContent = `${me.abs} / ${me.max}`;
  $('meEnergy').innerHTML = energyPips(me.energy, me.maxEnergy);
  $('meBuff').textContent = me.buffs[0] ? me.buffs[0].t : '';
  $('meBuff').className = 'buff' + (me.buffs[0] && me.buffs[0].neg ? ' neg' : '');

  $('meBar').dataset.lvl = hpLevel(me.hp);
  root.style.setProperty('--me-p', (me.hp / 100).toFixed(4));
  root.style.setProperty('--me-g', (Math.min(100, me.hp + 5) / 100).toFixed(4));

  // ── 敌方
  const foe = d.foe;
  $('foeName').textContent = foe.name;
  avatar($('foeAvatar'), foe.name, foe.types, 'foe');
  typeChips($('foeTypes'), foe.types);
  $('foePct').textContent = Math.round(foe.hp);
  $('foeHpRange').textContent = foe.hpRange;
  $('foeDefRange').textContent = foe.defRange;
  $('foeNature').textContent = foe.nature;
  $('foeConf').textContent = foe.conf + '%';

  $('foeBar').dataset.lvl = hpLevel(foe.hp);
  root.style.setProperty('--foe-p', (foe.hp / 100).toFixed(4));
  root.style.setProperty('--foe-g', (Math.min(100, foe.hp + 5) / 100).toFixed(4));

  $('foeDots').innerHTML = Array.from({ length: 5 }, (_, i) =>
    `<i class="${i < foe.confDots ? 'on' : ''}"></i>`).join('');

  // ── 克制关系
  if (d.matchup) {
    const m = d.matchup;
    $('muOut').textContent = '×' + m.out.toFixed(1);
    $('muIn').textContent = '×' + m.in.toFixed(1);
    $('muOut').parentElement.className = 'mu-side' + (m.out > 1 ? ' good' : m.out < 1 ? ' bad' : '');
    $('muIn').parentElement.className = 'mu-side' + (m.in > 1 ? ' bad' : m.in < 1 ? ' good' : '');
    const tag = $('muTag');
    tag.textContent = m.tag;
    tag.className = 'mu-tag' + (m.out > m.in ? ' good' : m.out < m.in ? ' bad' : '');
  }

  // ── 技能（两行式：名字/伤害一行，伤害条全宽第二行）
  const wrap = $('skills');
  wrap.innerHTML = '';
  d.skills.forEach((s, i) => {
    const na = s.dmg == null;
    const row = document.createElement('div');
    row.className = 'skill' + (na ? ' na' : '') + skillTier(s);
    row.style.setProperty('--w', na ? 0 : Math.min(1, s.pct / 100).toFixed(4));

    row.innerHTML = `
      <span class="sk-idx">${i + 1}</span>
      <span class="sk-name">${s.name}<span class="sk-meta">${s.el}·${s.cost}</span></span>
      <span class="sk-dmg">${na ? '—' : s.dmg}</span>
      <span class="sk-pct">${na ? (s.note || '—') : s.pct + '%'}</span>
      <span class="sk-bar"><i></i></span>
    `;
    wrap.appendChild(row);
  });

  // ── 结论
  const v = d.verdict;
  $('vKill').textContent = v.kill;
  $('vDie').textContent = v.die;
  const order = $('vOrder');
  order.textContent = v.order;
  order.className = v.orderGood ? 'good' : 'bad';
}

/* ============================================================
   交互
   ============================================================ */

// 透明度：只改面板底色 alpha。文字带描边，所以拉到 20% 仍然可读
// （实测：alpha 0.20 时主信息对比度 10.65:1，见 ui/compose_test.py）
//
// 但图形元素（血条底槽、分隔线）没有描边，会跟着面板一起消失。
// 所以给它们一个不透明度下限：面板 20% 时它们仍有 55% 的相对强度。
function bindAlpha() {
  const slider = $('alpha');
  const label = $('alphaVal');
  const apply = () => {
    const v = +slider.value;
    const a = v / 100;
    root.style.setProperty('--alpha', a.toFixed(2));
    label.textContent = v + '%';

    // 图形元素的下限补偿：alpha 越低，它们相对越不透明
    // 边框尤其重要 —— 面板 20% 时如果边框也消失，用户分不清面板和游戏画面
    const boost = Math.min(1, 0.55 + (1 - a) * 0.55);
    root.style.setProperty('--well-a', (0.34 * boost).toFixed(3));
    root.style.setProperty('--line-a', (0.085 * boost).toFixed(3));
    root.style.setProperty('--line-soft-a', (0.045 * boost).toFixed(3));
    root.style.setProperty('--edge-a', (0.10 * boost + (1 - a) * 0.10).toFixed(3));
    root.style.setProperty('--edge-hi-a', (0.11 * boost).toFixed(3));
  };
  slider.addEventListener('input', apply);
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

// 点击穿透（视觉稿只切状态，正式版走 WS_EX_TRANSPARENT）
function bindPass() {
  const btn = $('btnPass');
  btn.addEventListener('click', () => {
    const on = btn.getAttribute('aria-pressed') !== 'true';
    btn.setAttribute('aria-pressed', String(on));
    document.body.classList.toggle('pass-through', on);
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
  };
  hdr.addEventListener('dblclick', (e) => {
    if (e.target.closest('button')) return;
    toggle();
  });
  btnFold.addEventListener('click', toggle);
}

// 拖拽：只有标题栏可拖，避免调滑杆时误拖整个窗口
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
  };
  hdr.addEventListener('pointerup', end);
  hdr.addEventListener('pointercancel', end);
}

function bindScenarios() {
  document.querySelectorAll('.demo-scen button').forEach((b) => {
    b.addEventListener('click', () => {
      document.querySelectorAll('.demo-scen button')
        .forEach((x) => x.classList.remove('active'));
      b.classList.add('active');
      current = b.dataset.scenario;
      render(MOCK[current]);
    });
  });
}

// 模拟血量跳动，验证补间是否顺滑
function bindPulse() {
  setInterval(() => {
    if (current !== 'A') return;
    const me = MOCK.A.me;
    me.hp = Math.max(70, Math.min(85, me.hp + (Math.random() - 0.5) * 6));
    me.abs = Math.round(me.max * me.hp / 100);
    render(MOCK.A);
  }, 2600);
}

/* ============================================================ */
document.addEventListener('DOMContentLoaded', () => {
  render(MOCK.A);
  bindAlpha();
  bindDrawer();
  bindPass();
  bindFold();
  bindDrag();
  bindScenarios();
  bindPulse();
});

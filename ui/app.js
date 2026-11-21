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
    : '状态未知';
  $('sysState').textContent = stateText;
  // 有数据了就说明机器人活着，不需要启动按钮
  const sb = $('btnStartBot');
  if (sb) sb.hidden = true;

  // ★ 独立模式：数据是**服务端自己看屏幕**读的，不是机器人推的
  //
  // 必须让用户一眼看出来，否则会以为"对面是谁/技能名"这些字段
  // 是真的读出来了，实际它们需要机器人（决策层 + 数据库）。
  const badge = $('sysStandalone');
  if (badge) badge.hidden = !st.standalone;

  // ── 我方
  const me = st.me || {};
  $('meName').textContent = me.name || '未知';
  loadAvatar($('meAvatar'), me.name, 'me');
  typeChips($('meTypes'), me.types);
  $('mePct').textContent = me.hp_pct == null ? '—' : Math.round(me.hp_pct);
  $('meAbs').textContent = (me.hp_abs != null && me.hp_max != null)
    ? `${me.hp_abs} / ${me.hp_max}` : '—';
  // 能量：读不到时显示"未知"，不画 pips（画 0 个会被误解成"没能量"）
  if (me.energy != null) {
    $('meEnergy').innerHTML = energyPips(me.energy, me.energy_max || 10);
  } else {
    $('meEnergy').innerHTML = '<span class="energy-na">能量未知</span>';
  }
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

  // ── 替补队伍
  // 只显示认得的（机器人只认得见过的名字），未知槽位不占位。
  // 打架时"还剩几只"和当前血量同等重要。
  const bench = st.bench || [];
  const benchEl = $('bench');
  if (!bench.length) {
    benchEl.innerHTML = '';
  } else {
    const alive = bench.filter((b) => b.alive !== false).length;
    benchEl.innerHTML = bench.map((b) => {
      const dead = b.alive === false;
      return `<span class="bench-item${dead ? ' dead' : ''}"
                    title="${b.name}${dead ? '（已阵亡）' : ''}">
        <span class="bi-av" data-name="${b.name}">${(b.name || '?').slice(0, 1)}</span>
        ${b.name}
      </span>`;
    }).join('') +
      `<span class="bench-count">剩 ${alive}/${bench.length}</span>`;

    // 头像懒加载（复用悬浮窗的限并发加载器）
    benchEl.querySelectorAll('.bi-av').forEach((el) => {
      loadAvatar(el, el.dataset.name, 'me');
    });
  }

  // ── 结论
  // 三项（击杀回合/被击杀回合/先后手）缺任何一项都不该给半吊子结论 ——
  // "2 回合击杀 · — 回合被击杀" 比不显示更让人困惑。
  const v = st.verdict;
  if (v && v.complete !== false && (v.kill_turns || v.die_turns)) {
    $('vKill').textContent = v.kill_turns != null ? v.kill_turns : '—';
    $('vDie').textContent = v.die_turns != null ? v.die_turns : '—';
    const order = $('vOrder');
    order.textContent = v.order === 'first' ? '先手'
      : v.order === 'second' ? '后手' : '—';
    order.className = v.order === 'first' ? 'good'
      : v.order === 'second' ? 'bad' : '';
  } else {
    $('vKill').textContent = '—';
    $('vDie').textContent = '—';
    const order = $('vOrder');
    order.textContent = '数据不足';
    order.className = '';
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
    (st.bench || []).map((b) => `${b.name}|${b.alive}`).join(','),
    st._stale,
  ].join('|');
  if (sig === lastSig) return;
  lastSig = sig;

  render(st);

  // 首次渲染完成后贴合一次窗口。
  // 为什么单独做：enter_embedded 测量时数据可能还没到（面板只有骨架），
  // 而之后如果 seq 不变（机器人卡住 / 结算页静止），
  // 下面的签名去重会直接 return，fit_window 永远不被调用。
  if (!firstFitDone) {
    firstFitDone = true;
    setTimeout(() => callNative('fit_window', true), 120);
  }

  // 数据变了，窗口高度可能也要变（技能卡数量会变）。
  // force=true 跳过去抖 —— 否则内容从"无技能"变"4 张卡"时，
  // 窗口可能停在偏小的尺寸上不调整。
  callNative('fit_window', true);
}

let firstFitDone = false;

function renderEmpty() {
  const sig = 'empty';
  if (sig === lastSig) return;
  lastSig = sig;

  // ★ 措辞必须准确
  //
  // 以前这里写"未连接"，很容易被理解成"悬浮窗和机器人断开了"。
  // 实际上 /api/state 返回 null 只有两种原因：
  //   1. 机器人**没启动**（最常见）
  //   2. 机器人启动过但已经退出，live_state.json 被清掉了
  // 真正"连不上"是 fetch 抛异常那条分支（显示"服务未连接"）。
  // 三种状态必须能区分开，否则排查方向全错。
  $('sysState').textContent = '机器人未启动';
  $('liveDot').className = 'live-dot off';
  // 直接把"启动"摆到状态旁边 —— 这是此时唯一有意义的操作
  const sb = $('btnStartBot');
  if (sb) sb.hidden = false;
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
  $('bench').innerHTML = '';
  $('vKill').textContent = '—';
  $('vDie').textContent = '—';
  $('vOrder').textContent = '—';

  setAccent('even');
}

/* ================================================================
   原生桥（pywebview）
   ----------------------------------------------------------------
   在真悬浮窗里，window.pywebview.api 提供原生能力：
     drag()                 让系统接管拖动（窗口真的会移动）
     set_click_through(b)   改 WS_EX_TRANSPARENT
     set_opacity(0~255)     整体透明度
     fit_window()           窗口贴合内容高度
     hide() / toggle_visible() / quit()

   浏览器里没有这个对象，所以全部降级到纯 CSS 行为。

   坑：不能写成 const NATIVE = window.pywebview.api ——
   pywebview 的 api 是**异步注入**的，脚本执行时它还不存在，
   那样 NATIVE 永远是 null。必须每次调用时现取。
   ================================================================ */
function nativeApi() {
  try {
    return (window.pywebview && window.pywebview.api) || null;
  } catch (_) {
    return null;
  }
}

function hasNative() {
  return !!nativeApi();
}

// 调用原生方法（失败静默降级）
async function callNative(method, ...args) {
  const api = nativeApi();
  if (!api || typeof api[method] !== 'function') return null;
  try {
    return await api[method](...args);
  } catch (_) {
    return null;
  }
}

/* ================================================================
   交互
   ================================================================ */

// 透明度：**整窗 alpha**（Win32 分层窗口）。
//
// ★ 最终方案（2026-11-21 彻查后确定）
// -----------------------------------
// 试过并被否掉的三条路：
//   1. CSS --alpha 半透明
//      -> 文字清晰，但透出的是 WebView2/Form 的白底（#F0F0F0），
//         看起来就是"纯白不透明"。设 Form.BackColor=Transparent 也没用：
//         WebView2 控件的 DefaultBackgroundColor 只能从 UI 线程访问，
//         设 Transparent 会把整个网页画布挖空（内容消失），
//         设不透明又覆盖页面。
//   2. DWM blur-behind
//      -> API 返回成功，但窗口不透明时模糊被盖住，看不到效果。
//   3. CSS + 整窗 alpha 双管
//      -> 两层相乘，0.4 × 0.4 = 0.16，变成灰雾。
//
// 最终：**只改整窗 alpha，CSS 保持高不透明度**
//   整窗 alpha  -> 面板整体变淡，背后游戏画面透出来（真正的"透明"）
//   CSS 不变    -> 面板底色始终够实，文字对比度稳定
//
// 实测（alpha 与面板亮度）：
//   alpha=255 -> 面板 229（不透明）
//   alpha=200 -> 面板 189（半透明，背后内容清晰可见）
//   alpha=128 -> 面板 126（更透，文字开始吃力）
//   alpha=0   -> 面板  23（全透明，内容没了）
//
// 下限 150（约 59%）：再低文字就吃力了。
const ALPHA_MIN = 150;

function bindAlpha() {
  const slider = $('alpha');
  const label = $('alphaVal');
  if (!slider) {
    console.warn('[alpha] slider 不存在，跳过绑定');
    return;
  }
  slider.min = String(ALPHA_MIN);

  slider.addEventListener('input', () => applyAlphaValue(+slider.value));
  slider.addEventListener('change', () => applyAlphaValue(+slider.value));

  try {
    const saved = localStorage.getItem('overlay.alpha');
    if (saved) slider.value = Math.max(ALPHA_MIN, +saved);
  } catch (_) {}

  // ★ 等 native 就绪后再应用
  //
  // pywebview 的 window.pywebview.api 是**异步注入**的，DOMContentLoaded
  // 时可能还没有。那时 hasNative() 返回 false，applyAlphaValue 会走
  // CSS 分支把 --alpha 改成 0.9 —— 而原生模式下 CSS 应该固定 1.0，
  // 否则两层相乘变灰雾（实测踩过）。
  const boot = () => {
    if (hasNative()) {
      applyAlphaValue(+slider.value || 230);
    } else {
      // 再等一轮；最多等 5 秒
      if (!window.__alphaWait) window.__alphaWait = 0;
      window.__alphaWait += 1;
      if (window.__alphaWait < 50) {
        setTimeout(boot, 100);
      } else {
        console.warn('[alpha] native 一直没就绪，按浏览器模式处理');
        applyAlphaValue(+slider.value || 230);
      }
    }
  };
  boot();
}

function currentAlpha() {
  try {
    const s = localStorage.getItem('overlay.alpha');
    if (s) return Math.max(ALPHA_MIN, +s);
  } catch (_) {}
  return 230;
}

// 把 150~255 的整窗 alpha 应用到原生窗口。
//
// ★ 原生模式下**绝不改 CSS --alpha**
//   CSS 固定 1.0（面板完全不透明），这样文字对比度恒定。
//   透明度全靠 SetLayeredWindowAttributes。
//   两层都改会相乘：0.78 × 0.78 = 0.61，变灰雾（实测踩过）。
function applyAlphaValue(v) {
  let val = +v;
  if (!isFinite(val)) val = 230;
  if (val < ALPHA_MIN) val = ALPHA_MIN;
  if (val > 255) val = 255;

  if (hasNative()) {
    callNative('set_opacity', Math.round(val));
  } else {
    // 浏览器预览：没有原生窗口，只能用 CSS 模拟
    root.style.setProperty('--alpha', (val / 255).toFixed(2));
  }

  const label = $('alphaVal');
  if (label) label.textContent = Math.round(val / 255 * 100) + '%';

  try { localStorage.setItem('overlay.alpha', String(val)); } catch (_) {}
  return val;
}

// 暴露给调试：window.__setAlpha(200)
window.__setAlpha = applyAlphaValue;

function bindDrawer() {
  const btn = $('btnSet');
  const drawer = $('drawer');
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const open = drawer.classList.toggle('open');
    btn.setAttribute('aria-expanded', String(open));
  });
}

// 点击穿透：真悬浮窗里改窗口样式，浏览器里只切状态
function bindPass() {
  const btn = $('btnPass');
  const paint = (on) => {
    btn.setAttribute('aria-pressed', String(on));
    // 穿透开着的时候按钮自己也点不到了 —— 用热键 Ctrl+Alt+P 关
    btn.textContent = on ? '已穿透（Ctrl+Alt+P 关）' : '点击穿透';
  };
  btn.addEventListener('click', async () => {
    const on = btn.getAttribute('aria-pressed') !== 'true';
    paint(on);
    if (hasNative()) {
      await callNative('set_click_through', on);
    } else {
      document.body.classList.toggle('pass-through', on);
    }
  });

  // 全局热键 Ctrl+Alt+P 在原生线程里处理，页面不知道。
  // 定时对齐一次，避免"穿透已经关了但按钮还写着已穿透"。
  if (hasNative()) {
    setInterval(async () => {
      try {
        const s = await callNative('get_state');
        if (s && typeof s.pass_through === 'boolean') {
          if (btn.getAttribute('aria-pressed') !== String(s.pass_through)) {
            paint(s.pass_through);
          }
        }
      } catch (_) {}
    }, 1200);
  }
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
    // 后端也存一份：真悬浮窗里窗口高度要跟着变，
    // 下次启动直接按折叠态开，避免展开态闪一下再折叠。
    callNative('set_folded', folded);
    // 折叠/展开后窗口要跟着缩，否则留一大片透明区域挡鼠标
    setTimeout(() => callNative('fit_window', true), 280);
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

// ── 拖动 ────────────────────────────────────────────────────────
//
// 踩过的三个坑（2026-11-21，用户报"拖动超级诡异"）：
//
// 1. 原生路径**没有 setPointerCapture**
//    原代码在 hasNative() 分支里直接 return，永远走不到 setPointerCapture。
//    pointermove/pointerup 都挂在 hdr 上 —— 鼠标一离开标题栏，事件就断了：
//    窗口卡住不动；在外面松手也收不到 pointerup，dragging 一直是 true，
//    之后鼠标随便一动窗口就跟着跑。这就是"诡异"的主因。
//
// 2. 每个 pointermove 都走一次 pywebview JS 桥
//    callNative 是 JSON 序列化的跨进程往返。鼠标 120Hz 移动就是每秒
//    120 次 IPC，窗口跟不上，然后一次性跳过去。
//    改用 requestAnimationFrame 合并：一帧最多发一次。
//
// 3. 坐标被多除了一次 dpr
//    后端 `w.move(int(nx / d), int(ny / d))` 除以 dpr，但实测
//    SetWindowPos(400) -> GetWindowRect 读回 400，两者**同口径**。
//    再除一次等于窗口只走鼠标的一半距离。
//
// 另外试过 WM_NCLBUTTONDOWN + HTCAPTION 的原生拖动（Electron 用的就是它），
// 但这个 layered + toolwindow + noactivate 的窗口上实测**窗口不动**
// （SendMessage 返回 0，光标移动了窗口没跟）。所以放弃那条路。
function bindDrag() {
  const overlay = $('overlay');
  const hdr = $('hdr');
  let dragging = false;
  let pending = null;      // 待发送的最新坐标（rAF 合并用）
  let rafId = 0;

  // 浏览器预览用
  let bsx = 0, bsy = 0, box = 0, boy = 0;

  const flush = () => {
    rafId = 0;
    if (!pending) return;
    const [x, y] = pending;
    pending = null;
    callNative('drag_move', x, y);
  };

  hdr.addEventListener('pointerdown', (e) => {
    if (e.target.closest('button')) return;
    if (e.button !== 0) return;

    // ★ 无论走哪条路径都必须 capture —— 否则鼠标离开标题栏就丢事件
    try { hdr.setPointerCapture(e.pointerId); } catch (_) {}

    dragging = true;
    overlay.classList.add('dragging');

    if (hasNative()) {
      callNative('drag', window.devicePixelRatio || 1, e.screenX, e.screenY);
      return;
    }

    // 浏览器：只能移动 DOM 元素（预览用）
    bsx = e.clientX; bsy = e.clientY;
    const r = overlay.getBoundingClientRect();
    box = r.left; boy = r.top;
  });

  hdr.addEventListener('pointermove', (e) => {
    if (!dragging) return;

    if (hasNative()) {
      // rAF 合并：一帧只发一次，避免 120Hz 的 IPC 洪泛
      pending = [e.screenX, e.screenY];
      if (!rafId) rafId = requestAnimationFrame(flush);
      return;
    }

    const nx = box + (e.clientX - bsx);
    const ny = boy + (e.clientY - bsy);
    const maxX = window.innerWidth - overlay.offsetWidth;
    const maxY = window.innerHeight - overlay.offsetHeight;
    overlay.style.left = Math.max(0, Math.min(maxX, nx)) + 'px';
    overlay.style.top = Math.max(0, Math.min(maxY, ny)) + 'px';
  });

  const end = (e) => {
    if (!dragging) return;
    dragging = false;

    // 把最后一帧没发出去的位移补上，否则松手位置会差一点
    if (rafId) {
      cancelAnimationFrame(rafId);
      rafId = 0;
      if (pending) {
        const [x, y] = pending;
        pending = null;
        callNative('drag_move', x, y);
      }
    }

    try { hdr.releasePointerCapture(e.pointerId); } catch (_) {}

    if (hasNative()) {
      callNative('drag_end');
      overlay.classList.remove('dragging');
      return;
    }

    overlay.classList.remove('dragging');
    try {
      localStorage.setItem('overlay.pos',
        overlay.style.left + ',' + overlay.style.top);
    } catch (_) {}
  };
  hdr.addEventListener('pointerup', end);
  hdr.addEventListener('pointercancel', end);

  try {
    const pos = localStorage.getItem('overlay.pos');
    if (pos) {
      const [l, t] = pos.split(',');
      overlay.style.left = l;
      overlay.style.top = t;
    }
  } catch (_) {}
}

/* ================================================================
   键盘快捷键
   ----------------------------------------------------------------
   打架时鼠标在游戏上，按快捷键比点面板快得多。

     Alt+T   折叠 / 展开
     Alt+A   透明度循环（100 / 78 / 55 / 35 / 20）
     Alt+P   点击穿透开关
     Alt+H   隐藏（Ctrl+Alt+O 叫回来）
     Esc     关设置抽屉
   ================================================================ */
function bindHotkeys() {
  const ALPHAS = [100, 78, 55, 35, 20];

  document.addEventListener('keydown', (e) => {
    if (e.altKey && !e.ctrlKey && !e.metaKey) {
      const k = e.key.toLowerCase();
      if (k === 't') {
        e.preventDefault();
        $('btnFold').click();
        return;
      }
      if (k === 'a') {
        e.preventDefault();
        const slider = $('alpha');
        const cur = +slider.value;
        // 找下一个更小的档，到底就回到 100
        let next = ALPHAS[0];
        for (const v of ALPHAS) {
          if (v < cur) { next = v; break; }
        }
        slider.value = next;
        slider.dispatchEvent(new Event('input'));
        return;
      }
      if (k === 'p') {
        e.preventDefault();
        $('btnPass').click();
        return;
      }
      if (k === 'h') {
        e.preventDefault();
        callNative('hide');
        return;
      }
    }
    if (e.key === 'Escape') {
      const drawer = $('drawer');
      if (drawer.classList.contains('open')) {
        drawer.classList.remove('open');
        $('btnSet').setAttribute('aria-expanded', 'false');
      }
    }
  });
}

// 启动机器人：悬浮窗里直接能拉起，不用切到控制台。
//
// 为什么放在悬浮窗：用户看伤害计算器时机器人常常没跑，
// 这时候"启动"是唯一有意义的操作，让人去另一个窗口找按钮很别扭。
function bindStartBot() {
  const btn = $('btnStartBot');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    btn.textContent = '启动中…';
    try {
      const r = await fetch(API + '/api/bot/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ supervisor: true }),
      });
      const d = await r.json();
      if (!d.ok) {
        // 后端会给出具体原因（比如"解释器缺 bleak"），
        // 这条信息对排查很关键，不能吞掉。
        alert('启动失败：' + (d.msg || '未知原因'));
      }
    } catch (e) {
      alert('启动失败：' + e.message);
    }
    btn.disabled = false;
    btn.textContent = '启动';
    // 启动后等一两轮轮询，数据来了按钮会自动隐藏
  });
}

// 主题切换：浅色 / 深色。
//
// 默认浅色 —— 这不是偏好，是实测结论：
// 游戏画面主要是亮色，浅色面板和背景**同向**，半透明时仍然亮，
// 文字对比度稳定；深色面板反向，混合后落在中灰，40% 下字就读不了了。
function bindTheme() {
  const seg = $('themeSeg');
  if (!seg) return;

  const apply = (theme) => {
    document.body.classList.toggle('theme-dark', theme === 'dark');
    document.body.classList.toggle('theme-light', theme !== 'dark');
    seg.querySelectorAll('button').forEach((b) => {
      b.classList.toggle('active', b.dataset.theme === theme);
    });
    try { localStorage.setItem('overlay.theme', theme); } catch (_) {}
  };

  seg.querySelectorAll('button').forEach((b) => {
    b.addEventListener('click', () => apply(b.dataset.theme));
  });

  let saved = 'light';
  try { saved = localStorage.getItem('overlay.theme') || 'light'; } catch (_) {}
  apply(saved);
}

/* ================================================================ */
// ★ 每个 bind 独立 try/catch
//
// 踩过的坑（2026-11-21，用户报"透明度完全无效果"）：
// 以前这里是平铺调用。任何一个 bind 抛异常（比如 bindAlpha 里
// slider 为 null 时 slider.min = ...），后面的全部不执行 ——
// 包括 poll()。于是整个页面看起来"是死的"：
//   透明度没反应、数据不刷新、拖动也不跟手。
// 而控制台可能只有一条不起眼的 TypeError。
//
// 现在逐个隔离，并在控制台打印哪个 bind 失败。
function safe(name, fn) {
  try {
    fn();
  } catch (e) {
    console.error('[init] ' + name + ' 失败:', e);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  safe('theme', bindTheme);
  safe('alpha', bindAlpha);
  safe('drawer', bindDrawer);
  safe('pass', bindPass);
  safe('fold', bindFold);
  safe('drag', bindDrag);
  safe('hotkeys', bindHotkeys);
  safe('startBot', bindStartBot);

  safe('renderEmpty', renderEmpty);
  safe('poll', poll);
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

  // 原生桥是异步注入的。等它就绪后打个标记，
  // 窗口壳和调试都能据此判断"现在是真悬浮窗环境"。
  const waitNative = (tries) => {
    if (hasNative()) {
      document.body.classList.add('native-ready');
      return;
    }
    if (tries <= 0) return;
    setTimeout(() => waitNative(tries - 1), 120);
  };
  waitNative(40);
});

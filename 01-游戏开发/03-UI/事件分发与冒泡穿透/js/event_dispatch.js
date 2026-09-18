/**
 * UI 事件分发：冒泡 / 捕获 / 穿透 —— JavaScript 版。
 *
 * 依据 WHATWG DOM Standard §2.9「Dispatching events」：
 *   · event path：target 沿 parent 一路到根
 *   · 捕获：path 逆序（根 → 叶）；冒泡：path 正序（叶 → 根）
 *   · bubbles=false 时冒泡阶段中「非 target 项」全部跳过
 *   · invoke 时克隆 currentTarget 的监听列表 → 本节点触发后新增的不生效，
 *     但尚未到达的节点上新增的会生效；removed 仍然有效
 *   · inner invoke 阶段过滤：capturing 只跑 capture=true，bubbling 只跑 capture=false
 *   · stop propagation flag 一旦置位，两个阶段剩余项全部跳过
 *   · stop immediate propagation flag 还会中断当前节点上剩余监听
 *
 * 这里刻意**不用**浏览器自带的 addEventListener，而是把规范算法显式实现一遍，
 * 以便逐条对照；末尾再用同一套 dispatch 演示游戏里的「命中测试 + 穿透」。
 *
 * 运行：node event_dispatch.js
 */
'use strict';

const CAPTURING_PHASE = 1;
const AT_TARGET = 2;
const BUBBLING_PHASE = 3;

class Node {
  constructor(name, rect = null, depth = 0) {
    this.name = name;
    this.parent = null;
    this.children = [];
    this.listeners = [];
    this.rect = rect;   // [x, y, w, h]
    this.depth = depth; // 越大越靠上
  }

  add(child) {
    child.parent = this;
    this.children.push(child);
    return child;
  }

  addListener(type, callback, capture = false, once = false) {
    const lsn = { type, callback, capture, once, removed: false };
    this.listeners.push(lsn);
    return lsn;
  }
}

class Event {
  constructor(type, bubbles = true) {
    this.type = type;
    this.bubbles = bubbles;
    this.target = null;
    this.currentTarget = null;
    this.eventPhase = 0;
    this.consumed = false;         // 游戏侧惯用：已被处理，不再向下穿透
    this._stopPropagation = false;
    this._stopImmediate = false;
  }

  stopPropagation() { this._stopPropagation = true; }
  stopImmediatePropagation() { this._stopPropagation = true; this._stopImmediate = true; }
}

function eventPath(target) {
  const path = [];
  let cur = target;
  while (cur !== null) { path.push(cur); cur = cur.parent; }
  return path;
}

function invoke(node, event, phase, log) {
  if (event._stopPropagation) return;          // 两阶段都停
  event.currentTarget = node;
  const listeners = node.listeners.slice();    // 克隆
  for (const lsn of listeners) {
    if (lsn.removed || lsn.type !== event.type) continue;
    if (phase === 'capturing' && !lsn.capture) continue;
    if (phase === 'bubbling' && lsn.capture) continue;
    if (lsn.once) lsn.removed = true;
    lsn.callback(event, log);
    if (event._stopImmediate) break;
  }
}

function dispatch(event, target, log = []) {
  const path = eventPath(target);
  event.target = target;
  for (const item of [...path].reverse()) {    // 捕获：根 → 叶
    event.eventPhase = item === target ? AT_TARGET : CAPTURING_PHASE;
    invoke(item, event, 'capturing', log);
  }
  for (const item of path) {                   // 冒泡：叶 → 根
    if (item !== target && !event.bubbles) continue;
    event.eventPhase = item === target ? AT_TARGET : BUBBLING_PHASE;
    invoke(item, event, 'bubbling', log);
  }
  event.eventPhase = 0;
  event.currentTarget = null;
  return log;
}

// -------------------------------------------------- 命中测试与穿透

function hitTest(nodes, px, py) {
  const sorted = [...nodes].sort((a, b) => b.depth - a.depth);
  for (const n of sorted) {
    if (!n.rect) continue;
    const [x, y, w, h] = n.rect;
    if (px >= x && px < x + w && py >= y && py < y + h) return n;
  }
  return null;
}

/** 命中 → 派发 → 无人消费则穿透到下一层。 */
function dispatchWithPassthrough(nodes, px, py, type = 'click') {
  const log = [];
  let remaining = [...nodes].sort((a, b) => b.depth - a.depth);
  while (remaining.length) {
    const hit = remaining.find((n) => {
      if (!n.rect) return false;
      const [x, y, w, h] = n.rect;
      return px >= x && px < x + w && py >= y && py < y + h;
    });
    if (!hit) break;
    log.push('hit:' + hit.name);
    const ev = new Event(type);
    dispatch(ev, hit, log);
    if (ev.consumed) break;
    remaining = remaining.slice(remaining.indexOf(hit) + 1);
  }
  return log;
}

// ---------------------------------------------------------------- demo

function buildTree() {
  const root = new Node('root');
  const panel = root.add(new Node('panel'));
  panel.add(new Node('button'));
  return root;
}

function rec(tag) {
  return (ev, log) => {
    const phase = { [CAPTURING_PHASE]: 'capture', [AT_TARGET]: 'at-target', [BUBBLING_PHASE]: 'bubble' }[ev.eventPhase] || '?';
    log.push(`${tag}@${ev.currentTarget.name}(${phase})`);
  };
}

function main() {
  console.log('[1] event path');
  const t = buildTree();
  const btn = t.children[0].children[0];
  console.log('  ', eventPath(btn).map((n) => n.name).join(' -> '));

  console.log('[2] 捕获逆序 / 冒泡正序');
  const log = [];
  for (const [name, node] of [['root', t], ['panel', t.children[0]], ['button', btn]]) {
    node.addListener('click', rec('C-' + name), true);
    node.addListener('click', rec('B-' + name), false);
  }
  dispatch(new Event('click'), btn, log);
  console.log('  ', log.join(' | '));

  console.log('[3] bubbles=false');
  const log2 = [];
  const t2 = buildTree();
  const btn2 = t2.children[0].children[0];
  for (const [name, node] of [['root', t2], ['panel', t2.children[0]], ['button', btn2]]) {
    node.addListener('click', rec('C-' + name), true);
    node.addListener('click', rec('B-' + name), false);
  }
  dispatch(new Event('click', false), btn2, log2);
  console.log('  ', log2.join(' | '));

  console.log('[4] stopPropagation 在捕获阶段');
  const log3 = [];
  const t3 = buildTree();
  t3.children[0].addListener('click', (ev, lg) => { lg.push('STOP@' + ev.currentTarget.name); ev.stopPropagation(); }, true);
  t3.children[0].children[0].addListener('click', rec('btn'), false);
  dispatch(new Event('click'), t3.children[0].children[0], log3);
  console.log('  ', log3.join(' | ') || '(空)');

  console.log('[5] stopImmediatePropagation');
  const log4 = [];
  const t4 = buildTree();
  const b4 = t4.children[0].children[0];
  b4.addListener('click', rec('btn1'));
  b4.addListener('click', (ev, lg) => { lg.push('btn2-SIP'); ev.stopImmediatePropagation(); });
  b4.addListener('click', rec('btn3'));
  t4.children[0].addListener('click', rec('panel'));
  dispatch(new Event('click'), b4, log4);
  console.log('  ', log4.join(' | '));

  console.log('[6] 派发中新增监听（尚未到达的祖先）');
  const log6 = [];
  const t6 = buildTree();
  t6.children[0].children[0].addListener('click', (ev, lg) => {
    t6.addListener('click', rec('root-added-in-flight'));
  });
  dispatch(new Event('click'), t6.children[0].children[0], log6);
  console.log('  ', log6.join(' | '));

  console.log('[8] 命中测试 + 穿透');
  const canvas = new Node('Canvas');
  const bg = canvas.add(new Node('背景', [0, 0, 400, 300], 0));
  const modal = canvas.add(new Node('半透明面板', [0, 0, 400, 300], 1));
  const button = canvas.add(new Node('按钮', [10, 10, 60, 30], 2));
  const nodes = [bg, modal, button];
  console.log('   点(20,20) 命中 =', hitTest(nodes, 20, 20).name);
  console.log('   点(200,200) 命中 =', hitTest(nodes, 200, 200).name);
  console.log('   未消费 →', dispatchWithPassthrough(nodes, 200, 200).join(' | '));
  modal.addListener('click', (ev, lg) => { lg.push('modal-consume'); ev.consumed = true; });
  console.log('   已消费 →', dispatchWithPassthrough(nodes, 200, 200).join(' | '));
}

main();

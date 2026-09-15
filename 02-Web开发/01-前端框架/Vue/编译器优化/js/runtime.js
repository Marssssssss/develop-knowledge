/**
 * Vue 3 运行时最小实现 — vnode 创建 / Block Tree / mount / 两种 patch 路径 (JavaScript)
 *
 * 与 Vue 官方运行时的对应关系:
 *   createVNode / openBlock / closeBlock / createBlock(setupBlock) / createStaticVNode / normalizeVNode
 *   编译器产出的 render 函数直接消费这些 API,本文件即"编译器与运行时的契约"。
 *
 * 简化:patch 时按索引对齐 diff(不做完整 keyed diff / 卸载);DOM 用可序列化的 El 模型代替真实 DOM,便于断言。
 * 基线 patchFull() 刻意不做 a === b 短路 —— 纯运行时框架每轮重建 vnode,identity 永远不等,
 * 这正是"静态提升"能省下工作的原因。
 */

'use strict';

const { PatchFlags } = require('./compiler.js');

const Fragment = Symbol('Fragment');

const stats = {
  vnodeCreations: 0,    // createVNode/createStaticVNode 调用次数(静态提升是否生效看这里)
  staticVnodeMounts: 0, // static vnode 通过 innerHTML 一次挂载的元素数
  nodeVisits: 0,        // patch 期间访问的节点数(Block Tree 是否生效看这里)
  propComparisons: 0,   // props 比较次数(patchFlag 靶向更新是否生效看这里)
  propWrites: 0,        // 真正写入 DOM 的次数
  reusedStatic: 0,      // 因同一引用/CACHED 而整棵跳过的子树次数
};
function resetStats() { for (const k of Object.keys(stats)) stats[k] = 0; }

// ==================== §1 vnode 创建 + Block Tree 收集 ====================

let blockStack = [];
let currentBlock = null;

function createVNode(type, props, children, patchFlag = 0, dynamicProps = null, isBlock = false) {
  stats.vnodeCreations++;
  const vnode = {
    type, props: props || {},
    children: children === undefined ? null : children,
    patchFlag, dynamicProps: dynamicProps || null,
    dynamicChildren: null, el: null, parentEl: null,
  };
  // 带 patchFlag 的动态节点自动挂到当前 block 的 dynamicChildren 上(块 vnode 自己不挂)
  if (patchFlag > 0 && !isBlock && currentBlock) currentBlock.push(vnode);
  return vnode;
}

function openBlock() { blockStack.push((currentBlock = [])); }
function closeBlock() {
  blockStack.pop();
  currentBlock = blockStack.length > 0 ? blockStack[blockStack.length - 1] : null;
}

/** 等价于官方 setupBlock:dynamicChildren = 本层 openBlock 收集的数组,再把自己交给父块 */
function createBlock(type, props, children, patchFlag, dynamicProps) {
  const vnode = createVNode(type, props, children, patchFlag, dynamicProps, true);
  vnode.dynamicChildren = currentBlock || [];
  closeBlock();
  if (currentBlock) currentBlock.push(vnode);
  return vnode;
}

function createStaticVNode(html, count) {
  stats.vnodeCreations++;
  return { type: 'static', html, count, patchFlag: PatchFlags.CACHED, props: {}, children: null, dynamicChildren: null, el: null, parentEl: null };
}
function createCommentVNode(text) {
  return { type: 'comment', text, patchFlag: 0, props: {}, children: null, dynamicChildren: null, el: null, parentEl: null };
}
function toDisplayString(v) { return v === null || v === undefined ? '' : String(v); }
function normalizeClass(v) {
  if (typeof v === 'string') return v;
  if (Array.isArray(v)) return v.map(normalizeClass).filter(Boolean).join(' ');
  if (v && typeof v === 'object') return Object.keys(v).filter((k) => v[k]).join(' ');
  return '';
}

const runtime = {
  createVNode, createBlock, openBlock, createStaticVNode, createCommentVNode,
  toDisplayString, normalizeClass, Fragment,
};

// ==================== §2 mount ====================

class El {
  constructor(tag) { this.tag = tag; this.props = {}; this.children = []; this.text = null; this.html = null; this.isComment = false; }
  appendChild(c) { this.children.push(c); }
}

/** normalizeVNode:字符串/数字子节点包成文本节点,保证 el.children 与 vnode.children 索引一一对应 */
function mountChild(child, parentEl) {
  if (typeof child === 'string' || typeof child === 'number') {
    const t = new El('#text');
    t.text = String(child);
    parentEl.appendChild(t);
    return t;
  }
  if (child && typeof child === 'object') return mountFresh(child, parentEl);
  return null;
}

function mountFresh(vnode, parentEl) {
  if (vnode.type === 'static') {                     // 静态树:直接 innerHTML,不建子树
    const el = new El('#static');
    el.html = vnode.html;
    stats.staticVnodeMounts += vnode.count;
    vnode.el = el; vnode.parentEl = parentEl;
    if (parentEl) parentEl.appendChild(el);
    return el;
  }
  if (vnode.type === 'comment') {
    const el = new El('#comment'); el.isComment = true;
    vnode.el = el; vnode.parentEl = parentEl;
    if (parentEl) parentEl.appendChild(el);
    return el;
  }
  const el = new El(vnode.type);
  for (const k of Object.keys(vnode.props || {})) el.props[k] = vnode.props[k];
  if (typeof vnode.children === 'string') el.text = vnode.children;   // children fast path
  vnode.el = el; vnode.parentEl = parentEl;
  if (parentEl) parentEl.appendChild(el);
  if (Array.isArray(vnode.children)) for (const c of vnode.children) mountChild(c, el);
  return el;
}
const mount = (vnode) => mountFresh(vnode, null);

function serialize(el) {
  if (!el) return '';
  if (el.tag === '#text') return el.text;
  if (el.tag === '#comment') return '<!---->';
  if (el.tag === '#static') return el.html;
  const attrs = Object.keys(el.props)
    .filter((k) => typeof el.props[k] !== 'function' && !k.startsWith('on'))
    .map((k) => ` ${k}="${el.props[k]}"`).join('');
  const inner = el.children.length > 0 ? el.children.map(serialize).join('') : (el.text || '');
  return `<${el.tag}${attrs}>${inner}</${el.tag}>`;
}

// ==================== §3 两条 patch 路径 ====================

const sameVNode = (a, b) => !!a && !!b && typeof a === 'object' && typeof b === 'object' && a.type === b.type;

/** 优化路径:只按 patchFlag 更新被标记的属性 + 只遍历 dynamicChildren(Block Tree / Tree Flattening) */
function patchOptimized(n1, n2, parentEl) {
  if (n1 === n2) { stats.reusedStatic++; return n1.el; }        // 静态提升:同一引用 → 整棵跳过
  if (!n2) return n1 ? n1.el : null;
  if (n2.patchFlag === PatchFlags.CACHED) { stats.reusedStatic++; return n2.el || (n1 && n1.el); }
  if (typeof n1 === 'string' || !n1) return mountFresh(n2, parentEl || n2.parentEl);

  const el = (n2.el = n1.el);
  n2.parentEl = n1.parentEl;
  stats.nodeVisits++;
  const flag = n2.patchFlag;
  if (flag > 0) {
    if (flag & PatchFlags.TEXT) {                              // children fast path:children 就是一个字符串
      if (n1.children !== n2.children) { el.text = n2.children; stats.propWrites++; }
    }
    if (flag & PatchFlags.CLASS) {
      stats.propComparisons++;
      if (n1.props.class !== n2.props.class) { el.props.class = n2.props.class; stats.propWrites++; }
    }
    if (flag & PatchFlags.STYLE) {
      stats.propComparisons++;
      if (n1.props.style !== n2.props.style) { el.props.style = n2.props.style; stats.propWrites++; }
    }
    if (flag & PatchFlags.PROPS) {
      for (const key of n2.dynamicProps || []) {
        stats.propComparisons++;
        if (n1.props[key] !== n2.props[key]) { el.props[key] = n2.props[key]; stats.propWrites++; }
      }
    }
  }
  if (n2.dynamicChildren) {
    // "优化模式":块有自己的 dynamicChildren → 只遍历这个扁平数组,**完全跳过**结构性 children diff
    // (对应官方 patchElement 里的 patchBlockChildren 分支;正因如此静态子树连遍历都省了)
    const oldDyn = n1.dynamicChildren || [];
    for (const child of n2.dynamicChildren) {
      const old = oldDyn.find((c) => sameVNode(c, child));
      if (!old) { mountFresh(child, el); stats.nodeVisits++; continue; }   // 新出现的块(v-if 切入)
      patchOptimized(old, child);
    }
  } else if (Array.isArray(n2.children)) {
    // 非块元素(如块内的 span/button):数组 children 走位置对齐 diff
    diffChildren(n1, n2, el, patchOptimized);
  }
  return el;
}

/** 基线路径:全量比较 props + 递归遍历全部 children(纯运行时框架的做法,不做 identity 短路) */
function patchFull(n1, n2, parentEl) {
  if (!n2) return n1 ? n1.el : null;
  if (typeof n1 === 'string' || !n1) return mountFresh(n2, parentEl || n2.parentEl);
  const el = (n2.el = n1.el);
  n2.parentEl = n1.parentEl;
  stats.nodeVisits++;
  const keys = new Set([...Object.keys(n1.props || {}), ...Object.keys(n2.props || {})]);
  for (const k of keys) {
    stats.propComparisons++;
    if (n1.props[k] !== n2.props[k]) { el.props[k] = n2.props[k]; stats.propWrites++; }
  }
  if (typeof n2.children === 'string') {
    if (n1.children !== n2.children) { el.text = n2.children; stats.propWrites++; }
  }
  if (Array.isArray(n2.children)) diffChildren(n1, n2, el, patchFull);
  return el;
}

/** 数组 children 的位置对齐 diff(字符串子节点就地改文本,元素子节点递归 patch) */
function diffChildren(n1, n2, el, patcher) {
  const oldCh = Array.isArray(n1.children) ? n1.children : [];
  const newCh = n2.children;
  for (let i = 0; i < newCh.length; i++) {
    const c = newCh[i];
    if (typeof c === 'string') {
      if (typeof oldCh[i] === 'string' && oldCh[i] !== c) {
        const textEl = el.children[i];
        if (textEl && textEl.tag === '#text') { textEl.text = c; stats.propWrites++; }
      }
    } else if (c && typeof c === 'object') {
      patcher(oldCh[i], c, el);
    }
  }
}

module.exports = { Fragment, stats, resetStats, runtime, El, mount, serialize, patchOptimized, patchFull, sameVNode };

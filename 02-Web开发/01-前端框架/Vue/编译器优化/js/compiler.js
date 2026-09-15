/**
 * Vue 3 模板编译器最小实现 — 编译期优化 (JavaScript)
 *   parse(模板字符串) → transform(静态提升 / patchFlag / Block Tree / 事件缓存)→ codegen(render 函数源码)
 *
 * 权威来源(实际读过):
 *   - https://vuejs.org/guide/extras/rendering-mechanism.html — Cache Static / Patch Flags / Tree Flattening / SSR Hydration
 *   - https://raw.githubusercontent.com/vuejs/core/main/packages/shared/src/patchFlags.ts — PatchFlags 枚举逐条定义
 *   - https://template-explorer.vuejs.org/ — 官方 playground,用于核对编译产物形状
 *
 * 简化(与官方编译器的差异):whitespace 用 'condense' 语义;指令只支持 v-if / :prop / @event / {{ }};
 *   不做 v-for、插槽、组件解析;连续静态元素 ≥ 5 个时压缩为单个 static vnode(innerHTML 挂载)。
 * 运行入口见同目录 compiler_check.js
 */

'use strict';

/** 转录自 vuejs/core packages/shared/src/patchFlags.ts(枚举名与位移一字不差) */
const PatchFlags = {
  TEXT: 1,
  CLASS: 1 << 1,
  STYLE: 1 << 2,
  PROPS: 1 << 3,
  FULL_PROPS: 1 << 4,
  NEED_HYDRATION: 1 << 5,
  STABLE_FRAGMENT: 1 << 6,
  KEYED_FRAGMENT: 1 << 7,
  UNKEYED_FRAGMENT: 1 << 8,
  NEED_PATCH: 1 << 9,
  DYNAMIC_SLOTS: 1 << 10,
  DEV_ROOT_FRAGMENT: 1 << 11,
  // 特殊标记:负数,永不参与按位匹配,只能 === 比较
  CACHED: -1,   // 旧版本名为 HOISTED
  BAIL: -2,
};

/** dev only 的反向映射,用于把 patchFlag 数字还原成 `/* TEXT, CLASS *\/` 注释 */
const PatchFlagNames = {};
for (const k of Object.keys(PatchFlags)) if (PatchFlags[k] > 0) PatchFlagNames[PatchFlags[k]] = k;
function flagComment(flag) {
  if (flag === PatchFlags.CACHED) return 'CACHED';
  if (flag === PatchFlags.BAIL) return 'BAIL';
  return Object.keys(PatchFlags)
    .filter((k) => PatchFlags[k] > 0 && (flag & PatchFlags[k]) === PatchFlags[k])
    .join(', ');
}

const STATIC_CONDENSE_THRESHOLD = 5;   // 连续静态元素达到该数量 → 压缩为一个 static vnode

// ==================== §1 parse:模板 → AST ====================

function findTagEnd(s, from) {
  let quote = null;
  for (let i = from + 1; i < s.length; i++) {
    const c = s[i];
    if (quote) { if (c === quote) quote = null; }
    else if (c === '"' || c === "'") quote = c;
    else if (c === '>') return i;
  }
  return s.length;
}

function parseProps(attrStr) {
  const props = [];
  const re = /([:@]?[\w-]+)(?:\s*=\s*"([^"]*)")?/g;
  let m;
  while ((m = re.exec(attrStr))) {
    const name = m[1];
    props.push({
      name,
      value: m[2] === undefined ? '' : m[2],
      kind: name[0] === ':' ? 'bind' : name[0] === '@' ? 'on' : name.startsWith('v-') ? 'directive' : 'static',
    });
  }
  return props;
}

function addTextNodes(parent, text) {
  const re = /\{\{([\s\S]*?)\}\}/g;
  let last = 0;
  let m;
  while ((m = re.exec(text))) {
    const between = text.slice(last, m.index);
    if (between.trim() !== '') parent.children.push({ type: 'TEXT', content: between });
    parent.children.push({ type: 'INTERPOLATION', content: m[1].trim() });
    last = m.index + m[0].length;
  }
  const tail = text.slice(last);
  if (tail.trim() !== '') parent.children.push({ type: 'TEXT', content: tail });
}

function parse(template) {
  const root = { type: 'ROOT', children: [] };
  const stack = [root];
  let i = 0;
  while (i < template.length) {
    const lt = template.indexOf('<', i);
    if (lt === -1) { addTextNodes(stack[stack.length - 1], template.slice(i)); break; }
    if (lt > i) addTextNodes(stack[stack.length - 1], template.slice(i, lt));
    if (template.startsWith('<!--', lt)) { i = template.indexOf('-->', lt) + 3; continue; }
    const gt = findTagEnd(template, lt);
    const raw = template.slice(lt + 1, gt).trim();
    if (raw.startsWith('/')) { stack.pop(); i = gt + 1; continue; }
    const selfClosing = raw.endsWith('/');
    const body = selfClosing ? raw.slice(0, -1).trim() : raw;
    const sp = body.search(/\s/);
    const tag = sp === -1 ? body : body.slice(0, sp);
    const node = { type: 'ELEMENT', tag, props: parseProps(sp === -1 ? '' : body.slice(sp + 1)), children: [] };
    stack[stack.length - 1].children.push(node);
    if (!selfClosing) stack.push(node);
    i = gt + 1;
  }
  return root;
}

// ==================== §2 transform:静态分析 + 标记 + 提升 + 块树 ====================

/** 递归判定"整棵子树静态":无动态绑定、无指令、无插值 */
function isStaticNode(node) {
  if (node.type === 'TEXT') return true;
  if (node.type !== 'ELEMENT') return false;                       // INTERPOLATION 永远动态
  for (const p of node.props) if (p.kind !== 'static') return false;
  for (const c of node.children) if (!isStaticNode(c)) return false;
  return true;
}

/** 单元素动态信息:patchFlag + dynamicProps(仅 PROPS 需要) */
function analyzeElement(node) {
  let flag = 0;
  const dynamicProps = [];
  for (const p of node.props) {
    if (p.kind === 'static') continue;
    if (p.name === ':class') flag |= PatchFlags.CLASS;
    else if (p.name === ':style') flag |= PatchFlags.STYLE;
    else if (p.kind === 'on') flag |= PatchFlags.NEED_HYDRATION;   // 事件监听需水合
    else if (p.kind === 'bind') { flag |= PatchFlags.PROPS; dynamicProps.push(p.name.slice(1)); }
    // 注意:v-if 不产生 patchFlag —— 它是结构指令,由 openBlock/createBlock 新建一个块表达
  }
  // TEXT:仅"唯一子节点是插值"的 children fast path(数组 children 由位置 diff 处理,官方也不给元素打这个标记)
  if (node.children.length === 1 && node.children[0].type === 'INTERPOLATION') flag |= PatchFlags.TEXT;
  return { flag, dynamicProps };
}

/** 结构指令判定:目前只支持 v-if(与 v-for 同理,都新建块) */
function hasIf(node) {
  return node.type === 'ELEMENT' && node.props.some((p) => p.kind === 'directive' && p.name === 'v-if');
}

/** 连续静态元素压缩成一个 static vnode(挂载时直接 innerHTML) */
function condenseStaticSiblings(children, hoisted, stats) {
  const out = [];
  let i = 0;
  while (i < children.length) {
    if (children[i].type === 'ELEMENT' && isStaticNode(children[i])) {
      let j = i;
      while (j < children.length && children[j].type === 'ELEMENT' && isStaticNode(children[j])) j++;
      const run = children.slice(i, j);
      if (run.length >= STATIC_CONDENSE_THRESHOLD) {
        const html = run.map(renderStaticHtml).join('');
        out.push(hoist({ type: 'STATIC_STRING', html, count: run.length }, hoisted));
        stats.condensed++;
        i = j;
        continue;
      }
    }
    out.push(children[i]);
    i++;
  }
  return out;
}

function renderStaticHtml(node) {
  const attrs = node.props.map((p) => ` ${p.name}="${p.value}"`).join('');
  const inner = node.children.map((c) => (c.type === 'TEXT' ? c.content : renderStaticHtml(c))).join('');
  return `<${node.tag}${attrs}>${inner}</${node.tag}>`;
}

function hoist(node, hoisted) {
  hoisted.push(node);
  return { type: 'HOISTED_REF', name: `_hoisted_${hoisted.length}`, source: node };
}

/**
 * 核心遍历:自底向上
 *   1) 静态子树 → 提升到 render 外(只创建一次)
 *   2) 连续静态兄弟 → 压缩成 static vnode
 *   3) 动态元素 → 打 patchFlag、生成 block(收集 dynamicChildren)
 */
function transformChildren(children, hoisted, stats) {
  const condensed = condenseStaticSiblings(children, hoisted, stats);
  const out = [];
  for (const child of condensed) {
    if (child.type === 'HOISTED_REF') { out.push(child); continue; }
    if (child.type === 'ELEMENT') {
      if (isStaticNode(child)) { out.push(hoist(child, hoisted)); continue; }   // 单个静态元素也提升
      const info = analyzeElement(child);
      child.children = transformChildren(child.children, hoisted, stats);
      child.patchFlag = info.flag;
      child.dynamicProps = info.dynamicProps;
      child.isBlock = hasIf(child);        // 结构指令(v-if)新建块;动态节点仍由父块收集
      if (child.isBlock) stats.blocks++;
      if (info.flag > 0) stats.dynamicNodes++;
    }
    out.push(child);
  }
  return out;
}

function transform(ast) {
  const hoisted = [];
  const stats = { dynamicNodes: 0, blocks: 1, condensed: 0 };   // 根 block 恒为 1
  ast.children = transformChildren(ast.children, hoisted, stats);
  return { ast, hoisted, stats };
}

// ==================== §3 codegen:AST → render 函数源码 ====================

function genProps(node) {
  const pairs = [];
  for (const p of node.props) {
    if (p.kind === 'static') pairs.push(`${JSON.stringify(p.name)}: ${JSON.stringify(p.value)}`);
    else if (p.name === ':class') pairs.push(`class: normalizeClass(_ctx.${p.value})`);
    else if (p.name === ':style') pairs.push(`style: _ctx.${p.value}`);
    else if (p.kind === 'bind') pairs.push(`${JSON.stringify(p.name.slice(1))}: _ctx.${p.value}`);
    else if (p.kind === 'on') {
      const ev = p.name.slice(1);
      pairs.push(`${JSON.stringify('on' + ev[0].toUpperCase() + ev.slice(1))}: _cache[0] || (_cache[0] = (...args) => _ctx.${p.value}(...args))`);
    }
    // v-if / v-for 是结构指令,不进 props(由 genVIf 生成三元 + 新建块)
  }
  return pairs.length === 0 ? 'null' : `{ ${pairs.join(', ')} }`;
}

function genChildren(node) {
  if (node.children.length === 0) return 'null';
  const parts = node.children.map((c) => {
    if (c.type === 'HOISTED_REF') return c.name;
    if (c.type === 'TEXT') return JSON.stringify(c.content);
    if (c.type === 'INTERPOLATION') return `toDisplayString(_ctx.${c.content})`;
    return genNode(c);          // 递归时同样要处理 v-if
  });
  // children fast path:唯一子节点是文本/插值时直接内联(官方编译产物即如此,如 createElementVNode("p",{...},_toDisplayString(_ctx.msg),1))
  if (node.children.length === 1 && (node.children[0].type === 'TEXT' || node.children[0].type === 'INTERPOLATION')) {
    return parts[0];
  }
  return `[\n      ${parts.join(',\n      ')},\n    ]`;
}

function genElement(node) {
  const ctor = node.isBlock ? 'createBlock' : 'createVNode';
  const extra = node.dynamicProps && node.dynamicProps.length > 0
    ? `, ${JSON.stringify(node.dynamicProps)}` : '';
  return `${ctor}(${JSON.stringify(node.tag)}, ${genProps(node)}, ${genChildren(node)}, ` +
    `${node.patchFlag} /* ${flagComment(node.patchFlag)} */${extra})`;
}

function genVIf(node) {
  const cond = node.props.find((p) => p.name === 'v-if').value;
  const without = { ...node, props: node.props.filter((p) => p.name !== 'v-if') };
  return `_ctx.${cond}\n      ? (openBlock(), ${genElement(without)})\n      : createCommentVNode("v-if", true)`;
}

function genNode(node) {
  if (hasIf(node)) return genVIf(node);
  return genElement(node);
}

function genHoistedSource(node) {
  if (node.type === 'STATIC_STRING') {
    return `createStaticVNode(${JSON.stringify(node.html)}, ${node.count})`;
  }
  const clone = { ...node, props: node.props, children: node.children };
  return `createVNode(${JSON.stringify(clone.tag)}, ${genProps(clone)}, ${genChildren(clone)}, ` +
    `${PatchFlags.CACHED} /* CACHED */)`;
}

function codegen(transformResult) {
  const { ast, hoisted } = transformResult;
  const decls = hoisted.map((h, i) => `  const _hoisted_${i + 1} = /*#__PURE__*/ ${genHoistedSource(h)};`).join('\n');
  // 根节点恒为 block(Vue 编译器对根元素强制 createBlock)
  if (ast.children.length === 1 && ast.children[0].type === 'ELEMENT') ast.children[0].isBlock = true;
  const roots = ast.children.map(genNode);
  // 单根元素:openBlock + createBlock(根恒为块);多根:Fragment 包一层 STABLE_FRAGMENT 块
  const rootExpr = roots.length === 1
    ? `(openBlock(), ${roots[0]})`
    : `(openBlock(), createBlock(Fragment, null, [\n    ${roots.join(',\n    ')},\n  ], ${PatchFlags.STABLE_FRAGMENT} /* STABLE_FRAGMENT */))`;
  const body = `function render(_ctx, _cache) {\n  return (${rootExpr});\n}`;
  return {
    source: `${decls}\n\n${body}`,
    factorySource: `
const { createVNode, createBlock, openBlock, createStaticVNode, createCommentVNode, toDisplayString, normalizeClass, Fragment } = __rt;
${decls}
${body}
return render;`,
  };
}

module.exports = {
  PatchFlags, PatchFlagNames, flagComment, STATIC_CONDENSE_THRESHOLD,
  parse, transform, analyzeElement, isStaticNode, hasIf, codegen, genElement, genNode, genVIf, renderStaticHtml,
};

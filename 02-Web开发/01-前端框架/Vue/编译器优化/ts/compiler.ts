/**
 * Vue 3 编译期优化 — TypeScript 类型化版本
 *
 * 与 js/compiler.js + js/runtime.js 等价,重点是把「模板 AST / vnode / patchFlag」
 * 三层的形状用可辨识联合与枚举钉死 —— 这也是官方 @vue/compiler-core 的 NodeTypes 设计思路。
 *
 * 本机无 tsc,未实际编译;类型仅作静态说明(与仓库其它 ts/ 目录同策略)。
 * 权威来源见 js/compiler.js 文件头。
 */

// ==================== 类型 ====================

/** 转录自 vuejs/core packages/shared/src/patchFlags.ts */
export enum PatchFlags {
  TEXT = 1,
  CLASS = 1 << 1,
  STYLE = 1 << 2,
  PROPS = 1 << 3,
  FULL_PROPS = 1 << 4,
  NEED_HYDRATION = 1 << 5,
  STABLE_FRAGMENT = 1 << 6,
  KEYED_FRAGMENT = 1 << 7,
  UNKEYED_FRAGMENT = 1 << 8,
  NEED_PATCH = 1 << 9,
  DYNAMIC_SLOTS = 1 << 10,
  DEV_ROOT_FRAGMENT = 1 << 11,
  /** 缓存过的静态 vnode(旧名 HOISTED):负数,只做 === 比较 */
  CACHED = -1,
  /** 退出优化模式:必须整棵 diff */
  BAIL = -2,
}

export type PropKind = 'static' | 'bind' | 'on' | 'directive';

export interface TemplateProp { name: string; value: string; kind: PropKind }

export interface TextNode { type: 'TEXT'; content: string }
export interface InterpolationNode { type: 'INTERPOLATION'; content: string }
export interface ElementNode {
  type: 'ELEMENT';
  tag: string;
  props: TemplateProp[];
  children: TemplateChild[];
  /** transform 阶段写入 */
  patchFlag?: number;
  dynamicProps?: string[];
  isBlock?: boolean;
}
export interface RootNode { type: 'ROOT'; children: TemplateChild[] }
export type TemplateChild = TextNode | InterpolationNode | ElementNode
  | { type: 'HOISTED_REF'; name: string; source: ElementNode | StaticStringNode }
  | StaticStringNode;
export interface StaticStringNode { type: 'STATIC_STRING'; html: string; count: number }

export interface VNode {
  type: string | symbol;
  props: Record<string, unknown>;
  children: string | VNode[] | null;
  patchFlag: number;
  dynamicProps: string[] | null;
  /** Block Tree:块才会有的扁平动态后代数组 */
  dynamicChildren: VNode[] | null;
  el: unknown;
}

export interface CompileStats { dynamicNodes: number; blocks: number; condensed: number }
export interface PatchStats {
  vnodeCreations: number; staticVnodeMounts: number; nodeVisits: number;
  propComparisons: number; propWrites: number; reusedStatic: number;
}

// ==================== §1 静态分析 ====================

export function isStaticNode(node: TemplateChild): boolean {
  if (node.type === 'TEXT') return true;
  if (node.type !== 'ELEMENT') return false;          // INTERPOLATION 永远动态
  for (const p of node.props) if (p.kind !== 'static') return false;
  return node.children.every(isStaticNode);
}

/** 单元素动态信息:与官方 transformElement 的 patchFlag 累积等价 */
export function analyzeElement(node: ElementNode): { flag: number; dynamicProps: string[] } {
  let flag = 0;
  const dynamicProps: string[] = [];
  for (const p of node.props) {
    if (p.kind === 'static') continue;
    if (p.name === ':class') flag |= PatchFlags.CLASS;
    else if (p.name === ':style') flag |= PatchFlags.STYLE;
    else if (p.kind === 'on') flag |= PatchFlags.NEED_HYDRATION;     // 事件需水合
    else if (p.kind === 'bind') { flag |= PatchFlags.PROPS; dynamicProps.push(p.name.slice(1)); }
    // v-if 是结构指令,不进 patchFlag —— 由 openBlock/createBlock 新建块表达
  }
  // TEXT 只用于"唯一子节点是插值"的 children fast path;数组 children 由位置 diff 处理
  if (node.children.length === 1 && node.children[0].type === 'INTERPOLATION') flag |= PatchFlags.TEXT;
  return { flag, dynamicProps };
}

export const hasIf = (node: ElementNode): boolean =>
  node.props.some((p) => p.kind === 'directive' && p.name === 'v-if');

export const STATIC_CONDENSE_THRESHOLD = 5;

/** 连续静态元素压缩成一个 static vnode(挂载时直接 innerHTML,等价官方 transformHoist 的 stringifyStatic) */
export function condenseStaticSiblings(
  children: TemplateChild[], hoisted: (ElementNode | StaticStringNode)[], stats: CompileStats,
): TemplateChild[] {
  const out: TemplateChild[] = [];
  let i = 0;
  while (i < children.length) {
    const head = children[i];
    if (head.type === 'ELEMENT' && isStaticNode(head)) {
      let j = i;
      while (j < children.length && children[j].type === 'ELEMENT' && isStaticNode(children[j])) j++;
      const run = children.slice(i, j) as ElementNode[];
      if (run.length >= STATIC_CONDENSE_THRESHOLD) {
        const node: StaticStringNode = {
          type: 'STATIC_STRING', html: run.map(renderStaticHtml).join(''), count: run.length,
        };
        hoisted.push(node);
        stats.condensed++;
        out.push({ type: 'HOISTED_REF', name: `_hoisted_${hoisted.length}`, source: node });
        i = j;
        continue;
      }
    }
    out.push(head);
    i++;
  }
  return out;
}

export function renderStaticHtml(node: ElementNode): string {
  const attrs = node.props.map((p) => ` ${p.name}="${p.value}"`).join('');
  const inner = node.children
    .map((c) => (c.type === 'TEXT' ? c.content : c.type === 'ELEMENT' ? renderStaticHtml(c) : ''))
    .join('');
  return `<${node.tag}${attrs}>${inner}</${node.tag}>`;
}

// ==================== §2 运行时契约(与 compiler 产出的 render 函数对接) ====================

export function createVNode(
  type: string | symbol,
  props: Record<string, unknown> | null,
  children: string | VNode[] | null,
  patchFlag = 0,
  dynamicProps: string[] | null = null,
  /** true 表示这是 createBlock 来的块 vnode:不把自己 push 进当前块的 dynamicChildren */
  isBlock = false,
): VNode {
  void isBlock;
  return {
    type, props: props ?? {}, children, patchFlag, dynamicProps,
    dynamicChildren: null, el: null,
  };
}

/** 挂载:静态树走 innerHTML,不建子树 */
export function mountStatic(vnode: { html: string; count: number }): string {
  return vnode.html;
}

/**
 * 优化路径:只按 patchFlag 更新标记过的属性;块用 dynamicChildren 寻址,
 * **不做结构性 children diff**(对应官方 patchElement 的 patchBlockChildren 分支)。
 */
export function patchOptimized(n1: VNode | null, n2: VNode, el: unknown, stats: PatchStats): unknown {
  if (n1 === n2) { stats.reusedStatic++; return n1.el; }
  if (n2.patchFlag === PatchFlags.CACHED) { stats.reusedStatic++; return n2.el ?? (n1 && n1.el); }
  stats.nodeVisits++;
  const flag = n2.patchFlag;
  if (flag > 0) {
    if (flag & PatchFlags.TEXT) stats.propWrites++;                      // children fast path
    if (flag & PatchFlags.CLASS) stats.propComparisons++;
    if (flag & PatchFlags.STYLE) stats.propComparisons++;
    if (flag & PatchFlags.PROPS) {
      for (const _key of n2.dynamicProps ?? []) stats.propComparisons++; // 只比较标出来的 key
    }
  }
  if (n2.dynamicChildren) {
    for (const child of n2.dynamicChildren) patchOptimized(null, child, el, stats);
  }
  return el;
}

/** 基线路径:全量比较 props + 递归全部 children —— 纯运行时框架每轮重建 vnode,identity 永远不等 */
export function patchFull(n1: VNode | null, n2: VNode, el: unknown, stats: PatchStats): unknown {
  stats.nodeVisits++;
  const keys = new Set([...Object.keys(n1?.props ?? {}), ...Object.keys(n2.props)]);
  for (const _k of keys) stats.propComparisons++;
  if (Array.isArray(n2.children)) for (const c of n2.children) patchFull(null, c, el, stats);
  return el;
}

// ==================== §3 对照表(编译期到底做了什么) ====================

export interface Optimization { name: string; trigger: string; effect: string; measured: string }

export const OPTIMIZATIONS: Optimization[] = [
  {
    name: 'Cache Static / 静态提升',
    trigger: '子树无动态绑定、无指令、无插值',
    effect: 'render 外只创建一次 vnode,后续渲染复用同一引用',
    measured: '每轮 render 的 vnode 创建数 9 → 4',
  },
  {
    name: 'stringifyStatic / 静态压缩',
    trigger: '连续 ≥5 个静态元素',
    effect: '压成一个 static vnode,挂载直接 innerHTML',
    measured: '5 个 div = 1 次 innerHTML',
  },
  {
    name: 'Patch Flags / 靶向更新',
    trigger: '元素有动态绑定',
    effect: '编译期把"要更新什么"编码成位掩码',
    measured: 'props 比较 6 → 2(只比 class/style)',
  },
  {
    name: 'Tree Flattening / Block Tree',
    trigger: '根节点 / v-if / v-for / 插槽出口',
    effect: '块的动态后代平铺成 dynamicChildren,跳过静态结构',
    measured: 'patch 访问节点 6 → 4',
  },
  {
    name: 'cacheHandler / 事件缓存',
    trigger: '模板内联 @click="fn"',
    effect: '编译为 _cache[0] || (_cache[0] = ...),引用跨渲染稳定',
    measured: 'handler 引用 === 恒定',
  },
];

/**
 * 一句话总结编译期优化的本质:
 *   把"运行时才能知道的 diff 范围"提前到编译期算完,Vue 才能真正做到"树结构编写、数组结构更新"。
 * 代价:模板自由度被限制(必须可静态分析),这也是 Vue 与纯运行时框架(React)的根本分野。
 */
export const COMPILER_INFORMED_VDOM_NOTE =
  'Compiler-Informed Virtual DOM:框架同时掌握编译期与运行期,才能在 js 运行时里安全地做静态假设。';

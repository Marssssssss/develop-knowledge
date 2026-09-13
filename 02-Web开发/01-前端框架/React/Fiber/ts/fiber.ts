/**
 * React Fiber 架构 — TypeScript 类型化版本
 *
 * 与 JS 版(fiber.js)等价,所有字段加类型,Fiber 构造/链接类型安全。
 * 不实际运行(本目录无 React 依赖),TS 通过 tsc --noEmit 校验。
 *
 * 运行:npx tsc --noEmit fiber.ts(假设有 tsconfig)
 */

// ---------------- 类型 ----------------
export type FiberTag = 'PLACEMENT' | 'UPDATE' | 'DELETION';

export interface VNode {
  type: string | FunctionComponent;
  props: Record<string, unknown> & { children?: VNode[] };
  key?: string | number | null;
}
export type FunctionComponent = (props: VNode['props']) => VNode | null;

export interface Fiber {
  type: VNode['type'];
  key: VNode['key'];
  props: VNode['props'];
  stateNode: HTMLElement | null;
  child: Fiber | null;
  sibling: Fiber | null;
  return: Fiber | null;
  alternate: Fiber | null;
  pendingProps: VNode['props'];
  memoizedState: unknown;
  effectTag: FiberTag;
  nextEffect: Fiber | null;
}

// ---------------- h 函数 ----------------
export function h(
  type: VNode['type'],
  props: VNode['props'] = {},
  ...children: VNode[]
): VNode {
  return { type, props: { ...props, children: children.flat() } };
}

// ---------------- 创建 Fiber ----------------
export function createFiber(vnode: VNode, returnFiber: Fiber | null): Fiber {
  return {
    type: vnode.type,
    key: vnode.key,
    props: vnode.props,
    stateNode: null,
    child: null,
    sibling: null,
    return: returnFiber,
    alternate: null,
    pendingProps: vnode.props,
    memoizedState: null,
    effectTag: 'PLACEMENT',
    nextEffect: null,
  };
}

// ---------------- 双缓冲 ----------------
export function createWorkInProgress(current: Fiber, pendingProps: VNode['props']): Fiber {
  let wip: Fiber;
  if (current.alternate === null) {
    wip = {
      ...createFiber({ type: current.type, props: pendingProps }, current.return),
      alternate: current,
      pendingProps,
    };
    current.alternate = wip;
  } else {
    wip = current.alternate;
    wip.pendingProps = pendingProps;
    wip.effectTag = 'UPDATE';
    wip.child = null;
    wip.sibling = null;
  }
  return wip;
}

// ---------------- render 阶段 ----------------
export let nextUnitOfWork: Fiber | null = null;
export let wipRoot: Fiber | null = null;
export let currentRoot: Fiber | null = null;

export function shouldYield(): boolean {
  return false; // 简化:真实 React 5ms 时间片
}

export function beginWork(wip: Fiber): Fiber | null {
  if (typeof wip.type === 'function') return updateFunctionComponent(wip);
  if (typeof wip.type === 'string') return updateHostComponent(wip);
  throw new Error(`Unknown fiber type: ${String(wip.type)}`);
}

function updateFunctionComponent(wip: Fiber): Fiber | null {
  const children = wip.props.children ?? [];
  reconcileChildren(wip, children as VNode[]);
  return wip.child;
}

function updateHostComponent(wip: Fiber): Fiber | null {
  if (wip.stateNode === null) {
    // 真实:document.createElement(wip.type)
    wip.stateNode = null as unknown as HTMLElement;
  }
  const children = wip.props.children ?? [];
  reconcileChildren(wip, children as VNode[]);
  return wip.child;
}

// ---------------- reconcileChildren ----------------
export function reconcileChildren(returnFiber: Fiber, children: VNode[]): void {
  let prevSibling: Fiber | null = null;
  let oldFiber: Fiber | null = returnFiber.alternate?.child ?? null;
  for (const child of children) {
    let newFiber: Fiber | null = null;
    const sameType = oldFiber !== null && oldFiber.type === child.type;
    if (sameType && oldFiber) {
      newFiber = createWorkInProgress(oldFiber, child.props);
      newFiber.effectTag = 'UPDATE';
    } else {
      newFiber = createFiber(child, returnFiber);
      newFiber.effectTag = 'PLACEMENT';
    }
    if (oldFiber) oldFiber = oldFiber.sibling;
    if (newFiber === null) continue;
    if (prevSibling === null) returnFiber.child = newFiber;
    else prevSibling.sibling = newFiber;
    prevSibling = newFiber;
  }
}

// ---------------- completeWork ----------------
export function completeWork(wip: Fiber): Fiber | null {
  const parent = wip.return;
  if (parent !== null) {
    // effect 链表 append
    if (wip.effectTag === 'PLACEMENT' || wip.effectTag === 'UPDATE') {
      const pAny = parent as Fiber & { firstEffect?: Fiber | null; lastEffect?: Fiber | null };
      if (pAny.lastEffect === null || pAny.lastEffect === undefined) {
        pAny.firstEffect = wip;
      } else {
        pAny.lastEffect!.nextEffect = wip;
      }
      pAny.lastEffect = wip;
    }
  }
  if (wip.sibling) return wip.sibling;
  return wip.return;
}

// ---------------- workLoop ----------------
export function performUnitOfWork(wip: Fiber): Fiber | null {
  const next = beginWork(wip);
  if (next === null) {
    let cur: Fiber | null = wip;
    while (cur !== null) {
      const r = completeWork(cur);
      if (r !== null) return r;
      cur = cur.return;
    }
    return null;
  }
  return next;
}

export function workLoop(): void {
  while (nextUnitOfWork !== null && !shouldYield()) {
    nextUnitOfWork = performUnitOfWork(nextUnitOfWork);
  }
  if (nextUnitOfWork === null && wipRoot !== null) commitRoot();
}

export function commitRoot(): void {
  let effect = (wipRoot as Fiber & { firstEffect?: Fiber | null }).firstEffect ?? null;
  const ops: string[] = [];
  while (effect !== null) {
    ops.push(effect.effectTag);
    effect = effect.nextEffect;
  }
  console.log('[render OK] ops:', ops.join(','));
  currentRoot = wipRoot;
  wipRoot = null;
  console.log('[commit OK] tree depth=', countTree(currentRoot));
}

function countTree(f: Fiber | null): number {
  if (!f) return 0;
  return 1 + countTree(f.child) + countTree(f.sibling);
}

export function render(vnode: VNode): void {
  const root = createFiber(vnode, null);
  wipRoot = root;
  currentRoot = currentRoot ?? ({} as Fiber);
  root.alternate = currentRoot;
  nextUnitOfWork = wipRoot;
  workLoop();
}

// ---------------- demo ----------------
const tree: VNode = h(
  'div',
  { id: 'app' },
  h('h1', {}, h('text', { children: [] } as unknown as VNode)),
  h('p', {}),
  h('section', {},
    h('span', {}),
    h('span', {}),
  ),
);
// render(tree);

/**
 * React Hooks 链表实现 — TypeScript 类型化
 *
 * 与 JS 版(hooks.js)等价,加类型约束,Fiber/Hook 类型清晰。
 * TS 通过 tsc --noEmit 校验。
 *
 * 运行:npx tsc --noEmit hooks.ts
 */

// ---------------- 类型 ----------------
export interface Hook {
  memoizedState: unknown;
  queue: { pending: Update | null };
  next: Hook | null;
}

export interface Update<T = unknown> {
  action: T | ((prev: T) => T);
  next: Update<T> | null;
}

export interface EffectHookState {
  deps: ReadonlyArray<unknown> | null;
  create: () => void | (() => void);
}

export interface MemoHookState<T> {
  value: T;
  deps: ReadonlyArray<unknown>;
}

// ---------------- 链表操作 ----------------
export function createHook(): Hook {
  return { memoizedState: undefined, queue: { pending: null }, next: null };
}

// 多个 update 接成环链表
export function dispatchAction<T>(hook: Hook, action: Update<T>['action']): void {
  const update: Update<T> = { action, next: null };
  const q = hook.queue as { pending: Update<T> | null };
  if (q.pending === null) {
    update.next = update; // 1 个元素,自己指向自己
    q.pending = update;
  } else {
    update.next = q.pending.next;
    q.pending.next = update;
    q.pending = update;
  }
}

// 消费环链表
export function flushQueue<T>(hook: Hook, base: T): T {
  const q = hook.queue as { pending: Update<T> | null };
  if (q.pending === null) return base;
  let p = q.pending.next as Update<T>;
  let current = base;
  do {
    current = typeof p.action === 'function'
      ? (p.action as (prev: T) => T)(current)
      : (p.action as T);
    p = p.next as Update<T>;
  } while (p !== q.pending);
  q.pending = null;
  return current;
}

// ---------------- Hooks Dispatch ----------------
export interface FiberLike {
  memoizedState: Hook | null;
}

export class HooksContext {
  isMount = true;
  workInProgress: FiberLike | null = null;
  currentHook: Hook | null = null;
  pendingEffects: Array<() => void | (() => void)> = [];

  resetForRender(fiber: FiberLike): void {
    this.workInProgress = fiber;
    this.currentHook = { memoizedState: undefined, queue: { pending: null }, next: null };
  }

  // walkNext 取链表下一节点,本 fiber 第一次调 any hook 时为 null,就新建
  walkNext(): Hook {
    let h = this.currentHook as Hook | null;
    if (h === null) throw new Error('currentHook must be initialized');
    if (h.next === null) h.next = createHook();
    this.currentHook = h.next;
    return h.next as Hook;
  }

  // ---- useState ----
  useState<T>(initial: T): [T, (a: T | ((p: T) => T)) => void] {
    const hook = this.walkNext();
    if (this.isMount) hook.memoizedState = initial;
    const base = (this.isMount ? initial : hook.memoizedState) as T;
    const next = flushQueue<T>(hook, base);
    hook.memoizedState = next;
    const setState = (a: T | ((p: T) => T)) => dispatchAction<T>(hook, a);
    return [hook.memoizedState as T, setState];
  }

  // ---- useReducer (reducer 版) ----
  useReducer<T, A>(
    reducer: (state: T, action: A) => T,
    initial: T,
  ): [T, (a: A) => void] {
    const [state, dispatch] = this.useState<T>(initial);
    const dispatchWrap = (action: A): void => dispatch((p: T) => reducer(p, action));
    return [state, dispatchWrap];
  }

  // ---- useRef ----
  useRef<T>(initial: T): { current: T } {
    const hook = this.walkNext();
    if (this.isMount) hook.memoizedState = { current: initial };
    return hook.memoizedState as { current: T };
  }

  // ---- useMemo ----
  useMemo<T>(factory: () => T, deps: ReadonlyArray<unknown>): T {
    const hook = this.walkNext();
    if (this.isMount) {
      hook.memoizedState = { value: factory(), deps };
    } else {
      const prev = hook.memoizedState as MemoHookState<T>;
      const same = deps.every((d, i) => Object.is(d, prev.deps[i] as unknown));
      if (!same) hook.memoizedState = { value: factory(), deps };
    }
    return (hook.memoizedState as MemoHookState<T>).value;
  }

  // ---- useEffect ----
  useEffect(create: () => void | (() => void), deps: ReadonlyArray<unknown> | null): void {
    const hook = this.walkNext();
    if (this.isMount) {
      hook.memoizedState = { deps, create };
      this.pendingEffects.push(create);
2: continue;
    } else {
      const prev = hook.memoizedState as EffectHookState;
      const same = Array.isArray(deps) && Array.isArray(prev.deps) &&
        deps.every((d, i) => Object.is(d, (prev.deps as ReadonlyArray<unknown>)[i]));
      if (!same) {
        hook.memoizedState = { deps, create };
        this.pendingEffects.push(create);
      }
    }
  }
}

// ---------------- 渲染循环 ----------------
export function renderFiber(c: HooksContext, fiber: FiberLike, render: () => void): void {
  c.isMount = !fiber.memoizedState;
  c.resetForRender(fiber);
  c.pendingEffects = [];
  render();
  for (const create of c.pendingEffects) create();
}

// ---------------- demo ----------------
export function demo(c: HooksContext, fiber: FiberLike): void {
  renderFiber(c, fiber, () => {
    const [count] = c.useState<number>(0);
    const [todo] = c.useReducer<number, number>((s, a) => s + a, 0);
    const ref = c.useRef<string>('init');
    const squared = c.useMemo<number>(() => count * count, [count]);
    c.useEffect(() => { /* side effect */ }, [count]);
    console.log(`[render ${c.isMount ? 'M' : 'U'}]`,
      'c=', count, 't=', todo, 'ref.current=', ref.current, 'sq=', squared);
  });
}

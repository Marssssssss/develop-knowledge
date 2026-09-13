/**
 * Zustand 极简 store 自实现 — TypeScript 版
 *
 * 与 JS 版(zustand.js)等价,加类型注解。
 * useStore 部分用 hook 但本目录无 React runtime,仅类型签名示范。
 *
 * 运行:npx tsc --noEmit zustand.ts
 */

// ---------------- 类型 ----------------
export type StateCreator<T> = (
  set: (partial: Partial<T> | ((state: T) => Partial<T>), replace?: boolean) => void,
  get: () => T,
  api: StoreApi<T>,
) => T;

export interface StoreApi<T> {
  getState: () => T;
  setState: (partial: Partial<T> | ((state: T) => Partial<T>), replace?: boolean) => void;
  subscribe: (listener: () => void) => () => void;
}

// ---------------- 1. createStore vanilla ----------------
export function createStore<T>(initializer: StateCreator<T>): StoreApi<T> {
  let state!: T;
  const listeners = new Set<() => void>();

  const setState: StoreApi<T>['setState'] = (partial, replace = false) => {
    const next = typeof partial === 'function'
      ? (partial as (s: T) => Partial<T>)(state)
      : partial;
    if (next === null || next === undefined) return;
    if (replace === true) {
      state = next as unknown as T;
    } else {
      state = { ...state, ...next } as unknown as T;
    }
    for (const l of listeners) l();
  };

  const getState = () => state;
  const subscribe = (l: () => void) => { listeners.add(l); return () => listeners.delete(l); };

  const api: StoreApi<T> = { getState, setState, subscribe };
  state = initializer(setState, getState, api);
  return api;
}

// ---------------- 2. subscribeWithSelector middleware ----------------
export interface SubscribeWithSelectorOptions<T, S> {
  equalityFn?: (a: S, b: S) => boolean;
  fireImmediately?: boolean;
}

export function subscribeWithSelector<T>(store: StoreApi<T>): StoreApi<T> & {
  subscribe: <S>(
    selector: (state: T) => S,
    listener: (selected: S, previous: S) => void,
    options?: SubscribeWithSelectorOptions<T, S>,
  ) => () => void;
} {
  // Simplified:扩展 subscribe 以支持 selector 重载
  const baseSub = store.subscribe;
  const selSub = <S>(
    selector: (state: T) => S,
    listener: (selected: S, previous: S) => void,
    options: SubscribeWithSelectorOptions<T, S> = {},
  ): (() => void) => {
    let current = selector(store.getState());
    if (options.fireImmediately === true) listener(current, current);
    const wrap = (): void => {
      const next = selector(store.getState());
      const eq = options.equalityFn ?? Object.is;
      if (!eq(current, next)) {
        const prev = current;
        current = next;
        listener(next, prev);
      }
    };
    return baseSub(wrap);
  };
  // 双重身份:其实真正使用的是 selSub,本 demo 不调纯 baseSub
  return Object.assign(store, { subscribe: selSub } as unknown as {
    subscribe: typeof selSub;
  });
}

// ---------------- 3. shallow 全等 ----------------
export function shallow<T extends object>(a: T, b: T): boolean {
  if (Object.is(a, b)) return true;
  if (a === null || b === null) return false;
  const ka = Object.keys(a), kb = Object.keys(b);
  if (ka.length !== kb.length) return false;
  for (const k of ka) if (!Object.is(a[k as keyof T], b[k as keyof T])) return false;
  return true;
}

// ---------------- 4. useStore 类型示意(无 React runtime) ----------------
export function makeUseStore<T, S = T>(store: StoreApi<T>) {
  return function useStore(selector: (state: T) => S): S {
    return selector(store.getState());
  };
}

// ---------------- 5. demo ----------------
export function demo(): void {
  interface BearState { bears: number; increase: () => void; reset: () => void; }
  const bearStore = createStore<BearState>((set) => ({
    bears: 0,
    increase: () => set((s) => ({ bears: s.bears + 1 })),
    reset: () => set({ bears: 0 }, true),
  }));

  console.log('initial:', bearStore.getState().bears);
  bearStore.getState().increase();
  bearStore.getState().increase();
  console.log('after +2:', bearStore.getState().bears);
  bearStore.getState().reset();
  console.log('after reset:', bearStore.getState().bears);

  // shallow 用例
  const sl1 = { a: 1, b: 2 };
  const sl2 = { a: 1, b: 2 };
  console.log('shallow(sl1, sl2):', shallow(sl1, sl2));

  // useStore demo
  const useBear = makeUseStore(bearStore);
  console.log('useBear slice:', useBear((s) => s.bears));
}

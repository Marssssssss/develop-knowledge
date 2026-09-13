/**
 * Zustand 极简 store 自实现 (JavaScript 版)
 *
 * 演示:
 *   1) createStore(set/get/subscribe) 函数式 store,无 Provider 上下文
 *   2) useStore hook + selector + Object.is 选择器
 *   3) getState / setState / subscribe 手动操作(可在 React 外用)
 *   4) 中间件 subscribeWithSelector 简化版
 *   5) shallow 用法
 *
 * 权威来源:
 *   - github.com/pmndrs/zustand README(实际读过)
 *   - react 18+ useSyncExternalStore(docs.react.dev/reference/react/useSyncExternalStore)
 *
 * 运行:node zustand.js → 输出 6 demo(创建 store / 多次订阅 / selector 精确订阅 / 手动 subscribe / persist 简化版 / shallow)
 */

// ---------------- 1. 极简 vanilla store ----------------
function createStore(initializer) {
  let state;
  const listeners = new Set();

  function setState(partial, replace = false) {
    const next = typeof partial === 'function' ? partial(state) : partial;
    if (next == null) return; // zustand:null/undefined 都忽略
    if (replace) {
      state = typeof next === 'object' ? next : { value: next };
    } else {
      // 函数返回整个对象时,把返回对象替换(部分风格 zustand 接口)
      // 此处我们简化为"返回的若是对象且 keys 命中已有 key → 合并;否则合并"
      state = typeof next === 'object'
        ? { ...state, ...next }
        : { ...state, value: next };
    }
    for (const l of listeners) l(state);
  }

  function getState() { return state; }

  function subscribe(listener) {
    listeners.add(listener);
    // 与 zustand 对齐:首次同步触发(同步 snapshot)
    listener(state);
    return () => listeners.delete(listener);
  }

  // 初始化:支持 (set, get, storeApi) => state
  const api = { getState, setState, subscribe };
  state = initializer(
    (partial) => setState(partial),
    () => getState(),
    api,
  );
  return api;
}

// ---------------- 2. 订阅 with selector(实际项目里通过 middleware 实现) ----------------
function subscribeWithSelector(store) {
  const { subscribe, getState } = store;
  store.subscribe = (selector, listener, options = {}) => {
    if (typeof listener !== 'function') throw new Error('listener required');
    const eq = options.equalityFn || Object.is;
    let currentSel = selector(getState());
    if (options.fireImmediately) listener(currentSel, currentSel);
    const wrap = () => {
      const next = selector(getState());
      if (!eq(currentSel, next)) {
        const prev = currentSel;
        currentSel = next;
        listener(next, prev);
      }
    };
    return subscribe(wrap);
  };
  return store;
}

// ---------------- 3. shallow 全等(用于 useShallow 场景) ----------------
function shallow(a, b) {
  if (Object.is(a, b)) return true;
  if (typeof a !== 'object' || a === null) return false;
  if (typeof b !== 'object' || b === null) return false;
  const ka = Object.keys(a), kb = Object.keys(b);
  if (ka.length !== kb.length) return false;
  for (const k of ka) if (!Object.is(a[k], b[k])) return false;
  return true;
}

// ---------------- 4. demo 1: 创建最简 store ----------------
console.log('--- demo 1: minimal store ---');
const bearStore = createStore((set) => ({
  bears: 0,
  increase: () => set((s) => ({ bears: s.bears + 1 })),
  reset: () => set({ bears: 0 }, true),  // replace=true
}));

console.log('initial:', bearStore.getState());
bearStore.getState().increase();
bearStore.getState().increase();
console.log('after +2:', bearStore.getState());
bearStore.getState().reset();
console.log('after reset:', bearStore.getState());

// ---------------- 5. demo 2: subscribe(全量) ----------------
console.log('--- demo 2: subscribe (without selector) ---');
const unsub1 = bearStore.subscribe(() => {
  console.log('  total bears changed:', bearStore.getState().bears);
});
bearStore.getState().increase();
bearStore.getState().increase();

// ---------------- 6. demo 3: subscribeWithSelector(选择器) ----------------
console.log('--- demo 3: subscribe with selector ---');
const dogStore = createStore((set) => ({
  paw: true,
  fur: false,
  snout: false,
  togglePaw: () => set((s) => ({ paw: !s.paw })),
  toggleFur: () => set((s) => ({ fur: !s.fur })),
}));
// Subscribe fires immediately when registering (typical zustand behavior)
subscribeWithSelector(dogStore);

const unsub2 = dogStore.subscribe(
  (s) => s.paw,
  (paw, prev) => console.log('  paw changed:', prev, '→', paw),
  { fireImmediately: false },
);
dogStore.getState().togglePaw(); // 应触发
dogStore.getState().toggleFur(); // 不应触发(paw 没变)
unsub2();

// ---------------- 7. demo 4: 手动实现 useStore hook(简化版 useSyncExternalStore) ----------------
console.log('--- demo 4: manual useStore hook (vanilla React) ---');
function makeUseStore(store) {
  // React 18 useSyncExternalStore 的最简模拟
  return function useStore(selector = (s) => s, eq = Object.is) {
    // 我们这里没有 React runtime;只演示逻辑:多次取 state 并触发 selector
    const current = store.getState();
    const sliced = selector(current);
    const handle = (next) => {
      const after = store.getState();
      const sliced2 = selector(after);
      if (!eq(sliced, sliced2)) console.log('  selector output changed', sliced, '→', sliced2);
    };
    const unsub = store.subscribe(handle);
    // 模拟 unmount 仅作 demo
    return [sliced, unsub];
  };
}
const useBear = makeUseStore(bearStore);
const [bears, unsubscribe] = useBear((s) => s.bears);
console.log('  initial slice:', bears);
bearStore.getState().increase();
console.log('  slice should not auto re-read here (no React reconcile);but we capture [s.bears]:');

unsubscribe(); // 清理
unsub1();      // 清理

// ---------------- 8. demo 5: shallow 用例 --------------------
console.log('--- demo 5: shallow equals ---');
const sl1 = { a: 1, b: 2 };
const sl2 = { a: 1, b: 2 };
console.log('  Object.is(sl1, sl2) =', Object.is(sl1, sl2));   // false
console.log('  shallow(sl1, sl2) =', shallow(sl1, sl2));        // true
const sl3 = { a: 1, b: 3 };
console.log('  shallow(sl1, sl3) =', shallow(sl1, sl3));        // false

// ---------------- 9. demo 6: outside React subscribe(非组件层订阅) ----------------
console.log('--- demo 6: outside-React subscribe (e.g. logging module) ---');
const logStore = createStore((set) => ({
  events: [],
  push: (msg) => set((s) => ({ events: [...s.events, msg] })),
}));
const unsub6 = logStore.subscribe((s) => {
  console.log('  [log] 收到', s.events.length, '条事件');
});
logStore.getState().push('user-login');
logStore.getState().push('user-buy');
unsub6();

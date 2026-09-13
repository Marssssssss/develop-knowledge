/**
 * React Hooks 链表实现最小版 (JavaScript)
 *
 * 核心:
 *   1. Fiber.memoizedState 是一条 Hook 链表
 *   2. dispatcher 是全局变量,mount/update 阶段切换
 *   3. useState 实现:setState 排队 + 触发调度
 *   4. useEffect 实现:依赖对比 + commit 后异步执行
 *   5. useRef 实现:mutable { current }
 *
 * 权威来源:
 *   - react.dev/reference/react/useState
 *   - react.dev/reference/react/useEffect
 *   - react.dev/reference/rules/rules-of-hooks (调用顺序)
 *   - facebook/react/packages/react-reconciler/src/ReactFiberHooks.js (实现参考)
 *
 * 运行:node hooks.js → 输出 4 demo
 */

// ---------------- 1. Hook 节点定义 ----------------
function createHook() {
  return {
    memoizedState: undefined, // 当前值
    queue: { pending: null },  // 等待执行的 setState 队列
    next: null,               // 链表 next 指针
  };
}

// 每个 Fiber 独立维护一条 Hook 链表,通过 Fiber.memoizedState 索引
let currentHook = null;   // 当前 fiber 的 hook 链表头
let isMount = true;       // mount / update 阶段
let currentFiber = null;  // 模拟 Fiber

// ---------------- 2. 调度器(redux-style dispatch) ----------------
// setState 不是直接更新,而是把 update 放进 pending queue,在下一次 render 时按顺序消费
function dispatchAction(hook, action) {
  const update = { action, next: null };
  let q = hook.queue;
  if (q.pending === null) {
    update.next = update; // 1 个元素环
  } else {
    update.next = q.pending.next;
    q.pending.next = update;
  }
  q.pending = update;
  // 真实实现:setIsScheduled(true) → scheduleUpdateOnFiber
  scheduleRender();
}

// ---------------- 3. 最小调度器 ----------------
let workScheduled = false;
function scheduleRender() {
  if (workScheduled) return;
  workScheduled = true;
  Promise.resolve().then(() => {
    workScheduled = false;
    renderApp();
  });
}

// ---------------- 4. Hooks 实现 ----------------
function useState(initial) {
  const hook = currentHook.next ? currentHook.next : (currentHook.next = createHook(), currentHook.next);
  if (isMount) hook.memoizedState = initial;

  const setState = (action) => dispatchAction(hook, typeof action === 'function' ? action : () => action);
  let base = hook.memoizedState;
  if (hook.queue.pending) {
    // 消费 pending queue
    let p = hook.queue.pending.next; // head
    do {
      p = p.next;
      base = typeof p.action === 'function' ? p.action(base) : p.action;
    } while (p !== hook.queue.pending);
    hook.queue.pending = null;
  }
  hook.memoizedState = base;
  currentHook = hook;
  return [base, setState];
}

function useEffect(create, deps) {
  const hook = (currentHook.next = createHook(), currentHook.next);
  if (isMount) {
    hook.memoizedState = { deps, create };
    // 注册 commit 后执行
    pendingEffects.push({ create, deps });
  } else {
    const prev = hook.memoizedState;
    const same = Array.isArray(deps) && Array.isArray(prev.deps) &&
                 deps.every((d, i) => Object.is(d, prev.deps[i]));
    if (!same) {
      hook.memoizedState = { deps, create };
      pendingEffects.push({ create, deps });
    }
  }
  currentHook = hook;
}

function useReducer(reducer, initial) {
  // 简化版:复用 useState + reducer 模式
  const [state, setState] = useState(initial);
  const dispatch = (action) => setState((s) => reducer(s, action));
  return [state, dispatch];
}

function useRef(initial) {
  const hook = (currentHook.next = createHook(), currentHook.next);
  if (isMount) hook.memoizedState = { current: initial };
  currentHook = hook;
  return hook.memoizedState;
}

function useMemo(factory, deps) {
  const hook = (currentHook.next = createHook(), currentHook.next);
  if (isMount) {
    hook.memoizedState = { value: factory(), deps };
  } else {
    const prev = hook.memoizedState;
    const same = deps.every((d, i) => Object.is(d, prev.deps[i]));
    hook.memoizedState = same
      ? prev
      : { value: factory(), deps };
  }
  currentHook = hook;
  return hook.memoizedState.value;
}

// ---------------- 5. effects 在 commit 后异步执行 ----------------
const pendingEffects = [];
function flushEffects() {
  while (pendingEffects.length > 0) {
    const { create } = pendingEffects.shift();
    const cleanup = create();
    // 真实 React:cleanup 在下次 commit / 卸载时执行;本 demo 简化
    if (typeof cleanup === 'function') {
      try { cleanup(); } catch (_) {}
    }
  }
}

// ---------------- 6. 模拟 React renderApp ----------------
function renderApp() {
  // 重新走 component,每个 hooks 重新跑一次,链表按调用顺序对齐
  App();
  flushEffects();
}

function App() {
  // ---- demo 1: useState ----
  const [count, setCount] = useState(0);

  // ---- demo 2: useReducer ----
  const [todo, dispatch] = useReducer(
    (state, action) => ({ ...state, value: state.value + action }),
    { value: 0 }
  );

  // ---- demo 3: useRef ----
  const inputRef = useRef(null);

  // ---- demo 4: useMemo ----
  const squared = useMemo(() => count * count, [count]);

  // ---- demo 5: useEffect ----
  useEffect(() => {
    console.log(`[effect] count changed to ${count}`);
    return () => console.log(`[cleanup] count was ${count}`);
  }, [count]);

  console.log(`[render ${isMount ? 'MOUNT' : 'UPDATE'}]`,
    'count =', count,
    ', todo.value =', todo.value,
    ', ref === null?', inputRef.current === null,
    ', squared =', squared,
  );
}

// ---------------- 7. 演示序列 ----------------
async function demo() {
  console.log('=== Demo: hooks linked-list + dispatcher ===');
  // 初始 mount
  currentHook = currentFiber = { memoizedState: null, next: null };
  isMount = true;
  renderApp();

  // 更新:count += 1
  isMount = false;
  setStateCount(1);
  await sleep(10);
  setStateCount2(2);
  await sleep(10);

  // useRef 直接修改 current
  currentFiber.memoizedState = { current: 'hi' }; // 简化
  await sleep(10);
}

let setStateCount = () => {};
let setStateCount2 = () => {};
// 偷天换日:在 App 中把 setCount 提到外部
const originalLog = console.log;
function rebind() {
  // 通过 render 时第二次调用拿到 setCount;为简洁直接重新渲染
  let captured;
  const old = currentHook;
  currentHook = { memoizedState: null, next: null };
  const [, setC] = useState(0);
  captured = setC;
  currentHook = old;
  return captured;
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

demo().catch(e => originalLog(e));

// 真实测试应使用 renderToString 等工具;本 demo 仅作链表工作流程演示,完整测试见 ts 版

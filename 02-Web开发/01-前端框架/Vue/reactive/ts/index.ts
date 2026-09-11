// Vue 3 Proxy 响应式最小实现 (TypeScript)
// 参考：cn.vuejs.org/guide/extras/reactivity-in-depth.html
//   (Vue 官方深入响应式系统指南, 2025)

// ============== 订阅存储 ==============
// 订阅关系存储在全局 WeakMap<target, Map<key, Set<effect>>> 中：
//   第一层：原始对象 -> 第二层 Map
//   第二层：属性名 -> 订阅了该属性的 effect 集合 (Set 自动去重)
const targetMap = new WeakMap<object, Map<string | symbol, Set<ReactiveEffect>>>();

// ============== effect 栈 ==============
// 嵌套 effect 时，需要栈来保证内层 effect 不污染外层。
// 例如：effect A 内调用 effect B，B 内收集的依赖应归 B 而非 A。
const effectStack: ReactiveEffect[] = [];
let activeEffect: ReactiveEffect | null = null;

interface ReactiveEffect {
  fn: () => void;
  deps: Set<ReactiveEffect>[];  // 反向索引：本 effect 被哪些 dep 订阅了，便于 cleanup
  run(): void;
}

// ============== track / trigger ==============
function track(target: object, key: string | symbol): void {
  // 没有 activeEffect 时（比如 reactive 对象在 effect 之外被读）跳过
  if (!activeEffect) return;

  let depsMap = targetMap.get(target);
  if (!depsMap) {
    depsMap = new Map();
    targetMap.set(target, depsMap);
  }

  let deps = depsMap.get(key);
  if (!deps) {
    deps = new Set();
    depsMap.set(key, deps);
  }

  // 双向绑定：dep 知道 effect，effect 也知道 dep（用于 cleanup）
  if (!deps.has(activeEffect)) {
    deps.add(activeEffect);
    activeEffect.deps.push(deps);
  }
}

function trigger(target: object, key: string | symbol): void {
  const depsMap = targetMap.get(target);
  if (!depsMap) return;
  const effects = depsMap.get(key);
  if (!effects) return;

  // 注意：必须拷贝一份再遍历，因为 effect.run() 内的 track 会修改当前 Set
  // （cleanup 在 run 开头执行，会从 deps 中 delete 当前 effect）
  const toRun = new Set(effects);
  toRun.forEach((eff) => eff.run());
}

// ============== cleanup ==============
// effect 重跑前清掉旧的依赖订阅，否则会出现：
//   effect(() => { if (state.flag) sum = state.a + state.b })
//   state.flag = false 后，sum 仍依赖 a/b，state.a 修改仍会无谓触发 effect
function cleanup(effect: ReactiveEffect): void {
  for (const dep of effect.deps) {
    dep.delete(effect);
  }
  effect.deps.length = 0;
}

// ============== effect ==============
function createReactiveEffect(fn: () => void): ReactiveEffect {
  const effect: ReactiveEffect = {
    fn,
    deps: [],
    run() {
      // cleanup 必须在 activeEffect 赋值之前执行
      cleanup(effect);
      effectStack.push(effect);
      activeEffect = effect;
      try {
        return fn();
      } finally {
        effectStack.pop();
        activeEffect = effectStack[effectStack.length - 1] ?? null;
      }
    },
  };
  return effect;
}

// 对外暴露的 effect(fn)：立即执行一次，返回包装后的 runner
export function effect(fn: () => void): ReactiveEffect {
  const e = createReactiveEffect(fn);
  e.run();
  return e;
}

// ============== reactive ==============
// 用 Proxy 拦截 get/set：读时 track，写时 trigger
// 嵌套对象会自动转成 reactive —— 这是 Vue 3 相对于 Vue 2 的关键改进之一
const reactiveCache = new WeakMap<object, object>();

export function reactive<T extends object>(target: T): T {
  // 命中缓存：同一个原始对象多次 reactive 返回同一个 Proxy
  // （Vue 官方实现的核心优化，避免 handler 栈过深）
  const cached = reactiveCache.get(target);
  if (cached) return cached as T;

  const proxy = new Proxy(target, {
    get(obj, key, receiver) {
      // Symbol / 内部 slot 直接走原 Reflect，不参与响应式
      if (typeof key === 'symbol') {
        return Reflect.get(obj, key, receiver);
      }
      const res = Reflect.get(obj, key, receiver);
      // 嵌套对象延迟转为 reactive —— lazy reactive
      // 仅在 get 时才递归，set 时不需要（设进去的值本身就走了 set 的 track/trigger）
      if (res !== null && typeof res === 'object') {
        return reactive(res as object);
      }
      track(obj, key);
      return res;
    },
    set(obj, key, value, receiver) {
      const oldValue = (obj as any)[key];
      const result = Reflect.set(obj, key, value, receiver);
      // 仅当值真正变化才 trigger，避免无意义的依赖触发
      if (oldValue !== value) {
        trigger(obj, key);
      }
      return result;
    },
  });

  reactiveCache.set(target, proxy);
  return proxy as T;
}

// ============== ref ==============
// 因为 Proxy 不能代理原始值（number/string/boolean），ref 用对象 + getter/setter
// 复杂值内部其实就是 reactive(value)
export interface Ref<T> {
  value: T;
}

export function ref<T>(raw: T): Ref<T> {
  const refObject: Ref<T> = {
    get value() {
      track(refObject, 'value');
      // 复杂类型：自动转 reactive（与 Vue 3.5 一致）
      if (typeof raw === 'object' && raw !== null) {
        return reactive(raw as object) as unknown as T;
      }
      return raw;
    },
    set value(newValue: T) {
      if (raw !== newValue) {
        (raw as any) = newValue;
        trigger(refObject, 'value');
      }
    },
  };
  return refObject;
}

// ============== 调试辅助 ==============
// 仅供 demo 用：查看全局订阅关系
export function _debugDumpSubscribers(target: object): void {
  const depsMap = targetMap.get(target);
  if (!depsMap) {
    console.log('[debug] no subscribers for', target);
    return;
  }
  console.log('[debug] subscribers of', target, ':');
  for (const [key, deps] of depsMap) {
    console.log(`  ${String(key)}: ${deps.size} effect(s)`);
  }
}
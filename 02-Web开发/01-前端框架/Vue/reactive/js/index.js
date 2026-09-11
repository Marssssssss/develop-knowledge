// Vue 3 Proxy 响应式最小实现 (JavaScript / ES Modules)
// 参考：cn.vuejs.org/guide/extras/reactivity-in-depth.html
//   (Vue 官方深入响应式系统指南, 2025)
// 与 ts/index.ts 同源；此处剥离类型注解、保留核心逻辑与边界处理。

// ============== 订阅存储 ==============
// 订阅关系存储在全局 WeakMap<target, Map<key, Set<effect>>> 中
const targetMap = new WeakMap();

// ============== effect 栈 ==============
// 嵌套 effect 时，需要栈来保证内层 effect 不污染外层
const effectStack = [];
let activeEffect = null;

// ============== track / trigger ==============
function track(target, key) {
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

function trigger(target, key) {
  const depsMap = targetMap.get(target);
  if (!depsMap) return;
  const effects = depsMap.get(key);
  if (!effects) return;

  // 必须拷贝一份再遍历：cleanup 在 run 开头会修改当前 Set
  const toRun = new Set(effects);
  toRun.forEach((eff) => eff.run());
}

// ============== cleanup ==============
function cleanup(effect) {
  for (const dep of effect.deps) {
    dep.delete(effect);
  }
  effect.deps.length = 0;
}

// ============== effect ==============
function createReactiveEffect(fn) {
  const effect = {
    fn,
    deps: [],
    run() {
      // cleanup 必须在 activeEffect 赋值之前
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

export function effect(fn) {
  const e = createReactiveEffect(fn);
  e.run();
  return e;
}

// ============== reactive ==============
const reactiveCache = new WeakMap();

export function reactive(target) {
  const cached = reactiveCache.get(target);
  if (cached) return cached;

  const proxy = new Proxy(target, {
    get(obj, key, receiver) {
      // Symbol / 内部 slot 不参与响应式
      if (typeof key === 'symbol') {
        return Reflect.get(obj, key, receiver);
      }
      const res = Reflect.get(obj, key, receiver);
      // 嵌套对象延迟转 reactive —— lazy reactive
      if (res !== null && typeof res === 'object') {
        return reactive(res);
      }
      track(obj, key);
      return res;
    },
    set(obj, key, value, receiver) {
      const oldValue = obj[key];
      const result = Reflect.set(obj, key, value, receiver);
      if (oldValue !== value) {
        trigger(obj, key);
      }
      return result;
    },
  });

  reactiveCache.set(target, proxy);
  return proxy;
}

// ============== ref ==============
// 因为 Proxy 不能代理原始值，ref 用对象 + getter/setter
export function ref(raw) {
  const refObject = {
    get value() {
      track(refObject, 'value');
      // 复杂类型自动转 reactive
      if (typeof raw === 'object' && raw !== null) {
        return reactive(raw);
      }
      return raw;
    },
    set value(newValue) {
      if (raw !== newValue) {
        raw = newValue;
        trigger(refObject, 'value');
      }
    },
  };
  return refObject;
}

// ============== 调试辅助 ==============
export function _debugDumpSubscribers(target) {
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
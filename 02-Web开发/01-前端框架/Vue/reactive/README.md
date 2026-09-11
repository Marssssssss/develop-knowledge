# Vue 3 Proxy 响应式最小实现

> 一个去掉所有边界后剩下的 Vue 3 响应式核心 — 用 100 多行代码完整复现 `reactive` / `ref` / `effect` / `track` / `trigger` 机制。

## 简介

Vue 3 的响应式系统是其"低侵入式状态管理"的基石。组件状态由普通 JavaScript 对象组成，当它们变化时，视图自动更新。这个 demo 用最小化代码复现 `@vue/reactivity` 包的核心机制：**Proxy 拦截 → 依赖收集 → 副作用触发**。

### 关键概念

- **响应式 (reactivity)**：使我们可以"声明式处理变化"的编程范式（Vue 官方文档原文）。一个 Excel 表格中的 `A2 = A0 + A1` 即典型例子：`A0/A1` 变化时 `A2` 自动更新。
- **副作用 (effect)**：会改变程序状态的函数（如更新 DOM、修改变量）。当它的依赖变化时，需要重新执行。
- **订阅者 (subscriber)**：订阅了某个响应式数据变化的副作用函数。
- **依赖收集 (track)**：在副作用读取响应式数据时，把"该副作用"登记为"该数据"的订阅者。
- **触发更新 (trigger)**：在响应式数据变化时，找到所有订阅者并重新执行它们。
- **Proxy**：ES6 提供的能力，可在对象属性被访问/修改时插入自定义逻辑，是 Vue 3 取代 Vue 2 `Object.defineProperty` 的关键技术。

### 历史背景

- Vue 2 (2016) 使用 `Object.defineProperty` 的 getter/setter 拦截属性读写，因此必须提前遍历对象所有 key（无法感知新增属性，需要 `Vue.set`），且不支持数组索引变化的拦截。
- Vue 3 (2020) 改用 ES6 **Proxy**，直接代理整个对象：能感知新增/删除属性、能拦截数组下标、`Map`/`Set` 等集合类型也能响应。
- 核心包 `@vue/reactivity` 是独立 npm 包，可脱离 Vue 框架单独使用。

## 原理详解

### 工作机制（5 步）

1. **`reactive(obj)` 创建 Proxy** — 用 `new Proxy(obj, handlers)` 包裹原对象，`handlers` 拦截 get/set/deleteProperty 等操作。
2. **`effect(fn)` 立即执行 fn** — 执行前把 `activeEffect` 设为包装后的函数；执行 fn 时若读了 `reactive` 对象的属性，就会触发 Proxy 的 get trap。
3. **get trap 调用 `track(target, key)`** — 在全局 `targetMap: WeakMap<原始对象, Map<key, Set<effect>>>` 中记录"原始对象 + key → 当前 activeEffect"。
4. **`state[key] = newValue` 触发 Proxy 的 set trap** — 调用 `trigger(target, key)`。
5. **`trigger` 找到所有订阅该 key 的 effect 并执行** — 触发 DOM 更新、computed 重算、watch 回调等。

### 数据结构（订阅关系）

```
targetMap (WeakMap)
├── 原始对象 A ─────────► depsMap (Map)
│                          ├── 'name' ──► Set { effect1, effect3 }
│                          └── 'age'  ──► Set { effect2 }
└── 原始对象 B ─────────► depsMap (Map)
                           └── 'price' ► Set { effect4 }
```

- **WeakMap 键为原始对象**：当原始对象失去引用被 GC 时，整条订阅链自动释放 — 这是 Vue 3 防内存泄漏的关键。
- **Map 值是 Set**：自动去重 — 同一 effect 在同一次执行中多次读同一 key，只登记一次。
- 完整源码片段（Vue 官方伪代码）：

```js
function reactive(obj) {
  return new Proxy(obj, {
    get(target, key) {
      track(target, key)
      return target[key]
    },
    set(target, key, value) {
      target[key] = value
      trigger(target, key)
    }
  })
}
```

### 核心 API

| API | 签名 | 作用 |
| --- | --- | --- |
| `reactive(obj)` | `(target: object) => Proxy` | 返回对象的 Proxy 代理；同一对象多次调用返回同一 Proxy |
| `ref(value)` | `(raw: T) => { value: T }` | 把任意值包装为有 `.value` 的对象；原始值走 getter/setter，对象内部走 reactive |
| `effect(fn)` | `(fn: () => void) => ReactiveEffect` | 创建一个响应式副作用，立即执行一次 |
| `track(target, key)` | 内部函数 | 在 effect 读取时记录订阅关系 |
| `trigger(target, key)` | 内部函数 | 在数据变化时找出所有订阅者并执行 |

### 嵌套 effect 的栈式处理

当 effect 内又调用 effect，内层 effect 的依赖收集必须归内层而不是外层 — 否则闭包外的依赖关系会污染。Vue 用 **栈** 而非单变量：

```js
const effectStack = [];
function effect(fn) {
  const eff = {
    run() {
      effectStack.push(eff);
      activeEffect = eff;
      try { fn(); }
      finally {
        effectStack.pop();
        activeEffect = effectStack[effectStack.length - 1] ?? null;
      }
    }
  };
  eff.run();
}
```

### cleanup：避免遗留订阅

同一个 effect 第二次执行时（比如它依赖的 flag 变了导致分支变化），如果不清掉旧依赖，旧分支里的变量修改仍会触发该 effect — 看起来工作但语义已 stale。

```js
function cleanup(effect) {
  for (const dep of effect.deps) dep.delete(effect);
  effect.deps.length = 0;
}
// effect.run 开头先 cleanup，再设 activeEffect，再执行 fn
```

这是为什么 effect 需要维护"反向索引" `effect.deps: Set<ReactiveEffect>[]`：每个 effect 知道自己被哪些 dep 订阅过，cleanup 时反向删除即可。

## 对比 / 选型

### Vue 2 `Object.defineProperty` vs Vue 3 Proxy

| 维度 | Vue 2 (defineProperty) | Vue 3 (Proxy) |
| --- | --- | --- |
| 拦截粒度 | 单个属性 | 整个对象 |
| 新增属性 | 需 `Vue.set` | 自动响应 |
| 删除属性 | 需 `Vue.delete` | 自动响应 |
| 数组索引 | 需重写数组方法 | 原生支持 |
| Map / Set | 不支持 | 支持 |
| 浏览器要求 | IE9+ (降级) | 不支持 IE11（Proxy 缺失） |
| 性能 | 初始化时遍历所有 key | 懒代理 (lazy reactive) |

### Vue reactivity vs Solid / Angular signal

| 维度 | Vue reactivity | Solid signal | Angular signal |
| --- | --- | --- | --- |
| 暴露形式 | `.value` (ref) 或属性访问 (reactive) | `.value` getter | `.value` getter |
| 订阅粒度 | 属性级 | 值级 | 值级 |
| 嵌套对象 | 自动 reactive (deep) | 手动 `unwrap` | 手动 |
| 写入触发 | Proxy set trap | `.set()` 调用 | `.set()` 调用 |

> Vue 3.5 引入了 `shallowRef` / `customRef` 让用户也能像 signal 一样手动控制订阅时机。

## 环境准备

- **操作系统**：任意（macOS / Linux / Windows）
- **运行时**：Node.js ≥ 20（TS 版需要 `--experimental-strip-types`，Node 22+ 原生支持）
- **依赖**：无第三方依赖；仅需 `typescript`（仅 TS 版编译检查需要）

## 运行方式

### TypeScript 版（Node 22+）

```bash
cd 02-Web开发/01-前端框架/Vue/reactive/ts
npm install --save-dev typescript
# 编译检查（不实际运行）
npx tsc --noEmit demo.ts index.ts
# 用 Node 22+ 直接跑（需 --experimental-strip-types）
node --experimental-strip-types demo.ts
```

### JavaScript 版（任意现代 Node）

```bash
cd 02-Web开发/01-前端框架/Vue/reactive/js
node demo.js
```

## 关键代码片段

### reactive（核心：Proxy + 嵌套懒代理 + 缓存）

```ts
export function reactive<T extends object>(target: T): T {
  const cached = reactiveCache.get(target);   // 同一对象多次 reactive 返回同一 Proxy
  if (cached) return cached as T;

  const proxy = new Proxy(target, {
    get(obj, key, receiver) {
      if (typeof key === 'symbol') return Reflect.get(obj, key, receiver);
      const res = Reflect.get(obj, key, receiver);
      // 嵌套对象 lazy reactive — 只在 get 时才递归
      if (res !== null && typeof res === 'object') return reactive(res as object);
      track(obj, key);                          // 仅原始值走 track
      return res;
    },
    set(obj, key, value, receiver) {
      const oldValue = (obj as any)[key];
      const result = Reflect.set(obj, key, value, receiver);
      if (oldValue !== value) trigger(obj, key);  // 同值不触发
      return result;
    },
  });
  reactiveCache.set(target, proxy);
  return proxy as T;
}
```

### track / trigger（订阅存储 + 拷贝触发）

```ts
function track(target: object, key: string | symbol): void {
  if (!activeEffect) return;
  let depsMap = targetMap.get(target);
  if (!depsMap) { depsMap = new Map(); targetMap.set(target, depsMap); }
  let deps = depsMap.get(key);
  if (!deps) { deps = new Set(); depsMap.set(key, deps); }
  if (!deps.has(activeEffect)) {
    deps.add(activeEffect);
    activeEffect.deps.push(deps);              // 反向索引，cleanup 用
  }
}

function trigger(target: object, key: string | symbol): void {
  const depsMap = targetMap.get(target);
  if (!depsMap) return;
  const effects = depsMap.get(key);
  if (!effects) return;
  const toRun = new Set(effects);              // 必须拷贝：cleanup 会修改 effects
  toRun.forEach((eff) => eff.run());
}
```

### effect（cleanup + 栈式 activeEffect）

```ts
function createReactiveEffect(fn: () => void): ReactiveEffect {
  const effect: ReactiveEffect = {
    fn, deps: [],
    run() {
      cleanup(effect);                         // 关键：先清掉旧订阅
      effectStack.push(effect);
      activeEffect = effect;
      try { return fn(); }
      finally {
        effectStack.pop();
        activeEffect = effectStack[effectStack.length - 1] ?? null;
      }
    },
  };
  return effect;
}
```

### ref（原始值的响应式包装）

```ts
export function ref<T>(raw: T): Ref<T> {
  const refObject: Ref<T> = {
    get value() {
      track(refObject, 'value');
      if (typeof raw === 'object' && raw !== null) {
        return reactive(raw as object) as unknown as T;  // 复杂类型自动 reactive
      }
      return raw;
    },
    set value(newValue: T) {
      if (raw !== newValue) {                   // 同值不触发
        (raw as any) = newValue;
        trigger(refObject, 'value');
      }
    },
  };
  return refObject;
}
```

## 性能与边界

### 时间复杂度

| 操作 | 复杂度 | 说明 |
| --- | --- | --- |
| `reactive(obj)` 首次 | O(1) | 仅建 Proxy，不遍历 |
| `reactive(obj)` 缓存命中 | O(1) | WeakMap 查询 |
| `effect(fn)` 首次 | O(fn) + O(依赖数) | 执行 fn + 收集订阅 |
| `state.x = y` 触发 | O(订阅数) | 遍历订阅 set 执行 |
| 嵌套对象访问 | O(深度) | 每层走一次 Proxy + reactive 调用 |

### 规模边界

- **订阅关系**：单次 effect 重跑理论无上限，但 `trigger` 会同步遍历所有订阅者 — 单 key 上千订阅时可能成为瓶颈（Vue 通过 scheduler 异步化缓解）。
- **嵌套深度**：Proxy 嵌套链深度 ≈ 原对象树深度；过深时调用栈和性能都会受影响（Vue 3 实测推荐 ≤ 10 层）。
- **WeakMap 键的 GC**：原始对象失去所有引用后，整条 depsMap 链会被回收；不能直接持有所需 Proxy 的原始对象否则 GC 不掉。

### 不应做的优化

- 不要在 effect 内修改它依赖的同一属性 — 会陷入无限递归（demo 故意未做防护）。

## 注意事项与常见坑

1. **Symbol key 不参与响应式** — Vue 官方做法，避免污染内置迭代。直接 `Reflect.get` 返回。
2. **同值赋值不触发** — `oldValue !== value` 检查避免无意义的依赖触发；如果业务需要"强制触发"，可手动用 `trigger(target, key)`。
3. **嵌套 reactive 是 lazy 的** — 只在 get 时递归；这意味着 `JSON.parse(JSON.stringify(reactiveObj))` 反序列化后丢响应式，需要再包一层 `reactive`。
4. **effect 必须 cleanup** — 否则条件分支切换时，旧分支里的依赖仍会触发该 effect（demo 5 演示）。
5. **嵌套 effect 必须用栈** — 单变量 activeEffect 会让内层 effect 把依赖登记到外层（demo 6 演示）。
6. **trigger 必须拷贝 Set** — 因为 effect.run 内的 cleanup 会修改原始 Set，直接遍历会跳过当前正在 cleanup 的 effect（也可能重新调度导致死循环）。
7. **IE11 不支持 Proxy** — Vue 3 因此放弃 IE11 支持。需兼容 IE11 时只能用 Vue 2 或单独的 reactive polyfill（不推荐）。
8. **reactive 不应包裹 `Date` / `RegExp` / 函数** — Proxy 拦截这些内置对象的某些方法时可能破坏原始语义；官方 `markRaw()` 可显式跳过。

## 参考资料（实际阅读过的权威来源）

- [Vue.js 官方指南：深入响应式系统](https://cn.vuejs.org/guide/extras/reactivity-in-depth.html) — 提供了 `reactive()` / `ref()` / `track()` / `trigger()` 的伪代码与 `WeakMap<target, Map<key, Set<effect>>>` 数据结构说明，本 demo 的核心算法直接基于此文档的伪代码。
- [Vue.js 官方指南：响应式 API](https://cn.vuejs.org/guide/reactivity-core.html) — `ref` / `reactive` / `computed` / `watch` 等 API 文档，本 demo 复现的就是其中 `ref` + `reactive` + `watchEffect` 的最小子集。
- [MDN: Proxy](https://developer.mozilla.org/zh-CN/docs/Web/JavaScript/Reference/Global_Objects/Proxy) — Proxy 规范与 handler 完整列表；本 demo 仅使用 `get` / `set`，其他 trap（`has` / `ownKeys` / `deleteProperty`）Vue 内部实现时也会用到。
- [MDN: WeakMap](https://developer.mozilla.org/zh-CN/docs/Web/JavaScript/Reference/Global_Objects/WeakMap) — WeakMap 的弱引用语义是 Vue 3 自动 GC 订阅链的关键。
- [Vue 3 源码 packages/reactivity](https://github.com/vuejs/core/tree/main/packages/reactivity) — 真实实现位于 `@vue/reactivity`，本 demo 是其精简版（去掉了 `ReactiveEffect` 类、`scheduler` 调度、`computed`、`readonly`、`shallowRef` 等边界）。
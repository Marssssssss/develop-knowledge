// JSI Sync HostObject Mock — 模拟 React Native New Architecture 的 JSI 同步 C++ 接口
//
// 权威资料(reactnative.dev/architecture/landing-page):
//   "JSI is an interface that allows JavaScript to hold a reference to a C++
//    object and vice-versa. With a memory reference, you can directly invoke
//    methods without serialization costs."
//
// 核心变化对比 legacy_bridge.js:
//   - 同步调用(直接 function call,无 await/then)
//   - 无 JSON 序列化(共享内存引用)
//   - 无消息队列(JS 与 native 同进程,函数调用直达)
//   - 模块懒加载(lazy on first access,不是启动时注入)
//
// 本 demo 用纯 JS 模拟 JSI HostObject 的语义(JS 侧看到的对象),
// 真正的实现是 C++ HostObject 暴露到 JS runtime(Hermes/JSC)。
// 跑法:node js/jsi_sync.js

'use strict';

// ---------- JSI HostObject 模拟 ----------
// 在真实 RN 中,jsi::HostObject 是 C++ 类,JS 通过 runtime.global() 拿到引用。
// 这里用普通 JS 对象模拟其"宿主方法",调用语义是同步的。
class JSIHostObject {
  constructor(name, methods) {
    this._name = name;
    this._methods = methods; // 函数表
  }
  // Property handler — JS 读 .multiply 时,触发这里的方法分发
  // 真实 JSI 中这对应 C++ 的 get(rt, name) 重载
  get(methodName) {
    const fn = this._methods[methodName];
    if (!fn) throw new Error(`${this._name}.${methodName} is not a function`);
    // 返回一个 wrapper,模拟"JS 调用直达 C++" — 同步,无 await
    return (...args) => fn(...args);
  }
}

// ---------- JSI Runtime 模拟 ----------
// 真实 JSI 由 Hermes/JSC 嵌入;这里只展示"global().setProperty()" 把 C++ 对象挂到 JS 全局。
class JSIRuntime {
  constructor() {
    this._globals = {};
    this._lazyRegistry = new Map(); // 模块名 -> 构造方法(懒)
    this._instantiated = new Set();
  }
  // 注册"懒模块":直到第一次 JS 访问才实例化(模拟 TurboModules 懒加载)
  registerLazyModule(name, factory) {
    this._lazyRegistry.set(name, factory);
  }
  // global().setProperty(runtime, "multiply", std::move(hostObj))
  installGlobal(name, hostObj) {
    this._globals[name] = hostObj;
  }
  get global() {
    return this._globals;
  }
  // 检查"模块是否已经实例化"
  isInstantiated(name) {
    return this._instantiated.has(name);
  }
  // 模拟 JS 首次访问 module.measure() 时,RN runtime 才实例化 module
  instantiate(name) {
    if (this._instantiated.has(name)) return;
    const factory = this._lazyRegistry.get(name);
    if (!factory) throw new Error(`No lazy module registered: ${name}`);
    const hostObj = factory();
    this.installGlobal(name, hostObj);
    this._instantiated.add(name);
  }
}

// ---------- 演示场景 ----------
const runtime = new JSIRuntime();

// 注册 3 个懒模块(对应 TurboModule 的懒加载语义)
// 真实 RN 中,TurboModuleRegistry.getEnforcing<Spec>("ModuleName") 才触发实例化
runtime.registerLazyModule('Camera', () => {
  return new JSIHostObject('Camera', {
    measure: () => ({ width: 1080, height: 1920, sync: true }),
    capture: () => ({ bufferId: 42, bytes: 30 * 1024 * 1024 }),
  });
});
runtime.registerLazyModule('Storage', () => {
  return new JSIHostObject('Storage', {
    getString: (key) => `[sync-read]${key}=value_${Date.now() % 1000}`,
  });
});
runtime.registerLazyModule('Sensors', () => {
  return new JSIHostObject('Sensors', {
    readAccelerometer: () => ({ x: 0.01, y: -0.02, z: 9.81, sync: true }),
  });
});

// 启动时,JS 全局上没有任何模块 — 验证"懒"特性
console.log('=== JSI Sync HostObject Demo ===\n');
console.log('[Before any module access]');
console.log('  runtime globals:', Object.keys(runtime.global));
console.log('  instantiated modules:', [...runtime._instantiated]);
console.log('  -> Nothing loaded yet (lazy)');
console.log();

// 场景 1:JS 第一次访问 Camera 模块 → RN runtime 实例化它
runtime.instantiate('Camera');
console.log('[After accessing Camera]');
console.log('  instantiated:', [...runtime._instantiated]);
console.log();

// 场景 2:同步调用 — 无 await / .then,直接拿返回值
console.log('[Sync call — no Promise, no callback, no JSON serialize]');
const layout = runtime.global.Camera.measure(); // 同步!
console.log('  Camera.measure() returned synchronously:', layout);
console.log('  typeof return:', typeof layout, '(not a Promise)');
console.log();

// 场景 3:对比 legacy bridge 的异步 measure 路径
// legacy: 必须 await bridge.call('Camera', 'measure', ...)
// JSI: 直接 const x = runtime.global.Camera.measure();
console.log('[Direct memory reference — like any other JS function call]');
const accel = runtime.global.Sensors.readAccelerometer();
console.log('  Sensors.readAccelerometer():', accel);
console.log('  no JSON serialization, no queue, no thread hop');
console.log();

// 场景 4:TurboModule 懒加载 vs Legacy eager 加载
console.log('[Lazy module loading — TurboModules]');
console.log(`  Camera   instantiated: ${runtime.isInstantiated('Camera')}`);
console.log(`  Storage instantiated: ${runtime.isInstantiated('Storage')}  <- not yet`);
console.log(`  Sensors  instantiated: ${runtime.isInstantiated('Sensors')}`);
// Storage 至今没被访问,所以没实例化
runtime.instantiate('Storage');
console.log(`  After accessing Storage -> ${runtime.isInstantiated('Storage')}`);
console.log();

// 场景 5:对比表(基于权威文档:reactnative.dev/architecture/landing-page)
console.log('[Old bridge vs JSI] (per React Native official docs):');
console.log('  Communication:   JSON serialization on every call  vs  direct C++ bindings');
console.log('  Mode:            async-only, queue-based           vs  synchronous calls possible');
console.log('  Memory:          copies data across threads        vs  shares memory references');
console.log('  Jank source:     queue backpressure on heavy use   vs  eliminated');
console.log('  Module loading:  eager (all at startup)            vs  lazy (on first access)');
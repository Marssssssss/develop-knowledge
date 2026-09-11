// TypeScript version of jsi_sync.js — JSI HostObject 模拟,带类型
// 跑法:node ts/jsi_sync.ts (Node 22+ experimental-strip-types)

interface JSIHostObjectLike {
  get(methodName: string): (...args: unknown[]) => unknown;
}

interface LazyRegistryEntry {
  factory: () => JSIHostObjectLike;
}

class JSIHostObject implements JSIHostObjectLike {
  constructor(
    private readonly name: string,
    private readonly methods: Record<string, (...args: unknown[]) => unknown>,
  ) {}
  get(methodName: string): (...args: unknown[]) => unknown {
    const fn = this.methods[methodName];
    if (!fn) throw new Error(`${this.name}.${methodName} is not a function`);
    return (...args: unknown[]) => fn(...args);
  }
}

class JSIRuntime {
  private _globals: Record<string, JSIHostObjectLike> = {};
  private _lazyRegistry: Map<string, LazyRegistryEntry> = new Map();
  private _instantiated: Set<string> = new Set();

  registerLazyModule(name: string, factory: () => JSIHostObjectLike): void {
    this._lazyRegistry.set(name, { factory });
  }

  installGlobal(name: string, hostObj: JSIHostObjectLike): void {
    this._globals[name] = hostObj;
  }

  get global(): Record<string, JSIHostObjectLike> {
    return this._globals;
  }

  isInstantiated(name: string): boolean {
    return this._instantiated.has(name);
  }

  instantiate(name: string): void {
    if (this._instantiated.has(name)) return;
    const entry = this._lazyRegistry.get(name);
    if (!entry) throw new Error(`No lazy module registered: ${name}`);
    this.installGlobal(name, entry.factory());
    this._instantiated.add(name);
  }
}

// ---------- demo ----------
const runtime = new JSIRuntime();

runtime.registerLazyModule('Camera', () =>
  new JSIHostObject('Camera', {
    measure: () => ({ width: 1080, height: 1920, sync: true }),
    capture: () => ({ bufferId: 42, bytes: 30 * 1024 * 1024 }),
  }),
);
runtime.registerLazyModule('Storage', () =>
  new JSIHostObject('Storage', {
    getString: (key: string) => `[sync-read]${key}=value_${Date.now() % 1000}`,
  }),
);
runtime.registerLazyModule('Sensors', () =>
  new JSIHostObject('Sensors', {
    readAccelerometer: () => ({ x: 0.01, y: -0.02, z: 9.81, sync: true }),
  }),
);

console.log('=== JSI Sync HostObject Demo (TypeScript) ===\n');
console.log('[Before any module access]');
console.log('  globals:', Object.keys(runtime.global));
console.log('  instantiated:', [...(runtime as unknown as { _instantiated: Set<string> })._instantiated]);
console.log();

runtime.instantiate('Camera');
console.log('[After accessing Camera] instantiated:', runtime.isInstantiated('Camera'));
console.log();

console.log('[Sync call — no Promise, no JSON]');
const layout: { width: number; height: number; sync: boolean } =
  // @ts-expect-error — JSI in reality returns typed C++ struct; here we cast
  runtime.global.Camera.measure();
console.log('  Camera.measure() ->', layout);
console.log('  typeof:', typeof layout);
console.log();

console.log('[Lazy loading — TurboModules]');
console.log(`  Camera   : ${runtime.isInstantiated('Camera')}`);
console.log(`  Storage  : ${runtime.isInstantiated('Storage')}  <- not yet`);
console.log(`  Sensors  : ${runtime.isInstantiated('Sensors')}`);
runtime.instantiate('Storage');
console.log(`  After instantiate Storage -> ${runtime.isInstantiated('Storage')}`);
console.log();

console.log('[Old bridge vs JSI] (per React Native official docs):');
console.log('  Communication:   JSON serialize on every call  vs  direct C++ bindings');
console.log('  Mode:            async-only queue-based        vs  synchronous calls possible');
console.log('  Memory:          copies across threads          vs  shares memory references');
console.log('  Module loading:  eager (all at startup)         vs  lazy (on first access)');
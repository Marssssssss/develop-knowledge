// TypeScript version of legacy_bridge.js — 同样的逻辑,加上 type annotations
// 跑法:node ts/legacy_bridge.ts (需要 Node 22+ experimental-strip-types)

// 也可以 npx tsx ts/legacy_bridge.ts (若安装 tsx)

interface CallLogEntry {
  module: string;
  method: string;
  argsBytes: number;
  resultBytes: number;
  queueWaitMs: number;
  sync: boolean;
}

interface BridgeStats {
  totalCalls: number;
  totalSerializationBytes: number;
  avgQueueWaitMs: number;
  asyncOnly: boolean;
  modulesEager: boolean;
}

type Callback = (err: Error | null, result?: unknown) => void;

class LegacyBridge {
  private nativeModules: Record<string, Record<string, (...args: unknown[]) => unknown>> = {};
  private callLog: CallLogEntry[] = [];

  registerModule(name: string, methods: Record<string, (...args: unknown[]) => unknown>): void {
    this.nativeModules[name] = methods;
  }

  call(moduleName: string, methodName: string, args: unknown[], callback?: Callback): Promise<unknown> {
    const start = Date.now();
    const argsJson = JSON.stringify(args);

    return new Promise((resolve, reject) => {
      setImmediate(() => {
        try {
          const fn = this.nativeModules[moduleName]?.[methodName];
          if (!fn) throw new Error(`No such module/method: ${moduleName}.${methodName}`);

          const result = fn(...args);
          const resultJson = JSON.stringify(result);
          const waited = Date.now() - start;

          this.callLog.push({
            module: moduleName,
            method: methodName,
            argsBytes: argsJson.length,
            resultBytes: resultJson.length,
            queueWaitMs: waited,
            sync: false,
          });

          if (callback) callback(null, result);
          resolve(result);
        } catch (err) {
          if (callback) callback(err as Error);
          reject(err);
        }
      });
    });
  }

  stats(): BridgeStats {
    const totalBytes = this.callLog.reduce((a, l) => a + l.argsBytes + l.resultBytes, 0);
    return {
      totalCalls: this.callLog.length,
      totalSerializationBytes: totalBytes,
      avgQueueWaitMs: this.callLog.reduce((a, l) => a + l.queueWaitMs, 0) / Math.max(1, this.callLog.length),
      asyncOnly: true,
      modulesEager: true,
    };
  }
}

// ---------- demo ----------
const bridge = new LegacyBridge();
['Camera', 'Storage', 'Network', 'Location', 'Sensors'].forEach((name) => {
  bridge.registerModule(name, {
    getInfo: (id: number) => ({ module: name, id, ts: Date.now() }),
  });
});

(async () => {
  console.log('=== Legacy Bridge Demo (TypeScript) ===\n');

  const N = 1000;
  const t0 = Date.now();
  for (let i = 0; i < N; i++) {
    const mod = ['Camera', 'Storage', 'Network', 'Location', 'Sensors'][i % 5];
    await bridge.call(mod, 'getInfo', [{ id: i, payload: 'x'.repeat(100) }]);
  }
  const totalMs = Date.now() - t0;
  const s = bridge.stats();
  console.log(`[Scenario 1] ${N} calls across 5 modules:`);
  console.log(`  total time:           ${totalMs} ms`);
  console.log(`  total JSON bytes:     ${s.totalSerializationBytes.toLocaleString()} B`);
  console.log(`  avg queue wait:       ${s.avgQueueWaitMs.toFixed(2)} ms/call`);
  console.log(`  mode:                 ${s.asyncOnly ? 'async-only' : 'sync-capable'}`);
  console.log(`  module loading:       ${s.modulesEager ? 'all eager' : 'lazy'}`);

  // 场景 2:证明 sync 调用不可能
  bridge.call('Camera', 'getInfo', [{ query: 'measure' }]).then((r) => {
    console.log(`\n[Scenario 2] async-only:`, JSON.stringify(r));
    console.log('  -> no way to synchronously read layout in legacy bridge');
  });

  await new Promise((r) => setTimeout(r, 50));
  console.log('\nLegacy bridge limitations (reactnative.dev/architecture/landing-page):');
  console.log('  - async-only / JSON serialize / single queue / eager modules / no type safety');
})();
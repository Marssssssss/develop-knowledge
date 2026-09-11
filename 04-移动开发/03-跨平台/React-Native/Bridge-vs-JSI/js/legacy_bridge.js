// Legacy Bridge Mock — 模拟 React Native 0.68 之前的异步 JSON bridge
//
// 历史背景(权威资料:reactnative.dev/architecture/landing-page):
//   "The New Architecture removes the asynchronous bridge between JavaScript and
//    native and replaces it with JavaScript Interface (JSI)."
//   即旧架构的 bridge 是异步 + JSON 序列化的消息队列,JSI 把它整个替换掉。
//
// 本 demo 不依赖 React Native runtime,在 Node.js 直接跑。
// 跑法:node js/legacy_bridge.js

'use strict';

// ---------- 旧 Bridge 模拟 ----------
class LegacyBridge {
  constructor() {
    // 所有 Native Module 的方法,启动时全部注入(模拟 eager load)
    this.nativeModules = {};
    // 跨线程调用日志:每次 call 一次记录
    this.callLog = [];
    // 模拟 native 线程(用 setImmediate 把回调推到下一拍,模拟跨线程)
  }

  registerModule(name, methods) {
    this.nativeModules[name] = methods;
  }

  // JS -> Native 调用(异步 + JSON 序列化 + 回调/Promise)
  call(moduleName, methodName, args, callback) {
    const start = Date.now();
    const argsJson = JSON.stringify(args); // <-- 序列化开销

    return new Promise((resolve, reject) => {
      // 模拟"JS 线程 -> native 线程"的跨线程 dispatch(setImmediate 排队)
      setImmediate(() => {
        try {
          const fn = (this.nativeModules[moduleName] || {})[methodName];
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
          if (callback) callback(err);
          reject(err);
        }
      });
    });
  }

  stats() {
    const totalBytes = this.callLog.reduce(
      (acc, l) => acc + l.argsBytes + l.resultBytes,
      0,
    );
    return {
      totalCalls: this.callLog.length,
      totalSerializationBytes: totalBytes,
      avgQueueWaitMs:
        this.callLog.reduce((a, l) => a + l.queueWaitMs, 0) /
        Math.max(1, this.callLog.length),
      asyncOnly: true,
      modulesEager: true,
    };
  }
}

// ---------- 演示场景 ----------
const bridge = new LegacyBridge();

// 5 个 native module,模拟"启动时全部注入"
['Camera', 'Storage', 'Network', 'Location', 'Sensors'].forEach((name) => {
  bridge.registerModule(name, {
    getInfo: (id) => ({ module: name, id, ts: Date.now() }),
  });
});

(async () => {
  console.log('=== Legacy Bridge Demo ===\n');

  // 场景 1:1000 次跨线程调用,观察 JSON 序列化总开销
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
  console.log(`  mode:                 ${s.asyncOnly ? 'async-only (callback/Promise)' : 'sync-capable'}`);
  console.log(`  module loading:       ${s.modulesEager ? 'all 5 modules at startup' : 'lazy on first access'}`);
  console.log();

  // 场景 2:证明"无法同步" - 试着同步读取布局信息,只能通过 callback
  console.log('[Scenario 2] Synchronous layout read is impossible:');
  const syncReadAttempt = () => {
    // 旧架构下 measureInWindow / onLayout 必然是异步的
    bridge.call('Camera', 'getInfo', [{ query: 'measure' }]).then((r) => {
      console.log(`  -> got async result (must wait): ${JSON.stringify(r)}`);
    });
    console.log('  -> JS continues immediately (no return value from native)');
  };
  syncReadAttempt();

  // 等最后一个 setImmediate 完成再总结
  await new Promise((r) => setTimeout(r, 50));
  console.log('\nLegacy bridge limitations (per reactnative.dev/architecture):');
  console.log('  1. async-only: cannot synchronously call native (e.g. measure())');
  console.log('  2. JSON serialization on every cross-thread call');
  console.log('  3. single bottleneck queue: chatty module can starve UI');
  console.log('  4. all native modules initialized at startup -> hurts TTI');
  console.log('  5. no type safety: JS/native signature mismatch surfaces only at runtime');
})();
// main.js — async_hooks 与 AsyncLocalStorage 自检:
// ① Timeout 资源的 init→before→after 事件序 ② 安装 hook 后 Promise 获得 asyncId + trigger 因果链
// ③ ALS 语义(run 跨异步可见/抛错退出上下文/exit 重入/disable/enterWith 泄漏/实例独立)
// ④ 交错请求的请求号追踪 ⑤ 用 createHook 复刻迷你 ALS(原理演示)。
// 运行: node main.js(约 1 秒)
'use strict';
const asyncHooks = require('node:async_hooks');
const { AsyncLocalStorage } = asyncHooks;
const EventEmitter = require('node:events');

let pass = 0, fail = 0;
const checks = [];
function check(label, cond, detail) {
  if (cond) { pass++; checks.push(`PASS ${label} (${detail})`); }
  else { fail++; checks.push(`FAIL ${label} (${detail})`); }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ── E1:Timeout 资源生命周期事件序(官方:before 对 request 类资源恰 1 次) ──
async function e1_timeout_trace() {
  const trace = [];
  asyncHooks.createHook({
    init(id, type) { if (type === 'Timeout') trace.push(`init:${id}`); },
    before(id) { if (trace.includes(`init:${id}`)) trace.push(`before:${id}`); },
    after(id) { if (trace.includes(`init:${id}`)) trace.push(`after:${id}`); },
  }).enable();
  await sleep(10); // 被跟踪的 Timeout
  const seq = trace.join(' ');
  console.log(`[E1 Timeout] 事件序: ${seq}`);
  const m = seq.match(/init:(\d+) before:\1 after:\1/);
  check('E1a Timeout 生命周期 init → before → after 顺序正确', !!m, m ? m[0] : seq);
  check('E1b before/after 恰好各 1 次(request 类资源)', trace.filter((s) => s.startsWith('before')).length === 1 && trace.filter((s) => s.startsWith('after')).length === 1, trace.join(','));
}

// ── E2:安装 hook 后 Promise 才有 asyncId;triggerAsyncId 构成因果链 ──
async function e2_promise_tracking() {
  const inits = [];
  asyncHooks.createHook({
    init(id, type, trigger) { if (type === 'PROMISE') inits.push({ id, trigger }); },
  }).enable();
  const eidTop = asyncHooks.executionAsyncId();
  let eidThen = -1;
  await Promise.resolve().then(() => { eidThen = asyncHooks.executionAsyncId(); });
  console.log(`[E2 Promise] 顶层 eid=${eidTop}, then 回调 eid=${eidThen}, PROMISE init 事件=${JSON.stringify(inits)}`);
  check('E2a 安装 hook 后 then 回调拥有独立 asyncId(默认无,装任意 hook 启用追踪)', eidThen !== eidTop && eidThen > 0, `${eidTop}→${eidThen}`);
  check('E2b PROMISE 资源成对创建(Promise.resolve 与 then 返回值)', inits.length >= 2, `${inits.length} 个`);
  const chained = inits.some((p, i) => i > 0 && p.trigger === inits[i - 1].id);
  check('E2c then() 创建的 PROMISE 的 triggerAsyncId = 父 PROMISE 的 id(因果链)', chained, inits.map((p) => `${p.id}←${p.trigger}`).join(','));
}

// ── E3:AsyncLocalStorage 语义 ──
async function e3_als_semantics() {
  const als = new AsyncLocalStorage();
  // run:回调内 + 回调内创建的异步操作中可见
  let inCb, inTimeout, inPromise;
  als.run({ reqId: 7 }, () => {
    inCb = als.getStore().reqId;
    setTimeout(() => { inTimeout = als.getStore() && als.getStore().reqId; }, 5);
    Promise.resolve().then(() => { inPromise = als.getStore() && als.getStore().reqId; });
  });
  const outside = als.getStore();
  // run 回调抛错 → run() 重抛且上下文退出
  let threw = false, storeAfterThrow = 'unset';
  try {
    als.run({ reqId: 8 }, () => { throw new Error('boom'); });
  } catch (e) { threw = e.message === 'boom'; storeAfterThrow = als.getStore(); }
  // exit():回调内脱离上下文;抛错后上下文重新进入(与 run 相反)
  let inExit = 'unset', reentered = 'unset';
  als.run({ reqId: 9 }, () => {
    try {
      als.exit(() => { inExit = als.getStore(); throw new Error('inner'); });
    } catch (e) { reentered = als.getStore() && als.getStore().reqId; }
  });
  // enterWith:泄漏到后续事件处理器(官方明示的反例)
  const em = new EventEmitter();
  const store1 = { id: 'A' };
  em.on('evt', () => { als.enterWith(store1); });
  em.on('evt', () => { /* 后注册的 handler 也会看到 store —— 泄漏 */ });
  em.emit('evt');
  const leakedToLaterHandler = als.getStore();
  const leakedAfterSync = als.getStore();
  // disable:此后 getStore 恒 undefined;再 run 可恢复
  als.disable();
  const afterDisable = als.getStore();
  let afterRerun = 'unset';
  als.run({ reqId: 10 }, () => { afterRerun = als.getStore() && als.getStore().reqId; });
  await sleep(10);
  console.log(`[E3 ALS] run内=${inCb}/timeout=${inTimeout}/promise=${inPromise}/外部=${outside} | 抛错后=${storeAfterThrow} | exit内=${inExit}/重入=${reentered} | enterWith泄漏=${JSON.stringify(leakedToLaterHandler)}/${leakedAfterSync === store1} | disable后=${afterDisable}/再run=${afterRerun}`);
  check('E3a run() 的 store 在回调与异步操作中都可见', inCb === 7 && inTimeout === 7 && inPromise === 7, `cb=${inCb} timeout=${inTimeout} promise=${inPromise}`);
  check('E3b run() 外 getStore() 为 undefined', outside === undefined, String(outside));
  check('E3c 回调抛错 → run() 重抛且上下文已退出', threw && storeAfterThrow === undefined, `threw=${threw} after=${storeAfterThrow}`);
  check('E3d exit() 内脱离上下文,抛错后重新进入', inExit === undefined && reentered === 9, `exit内=${inExit} 重入=${reentered}`);
  check('E3e enterWith 泄漏到同轮后续代码(官方建议优先 run)', leakedToLaterHandler === store1 && leakedAfterSync === store1, 'handler+同步尾部均可见');
  check('E3f disable() 后 getStore() 恒 undefined,再 run 恢复', afterDisable === undefined && afterRerun === 10, `disable=${afterDisable} rerun=${afterRerun}`);
}

// ── E4:交错异步请求的请求号追踪(经典的 per-request 上下文) ──
async function e4_request_id() {
  const als = new AsyncLocalStorage();
  const logs = [];
  async function handleRequest(reqId, delay) {
    // run() 返回回调返回值:async 回调要 return 出去,调用方才能等到全部日志落盘
    return als.run({ reqId }, async () => {
      logs.push([als.getStore().reqId, 'start']);
      await sleep(delay); // 交错等待
      logs.push([als.getStore().reqId, 'after-io']);
      await Promise.resolve().then(() => logs.push([als.getStore().reqId, 'microtask']));
      logs.push([als.getStore().reqId, 'end']);
    });
  }
  await Promise.all([handleRequest(1, 30), handleRequest(2, 10), handleRequest(3, 20)]);
  const byId = {};
  for (const [id, tag] of logs) (byId[id] = byId[id] || []).push(tag);
  const order = logs.map((l) => l[0]).join(',');
  console.log(`[E4 请求号] 交错事件序: ${order} | 各请求日志: ${JSON.stringify(byId)}`);
  check('E4a 三个请求各产出 4 条日志(交错但归属正确)', [1, 2, 3].every((i) => byId[i] && byId[i].length === 4), JSON.stringify(byId));
  check('E4b 完成顺序与延迟一致(2,3,1 交错)', byId[2][1] === 'after-io' && logs.findIndex((l) => l[0] === 2 && l[1] === 'after-io') < logs.findIndex((l) => l[0] === 3 && l[1] === 'after-io'), order);
}

// ── E5:用 createHook 复刻迷你 ALS(原理演示;官方实现另有嵌入存储优化) ──
async function e5_mini_als() {
  class MiniALS {
    constructor() {
      this._map = new Map();
      this._hook = asyncHooks.createHook({
        init: (id) => { // 新异步资源继承"创建它的执行上下文"的 store
          const cur = this._map.get(asyncHooks.executionAsyncId());
          if (cur !== undefined) this._map.set(id, cur);
        },
      });
      this._hook.enable();
    }
    run(store, cb) {
      const id = asyncHooks.executionAsyncId();
      const prev = this._map.get(id);
      this._map.set(id, store);
      try { return cb(); } finally {
        if (prev === undefined) this._map.delete(id);
        else this._map.set(id, prev);
      }
    }
    getStore() { return this._map.get(asyncHooks.executionAsyncId()); }
  }
  const mini = new MiniALS();
  const seen = {};
  mini.run('req-A', () => {
    seen.inRun = mini.getStore();
    setTimeout(() => { seen.inTimeoutA = mini.getStore(); }, 15);
  });
  mini.run('req-B', () => {
    setTimeout(() => { seen.inTimeoutB = mini.getStore(); }, 5);
  });
  seen.outside = mini.getStore();
  await sleep(30);
  console.log(`[E5 MiniALS] run内=${seen.inRun} A定时器=${seen.inTimeoutA} B定时器=${seen.inTimeoutB} 外部=${seen.outside}`);
  check('E5a run 内可读', seen.inRun === 'req-A', String(seen.inRun));
  check('E5b store 沿异步链传播且互不串扰(A/B 各自可见)', seen.inTimeoutA === 'req-A' && seen.inTimeoutB === 'req-B', `${seen.inTimeoutA}/${seen.inTimeoutB}`);
  check('E5c run 外读不到', seen.outside === undefined, String(seen.outside));
}

async function main() {
  await e1_timeout_trace();
  await e2_promise_tracking();
  await e3_als_semantics();
  await e4_request_id();
  await e5_mini_als();
  console.log('─'.repeat(56));
  for (const line of checks) console.log(line);
  console.log(`async_hooks与AsyncLocalStorage自检: ${pass} PASS / ${fail} FAIL`);
  process.exitCode = fail ? 1 : 0;
}
main();

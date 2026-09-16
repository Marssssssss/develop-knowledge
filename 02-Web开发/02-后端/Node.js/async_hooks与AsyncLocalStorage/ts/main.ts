// main.ts — async_hooks 与 AsyncLocalStorage 自检(main.js 的 TypeScript 移植)。
// 运行: node --experimental-strip-types main.ts
'use strict';
import * as asyncHooks from 'node:async_hooks';
import { AsyncLocalStorage } from 'node:async_hooks';
import { EventEmitter } from 'node:events';

let pass = 0;
let fail = 0;
const checks: string[] = [];
function check(label: string, cond: boolean, detail: string): void {
  if (cond) { pass++; checks.push(`PASS ${label} (${detail})`); }
  else { fail++; checks.push(`FAIL ${label} (${detail})`); }
}
const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

interface ReqStore { reqId: number }

async function e1TimeoutTrace(): Promise<void> {
  const trace: string[] = [];
  asyncHooks.createHook({
    init(id: number, type: string) { if (type === 'Timeout') trace.push(`init:${id}`); },
    before(id: number) { if (trace.includes(`init:${id}`)) trace.push(`before:${id}`); },
    after(id: number) { if (trace.includes(`init:${id}`)) trace.push(`after:${id}`); },
  }).enable();
  await sleep(10);
  const seq = trace.join(' ');
  console.log(`[E1 Timeout] 事件序: ${seq}`);
  const m = seq.match(/init:(\d+) before:\1 after:\1/);
  check('E1a Timeout 生命周期 init → before → after 顺序正确', m !== null, m ? m[0] : seq);
  check('E1b before/after 恰好各 1 次(request 类资源)', trace.filter((s) => s.startsWith('before')).length === 1 && trace.filter((s) => s.startsWith('after')).length === 1, trace.join(','));
}

async function e2PromiseTracking(): Promise<void> {
  const inits: Array<{ id: number; trigger: number }> = [];
  asyncHooks.createHook({
    init(id: number, type: string, trigger: number) { if (type === 'PROMISE') inits.push({ id, trigger }); },
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

async function e3AlsSemantics(): Promise<void> {
  const als = new AsyncLocalStorage<ReqStore>();
  let inCb: number | undefined;
  let inTimeout: number | undefined;
  let inPromise: number | undefined;
  als.run({ reqId: 7 }, () => {
    inCb = als.getStore()!.reqId;
    setTimeout(() => { inTimeout = als.getStore()?.reqId; }, 5);
    Promise.resolve().then(() => { inPromise = als.getStore()?.reqId; });
  });
  const outside = als.getStore();
  let threw = false;
  let storeAfterThrow: ReqStore | undefined = 'unset' as unknown as ReqStore | undefined;
  try {
    als.run({ reqId: 8 }, () => { throw new Error('boom'); });
  } catch (e) { threw = (e as Error).message === 'boom'; storeAfterThrow = als.getStore(); }
  let inExit: ReqStore | undefined = 'unset' as unknown as ReqStore | undefined;
  let reentered: number | undefined;
  als.run({ reqId: 9 }, () => {
    try {
      als.exit(() => { inExit = als.getStore(); throw new Error('inner'); });
    } catch { reentered = als.getStore()?.reqId; }
  });
  const em = new EventEmitter();
  const store1 = { id: 'A' };
  em.on('evt', () => { als.enterWith(store1 as unknown as ReqStore); });
  em.on('evt', () => { /* 后注册的 handler 也会看到 store —— 泄漏 */ });
  em.emit('evt');
  const leakedToLaterHandler = als.getStore();
  const leakedAfterSync = als.getStore();
  als.disable();
  const afterDisable = als.getStore();
  let afterRerun: number | undefined;
  als.run({ reqId: 10 }, () => { afterRerun = als.getStore()?.reqId; });
  await sleep(10);
  console.log(`[E3 ALS] run内=${inCb}/timeout=${inTimeout}/promise=${inPromise}/外部=${outside} | 抛错后=${storeAfterThrow} | exit内=${inExit}/重入=${reentered} | enterWith泄漏=${JSON.stringify(leakedToLaterHandler)}/${leakedAfterSync === store1} | disable后=${afterDisable}/再run=${afterRerun}`);
  check('E3a run() 的 store 在回调与异步操作中都可见', inCb === 7 && inTimeout === 7 && inPromise === 7, `cb=${inCb} timeout=${inTimeout} promise=${inPromise}`);
  check('E3b run() 外 getStore() 为 undefined', outside === undefined, String(outside));
  check('E3c 回调抛错 → run() 重抛且上下文已退出', threw && storeAfterThrow === undefined, `threw=${threw} after=${storeAfterThrow}`);
  check('E3d exit() 内脱离上下文,抛错后重新进入', inExit === undefined && reentered === 9, `exit内=${inExit} 重入=${reentered}`);
  check('E3e enterWith 泄漏到同轮后续代码(官方建议优先 run)', leakedToLaterHandler === store1 && leakedAfterSync === store1, 'handler+同步尾部均可见');
  check('E3f disable() 后 getStore() 恒 undefined,再 run 恢复', afterDisable === undefined && afterRerun === 10, `disable=${afterDisable} rerun=${afterRerun}`);
}

async function e4RequestId(): Promise<void> {
  const als = new AsyncLocalStorage<ReqStore>();
  const logs: Array<[number, string]> = [];
  async function handleRequest(reqId: number, delay: number): Promise<void> {
    return als.run({ reqId }, async () => {
      logs.push([als.getStore()!.reqId, 'start']);
      await sleep(delay);
      logs.push([als.getStore()!.reqId, 'after-io']);
      await Promise.resolve().then(() => logs.push([als.getStore()!.reqId, 'microtask']));
      logs.push([als.getStore()!.reqId, 'end']);
    });
  }
  await Promise.all([handleRequest(1, 30), handleRequest(2, 10), handleRequest(3, 20)]);
  const byId: Record<number, string[]> = {};
  for (const [id, tag] of logs) (byId[id] = byId[id] || []).push(tag);
  const order = logs.map((l) => l[0]).join(',');
  console.log(`[E4 请求号] 交错事件序: ${order} | 各请求日志: ${JSON.stringify(byId)}`);
  check('E4a 三个请求各产出 4 条日志(交错但归属正确)', [1, 2, 3].every((i) => byId[i] && byId[i].length === 4), JSON.stringify(byId));
  check('E4b 完成顺序与延迟一致(2,3,1 交错)', byId[2][1] === 'after-io' && logs.findIndex((l) => l[0] === 2 && l[1] === 'after-io') < logs.findIndex((l) => l[0] === 3 && l[1] === 'after-io'), order);
}

async function e5MiniAls(): Promise<void> {
  class MiniALS {
    private _map = new Map<number, unknown>();
    private _hook = asyncHooks.createHook({
      init: (id: number): void => {
        const cur = this._map.get(asyncHooks.executionAsyncId());
        if (cur !== undefined) this._map.set(id, cur);
      },
    });
    constructor() { this._hook.enable(); }
    run<T>(store: unknown, cb: () => T): T {
      const id = asyncHooks.executionAsyncId();
      const prev = this._map.get(id);
      this._map.set(id, store);
      try { return cb(); } finally {
        if (prev === undefined) this._map.delete(id);
        else this._map.set(id, prev);
      }
    }
    getStore(): unknown { return this._map.get(asyncHooks.executionAsyncId()); }
  }
  const mini = new MiniALS();
  const seen: Record<string, unknown> = {};
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

async function main(): Promise<void> {
  await e1TimeoutTrace();
  await e2PromiseTracking();
  await e3AlsSemantics();
  await e4RequestId();
  await e5MiniAls();
  console.log('─'.repeat(56));
  for (const line of checks) console.log(line);
  console.log(`async_hooks与AsyncLocalStorage自检: ${pass} PASS / ${fail} FAIL`);
  process.exitCode = fail ? 1 : 0;
}
main();

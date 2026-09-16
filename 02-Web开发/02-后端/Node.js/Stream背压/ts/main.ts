// main.ts — Stream 背压机制自检(main.js 的 TypeScript 移植)。
// 运行: node --experimental-strip-types main.ts
'use strict';
import { Writable, Readable } from 'node:stream';

let pass = 0;
let fail = 0;
const checks: string[] = [];
function check(label: string, cond: boolean, detail: string): void {
  if (cond) { pass++; checks.push(`PASS ${label} (${detail})`); }
  else { fail++; checks.push(`FAIL ${label} (${detail})`); }
}
const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

interface CountingWritable extends Writable {
  __received(): number;
}

function slowWritable(delayMs: number, opts: WritableOptions): CountingWritable {
  let received = 0;
  const w = new Writable({
    ...opts,
    write(chunk: unknown, enc: string, cb: (err?: Error | null) => void): void {
      received++;
      setTimeout(cb, delayMs);
    },
  }) as CountingWritable;
  w.__received = () => received;
  return w;
}

async function e1WriteDrain(): Promise<void> {
  const w = slowWritable(10, { objectMode: true, highWaterMark: 4 });
  const returns: boolean[] = [];
  for (let i = 0; i < 10; i++) returns.push(w.write({ i }));
  const drainCount = await new Promise<number>((res) => {
    let n = 0;
    w.on('drain', () => { n++; if (w.__received() === 10) res(n); });
  });
  console.log(`[E1 write/drain] 返回值序列(10 次写入): ${returns.map(Number).join('')}  drain 次数: ${drainCount}`);
  check('E1a 前 3 次写入返回 true(缓冲 1..3 < hwm=4)', returns.slice(0, 3).every(Boolean), '111');
  check('E1b 第 4 次起返回 false(缓冲 ≥ hwm)', !returns.slice(3).some(Boolean), '0000000');
  check('E1c 缓冲清空后恰好 1 次 drain', drainCount === 1, `drain=${drainCount}`);
}

async function e2BoundedVsUnbounded(): Promise<void> {
  const wa = slowWritable(1, { objectMode: true, highWaterMark: 4 });
  let maxLenA = 0;
  for (let i = 0; i < 200; i++) {
    wa.write({ i });
    maxLenA = Math.max(maxLenA, wa.writableLength);
  }
  const wb = slowWritable(1, { objectMode: true, highWaterMark: 4 });
  let maxLenB = 0;
  await new Promise<void>((res) => {
    let i = 0;
    const next = (): void => {
      while (i < 200) {
        maxLenB = Math.max(maxLenB, wb.writableLength);
        const ok = wb.write({ i: i++ });
        if (!ok) { wb.once('drain', next); return; }
      }
      wb.end(res);
    };
    next();
  });
  await Promise.all([new Promise<void>((r) => wa.end(r)), sleep(0)]);
  console.log(`[E2 背压对比] 无背压: 缓冲峰值 ${maxLenA} | 有背压: 缓冲峰值 ${maxLenB}(hwm=4)`);
  check('E2a 无背压时缓冲峰值远超 hwm(内存隐患)', maxLenA >= 100, `peak=${maxLenA}/200`);
  check('E2b 有背压时缓冲峰值 ≤ hwm+1', maxLenB <= 5, `peak=${maxLenB}`);
  check('E2c 两种方式数据完整送达 200 条', wa.__received() === 200 && wb.__received() === 200, `${wa.__received()}/${wb.__received()}`);
}

async function e3PipeAutoPressure(): Promise<void> {
  const SOURCE_N = 100;
  let produced = 0;
  const r = new Readable({
    objectMode: true,
    highWaterMark: 4,
    read(): void {
      if (produced < SOURCE_N) this.push({ i: produced++ });
      else if (produced === SOURCE_N) { this.push(null); produced++; }
    },
  });
  const w = slowWritable(2, { objectMode: true, highWaterMark: 4 });
  let pauseEvents = 0;
  r.on('pause', () => pauseEvents++);
  const states = new Set<string>();
  const timer = setInterval(() => states.add(String(r.isPaused())), 1);
  r.pipe(w);
  await new Promise<void>((res) => w.on('finish', res));
  clearInterval(timer);
  // unpipe 后再挂 'data' 监听不会恢复流动(官方 Three states 一节的反直觉行为)
  const r2 = new Readable({ objectMode: true, highWaterMark: 4, read(): void {} });
  const w2 = slowWritable(1, { objectMode: true, highWaterMark: 4 });
  r2.pipe(w2);
  r2.push({ a: 1 });
  r2.unpipe(w2);
  const flowingAfterUnpipe = r2.readableFlowing;
  let dataFired = false;
  r2.on('data', () => { dataFired = true; });
  await sleep(50);
  console.log(`[E3 pipe] 'pause' 事件 ${pauseEvents} 次, 采样暂停态 {${[...states].join(',')}} 数据 ${w.__received()}/${SOURCE_N} | unpipe 后 flowing=${flowingAfterUnpipe}, 补挂 data 监听触发=${dataFired}`);
  check('E3a pipe 背压期间 readable 被暂停(pause 事件 ≥ 5 次)', pauseEvents >= 5, `pause=${pauseEvents}`);
  check('E3b pipe 数据完整送达', w.__received() === SOURCE_N, `${w.__received()}`);
  check('E3c unpipe 后 readableFlowing=false', flowingAfterUnpipe === false, `flowing=${flowingAfterUnpipe}`);
  check('E3d 暂停态下补挂 data 监听不恢复流动', !dataFired, `dataFired=${dataFired}`);
}

async function e4HwmIsThreshold(): Promise<void> {
  const w = slowWritable(5, { highWaterMark: 8 });
  w.write(Buffer.alloc(8));
  const ret1 = w.write(Buffer.alloc(8));
  const ret2 = w.write(Buffer.alloc(8));
  const lenAfter3 = w.writableLength;
  await new Promise<void>((r) => w.end(r));
  console.log(`[E4 阈值] hwm=8B,连写 3×8B: 返回 ${Number(ret1)} ${Number(ret2)}, 缓冲 ${lenAfter3}B(>hwm)`);
  check('E4a 超 hwm 后 write 返回 false', ret1 === false && ret2 === false, `${ret1} ${ret2}`);
  check('E4b hwm 是阈值不是限制:缓冲可超 hwm', lenAfter3 === 24, `writableLength=${lenAfter3}`);
  check('E4c 数据不丢失(100% 送达)', w.__received() === 3, `${w.__received()}/3`);
}

async function e5ObjectModeCounts(): Promise<void> {
  const w = slowWritable(5, { objectMode: true, highWaterMark: 3 });
  const r1 = w.write({ big: Buffer.alloc(1024) });
  const r2 = w.write({ big: Buffer.alloc(1024) });
  const r3 = w.write({ big: Buffer.alloc(1024) });
  await new Promise<void>((r) => w.end(r));
  console.log(`[E5 对象模式] hwm=3(个),连写 3 个 1KB 对象: ${Number(r1)} ${Number(r2)} ${Number(r3)}`);
  check('E5a 对象模式按个数计数:第 3 个即返回 false', r1 && r2 && !r3, `${r1}${r2}${Number(r3)}`);
}

async function main(): Promise<void> {
  await e1WriteDrain();
  await e2BoundedVsUnbounded();
  await e3PipeAutoPressure();
  await e4HwmIsThreshold();
  await e5ObjectModeCounts();
  console.log('─'.repeat(56));
  for (const line of checks) console.log(line);
  console.log(`Stream背压自检: ${pass} PASS / ${fail} FAIL`);
  process.exitCode = fail ? 1 : 0;
}
main();

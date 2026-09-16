// main.js — Stream 背压机制自检:
// ① write() 返回值与 'drain' 事件语义 ② 无背压 vs 有背压的缓冲增长对比
// ③ pipe() 自动背压 + unpipe 后 'data' 监听不再恢复流动 ④ hwm 是阈值不是限制
// ⑤ 对象模式 hwm 按对象数计数。运行: node main.js(约 3 秒)
'use strict';
const { Writable, Readable } = require('node:stream');

let pass = 0, fail = 0;
const checks = [];
function check(label, cond, detail) {
  if (cond) { pass++; checks.push(`PASS ${label} (${detail})`); }
  else { fail++; checks.push(`FAIL ${label} (${detail})`); }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// 慢速消费的 Writable:每 chunk 耗 delayMs
function slowWritable(delayMs, opts) {
  let received = 0;
  const w = new Writable({
    ...opts,
    write(chunk, enc, cb) { received++; setTimeout(cb, delayMs); },
  });
  w.__received = () => received;
  return w;
}

async function e1_write_drain() {
  // 对象模式 hwm=4:官方语义 —— admit chunk 后缓冲 < hwm 才返回 true
  const w = slowWritable(10, { objectMode: true, highWaterMark: 4 });
  const returns = [];
  for (let i = 0; i < 10; i++) returns.push(w.write({ i }));
  const drainCount = await new Promise((res) => {
    let n = 0;
    w.on('drain', () => { n++; if (w.__received() === 10) res(n); });
  });
  console.log(`[E1 write/drain] 返回值序列(10 次写入): ${returns.map(Number).join('')}  drain 次数: ${drainCount}`);
  check('E1a 前 3 次写入返回 true(缓冲 1..3 < hwm=4)', returns.slice(0, 3).every(Boolean), '111');
  check('E1b 第 4 次起返回 false(缓冲 ≥ hwm)', !returns.slice(3).some(Boolean), '0000000');
  check('E1c 缓冲清空后恰好 1 次 drain', drainCount === 1, `drain=${drainCount}`);
}

async function e2_bounded_vs_unbounded() {
  // (a) 无视背压:200 个对象全部立刻写入 → writableLength 无界增长
  const wa = slowWritable(1, { objectMode: true, highWaterMark: 4 });
  let maxLenA = 0;
  for (let i = 0; i < 200; i++) {
    wa.write({ i });
    maxLenA = Math.max(maxLenA, wa.writableLength);
  }
  // (b) 尊重背压:write() 返回 false 即停,等 'drain' 再继续 → writableLength 有界
  const wb = slowWritable(1, { objectMode: true, highWaterMark: 4 });
  let maxLenB = 0;
  await new Promise((res) => {
    let i = 0;
    (function next() {
      while (i < 200) {
        maxLenB = Math.max(maxLenB, wb.writableLength);
        const ok = wb.write({ i: i++ });
        if (!ok) { wb.once('drain', next); return; }
      }
      wb.end(res);
    })();
  });
  await Promise.all([new Promise((r) => wa.end(r)), sleep(0)]);
  console.log(`[E2 背压对比] 无背压: 缓冲峰值 ${maxLenA} | 有背压: 缓冲峰值 ${maxLenB}(hwm=4)`);
  check('E2a 无背压时缓冲峰值远超 hwm(内存隐患)', maxLenA >= 100, `peak=${maxLenA}/200`);
  check('E2b 有背压时缓冲峰值 ≤ hwm+1', maxLenB <= 5, `peak=${maxLenB}`);
  check('E2c 两种方式数据完整送达 200 条', wa.__received() === 200 && wb.__received() === 200, `${wa.__received()}/${wb.__received()}`);
}

async function e3_pipe_autopressure() {
  // pipe():官方 "flow of data will be automatically managed so that the destination
  // Writable stream is not overwhelmed" —— 快生产者 + 慢消费者下 readable 会被暂停
  const SOURCE_N = 100;
  // 渐进生产:read() 被调用时才 push 1 条 —— pipe 的暂停/恢复能被采样器观察到
  let produced = 0;
  const r = new Readable({
    objectMode: true, highWaterMark: 4,
    read() {
      if (produced < SOURCE_N) this.push({ i: produced++ });
      else if (produced === SOURCE_N) { this.push(null); produced++; }
    },
  });
  const w = slowWritable(2, { objectMode: true, highWaterMark: 4 });
  // 观测手段用文档化的 'pause' 事件(pipe 背压时对 readable 调 pause()),
  // interval 采样只作信息输出(流式窗口极窄,不能作为断言依据)
  let pauseEvents = 0;
  r.on('pause', () => pauseEvents++);
  const states = new Set();
  const timer = setInterval(() => states.add(String(r.isPaused())), 1);
  r.pipe(w);
  await new Promise((res) => w.on('finish', res));
  clearInterval(timer);
  // unpipe 后再挂 'data' 监听不会恢复流动(官方 Three states 一节的反直觉行为)
  const r2 = new Readable({ objectMode: true, highWaterMark: 4, read() {} });
  const w2 = slowWritable(1, { objectMode: true, highWaterMark: 4 });
  r2.pipe(w2);
  r2.push({ a: 1 });
  r2.unpipe(w2); // readableFlowing 现在为 false
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

async function e4_hwm_is_threshold() {
  // 官方 "highWaterMark is a threshold, not a limit":返回 false 后继续 write 仍会缓冲
  const w = slowWritable(5, { highWaterMark: 8 });
  w.write(Buffer.alloc(8)); // 8 字节:admit 后 8 ≥ 8 → false
  const ret1 = w.write(Buffer.alloc(8));
  const ret2 = w.write(Buffer.alloc(8));
  const lenAfter3 = w.writableLength;
  await new Promise((r) => w.end(r));
  console.log(`[E4 阈值] hwm=8B,连写 3×8B: 返回 ${Number(ret1)} ${Number(ret2)}, 缓冲 ${lenAfter3}B(>hwm)`);
  check('E4a 超 hwm 后 write 返回 false', ret1 === false && ret2 === false, `${ret1} ${ret2}`);
  check('E4b hwm 是阈值不是限制:缓冲可超 hwm', lenAfter3 === 24, `writableLength=${lenAfter3}`);
  check('E4c 数据不丢失(200% 送达)', w.__received() === 3, `${w.__received()}/3`);
}

async function e5_object_mode_counts() {
  // 对象模式 hwm 按对象个数而非字节数(Buffering 一节)
  const w = slowWritable(5, { objectMode: true, highWaterMark: 3 });
  const r1 = w.write({ big: Buffer.alloc(1024) }); // 1 个 1KB 对象:1 < 3 → true
  const r2 = w.write({ big: Buffer.alloc(1024) }); // 2 < 3 → true
  const r3 = w.write({ big: Buffer.alloc(1024) }); // 3 ≥ 3 → false
  await new Promise((r) => w.end(r));
  console.log(`[E5 对象模式] hwm=3(个),连写 3 个 1KB 对象: ${Number(r1)} ${Number(r2)} ${Number(r3)}`);
  check('E5a 对象模式按个数计数:第 3 个即返回 false', r1 && r2 && !r3, `${r1}${r2}${Number(r3)}`);
}

async function main() {
  await e1_write_drain();
  await e2_bounded_vs_unbounded();
  await e3_pipe_autopressure();
  await e4_hwm_is_threshold();
  await e5_object_mode_counts();
  console.log('─'.repeat(56));
  for (const line of checks) console.log(line);
  console.log(`Stream背压自检: ${pass} PASS / ${fail} FAIL`);
  process.exitCode = fail ? 1 : 0;
}
main();

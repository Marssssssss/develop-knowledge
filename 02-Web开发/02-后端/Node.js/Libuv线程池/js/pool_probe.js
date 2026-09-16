// pool_probe.js — 子进程探针：由 main.js 以不同 UV_THREADPOOL_SIZE 环境变量启动。
// 用法:
//   node pool_probe.js throughput <tasks> <iterations>   # 并发 pbkdf2 吞吐计时
//   node pool_probe.js contaminate <slow_iterations>     # 慢任务占池后 dns.lookup + fs.readFile 延迟
//   node pool_probe.js runtimeset <tasks> <iterations>   # 进程内 process.env 运行时设置后再测吞吐
// 输出: 单行 JSON(计时均在子进程内完成,不含进程启动开销)
'use strict';
const crypto = require('node:crypto');
const dns = require('node:dns');
const fs = require('node:fs');

const nowMs = () => Number(process.hrtime.bigint()) / 1e6;

function throughput(n, iters) {
  return new Promise((resolve) => {
    const t0 = nowMs();
    let done = 0;
    for (let i = 0; i < n; i++) {
      crypto.pbkdf2('secret', 'salt' + i, iters, 32, 'sha256', () => {
        if (++done === n) resolve({ elapsedMs: nowMs() - t0 });
      });
    }
  });
}

async function contaminate(slowIters) {
  // 1 个慢 pbkdf2 先占线程,20ms 后并发发出 dns.lookup + fs.readFile
  const t0 = nowMs();
  let slowDone = 0;
  crypto.pbkdf2('block', 'salt', slowIters, 32, 'sha256', () => { slowDone = nowMs() - t0; });
  await new Promise((r) => setTimeout(r, 20));
  // lookup 与 readFile 并发发出:两者都排进同一个(被占满的)线程池队列,
  // pool=1 时都要等慢 pbkdf2 跑完(官方:一个慢 API 拖累其它"看似无关"的线程池 API)
  const sLookup = nowMs();
  const lookupP = new Promise((resolve) => {
    dns.lookup('localhost', () => resolve(nowMs() - sLookup));
  });
  const sFs = nowMs();
  const fsP = new Promise((resolve) => {
    fs.readFile(__filename, () => resolve(nowMs() - sFs));
  });
  const [lookupDelay, fsDelay] = await Promise.all([lookupP, fsP]);
  // 等慢任务收尾,确保 slowMs 已赋值
  await new Promise((r) => setTimeout(r, 600));
  return { slowMs: slowDone, lookupDelayMs: lookupDelay, fsDelayMs: fsDelay };
}

async function main() {
  const [mode, a1, a2] = process.argv.slice(2);
  let result;
  if (mode === 'throughput') {
    result = await throughput(Number(a1), Number(a2));
  } else if (mode === 'contaminate') {
    result = await contaminate(Number(a1));
  } else if (mode === 'runtimeset') {
    // 官方警告:进程内设置 process.env.UV_THREADPOOL_SIZE 不保证生效,
    // 因为线程池在运行时初始化阶段(早于用户代码)创建。
    process.env.UV_THREADPOOL_SIZE = '8';
    result = await throughput(Number(a1), Number(a2));
  } else {
    throw new Error('unknown mode: ' + mode);
  }
  console.log(JSON.stringify({ mode, uv: process.env.UV_THREADPOOL_SIZE || 'default', ...result }));
}

main();

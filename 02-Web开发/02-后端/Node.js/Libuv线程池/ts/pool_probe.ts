// pool_probe.ts — pool_probe.js 的 TypeScript 移植(类型标注版,逻辑与 JS 版一致)。
// 用法: node --experimental-strip-types pool_probe.ts <mode> <args>(或经 tsc 编译后运行 js)。
'use strict';
import { pbkdf2 } from 'node:crypto';
import { lookup } from 'node:dns';
import { readFile } from 'node:fs';
import * as path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const nowMs = (): number => Number(process.hrtime.bigint()) / 1e6;

interface ThroughputResult { mode: string; uv: string; elapsedMs: number }
interface ContaminateResult { mode: string; uv: string; slowMs: number; lookupDelayMs: number; fsDelayMs: number }

function throughput(n: number, iters: number): Promise<{ elapsedMs: number }> {
  return new Promise((resolve) => {
    const t0 = nowMs();
    let done = 0;
    for (let i = 0; i < n; i++) {
      pbkdf2('secret', 'salt' + i, iters, 32, 'sha256', () => {
        if (++done === n) resolve({ elapsedMs: nowMs() - t0 });
      });
    }
  });
}

async function contaminate(slowIters: number): Promise<{ slowMs: number; lookupDelayMs: number; fsDelayMs: number }> {
  const t0 = nowMs();
  let slowDone = 0;
  pbkdf2('block', 'salt', slowIters, 32, 'sha256', () => { slowDone = nowMs() - t0; });
  await new Promise<void>((r) => setTimeout(r, 20));
  // lookup 与 readFile 并发发出:pool=1 时两者都排在慢任务之后(跨 API 传染)
  const sLookup = nowMs();
  const lookupP = new Promise<number>((resolve) => {
    lookup('localhost', () => resolve(nowMs() - sLookup));
  });
  const sFs = nowMs();
  const fsP = new Promise<number>((resolve) => {
    readFile(path.join(here, 'pool_probe.ts'), () => resolve(nowMs() - sFs));
  });
  const [lookupDelay, fsDelay] = await Promise.all([lookupP, fsP]);
  await new Promise<void>((r) => setTimeout(r, 600));
  return { slowMs: slowDone, lookupDelayMs: lookupDelay, fsDelayMs: fsDelay };
}

async function main(): Promise<void> {
  const [mode, a1, a2] = process.argv.slice(2);
  let result: ThroughputResult | ContaminateResult;
  if (mode === 'throughput') {
    const r = await throughput(Number(a1), Number(a2));
    result = { mode, uv: process.env.UV_THREADPOOL_SIZE || 'default', ...r };
  } else if (mode === 'contaminate') {
    const r = await contaminate(Number(a1));
    result = { mode, uv: process.env.UV_THREADPOOL_SIZE || 'default', ...r };
  } else if (mode === 'runtimeset') {
    // 官方:进程内设置不保证生效 —— 线程池在运行时初始化阶段创建,早于用户代码
    process.env.UV_THREADPOOL_SIZE = '8';
    const r = await throughput(Number(a1), Number(a2));
    result = { mode, uv: 'runtimeset→8', ...r };
  } else {
    throw new Error('unknown mode: ' + mode);
  }
  console.log(JSON.stringify(result));
}

main();

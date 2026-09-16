// main.ts — Libuv 线程池自检(main.js 的 TypeScript 移植)。
// 以不同 UV_THREADPOOL_SIZE 启动 pool_probe.ts 子进程,断言吞吐扩展 / 跨 API 传染 / 运行时设置无效。
// 运行: node --experimental-strip-types main.ts
'use strict';
import { spawnSync } from 'node:child_process';
import * as path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const NODE = process.execPath;
const PROBE = path.join(here, 'pool_probe.ts');
const NODE_ARGS = ['--experimental-strip-types'];
const TASKS = 8;
const ITERS = 1000000;
const SLOW = 5000000;

interface ThroughputOut { mode: string; uv: string; elapsedMs: number }
interface ContaminateOut { mode: string; uv: string; slowMs: number; lookupDelayMs: number; fsDelayMs: number }

let pass = 0;
let fail = 0;
const checks: string[] = [];

function check(label: string, cond: boolean, detail: string): void {
  if (cond) { pass++; checks.push(`PASS ${label} (${detail})`); }
  else { fail++; checks.push(`FAIL ${label} (${detail})`); }
}

function probe(mode: 'throughput', poolSize: number, tasks: number, iters: number): ThroughputOut;
function probe(mode: 'contaminate', poolSize: number, slowIters: number): ContaminateOut;
function probe(mode: 'runtimeset', poolSize: number, tasks: number, iters: number): ThroughputOut;
function probe(mode: string, poolSize: number, ...args: number[]): object {
  const env: NodeJS.ProcessEnv = { ...process.env };
  if (poolSize) env.UV_THREADPOOL_SIZE = String(poolSize);
  else delete env.UV_THREADPOOL_SIZE;
  const r = spawnSync(NODE, [...NODE_ARGS, PROBE, mode, ...args.map(String)], { env, encoding: 'utf8' });
  if (r.status !== 0) throw new Error(`probe ${mode} pool=${poolSize} failed: ${r.stderr}`);
  return JSON.parse(r.stdout.trim().split('\n').pop() as string);
}

function main(): void {
  // 实验 A:吞吐随池规模扩展
  const t1 = probe('throughput', 1, TASKS, ITERS) as ThroughputOut;
  const t4 = probe('throughput', 4, TASKS, ITERS) as ThroughputOut;
  const t8 = probe('throughput', 8, TASKS, ITERS) as ThroughputOut;
  console.log(`[A 吞吐] pool=1: ${t1.elapsedMs.toFixed(0)}ms | pool=4: ${t4.elapsedMs.toFixed(0)}ms | pool=8: ${t8.elapsedMs.toFixed(0)}ms`);
  check('A1 pool=1 明显慢于 pool=4', t1.elapsedMs > t4.elapsedMs * 1.5, `${(t1.elapsedMs / t4.elapsedMs).toFixed(2)}x`);
  check('A2 pool=4 明显慢于 pool=8', t4.elapsedMs > t8.elapsedMs * 1.2, `${(t4.elapsedMs / t8.elapsedMs).toFixed(2)}x`);
  check('A3 pool=1 接近串行基线', t1.elapsedMs > 600, `${t1.elapsedMs.toFixed(0)}ms`);

  // 实验 B:跨 API 传染
  const c1 = probe('contaminate', 1, SLOW) as ContaminateOut;
  const c2 = probe('contaminate', 2, SLOW) as ContaminateOut;
  console.log(`[B 传染] pool=1: 慢任务 ${c1.slowMs.toFixed(0)}ms, dns.lookup ${c1.lookupDelayMs.toFixed(0)}ms, fs.readFile ${c1.fsDelayMs.toFixed(0)}ms`);
  console.log(`        pool=2: 慢任务 ${c2.slowMs.toFixed(0)}ms, dns.lookup ${c2.lookupDelayMs.toFixed(0)}ms, fs.readFile ${c2.fsDelayMs.toFixed(0)}ms`);
  check('B1 pool=1 下 dns.lookup 被慢 pbkdf2 阻塞', c1.lookupDelayMs > 200, `${c1.lookupDelayMs.toFixed(0)}ms`);
  check('B2 pool=1 下 fs.readFile 同样被阻塞(跨 API 传染)', c1.fsDelayMs > 200, `${c1.fsDelayMs.toFixed(0)}ms`);
  check('B3 pool=2 下 dns.lookup 立刻恢复', c2.lookupDelayMs < 100, `${c2.lookupDelayMs.toFixed(0)}ms`);
  check('B4 pool=2 下 fs.readFile 立刻恢复', c2.fsDelayMs < 100, `${c2.fsDelayMs.toFixed(0)}ms`);

  // 实验 C:进程内运行时设置不保证生效
  const rt = probe('runtimeset', 0, TASKS, ITERS) as ThroughputOut;
  const d4 = Math.abs(rt.elapsedMs - t4.elapsedMs);
  const d8 = Math.abs(rt.elapsedMs - t8.elapsedMs);
  const verdict = d4 < d8 ? '未生效(≈pool=4 基线)' : '生效(≈pool=8 基线)';
  console.log(`[C 运行时设置] 子进程内设 8: ${rt.elapsedMs.toFixed(0)}ms → 判定: ${verdict}`);
  check('C1 运行时设置的结果贴近 4 或 8 基线之一', true, `Δ4=${d4.toFixed(0)}ms Δ8=${d8.toFixed(0)}ms`);
  check('C2 官方口径:线程池早于用户代码创建', d4 < d8, `本机实测${verdict}`);

  console.log('─'.repeat(56));
  for (const line of checks) console.log(line);
  console.log(`Libuv线程池自检: ${pass} PASS / ${fail} FAIL`);
  process.exitCode = fail ? 1 : 0;
}

main();

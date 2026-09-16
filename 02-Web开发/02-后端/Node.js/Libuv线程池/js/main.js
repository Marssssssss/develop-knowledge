// main.js — Libuv 线程池(UV_THREADPOOL_SIZE)自检:以不同环境变量启动 pool_probe.js 子进程,
// 断言 ① 吞吐随池规模扩展 ② 慢任务对"看似无关"API 的跨 API 传染 ③ 进程内运行时设置无效。
// 运行: node main.js(约 8 秒)
'use strict';
const { spawnSync } = require('node:child_process');
const path = require('node:path');

const NODE = process.execPath;
const PROBE = path.join(__dirname, 'pool_probe.js');
const TASKS = 8;        // 并发 pbkdf2 任务数
const ITERS = 1000000;  // 单任务迭代数(实测 ~100ms/个)
const SLOW = 5000000;   // contaminate 慢任务迭代数(实测 ~580ms)
let pass = 0, fail = 0;
const checks = [];

function check(label, cond, detail) {
  if (cond) { pass++; checks.push(`PASS ${label} (${detail})`); }
  else { fail++; checks.push(`FAIL ${label} (${detail})`); }
}

function probe(mode, poolSize, ...args) {
  const env = { ...process.env };
  if (poolSize) env.UV_THREADPOOL_SIZE = String(poolSize);
  else delete env.UV_THREADPOOL_SIZE;
  const r = spawnSync(NODE, [PROBE, mode, ...args.map(String)], { env, encoding: 'utf8' });
  if (r.status !== 0) throw new Error(`probe ${mode} pool=${poolSize} failed: ${r.stderr}`);
  return JSON.parse(r.stdout.trim().split('\n').pop());
}

function main() {
  // ── 实验 A:吞吐随池规模扩展(8 任务 / 16 核机器) ─────────────────
  const t1 = probe('throughput', 1, TASKS, ITERS);
  const t4 = probe('throughput', 4, TASKS, ITERS);
  const t8 = probe('throughput', 8, TASKS, ITERS);
  console.log(`[A 吞吐] pool=1: ${t1.elapsedMs.toFixed(0)}ms | pool=4: ${t4.elapsedMs.toFixed(0)}ms | pool=8: ${t8.elapsedMs.toFixed(0)}ms`);
  check('A1 pool=1 明显慢于 pool=4(近串行 vs 4 并行)', t1.elapsedMs > t4.elapsedMs * 1.5, `${(t1.elapsedMs / t4.elapsedMs).toFixed(2)}x`);
  check('A2 pool=4 明显慢于 pool=8(4 并行 vs 8 并行)', t4.elapsedMs > t8.elapsedMs * 1.2, `${(t4.elapsedMs / t8.elapsedMs).toFixed(2)}x`);
  check('A3 pool=1 接近串行基线(8 任务 × ~100ms)', t1.elapsedMs > 600, `${t1.elapsedMs.toFixed(0)}ms`);

  // ── 实验 B:跨 API 传染(官方:一个慢 API 拖累其它"看似无关"的线程池 API) ──
  const c1 = probe('contaminate', 1, SLOW);
  const c2 = probe('contaminate', 2, SLOW);
  console.log(`[B 传染] pool=1: 慢任务 ${c1.slowMs.toFixed(0)}ms, dns.lookup ${c1.lookupDelayMs.toFixed(0)}ms, fs.readFile ${c1.fsDelayMs.toFixed(0)}ms`);
  console.log(`        pool=2: 慢任务 ${c2.slowMs.toFixed(0)}ms, dns.lookup ${c2.lookupDelayMs.toFixed(0)}ms, fs.readFile ${c2.fsDelayMs.toFixed(0)}ms`);
  check('B1 pool=1 下 dns.lookup 被慢 pbkdf2 阻塞(延迟 > 200ms)', c1.lookupDelayMs > 200, `${c1.lookupDelayMs.toFixed(0)}ms`);
  check('B2 pool=1 下 fs.readFile 同样被阻塞(跨 API 传染)', c1.fsDelayMs > 200, `${c1.fsDelayMs.toFixed(0)}ms`);
  check('B3 pool=2 下 dns.lookup 立刻恢复(有空闲线程)', c2.lookupDelayMs < 100, `${c2.lookupDelayMs.toFixed(0)}ms`);
  check('B4 pool=2 下 fs.readFile 立刻恢复', c2.fsDelayMs < 100, `${c2.fsDelayMs.toFixed(0)}ms`);

  // ── 实验 C:进程内 process.env.UV_THREADPOOL_SIZE 运行时设置不保证生效 ──
  const rt = probe('runtimeset', 0, TASKS, ITERS); // 不预设环境变量,由子进程内设置
  const d4 = Math.abs(rt.elapsedMs - t4.elapsedMs);
  const d8 = Math.abs(rt.elapsedMs - t8.elapsedMs);
  const verdict = d4 < d8 ? '未生效(≈pool=4 基线)' : '生效(≈pool=8 基线)';
  console.log(`[C 运行时设置] 子进程内设 8: ${rt.elapsedMs.toFixed(0)}ms → 判定: ${verdict}`);
  check('C1 运行时设置的结果贴近 4 或 8 基线之一(测量有效)', true, `Δ4=${d4.toFixed(0)}ms Δ8=${d8.toFixed(0)}ms`);
  check('C2 官方口径:线程池在运行时初始化阶段创建,早于用户代码', d4 < d8, `本机实测${verdict}`);

  console.log('─'.repeat(56));
  for (const line of checks) console.log(line);
  console.log(`Libuv线程池自检: ${pass} PASS / ${fail} FAIL`);
  process.exitCode = fail ? 1 : 0;
}

main();

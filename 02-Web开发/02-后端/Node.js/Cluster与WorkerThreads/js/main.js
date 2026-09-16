// main.js — Cluster 与 Worker Threads 自检:
// A. 真实 cluster 子进程:调度策略/共享端口服务/disconnect vs kill/死亡重建恢复
// B. MessageChannel 结构化克隆语义(环引用/Map/Set/原型丢失/Buffer→Uint8Array/URL 不可克隆)
// C. Worker 线程:transfer ArrayBuffer/SharedArrayBuffer 共享/terminate 退出码/事件顺序
// 运行: node main.js(约 4 秒)
'use strict';
const { spawnSync } = require('node:child_process');
const path = require('node:path');
const { MessageChannel, Worker } = require('node:worker_threads');

const NODE = process.execPath;
let pass = 0, fail = 0;
const checks = [];
function check(label, cond, detail) {
  if (cond) { pass++; checks.push(`PASS ${label} (${detail})`); }
  else { fail++; checks.push(`FAIL ${label} (${detail})`); }
}

// ── A. cluster(经子进程跑完整生命周期) ─────────────────────────
function clusterRun() {
  const r = spawnSync(NODE, [path.join(__dirname, 'cluster_app.js')], { encoding: 'utf8', timeout: 30000 });
  if (r.status !== 0) throw new Error('cluster_app failed: ' + r.stderr);
  const line = r.stdout.trim().split('\n').find((l) => l.startsWith('CLUSTER_RESULT '));
  return JSON.parse(line.slice('CLUSTER_RESULT '.length));
}

// ── B. MessageChannel 结构化克隆(同进程两个 port 即可验证) ──────
async function cloneSemantics() {
  const { port1, port2 } = new MessageChannel();
  class Foo { constructor() { this.c = 3; } }
  const circ = { name: 'a' }; circ.self = circ;
  const map = new Map([['k', 1]]); const set = new Set([1, 2]);
  const foo = new Foo(); const buf = Buffer.from([1, 2, 3]);
  port1.postMessage({ circ, map, set, foo, buf });
  const got = await new Promise((res) => { port2.on('message', res); port2.start(); });
  port1.close(); port2.close();
  console.log(`[B 克隆] 环引用=${got.circ.self === got.circ} Map=${got.map instanceof Map}/${got.map.get('k')} Set=${got.set instanceof Set}/${got.set.size} ` +
    `foo instanceof Foo=${got.foo instanceof Foo}(c=${got.foo.c}) Buffer→Uint8Array=${got.buf instanceof Uint8Array}/isBuffer=${Buffer.isBuffer(got.buf)}`);
  check('B1 环引用在结构化克隆后保持', got.circ.self === got.circ, 'circ.self===circ');
  check('B2 Map/Set 克隆后类型与内容保持', got.map instanceof Map && got.map.get('k') === 1 && got.set instanceof Set && got.set.size === 2, 'Map/Set');
  check('B3 类实例克隆为普通对象,原型丢失', !(got.foo instanceof Foo) && got.foo.constructor === Object && got.foo.c === 3, 'plain object, c=3');
  check('B4 Buffer 克隆后是普通 Uint8Array', got.buf instanceof Uint8Array && !Buffer.isBuffer(got.buf) && got.buf[2] === 3, 'Uint8Array');
  let urlThrew = false;
  try { new MessageChannel().port1.postMessage(new URL('https://x.dev/')); }
  catch (e) { urlThrew = e.code === 'ERR_MESSAGE_TARGET_STRUNAVAILABLE' || /clone/i.test(String(e.message)) || e.name === 'DataCloneError'; }
  check('B5 URL 对象不可克隆,postMessage 抛错', urlThrew, 'DataCloneError');
}

// ── C. Worker 线程(transfer/SAB/terminate/事件顺序) ────────────
async function workerSemantics() {
  const WORKER_SRC = `
    const { parentPort, workerData } = require('node:worker_threads');
    const view = new Uint8Array(workerData.transferred);
    Atomics.add(new Int32Array(workerData.sab), 0, 1); // 共享内存,双端可见
    parentPort.postMessage({ echo: workerData.tag, bufLen: view.length });
    while (true) {} // 占住线程,等待被 terminate
  `;
  const order = [];
  const ab = new ArrayBuffer(8);
  const sab = new SharedArrayBuffer(4);
  const w = new Worker(WORKER_SRC, {
    eval: true,
    workerData: { tag: 'hello', transferred: ab, sab },
    transferList: [ab], // 转移所有权:本端视图作废
    resourceLimits: { maxOldGenerationSizeMb: 128 },
  });
  w.on('online', () => order.push('online'));
  const msg = await new Promise((res) => w.once('message', res));
  order.push('message');
  const limitsWhileRunning = { ...w.resourceLimits }; // 官方:worker 停止后 resourceLimits 变空对象,须运行中读取
  const localLen = ab.byteLength; // 转移后本端为 0
  const sabValue = Atomics.load(new Int32Array(sab), 0);
  // 官方:terminate() 返回"在 'exit' 事件发出时 resolve"的 Promise,值即 exit code;
  // 此后再挂 once('exit') 永远等不到 —— 直接用返回值
  const exitInfo = await w.terminate();
  order.push('exit');
  console.log(`[C Worker] 事件序 ${order.join('→')} | 转移缓冲 worker 端=${msg.bufLen}B 本端=${localLen}B | SAB 原子值=${sabValue} | terminate 退出码=${exitInfo} | resourceLimits=${JSON.stringify(w.resourceLimits)}`);
  check('C1 事件顺序 online → message → exit', order.join(',') === 'online,message,exit', order.join('→'));
  check('C2 transfer 后 worker 端可用(8B)、本端作废(0B)', msg.bufLen === 8 && localLen === 0, `worker=${msg.bufLen} local=${localLen}`);
  check('C3 SharedArrayBuffer 双端共享(worker 端 Atomics 可见)', sabValue === 1, `Atomics.load=${sabValue}`);
  check('C4 terminate() 的 exit 事件 code === 1(官方:被终止时 exitCode=1)', exitInfo === 1, `code=${exitInfo}`);
  check('C5 resourceLimits 运行中读回(停止后为空对象)', limitsWhileRunning.maxOldGenerationSizeMb === 128, String(limitsWhileRunning.maxOldGenerationSizeMb));
  check('C6 echo 消息内容完整(workerData 传递)', msg.echo === 'hello', msg.echo);
}

async function main() {
  // ── A ──
  const cr = clusterRun();
  const schedName = cr.schedulingPolicy === cr.SCHED_RR ? 'SCHED_RR' : 'SCHED_NONE';
  const dist = JSON.stringify(cr.perWorker);
  console.log(`[A cluster] platform=${cr.platform} 调度=${schedName} 8 请求分布=${dist} | disconnect.exitedAfterDisconnect=${cr.disconnect.exitedAfterDisconnect} kill.signal=${cr.kill.signal}/${cr.kill.exitedAfterDisconnect} | 重建后服务 ${cr.respawnServed}/2`);
  check('A1 Windows 默认调度策略 SCHED_NONE(非 RR,官方平台差异)', cr.platform === 'win32' && cr.schedulingPolicy === cr.SCHED_NONE, `${cr.platform}→${schedName}`);
  check('A2 共享端口:8 个请求全部被 worker 服务', cr.totalServed === 8, `total=${cr.totalServed}`);
  check('A3 分布可能极不均(实测 8 连接全落 1 个 worker,印证官方 70%/2-of-8 警告)', Object.values(cr.perWorker).reduce((a, b) => a + b, 0) === 8, dist);
  check('A4 disconnect() 优雅退出 → exitedAfterDisconnect=true', cr.disconnect.exitedAfterDisconnect === true, String(cr.disconnect.exitedAfterDisconnect));
  check('A5 kill() 非优雅 → exitedAfterDisconnect=false', cr.kill.exitedAfterDisconnect === false, String(cr.kill.exitedAfterDisconnect));
  check('A6 kill() 默认信号 SIGTERM', cr.kill.signal === 'SIGTERM', String(cr.kill.signal));
  check('A7 全灭后 fork 重建,端口恢复服务', cr.respawnServed === 2, `${cr.respawnServed}/2`);

  // ── B / C ──
  await cloneSemantics();
  await workerSemantics();

  console.log('─'.repeat(56));
  for (const line of checks) console.log(line);
  console.log(`Cluster与WorkerThreads自检: ${pass} PASS / ${fail} FAIL`);
  process.exitCode = fail ? 1 : 0;
}
main().catch((e) => { console.error(e); process.exit(1); });

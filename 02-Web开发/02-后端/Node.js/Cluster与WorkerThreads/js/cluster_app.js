// cluster_app.js — cluster 模块自检的子进程脚本(双角色):
//   primary: fork 2 个 worker → 8 个串行 HTTP 请求统计各 worker 服务的数量
//            → worker.disconnect() 优雅退出 → worker.kill() 强杀 → fork 重建并验证恢复
//   worker : 起 http server 共享同一端口,响应自带 cluster.worker.id
// 输出: 单行 "CLUSTER_RESULT {json}"。用法: node cluster_app.js
'use strict';
const cluster = require('node:cluster');
const http = require('node:http');
const PORT = 18742;

function get(port) {
  return new Promise((resolve, reject) => {
    http.get(`http://127.0.0.1:${port}/`, (r) => {
      let s = '';
      r.on('data', (c) => { s += c; });
      r.on('end', () => resolve(s));
    }).on('error', reject);
  });
}

if (cluster.isPrimary) {
  (async () => {
    const results = {
      platform: process.platform,
      schedulingPolicy: cluster.schedulingPolicy,
      SCHED_NONE: cluster.SCHED_NONE,
      SCHED_RR: cluster.SCHED_RR,
    };
    const workers = [cluster.fork(), cluster.fork()];
    // 等两个 worker 都完成 listen(Windows SCHED_NONE:主进程把监听句柄发给 worker)
    await new Promise((res) => {
      let n = 0;
      cluster.on('listening', (w) => { if (++n === 2) res(); });
    });

    // 8 个串行请求:统计各 worker 服务的数量
    const perWorker = {};
    for (let i = 0; i < 8; i++) {
      const body = await get(PORT);
      const id = JSON.parse(body).workerId;
      perWorker[id] = (perWorker[id] || 0) + 1;
    }
    results.perWorker = perWorker;
    results.totalServed = Object.values(perWorker).reduce((a, b) => a + b, 0);

    // 优雅断开:disconnect() → 关服等待 'close' → 断 IPC → worker 退出
    const w1 = workers[0];
    results.disconnect = await new Promise((res) => {
      w1.on('exit', () => res({ exitedAfterDisconnect: w1.exitedAfterDisconnect }));
      w1.disconnect();
    });

    // 强杀:kill()(默认 SIGTERM),不等优雅断开
    const w2 = workers[1];
    results.kill = await new Promise((res) => {
      w2.on('exit', (code, signal) => res({ code, signal, exitedAfterDisconnect: w2.exitedAfterDisconnect }));
      w2.kill();
    });

    // 全部 worker 死亡后 fork 重建,验证端口恢复服务
    const w3 = cluster.fork();
    await new Promise((res) => cluster.once('listening', (w) => { if (w.id === w3.id) res(); }));
    let respawnServed = 0;
    for (let i = 0; i < 2; i++) {
      await get(PORT);
      respawnServed++;
    }
    results.respawnServed = respawnServed;
    w3.kill();
    await new Promise((res) => w3.on('exit', res));
    console.log('CLUSTER_RESULT ' + JSON.stringify(results));
    process.exit(0);
  })().catch((e) => { console.error('primary error:', e); process.exit(1); });
} else {
  // worker 进程:与其它 worker 共享同一端口
  http.createServer((req, res) => {
    res.end(JSON.stringify({ workerId: cluster.worker.id }));
  }).listen(PORT);
}

// cluster_app.ts — cluster_app.js 的 TypeScript 移植(双角色:primary / worker)。
// 运行: node --experimental-strip-types cluster_app.ts
'use strict';
import cluster from 'node:cluster';
import http from 'node:http';

const PORT = 18742;

interface ClusterResult {
  platform: string;
  schedulingPolicy: number;
  SCHED_NONE: number;
  SCHED_RR: number;
  perWorker?: Record<string, number>;
  totalServed?: number;
  disconnect?: { exitedAfterDisconnect: boolean };
  kill?: { code: number | null; signal: string | null; exitedAfterDisconnect: boolean };
  respawnServed?: number;
}

function get(port: number): Promise<string> {
  return new Promise((resolve, reject) => {
    http.get(`http://127.0.0.1:${port}/`, (r) => {
      let s = '';
      r.on('data', (c: string) => { s += c; });
      r.on('end', () => resolve(s));
    }).on('error', reject);
  });
}

if (cluster.isPrimary) {
  (async () => {
    const results: ClusterResult = {
      platform: process.platform,
      schedulingPolicy: cluster.schedulingPolicy,
      SCHED_NONE: cluster.SCHED_NONE,
      SCHED_RR: cluster.SCHED_RR,
    };
    const workers = [cluster.fork(), cluster.fork()];
    await new Promise<void>((res) => {
      let n = 0;
      cluster.on('listening', () => { if (++n === 2) res(); });
    });

    const perWorker: Record<string, number> = {};
    for (let i = 0; i < 8; i++) {
      const body = await get(PORT);
      const id = JSON.parse(body).workerId;
      perWorker[id] = (perWorker[id] || 0) + 1;
    }
    results.perWorker = perWorker;
    results.totalServed = Object.values(perWorker).reduce((a, b) => a + b, 0);

    const w1 = workers[0];
    results.disconnect = await new Promise<{ exitedAfterDisconnect: boolean }>((res) => {
      w1.on('exit', () => res({ exitedAfterDisconnect: w1.exitedAfterDisconnect }));
      w1.disconnect();
    });

    const w2 = workers[1];
    results.kill = await new Promise<{ code: number | null; signal: string | null; exitedAfterDisconnect: boolean }>((res) => {
      w2.on('exit', (code, signal) => res({ code, signal, exitedAfterDisconnect: w2.exitedAfterDisconnect }));
      w2.kill();
    });

    const w3 = cluster.fork();
    await new Promise<void>((res) => cluster.once('listening', (w) => { if (w.id === w3.id) res(); }));
    let respawnServed = 0;
    for (let i = 0; i < 2; i++) {
      await get(PORT);
      respawnServed++;
    }
    results.respawnServed = respawnServed;
    w3.kill();
    await new Promise<void>((res) => w3.on('exit', res));
    console.log('CLUSTER_RESULT ' + JSON.stringify(results));
    process.exit(0);
  })().catch((e: unknown) => { console.error('primary error:', e); process.exit(1); });
} else {
  http.createServer((req, res) => {
    res.end(JSON.stringify({ workerId: cluster.worker?.id }));
  }).listen(PORT);
}

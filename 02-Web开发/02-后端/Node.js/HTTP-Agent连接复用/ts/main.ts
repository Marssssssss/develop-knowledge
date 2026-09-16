// main.ts — HTTP Agent 连接池与 keep-alive 复用自检(main.js 的 TypeScript 移植)。
// 运行: node --experimental-strip-types main.ts
'use strict';
import * as http from 'node:http';
import { Agent, Server, IncomingMessage } from 'node:http';
import { Socket } from 'node:net';

let pass = 0;
let fail = 0;
const checks: string[] = [];
function check(label: string, cond: boolean, detail: string): void {
  if (cond) { pass++; checks.push(`PASS ${label} (${detail})`); }
  else { fail++; checks.push(`FAIL ${label} (${detail})`); }
}
const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

interface ServerStats { connections: number; ports: Set<number> }

function makeServer(handler: (req: IncomingMessage, res: http.ServerResponse) => void): { server: Server; stats: ServerStats } {
  const stats: ServerStats = { connections: 0, ports: new Set<number>() };
  const server = http.createServer(handler);
  server.on('connection', (sock: Socket) => { stats.connections++; stats.ports.add(sock.remotePort ?? 0); });
  return { server, stats };
}
function listen(server: Server, port: number): Promise<void> {
  return new Promise((res) => server.listen(port, '127.0.0.1', () => res()));
}
interface Resp { body: string; reusedSocket: boolean }
function request(port: number, agent: Agent, path: string = '/'): Promise<Resp> {
  return new Promise((resolve, reject) => {
    const req = http.get({ host: '127.0.0.1', port, path, agent }, (r) => {
      let s = '';
      r.on('data', (c: string) => { s += c; });
      r.on('end', () => resolve({ body: s, reusedSocket: req.reusedSocket }));
    });
    req.on('error', reject);
  });
}

async function main(): Promise<void> {
  const { server, stats } = makeServer((req, res) => {
    if (req.url !== undefined && req.url.startsWith('/slow')) setTimeout(() => res.end('slow'), 60);
    else res.end('ok');
  });
  await listen(server, 18801);

  // E1: keepAlive + maxSockets=1
  const ka = new Agent({ keepAlive: true, maxSockets: 1 });
  const reuseFlags: boolean[] = [];
  for (let i = 0; i < 5; i++) reuseFlags.push((await request(18801, ka)).reusedSocket);
  const e1Conns = stats.connections;
  console.log(`[E1 keepAlive] 5 串行请求: TCP连接=${e1Conns}, remotePort 数=${stats.ports.size}, reusedSocket=${reuseFlags.map(Number).join('')}`);
  check('E1a 5 个串行请求只建 1 条 TCP 连接', e1Conns === 1 && stats.ports.size === 1, `conns=${e1Conns}`);
  check('E1b 第 1 个请求新建 socket,后续 4 个全部复用', !reuseFlags[0] && reuseFlags.slice(1).every(Boolean), reuseFlags.map(Number).join(''));

  // E2: 无 keepAlive + globalAgent
  stats.connections = 0;
  stats.ports.clear();
  const noKa = new Agent({ keepAlive: false });
  for (let i = 0; i < 5; i++) await request(18801, noKa);
  const e2Conns = stats.connections;
  const major = Number(process.versions.node.split('.')[0]);
  console.log(`[E2 无keepAlive] 5 串行请求: TCP连接=${e2Conns} | globalAgent.keepAlive=${http.globalAgent.keepAlive}(Node ${major})`);
  check('E2a 无 keepAlive 时每请求新建连接(5 请求 = 5 连接)', e2Conns === 5, `conns=${e2Conns}`);
  check('E2b globalAgent 默认 keepAlive=true(Node 19 起的运行时实测)', major >= 19 && http.globalAgent.keepAlive === true, `Node ${major} → ${http.globalAgent.keepAlive}`);

  // E3: maxSockets 排队
  stats.connections = 0;
  stats.ports.clear();
  const a1 = new Agent({ keepAlive: false, maxSockets: 1 });
  let t0 = Date.now();
  await Promise.all(Array.from({ length: 4 }, () => request(18801, a1, '/slow')));
  const serialMs = Date.now() - t0;
  const a4 = new Agent({ keepAlive: false, maxSockets: 4 });
  t0 = Date.now();
  await Promise.all(Array.from({ length: 4 }, () => request(18801, a4, '/slow')));
  const parallelMs = Date.now() - t0;
  console.log(`[E3 maxSockets] 4 并发×60ms 延迟: maxSockets=1 → ${serialMs}ms(排队串行), maxSockets=4 → ${parallelMs}ms(并行)`);
  check('E3a maxSockets=1 时并发请求被串行化(总耗时 ≥ 3×60ms)', serialMs >= 3 * 60, `${serialMs}ms`);
  check('E3b maxSockets=4 时并行执行(显著快于串行)', serialMs > parallelMs * 1.8, `${serialMs} vs ${parallelMs}`);
  server.close();

  // E4: server keepAliveTimeout(实际关闭点 ~3s 含宽限;设太小会被 agent 的 buffer 逻辑直接销毁)
  const s2stats = { connections: 0 };
  const s2 = http.createServer((req, res) => res.end('ok'));
  s2.keepAliveTimeout = 2000;
  s2.on('connection', () => s2stats.connections++);
  await listen(s2, 18802);
  const agent4 = new Agent({ keepAlive: true, maxSockets: 1 });
  await request(18802, agent4);
  await sleep(100);
  const pooled = Object.values(agent4.freeSockets).flat().length;
  await sleep(3500);
  const pooledAfterClose = Object.values(agent4.freeSockets).flat().length;
  const lastResp = await request(18802, agent4);
  console.log(`[E4 空闲超时] 响应后池中空闲 socket=${pooled}, server 关闭后=${pooledAfterClose}, 下一请求 reusedSocket=${lastResp.reusedSocket}, 累计连接=${s2stats.connections}`);
  check('E4a keep-alive 响应后 socket 进入 freeSockets 池', pooled === 1, `pooled=${pooled}`);
  check('E4b server 关空闲连接后,池项被移除', pooledAfterClose === 0, `pooled=${pooledAfterClose}`);
  check('E4c 池空后下一请求重建 TCP 连接(累计 2 条,不复用)', s2stats.connections === 2 && !lastResp.reusedSocket, `conns=${s2stats.connections} reused=${lastResp.reusedSocket}`);
  agent4.destroy();
  s2.close();

  console.log('─'.repeat(56));
  for (const line of checks) console.log(line);
  console.log(`HTTP-Agent连接复用自检: ${pass} PASS / ${fail} FAIL`);
  process.exitCode = fail ? 1 : 0;
}
main().catch((e: unknown) => { console.error(e); process.exit(1); });

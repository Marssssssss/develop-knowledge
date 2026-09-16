// main.js — HTTP Agent 连接池与 keep-alive 复用自检(真实本地 http server):
// ① keepAlive Agent:5 个串行请求复用 1 条 TCP 连接(reusedSocket)
// ② 无 keepAlive:每请求新建连接;globalAgent 在 Node≥19 默认 keepAlive=true
// ③ maxSockets=1 时并发请求排队串行化(对比 maxSockets=4)
// ④ server keepAliveTimeout 到期关空闲连接 → 连接池移除 → 下一请求重连。
// 运行: node main.js(约 3 秒)
'use strict';
const http = require('node:http');

let pass = 0, fail = 0;
const checks = [];
function check(label, cond, detail) {
  if (cond) { pass++; checks.push(`PASS ${label} (${detail})`); }
  else { fail++; checks.push(`FAIL ${label} (${detail})`); }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// 主测试 server:统计 TCP 连接数与 remotePort 集合;可选响应延迟
function makeServer(handler) {
  const stats = { connections: 0, ports: new Set() };
  const server = http.createServer(handler);
  server.on('connection', (sock) => { stats.connections++; stats.ports.add(sock.remotePort); });
  return { server, stats };
}
function listen(server, port) {
  return new Promise((res) => server.listen(port, '127.0.0.1', () => res()));
}
function request(port, agent, path = '/') {
  return new Promise((resolve, reject) => {
    const req = http.get({ host: '127.0.0.1', port, path, agent }, (r) => {
      let s = '';
      r.on('data', (c) => { s += c; });
      r.on('end', () => resolve({ body: s, reusedSocket: req.reusedSocket }));
    });
    req.on('error', reject);
  });
}

async function main() {
  // ── E1/E2/E3 主 server ──
  const { server, stats } = makeServer((req, res) => {
    if (req.url.startsWith('/slow')) setTimeout(() => res.end('slow'), 60);
    else res.end('ok');
  });
  await listen(server, 18801);

  // E1: keepAlive + maxSockets=1 → 5 个串行请求 1 条连接
  const ka = new http.Agent({ keepAlive: true, maxSockets: 1 });
  const reuseFlags = [];
  for (let i = 0; i < 5; i++) reuseFlags.push((await request(18801, ka)).reusedSocket);
  const e1Conns = stats.connections;
  console.log(`[E1 keepAlive] 5 串行请求: TCP连接=${e1Conns}, remotePort 数=${stats.ports.size}, reusedSocket=${reuseFlags.map(Number).join('')}`);
  check('E1a 5 个串行请求只建 1 条 TCP 连接', e1Conns === 1 && stats.ports.size === 1, `conns=${e1Conns}`);
  check('E1b 第 1 个请求新建 socket,后续 4 个全部复用', !reuseFlags[0] && reuseFlags.slice(1).every(Boolean), reuseFlags.map(Number).join(''));

  // E2: 无 keepAlive → 每请求一条连接;globalAgent(Node≥19 默认开 keepAlive)
  stats.connections = 0; stats.ports.clear();
  const noKa = new http.Agent({ keepAlive: false });
  for (let i = 0; i < 5; i++) await request(18801, noKa);
  const e2Conns = stats.connections;
  const major = Number(process.versions.node.split('.')[0]);
  console.log(`[E2 无keepAlive] 5 串行请求: TCP连接=${e2Conns} | globalAgent.keepAlive=${http.globalAgent.keepAlive}(Node ${major})`);
  check('E2a 无 keepAlive 时每请求新建连接(5 请求 = 5 连接)', e2Conns === 5, `conns=${e2Conns}`);
  check('E2b globalAgent 默认 keepAlive=true(Node 19 起的运行时实测)', major >= 19 && http.globalAgent.keepAlive === true, `Node ${major} → ${http.globalAgent.keepAlive}`);

  // E3: maxSockets=1 排队串行化 vs maxSockets=4 并行
  stats.connections = 0; stats.ports.clear();
  const a1 = new http.Agent({ keepAlive: false, maxSockets: 1 });
  let t0 = Date.now();
  await Promise.all(Array.from({ length: 4 }, () => request(18801, a1, '/slow')));
  const serialMs = Date.now() - t0;
  const a4 = new http.Agent({ keepAlive: false, maxSockets: 4 });
  t0 = Date.now();
  await Promise.all(Array.from({ length: 4 }, () => request(18801, a4, '/slow')));
  const parallelMs = Date.now() - t0;
  console.log(`[E3 maxSockets] 4 并发×60ms 延迟: maxSockets=1 → ${serialMs}ms(排队串行), maxSockets=4 → ${parallelMs}ms(并行)`);
  check('E3a maxSockets=1 时并发请求被串行化(总耗时 ≥ 3×60ms)', serialMs >= 3 * 60, `${serialMs}ms`);
  check('E3b maxSockets=4 时并行执行(显著快于串行)', serialMs > parallelMs * 1.8, `${serialMs} vs ${parallelMs}`);
  server.close();

  // ── E4: server keepAliveTimeout 关空闲连接 ──
  // 注意:keepAliveTimeout=2000 的实际关闭点实测约 3s(含宽限),等待须 ≥ 3.5s;
  // 且不能设得过小(如 100ms):server 的 keep-alive hint 减去 agent 的 1000ms buffer
  // 已是过去时刻,socket 会直接销毁而根本不进 freeSockets 池
  const s2stats = { connections: 0 };
  const s2 = http.createServer((req, res) => res.end('ok'));
  s2.keepAliveTimeout = 2000;
  s2.on('connection', () => s2stats.connections++);
  await listen(s2, 18802);
  const agent4 = new http.Agent({ keepAlive: true, maxSockets: 1 });
  await request(18802, agent4);
  await sleep(100); // 等响应后的 socket 归还连接池
  const pooled = Object.values(agent4.freeSockets).flat().length;
  await sleep(3500); // server 关闭空闲连接(实测 ~3s),agent 收到 'close' 移除池项
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
main().catch((e) => { console.error(e); process.exit(1); });

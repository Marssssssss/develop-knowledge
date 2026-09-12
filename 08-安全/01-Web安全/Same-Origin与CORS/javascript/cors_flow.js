// Same-Origin 与 CORS - 浏览器 fetch 流程模拟(Node.js)
// 演示浏览器内部对 CORS 的处理:预检 → 凭据检查 → 响应校验

// ───────────── 模拟浏览器 fetch 引擎 ─────────────

class CORSError extends Error {
  constructor(message, phase) {
    super(message);
    this.phase = phase;   // 'preflight' | 'response' | 'credential'
  }
}

class Browser {
  constructor() {
    this.origin = 'https://app.example.com';
    this.cookieJar = new Map();   // origin → cookies
  }

  async fetch(url, options = {}) {
    const target = new URL(url);
    const sameOrigin = this._isSameOrigin(target);

    // 1. 决定是否需要预检
    const needPreflight = this._needsPreflight(options);

    // 2. 预检阶段
    if (needPreflight) {
      const preflightResp = await this._server.dispatchPreflight(target, this.origin, options);
      if (!preflightResp.allow) {
        throw new CORSError(preflightResp.reason, 'preflight');
      }
      console.log(`    [preflight] ✅ ${preflightResp.reason}`);
    }

    // 3. 实际请求阶段
    const req = {
      method: options.method || 'GET',
      headers: { ...(options.headers || {}), Origin: this.origin },
      credentials: options.credentials || 'same-origin',
    };
    if (req.credentials === 'include' && target.origin !== this.origin) {
      // 跨源 + credentials: include,需要 SameSite=None + Secure 等
      // 此处仅演示服务端校验逻辑
    }

    // 4. 浏览器发出实际请求(模拟)
    const actualResp = await this._server.dispatchActual(target, req, sameOrigin);

    // 5. 浏览器检查响应
    if (!sameOrigin) {
      // CORS 校验
      const acao = actualResp.headers['access-control-allow-origin'];
      const acac = actualResp.headers['access-control-allow-credentials'];
      if (!acao) {
        throw new CORSError('No Access-Control-Allow-Origin header', 'response');
      }
      // ★ 凭据 + 通配符硬约束
      if (req.credentials === 'include' && acao === '*') {
        throw new CORSError(
          'Credentialed request + Access-Control-Allow-Origin: * → browser blocks (MDN hard rule)',
          'credential',
        );
      }
      // 凭据请求必须具体 origin
      if (req.credentials === 'include' && acao !== '*' && acao !== this.origin) {
        throw new CORSError(
          `Access-Control-Allow-Origin (${acao}) ≠ request Origin (${this.origin}) → block`,
          'credential',
        );
      }
      // 非凭据:* 可;具体需匹配
      if (acao !== '*' && acao !== this.origin) {
        throw new CORSError(
          `Access-Control-Allow-Origin (${acao}) ≠ request Origin (${this.origin}) → block`,
          'response',
        );
      }
      console.log(`    [response check] ✅ ACAO match (${acao})`);
    }

    return actualResp;
  }

  _isSameOrigin(target) {
    const cur = new URL(this.origin);
    return cur.protocol === target.protocol
        && cur.hostname === target.hostname
        && cur.port === target.port;
  }

  _needsPreflight(options) {
    const method = (options.method || 'GET').toUpperCase();
    const SAFE = new Set(['GET', 'HEAD', 'POST']);
    if (!SAFE.has(method)) return true;
    // 自定义头
    const safelist = new Set(['accept', 'accept-language', 'content-language', 'range', 'content-type']);
    const customHeaders = Object.keys(options.headers || {});
    if (customHeaders.some(h => !safelist.has(h.toLowerCase()))) return true;
    // Content-Type
    const ct = (options.headers || {})['Content-Type'] || '';
    const safelistCT = ['application/x-www-form-urlencoded', 'multipart/form-data', 'text/plain'];
    if (ct && !safelistCT.includes(ct.toLowerCase())) return true;
    return false;
  }
}

// ───────────── 模拟服务端 ─────────────

class FakeServer {
  constructor() {
    this.allowOrigin = null;       // null = 根据 Origin 反射(高危!)
    this.allowCredentials = false;
    this.allowedMethods = new Set(['GET', 'POST', 'DELETE']);
    this.allowedHeaders = new Set(['Content-Type', 'X-Auth-Token']);
  }

  async dispatchPreflight(target, reqOrigin, options) {
    const method = (options.method || 'GET').toUpperCase();
    const customHeaders = Object.keys(options.headers || {});

    // 动态 origin 反射(漏洞场景!)
    if (this.allowOrigin === '*') {
      return {
        allow: true,
        reason: `preflight OK (ACAO=*)`,
        headers: {
          'Access-Control-Allow-Origin': '*',
          'Access-Control-Allow-Methods': [...this.allowedMethods].join(', '),
          'Access-Control-Allow-Headers': [...this.allowedHeaders].join(', '),
        },
      };
    }
    if (this.allowOrigin === null) {
      // ⚠️ 漏洞:反射 Origin
      return {
        allow: true,
        reason: `preflight OK (⚠️ Origin reflected: ${reqOrigin})`,
        headers: {
          'Access-Control-Allow-Origin': reqOrigin,
          'Access-Control-Allow-Credentials': 'true',
          'Vary': 'Origin',
          'Access-Control-Allow-Methods': [...this.allowedMethods].join(', '),
          'Access-Control-Allow-Headers': [...this.allowedHeaders].join(', '),
        },
      };
    }
    // 白名单
    if (reqOrigin !== this.allowOrigin) {
      return { allow: false, reason: `Origin ${reqOrigin} not in allowlist (${this.allowOrigin})` };
    }
    if (!this.allowedMethods.has(method)) {
      return { allow: false, reason: `Method ${method} not allowed` };
    }
    const badHeaders = customHeaders.filter(h => !this.allowedHeaders.has(h));
    if (badHeaders.length) {
      return { allow: false, reason: `Headers ${badHeaders} not in allowlist` };
    }
    return {
      allow: true,
      reason: `preflight OK (origin=${reqOrigin})`,
      headers: {
        'Access-Control-Allow-Origin': reqOrigin,
        'Vary': 'Origin',
        'Access-Control-Allow-Methods': [...this.allowedMethods].join(', '),
        'Access-Control-Allow-Headers': [...this.allowedHeaders].join(', '),
        'Access-Control-Max-Age': '86400',
      },
    };
  }

  async dispatchActual(target, req, sameOrigin) {
    // 漏洞配置 1:动态反射 + credentials:true
    if (this.allowOrigin === null) {
      return {
        status: 200,
        headers: {
          'Access-Control-Allow-Origin': req.headers.Origin,   // 反射!
          'Access-Control-Allow-Credentials': 'true',
          'Vary': 'Origin',
        },
        body: { ok: true, data: 'sensitive-data' },
      };
    }
    if (this.allowOrigin === '*') {
      return {
        status: 200,
        headers: {
          'Access-Control-Allow-Origin': '*',
        },
        body: { ok: true },
      };
    }
    return {
      status: 200,
      headers: {
        'Access-Control-Allow-Origin': this.allowOrigin,
        'Vary': 'Origin',
      },
      body: { ok: true },
    };
  }
}

// ───────────── Demo 1: 同源 vs 跨源 ─────────────

function demoSameOrigin() {
  console.log('─'.repeat(65));
  console.log('[Demo 1] 同源 vs 跨源请求');
  console.log('─'.repeat(65));
  const browser = new Browser();
  browser.origin = 'https://app.example.com';

  // 直接模拟同源 vs 跨源(无 CORS 头也能读)
  const tests = [
    ['https://app.example.com/api/data', 'https://app.example.com (同源)'],
    ['https://api.example.com/api/data', 'https://api.example.com (跨源 host)'],
    ['http://app.example.com/api/data',  'http://app.example.com (跨源 scheme)'],
    ['https://app.example.com:8080/api/data', 'https://app.example.com:8080 (跨源 port)'],
  ];
  for (const [url, label] of tests) {
    const target = new URL(url);
    const cur = new URL(browser.origin);
    const same = cur.protocol === target.protocol && cur.hostname === target.hostname
                  && cur.port === target.port;
    const marker = same ? '✅ 同源' : '❌ 跨源';
    console.log(`  ${marker}  ${label}`);
    console.log(`        ${same ? '可直接读响应' : '需 CORS 响应头授权'}`);
  }
  console.log();
}

function demoSimpleVsPreflight() {
  console.log('─'.repeat(65));
  console.log('[Demo 2] 触发预检的 4 类场景');
  console.log('─'.repeat(65));

  const browser = new Browser();
  const cases = [
    { method: 'GET',  headers: {},                              label: 'GET 无自定义头 → SIMPLE' },
    { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, label: 'POST 表单 → SIMPLE' },
    { method: 'POST', headers: { 'Content-Type': 'application/json' },                   label: 'POST JSON → PREFLIGHT' },
    { method: 'POST', headers: { 'X-Auth-Token': 'abc' },                                 label: 'POST + 自定义头 → PREFLIGHT' },
    { method: 'DELETE', headers: {},                                                      label: 'DELETE 方法 → PREFLIGHT' },
    { method: 'POST', headers: { 'Content-Type': 'multipart/form-data' },                label: 'POST multipart → SIMPLE' },
  ];
  for (const c of cases) {
    const need = browser._needsPreflight(c);
    const marker = need ? '🔄 PREFLIGHT' : '✈️ SIMPLE  ';
    console.log(`  ${marker}  ${c.label}`);
  }
  console.log();
}

// ───────────── Demo 3 + 4: 漏洞 + 合法配置(精简合并) ─────────────

async function demoReflectAndFix() {
  console.log('─'.repeat(65));
  console.log('[Demo 3+4] ⚠️ 漏洞:Origin 反射 + ✅ 修复:具体 origin 白名单');
  console.log('─'.repeat(65));

  const browser = new Browser();
  browser.origin = 'https://app.example.com';
  const server = new FakeServer();
  browser._server = server;

  const runFetch = async (label, serverCfg, evilOrigin) => {
    Object.assign(server, serverCfg);
    if (evilOrigin) browser.origin = evilOrigin;
    else browser.origin = 'https://app.example.com';
    console.log(`  ${label}`);
    try {
      const resp = await browser.fetch('https://app.example.com/api/me',
        { method: 'GET', credentials: 'include' });
      console.log(`    ✅ 通过:status=${resp.status}`);
    } catch (e) {
      console.log(`    🛑 ${e.message}`);
    }
  };

  // 漏洞:反射任意 Origin + credentials → 攻击者可读
  await runFetch('[漏洞] ACAO=反射 origin + ACAC=true', { allowOrigin: null }, 'https://evil.com');
  await runFetch('[漏洞] ACAO=* + ACAC=true(MDN 硬约束)', { allowOrigin: '*', allowCredentials: true });
  // 修复:白名单 + 具体 origin
  await runFetch('[修复] 白名单具体 origin + ACAC=true', { allowOrigin: 'https://app.example.com', allowCredentials: true });
  await runFetch('[修复] 白名单 + evil.com 跨源', { allowOrigin: 'https://app.example.com', allowCredentials: true }, 'https://evil.com');
  console.log();
  console.log('  ★ MDN 硬约束:凭据请求 ACAO=* 永远被拒绝(浏览器层面)');
  console.log();
}

// ───────────── Demo 5: Vary: Origin 防缓存串味 ─────────────

function demoVaryOrigin() {
  console.log('─'.repeat(65));
  console.log('[Demo 5] Vary: Origin 防 CDN 缓存串味');
  console.log('─'.repeat(65));
  console.log('  请求 1: app-a.com GET /api/data');
  console.log('    响应: ACAO: https://app-a.com + Vary: Origin');
  console.log('    CDN 缓存键:(url=/api/data, Origin=app-a.com)');
  console.log('  请求 2: app-b.com GET /api/data → cache MISS → 重新生成 ACAO');
  console.log('  ❌ 不写 Vary:Origin → CDN 缓存串味 → CORS 错误');
  console.log();
}

async function main() {
  console.log('='.repeat(65));
  console.log('Same-Origin Policy 与 CORS — 浏览器 fetch 流程模拟');
  console.log('='.repeat(65));
  console.log();
  demoSameOrigin();
  demoSimpleVsPreflight();
  await demoReflectAndFix();
  demoVaryOrigin();
}

main().catch(console.error);
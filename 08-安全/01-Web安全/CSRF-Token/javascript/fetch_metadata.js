// CSRF Token - Fetch Metadata / Origin 校验策略(Node.js 演示)
// 与 Python 版互补:本文件侧重浏览器→服务端的 HTTP 请求头判定。

const crypto = require('crypto');

// ─────────────── Fetch Metadata 决策(OWASP 推荐) ───────────────

function fetchMetadataDecision(method, secFetchSite, secFetchMode, secFetchDest) {
  const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

  // 例外:顶级导航 + GET(用户主动操作)
  if (secFetchMode === 'navigate' && method === 'GET'
      && secFetchDest !== 'object' && secFetchDest !== 'embed') {
    return { allow: true, reason: 'top-level GET navigation (allowed)' };
  }

  if (secFetchSite === 'same-origin') {
    return { allow: true, reason: 'same-origin → directly allow' };
  }
  if (secFetchSite === 'cross-site' && !SAFE_METHODS.has(method)) {
    return { allow: false, reason: 'cross-site + state-changing method → block (primary rule)' };
  }
  if (secFetchSite === 'same-site' && !SAFE_METHODS.has(method)) {
    return { allow: false, reason: 'same-site + state-changing method → block by default (require subdomain whitelist)' };
  }
  if (secFetchSite === 'none') {
    if (SAFE_METHODS.has(method)) {
      return { allow: true, reason: "Sec-Fetch-Site: none + safe method → allow (user typed/bookmark)" };
    }
    return { allow: false, reason: 'Sec-Fetch-Site: none + state-changing method → block' };
  }
  return { allow: false, reason: 'unknown Sec-Fetch-Site → fail-closed' };
}

function demoFetchMetadata() {
  console.log('─'.repeat(65));
  console.log('[Demo 1] Fetch Metadata Headers 决策(OWASP 主策略)');
  console.log('─'.repeat(65));

  const cases = [
    { method: 'POST',   site: 'cross-site', mode: 'cors',       dest: 'empty',    label: 'CSRF attack: fetch POST' },
    { method: 'POST',   site: 'same-origin',mode: 'cors',       dest: 'empty',    label: 'legit: same-origin fetch' },
    { method: 'POST',   site: 'same-site',  mode: 'cors',       dest: 'empty',    label: 'sibling subdomain (default block)' },
    { method: 'GET',    site: 'cross-site', mode: 'navigate',   dest: 'document', label: 'external link click GET' },
    { method: 'GET',    site: 'cross-site', mode: 'no-cors',    dest: 'image',    label: 'evil site <img> tag' },
    { method: 'DELETE', site: 'cross-site', mode: 'cors',       dest: 'empty',    label: 'CSRF DELETE' },
  ];

  for (const c of cases) {
    const result = fetchMetadataDecision(c.method, c.site, c.mode, c.dest);
    const marker = result.allow ? '✅ ALLOW' : '🛑 BLOCK';
    console.log(`  ${marker}  ${c.method.padEnd(6)} site=${c.site.padEnd(11)} mode=${c.mode.padEnd(9)} dest=${c.dest.padEnd(8)} (${c.label})`);
    console.log(`        ${result.reason}`);
  }
  console.log();
  console.log('  ⚠️  Hard requirement:必须配 Origin/Referer 兜底(legacy browsers 不发 Sec-Fetch-*)');
  console.log();
}

// ─────────────── Origin 校验(精确字符串匹配) ───────────────

function verifyOrigin(originHeader, refererHeader, allowedOrigins) {
  if (originHeader) {
    if (allowedOrigins.includes(originHeader)) {
      return { allow: true, reason: 'Origin in allowlist' };
    }
    return { allow: false, reason: `Origin not in allowlist (origin=${JSON.stringify(originHeader)})` };
  }
  if (refererHeader) {
    try {
      const url = new URL(refererHeader);
      const refOrigin = `${url.protocol}//${url.host}/`;   // 尾 / 重要
      if (allowedOrigins.includes(refOrigin)) {
        return { allow: true, reason: 'Referer origin in allowlist' };
      }
      return { allow: false, reason: `Referer not in allowlist (ref=${refOrigin})` };
    } catch (e) {
      return { allow: false, reason: `Referer parse error: ${e.message}` };
    }
  }
  return { allow: false, reason: 'both Origin and Referer missing → fail-closed' };
}

function demoOriginVerify() {
  console.log('─'.repeat(65));
  console.log('[Demo 2] Origin/Referer 兜底校验');
  console.log('─'.repeat(65));
  const allowed = ['https://app.example.com/'];
  const cases = [
    { origin: 'https://app.example.com',                 referer: null,                                label: 'legit POST' },
    { origin: 'https://evil.com',                        referer: null,                                label: 'CSRF attack' },
    { origin: null,                                      referer: 'https://app.example.com/page',       label: 'Referer fallback legit' },
    { origin: null,                                      referer: 'https://app.example.com.attacker.com/', label: 'prefix bypass attempt' },
    { origin: 'https://app.example.com.attacker.com',   referer: null,                                label: 'Origin prefix bypass' },
    { origin: null,                                      referer: null,                                label: 'privacy browser (both missing)' },
  ];
  for (const c of cases) {
    const result = verifyOrigin(c.origin, c.referer, allowed);
    const marker = result.allow ? '✅ ALLOW' : '🛑 BLOCK';
    console.log(`  ${marker}  ${c.label}`);
    console.log(`        ${result.reason}`);
  }
  console.log();
  console.log('  必须用 === 精确匹配(尾 /),不能 startsWith/endsWith');
  console.log('  → example.com.attacker.com 不能通过 example.com 匹配');
  console.log();
}

// ─────────────── SameSite 决策 ───────────────

function samesiteDecision(samesite, method, isTopLevel) {
  const SAFE = new Set(['GET', 'HEAD', 'OPTIONS']);
  if (samesite === 'Strict') {
    return { send: false, reason: 'Strict: no cross-site cookie (even top-level nav)' };
  }
  if (samesite === 'Lax') {
    if (SAFE.has(method) && isTopLevel) {
      return { send: true, reason: 'Lax: top-level GET/HEAD/OPTIONS → send' };
    }
    if (SAFE.has(method)) {
      return { send: false, reason: 'Lax: cross-site GET non-top-level (iframe/img) → block' };
    }
    return { send: false, reason: 'Lax: cross-site POST/PUT/DELETE → block' };
  }
  if (samesite === 'None') {
    return { send: true, reason: 'None: any cross-site (requires Secure)' };
  }
  return { send: true, reason: 'unset → browser default (Chrome=Lax, Firefox=Lax, Safari=None)' };
}

function demoSamesite() {
  console.log('─'.repeat(65));
  console.log('[Demo 3] SameSite 跨站 cookie 发送判定');
  console.log('─'.repeat(65));
  const cases = [
    { attr: 'Strict', method: 'GET',  top: true,  ctx: 'external link click (top nav)' },
    { attr: 'Strict', method: 'POST', top: true,  ctx: 'cross-site form POST' },
    { attr: 'Lax',    method: 'GET',  top: true,  ctx: 'external link click' },
    { attr: 'Lax',    method: 'GET',  top: false, ctx: 'evil iframe/img load' },
    { attr: 'Lax',    method: 'POST', top: true,  ctx: 'cross-site form POST' },
    { attr: 'Lax',    method: 'POST', top: false, ctx: 'fetch() cross-site POST' },
    { attr: 'None',   method: 'POST', top: false, ctx: 'fetch() cross-site POST (Secure=true)' },
  ];
  for (const c of cases) {
    const r = samesiteDecision(c.attr, c.method, c.top);
    const marker = r.send ? '📤 SEND' : '🚫 NO-SEND';
    console.log(`  ${marker}  SameSite=${c.attr.padEnd(7)} ${c.method.padEnd(5)} top=${String(c.top).padEnd(5)} (${c.ctx})`);
    console.log(`        ${r.reason}`);
  }
  console.log();
  console.log('  ⚠️  关键陷阱:Lax 不阻止 GET 状态变更!');
  console.log('  若 /admin/delete?id=123 走 GET,Lax 失效 → 必须配合 token 或改 POST');
  console.log();
}

// ─────────────── main ───────────────

function main() {
  console.log('='.repeat(65));
  console.log('CSRF 防御 — Fetch Metadata / Origin / SameSite (Node.js)');
  console.log('='.repeat(65));
  console.log();
  demoFetchMetadata();
  demoOriginVerify();
  demoSamesite();
}

main();
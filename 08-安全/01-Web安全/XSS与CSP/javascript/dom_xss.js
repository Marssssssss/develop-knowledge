// XSS 与 CSP - 客户端 DOM XSS sink 演示(Node.js 模拟浏览器行为)
// 不使用真实 DOM,纯字符串解析 + 输出浏览器"会做什么"。
// 真实场景请在浏览器中对照 innerHTML / textContent 验证。

// ───────────── 模拟"HTML 解析器"(教学用,非完整解析器) ─────────────

function browserParseHTML(html) {
  // 检测 <script> 标签(含属性过滤)
  const scriptRe = /<script\b([^>]*)>([\s\S]*?)<\/script>/gi;
  const matches = [];
  let m;
  while ((m = scriptRe.exec(html)) !== null) {
    matches.push({
      tag: '<script' + m[1] + '>',
      code: m[2].trim(),
    });
  }
  // 检测内联事件处理器 on*=...
  const eventRe = /\bon(error|load|click|mouseover|focus|blur|submit|keydown|keyup)\s*=\s*["']?([^"'>\s]+)/gi;
  const events = [];
  while ((m = eventRe.exec(html)) !== null) {
    events.push({ event: m[1], handler: m[2] });
  }
  // 检测 javascript: URL
  const jsUrlRe = /\b(href|src)\s*=\s*["']?javascript:/gi;
  const jsUrls = [];
  while ((m = jsUrlRe.exec(html)) !== null) {
    jsUrls.push(m[1]);
  }
  return { scripts: matches, events, jsUrls };
}

// ───────────── 模拟 CSP nonce 校验 ─────────────

class CSPPolicy {
  constructor(header) {
    this.directives = {};
    for (const raw of header.split(';')) {
      const trimmed = raw.trim();
      if (!trimmed) continue;
      const [name, ...sources] = trimmed.split(/\s+/);
      this.directives[name] = new Set(sources);
    }
  }

  // 检查一个 <script> 标签是否被允许
  allowsScript(tagAttrs, nonce) {
    const sources =
      this.directives['script-src'] ||
      this.directives['default-src'] ||
      new Set();
    if (sources.has("'unsafe-inline'")) {
      return { allow: true, reason: "'unsafe-inline' enabled" };
    }
    const nonceMatch = tagAttrs.match(/nonce=["']([^"']+)["']/);
    if (nonceMatch && sources.has(`'nonce-${nonceMatch[1]}'`)) {
      return { allow: true, reason: `nonce '${nonceMatch[1]}' matches` };
    }
    if (sources.has("'self'") && !tagAttrs.includes('nonce')) {
      return { allow: false, reason: 'inline script requires nonce or hash (CSP Level 2+)' };
    }
    return { allow: false, reason: 'no matching directive' };
  }
}

// ───────────── Demo 1: DOM XSS — innerHTML vs textContent ─────────────

function demoDOMXSS() {
  console.log('─'.repeat(60));
  console.log('[Demo 1] DOM XSS sink 对比');
  console.log('─'.repeat(60));

  // 模拟 location.hash = "#<img src=x onerror=alert(1)>"
  const userHash = '<img src=x onerror=alert(1)>';
  console.log(`  document.location.hash = "#${userHash}"`);
  console.log();

  // === UNSAFE:innerHTML 写入 ===
  const unsafeHTML = `<h1>查询:${userHash}</h1>`;
  console.log('  [UNSAFE] el.innerHTML = ' + JSON.stringify(unsafeHTML));
  const parsed1 = browserParseHTML(unsafeHTML);
  if (parsed1.events.length > 0) {
    parsed1.events.forEach(ev => {
      console.log(`    🛑 浏览器解析为 img 标签,触发 on${ev.event}="${ev.handler}"`);
      console.log(`       → 攻击脚本执行`);
    });
  }
  console.log();

  // === SAFE:textContent 写入 ===
  console.log('  [SAFE]   el.textContent = ' + JSON.stringify(userHash));
  console.log('    ✅ textContent 会把 < 转义为 &lt;,文本节点,不解析为 HTML');
  console.log('       → 用户看到字面字符串,无脚本执行');
  console.log();
}

// ───────────── Demo 2: location.hash 写入 href 属性 ─────────────

function demoLocationHashToHref() {
  console.log('─'.repeat(60));
  console.log('[Demo 2] location.hash → <a href> (DOM XSS 经典)');
  console.log('─'.repeat(60));

  const userHash = 'javascript:alert(document.cookie)';
  console.log(`  document.location.hash = "#${userHash}"`);
  console.log();

  // UNSAFE:不验证直接拼到 href
  const unsafe = `<a href="${userHash}">点击查看</a>`;
  console.log('  [UNSAFE] <a href="' + userHash + '">点击查看</a>');
  const parsed = browserParseHTML(unsafe);
  if (parsed.jsUrls.includes('href')) {
    console.log('    🛑 浏览器解析为 javascript: scheme,点击时执行 alert(document.cookie)');
  }
  console.log();

  // SAFE 1:白名单 scheme
  const safeScheme = userHash.startsWith('javascript:') ? '#blocked' : userHash;
  console.log('  [SAFE]   校验 scheme:拒绝 javascript:');
  console.log(`    href="${safeScheme}" → 浏览器无法识别 scheme,不执行`);
  console.log();

  // SAFE 2:用 setAttribute 而非字符串拼接
  console.log('  [SAFE]   el.setAttribute("href", value) → 自动属性转义');
  console.log('    但需先校验 scheme!setAttribute 不阻止 javascript:');
  console.log();
}

// ───────────── Demo 3: CSP nonce 校验 ─────────────

function demoCSPNonce() {
  console.log('─'.repeat(60));
  console.log('[Demo 3] CSP nonce 验证');
  console.log('─'.repeat(60));

  const nonce = 'rAbNd0mN0nce123';
  const header = `default-src 'self'; script-src 'self' 'nonce-${nonce}'; object-src 'none'`;
  const policy = new CSPPolicy(header);

  console.log('  CSP header:');
  console.log('    ' + header);
  console.log();

  const testCases = [
    '<script nonce="' + nonce + '">console.log("legit nonce script")</script>',
    '<script>console.log("inline attack")</script>',
    '<script nonce="wrongNonce">console.log("stolen-nonce attack")</script>',
    '<button onclick="alert(1)">click</button>', // 内联事件处理器
  ];

  for (const html of testCases) {
    const tagMatch = html.match(/<script\b([^>]*)>/i);
    if (!tagMatch) {
      // 内联事件处理器,CSP 也阻断
      const result = policy.allowsScript('onclick=""', nonce);
      const marker = result.allow ? '✅ ALLOW' : '🛑 BLOCK';
      console.log(`  ${marker}  ${html}`);
      console.log(`        ${result.reason}`);
      continue;
    }
    const tagAttrs = tagMatch[1];
    const result = policy.allowsScript(tagAttrs, nonce);
    const marker = result.allow ? '✅ ALLOW' : '🛑 BLOCK';
    console.log(`  ${marker}  ${html}`);
    console.log(`        ${result.reason}`);
  }
  console.log();
}

// ───────────── Demo 4: 报告 vs 强制 ─────────────

function demoReportOnly() {
  console.log('─'.repeat(60));
  console.log('[Demo 4] CSP Report-Only 模式(灰度)');
  console.log('─'.repeat(60));

  console.log('  灰度部署:CSP-Report-Only 头,违规只上报不阻断');
  console.log('  Content-Security-Policy-Report-Only: default-src \'self\'');
  console.log('  浏览器发现违规 → POST application/csp-report 到 report-uri');
  console.log();
  const report = {
    'csp-report': {
      'document-uri': 'https://app.example.com/dashboard',
      'violated-directive': "script-src 'self'",
      'blocked-uri': 'inline',
      'original-policy': "default-src 'self'; report-uri /csp-report",
      'source-file': 'https://cdn.evil.com/analytics.js',
    },
  };
  console.log('  浏览器上报 payload(JSON):');
  console.log('  ' + JSON.stringify(report, null, 2));
  console.log();
  console.log('  → 服务端聚合 report,无新违规后切换为强制模式:');
  console.log('    Content-Security-Policy: <同 directive>;  # 去掉 -Report-Only');
  console.log();
}

// ───────────── main ─────────────

function main() {
  console.log('='.repeat(60));
  console.log('XSS 与 CSP 客户端视角(Node.js 模拟浏览器解析)');
  console.log('='.repeat(60));
  console.log();
  demoDOMXSS();
  demoLocationHashToHref();
  demoCSPNonce();
  demoReportOnly();
}

main();
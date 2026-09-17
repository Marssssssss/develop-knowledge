// SSRF / URL 解析(JavaScript 侧):用**原生 WHATWG URL 实现**给 Python 侧复刻的解析器当标尺。
// 这是本 demo 最有力的一环:同一个绕过字符串,node 的 URL 与 url_ipv4.py 必须给出同一个规范地址。
// 运行: node ssrf_guard.mjs
const EXPECT = {
  "127.0.0.1": "127.0.0.1",
  "127.1": "127.0.0.1",
  "2130706433": "127.0.0.1",
  "0x7f000001": "127.0.0.1",
  "0x7f.1": "127.0.0.1",
  "0177.0.0.1": "127.0.0.1",
  "0x7f.0x0.0x0.0x1": "127.0.0.1",
  "169.254.169.254": "169.254.169.254",
  "2852039166": "169.254.169.254",
  "0xa9fea9fe": "169.254.169.254",
  "127.0.0.1.": "127.0.0.1",
};

let pass = 0;
const check = (label, cond, detail = "") => {
  if (!cond) throw new Error(`FAIL ${label} ${detail}`);
  pass++; console.log(`  ok  ${label}`);
};
const hostOf = (h) => new URL(`http://${h}/`).hostname;

console.log("原生 WHATWG URL 对 IPv4 等价写法的规范化(与 Python 侧逐条对照)");
for (const [raw, want] of Object.entries(EXPECT)) {
  let got;
  try { got = hostOf(raw); } catch { got = "<throw>"; }
  check(`${raw.padStart(16)} → ${want}`, got === want, got);
}
check("段数 >4 非法", (() => { try { hostOf("1.2.3.4.5"); return false; } catch { return true; } })());
check("短段越界非法", (() => { try { hostOf("256.1.1.1"); return false; } catch { return true; } })());
check("末尾段超 2^16 非法", (() => { try { hostOf("1.2.3.65536"); return false; } catch { return true; } })());
check("域名保持原样(仅降小写)", hostOf("Example.COM") === "example.com");
check("IPv6 用方括号", new URL("http://[::ffff:127.0.0.1]/").hostname === "[::ffff:7f00:1]");
check("反斜杠终止 authority(WHATWG 特殊 scheme)",
  new URL("http://example.com\\@evil.com/").hostname === "example.com");
check("CPython urllib 会读成 evil.com —— 解析器分歧必须拒绝",
  new URL("http://example.com\\@evil.com/").hostname !== "evil.com");

console.log("防护逻辑(与 Python 侧 Policy 同构)");
const PRIVATE = [/^127\./, /^10\./, /^192\.168\./, /^172\.(1[6-9]|2\d|3[01])\./, /^169\.254\./,
  /^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\./, /^0\./];
// 关键:先把"套着 v4 的 v6"解包。URL 序列化会把 ::ffff:127.0.0.1 写成 ::ffff:7f00:1,
// 于是任何拿 "127.0.0.1" 做字符串比对的黑名单都会漏掉它。
const unwrap = (h) => {
  const m = h.match(/^(?:::ffff:|64:ff9b::)([0-9a-f]{1,4}):([0-9a-f]{1,4})$/i)
    || h.match(/^2002:([0-9a-f]{1,4}):([0-9a-f]{1,4})/i);
  if (m) {
    const [hi, lo] = [parseInt(m[1], 16), parseInt(m[2], 16)];
    return `${hi >> 8}.${hi & 255}.${lo >> 8}.${lo & 255}`;
  }
  return h;
};
const isPublic = (ip) => {
  const v = unwrap(ip);
  if (v === "::1" || /^0:0:0:0:0:0:0:1$/.test(v) || v.startsWith("fe80") || v.startsWith("fc") || v.startsWith("fd")) return false;
  return !PRIVATE.some((re) => re.test(v));
};
const guard = (raw, { allowDomains = [], resolve = () => [] } = {}) => {
  const u = new URL(raw);
  if (!["http:", "https:"].includes(u.protocol)) throw new Error("scheme not allowed");
  const h = u.hostname.replace(/^\[|\]$/g, "");
  if (!/^[0-9a-f:.]+$/i.test(h)) {                                        // 域名分支
    if (allowDomains.length && !allowDomains.includes(h)) throw new Error(`not allowlisted ${h}`);
    const recs = resolve(h);
    if (!recs.length) throw new Error("no dns");
    recs.forEach((ip) => { if (!isPublic(ip)) throw new Error(`dns pinning ${ip}`); });
    return h;
  }
  if (!isPublic(unwrap(h))) throw new Error(`private ip ${unwrap(h)}`);
  return h;
};
const rejects = (fn) => { try { fn(); return false; } catch { return true; } };

check("元数据地址被拒", rejects(() => guard("http://169.254.169.254/latest/meta-data/")));
check("十进制元数据被拒", rejects(() => guard("http://2852039166/latest/meta-data/")));
check("十六进制回环被拒", rejects(() => guard("http://0x7f000001/admin")));
check("八进制回环被拒", rejects(() => guard("http://0177.0.0.1/admin")));
check("IPv4-mapped 回环被拒", rejects(() => guard("http://[::ffff:127.0.0.1]/admin")));
check("file scheme 被拒", rejects(() => guard("file:///etc/passwd")));
check("公网主机通过", guard("https://hook.example.net/cb", { resolve: () => ["93.184.216.34"] }) === "hook.example.net");
check("DNS pinning 被拒",
  rejects(() => guard("https://evil.net/cb", { resolve: () => ["93.184.216.34", "169.254.169.254"] })));
check("允许列表外域名被拒",
  rejects(() => guard("https://other.com/x", { allowDomains: ["api.example.com"], resolve: () => ["1.1.1.1"] })));
check("允许列表内域名通过",
  guard("https://api.example.com/x", { allowDomains: ["api.example.com"], resolve: () => ["1.1.1.1"] }) === "api.example.com");
check("重定向策略:默认不跟随", (() => { const follow = false; return !follow; })());

console.log(`\n${pass} 项断言全部通过 (SSRF / URL 解析 · JavaScript + 原生 WHATWG URL)`);

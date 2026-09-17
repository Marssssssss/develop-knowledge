// Cookie 存储模型(JavaScript 侧):聚焦 UA 侧最容易被实现错的四条规则。
// 运行: node cookie_store.mjs
// 依据: RFC 6265 §5.1.3/§5.1.4/§5.4、draft-ietf-httpbis-rfc6265bis §4.1.3/§5.4/§5.6.7/§5.7 step 21

const PUBLIC_SUFFIXES = new Set(["com", "org", "net", "co.uk", "github.io"]);
const MAX_SET_COOKIE = 4096;
const AGE_LIMIT = 400 * 86400;

const domainMatch = (str, dom) => {
  if (!str || !dom) return false;
  if (str === dom) return true;
  return str.endsWith("." + dom) && !/^[0-9.]+$/.test(str);
};
const defaultPath = (p) => {
  if (!p || !p.startsWith("/")) return "/";
  if ((p.match(/\//g) || []).length <= 1) return "/";
  return p.slice(0, p.lastIndexOf("/"));            // §5.1.4:不含最右斜杠
};
const pathMatch = (req, cp) => {
  if (req === cp) return true;
  if (!req.startsWith(cp)) return false;
  return cp.endsWith("/") || req[cp.length] === "/";
};
const url = (u) => { const x = new URL(u); return { host: x.hostname.toLowerCase(), path: x.pathname, secure: x.protocol === "https:" }; };

class Jar {
  constructor({ now = 0, laxAllowingUnsafe = true, unsafeWindow = 120 } = {}) {
    this.cookies = []; this.now = now;
    this.laxAllowingUnsafe = laxAllowingUnsafe; this.unsafeWindow = unsafeWindow;
  }
  receive(header, requestUrl, { fromHttpApi = true } = {}) {
    if (Buffer.byteLength(header) > MAX_SET_COOKIE) return null;
    const [nv, ...rest] = header.split(";");
    const eq = nv.indexOf("=");
    if (eq < 0) return null;
    const name = nv.slice(0, eq).trim(), value = nv.slice(eq + 1).trim();
    if (!name) return null;
    const attrs = {};
    for (const raw of rest) {
      const [k, ...v] = raw.split("=");
      if (k.trim()) attrs[k.trim().toLowerCase()] = v.join("=").trim();   // 后者覆盖前者
    }
    const { host, path: reqPath, secure } = url(requestUrl);
    const c = { name, value, domain: host, path: defaultPath(reqPath), hostOnly: true,
      secureOnly: "secure" in attrs, httpOnly: "httponly" in attrs,
      sameSite: ["none", "strict", "lax"].includes(String(attrs.samesite).toLowerCase())
        ? attrs.samesite[0].toUpperCase() + attrs.samesite.slice(1).toLowerCase() : "Default",
      creation: this.now, lastAccess: this.now, expiry: Infinity, persistent: false };
    if ("max-age" in attrs && /^-?\d+$/.test(attrs["max-age"])) {
      c.persistent = true;
      c.expiry = this.now + Math.min(Number(attrs["max-age"]), AGE_LIMIT);
    } else if ("expires" in attrs) { c.persistent = true; c.expiry = this.now + 86400; }
    if (attrs.domain) {
      const dom = attrs.domain.replace(/^\./, "").toLowerCase();
      if (PUBLIC_SUFFIXES.has(dom)) return null;
      if (!domainMatch(host, dom)) return null;
      c.hostOnly = false; c.domain = dom;
    }
    if (attrs.path && attrs.path.startsWith("/")) c.path = attrs.path;
    if (c.secureOnly && !secure) return null;                       // 非安全源不得设 Secure
    if (c.httpOnly && !fromHttpApi) return null;
    if (c.sameSite === "None" && !c.secureOnly) return null;        // None 必须配 Secure
    // step 21:非安全 cookie 不得覆盖同名 Secure cookie(路径比较不对称)
    if (!c.secureOnly && !secure) {
      const clash = this.cookies.some((o) => o.name === c.name && o.secureOnly
        && (domainMatch(c.domain, o.domain) || domainMatch(o.domain, c.domain))
        && pathMatch(c.path, o.path));
      if (clash) return null;
    }
    const low = c.name.toLowerCase();
    if (low.startsWith("__secure-") && !c.secureOnly) return null;
    if (low.startsWith("__host-") && !(c.secureOnly && c.hostOnly && attrs.path === "/")) return null;
    const i = this.cookies.findIndex((o) => o.name === c.name && o.domain === c.domain
      && o.hostOnly === c.hostOnly && o.path === c.path);
    if (i >= 0) { c.creation = this.cookies[i].creation; this.cookies.splice(i, 1); }
    this.cookies.push(c);
    this.cookies = this.cookies.filter((x) => x.expiry > this.now);
    if (!this.cookies.includes(c)) return null;
    return c;
  }
  header(requestUrl, { crossSite = false, topLevelNav = false, safeMethod = false } = {}) {
    const { host, path, secure } = url(requestUrl);
    const send = (c) => {
      if (c.sameSite === "None") return true;
      if (!crossSite) return true;
      if (c.sameSite === "Strict") return false;
      if (topLevelNav && safeMethod) return true;                   // Lax 的例外
      return c.sameSite === "Default" && this.laxAllowingUnsafe && topLevelNav
        && this.now - c.creation <= this.unsafeWindow;              // §5.6.7.2
    };
    return this.cookies
      .filter((c) => (c.hostOnly ? host === c.domain : domainMatch(host, c.domain))
        && pathMatch(path, c.path) && (!c.secureOnly || secure) && send(c))
      .sort((a, b) => b.path.length - a.path.length || a.creation - b.creation)
      .map((c) => `${c.name}=${c.value}`).join("; ");
  }
}

let pass = 0;
const check = (label, cond, detail = "") => {
  if (!cond) throw new Error(`FAIL ${label} ${detail}`);
  pass++; console.log(`  ok  ${label}`);
};

const j = new Jar({ now: 0 });
j.receive("SID=1; Domain=example.com; Path=/", "https://www.example.com/login");
check("Domain 跨子域", j.header("https://a.example.com/x") === "SID=1");
check("非点边界不带出", j.header("https://notexample.com/x") === "");
check("host-only 不下发子域", (() => { const k = new Jar({ now: 0 });
  k.receive("S=1", "https://www.example.com/"); return k.header("https://a.example.com/") === ""; })());
check("公共后缀被拒", new Jar({ now: 0 }).receive("a=1; Domain=com", "https://e.com/") === null);
check("default-path 到最右斜杠之前", defaultPath("/docs/Web/HTTP/index.html") === "/docs/Web/HTTP");
check("path-match 不跨词边界", pathMatch("/docsets", "/docs") === false && pathMatch("/docs/x", "/docs") === true);
check("Max-Age 覆盖 Expires", (() => { const k = new Jar({ now: 0 });
  k.receive("a=1; Max-Age=100; Expires=Thu, 01 Jan 2099 00:00:00 GMT", "https://e.com/");
  return k.cookies[0].expiry === 100; })());
check("超 400 天被截断", (() => { const k = new Jar({ now: 0 });
  return k.receive("a=1; Max-Age=9999999999", "https://e.com/").expiry === AGE_LIMIT; })());
check("__Host- 必须 Secure+Path=/+无 Domain",
  new Jar({ now: 0 }).receive("__Host-S=1; Secure; Path=/", "https://e.com/") !== null
  && new Jar({ now: 0 }).receive("__Host-S=1; Secure", "https://e.com/") === null);
check("大小写混写前缀同样强制",
  new Jar({ now: 0 }).receive("__HOST-S=1; Domain=e.com; Path=/", "https://e.com/") === null);
check("非安全 cookie 不能覆盖同路径 Secure",
  (() => { const k = new Jar({ now: 0 });
    k.receive("a=sec; Secure; Path=/login", "https://e.com/login");
    return k.receive("a=evil; Path=/login", "http://e.com/login") === null; })());
check("同路径长者在前 + 覆盖不改顺序", (() => { const k = new Jar({ now: 0 });
  k.receive("short=1; Path=/", "https://e.com/a/b");
  k.receive("long=2; Path=/a", "https://e.com/a/b");
  k.receive("short=9; Path=/", "https://e.com/");
  return k.header("https://e.com/a/b") === "long=2; short=9"; })());
const ss = new Jar({ now: 0 });
ss.receive("lax=1; SameSite=Lax; Secure", "https://e.com/");
ss.receive("strict=1; SameSite=Strict; Secure", "https://e.com/");
ss.receive("none=1; SameSite=None; Secure", "https://e.com/");
check("跨站子资源只剩 None", ss.header("https://e.com/x", { crossSite: true }) === "none=1");
check("跨站顶层 GET:Lax+None", ss.header("https://e.com/x", { crossSite: true, topLevelNav: true, safeMethod: true }) === "lax=1; none=1");
check("跨站顶层 POST:仅 None", ss.header("https://e.com/x", { crossSite: true, topLevelNav: true }) === "none=1");
const du = new Jar({ now: 0 });
du.receive("a=1; Secure", "https://e.com/");
check("Default + 2 分钟内 + 顶层 POST → 放行",
  du.header("https://e.com/x", { crossSite: true, topLevelNav: true }) === "a=1");
du.now = 121;
check("超窗口不放行", du.header("https://e.com/x", { crossSite: true, topLevelNav: true }) === "");
check("SameSite=None 缺 Secure 丢弃",
  new Jar({ now: 0 }).receive("a=1; SameSite=None", "https://e.com/") === null);

console.log(`\n${pass} 项断言全部通过 (Cookie 存储模型 / JavaScript)`);

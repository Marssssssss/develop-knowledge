// OAuth 2.0 + PKCE(JavaScript 侧):用 Web Crypto 复现 RFC 7636 §4.2 的 S256 变换,
// 并对照 Python 侧同一向量,证明"跨语言实现只要忠于规范就得到同一字节"。
// 运行: node oauth_pkce.mjs
import { createHash, timingSafeEqual } from "node:crypto";

const b64url = (buf) =>
  Buffer.from(buf).toString("base64").replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");

const s256 = (verifier) => b64url(createHash("sha256").update(verifier, "ascii").digest());

// RFC 7636 §4.1: 43*128unreserved
const UNRESERVED = /^[A-Za-z0-9\-._~]{43,128}$/;

const safeEq = (a, b) => {
  const ba = Buffer.from(a, "ascii"), bb = Buffer.from(b, "ascii");
  return ba.length === bb.length && timingSafeEqual(ba, bb);
};

let pass = 0;
const check = (label, cond, detail = "") => {
  if (!cond) throw new Error(`FAIL ${label} ${detail}`);
  pass++;
  console.log(`  ok  ${label}`);
};
const throws = (fn) => { try { fn(); return null; } catch (e) { return e.message.split(":")[0]; } };

// --- RFC 7636 附录 B 向量(与 Python 侧同一组字节) ---
const V = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk";
const seed = [116, 24, 223, 180, 151, 153, 224, 37, 79, 250, 96, 125, 216, 173, 187, 186,
  22, 212, 37, 77, 105, 214, 191, 240, 91, 88, 5, 88, 83, 132, 141, 121];
check("32 字节随机序列 → 43 字符 verifier", b64url(Buffer.from(seed)) === V, b64url(Buffer.from(seed)));
check("S256 challenge", s256(V) === "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM", s256(V));
check("Web Crypto 与 node:crypto 一致",
  s256(V) === b64url(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(V))));
check("verifier 语法窗口", UNRESERVED.test(V) && !UNRESERVED.test("a".repeat(42))
  && !UNRESERVED.test("a".repeat(129)) && !UNRESERVED.test(V.slice(0, 42) + "!"));

// --- 服务端校验(RFC 7636 §4.6)---
const verify = (verifier, challenge, method) => {
  if (!UNRESERVED.test(verifier)) throw new Error("invalid_grant: syntax");
  const computed = method === "plain" ? verifier : s256(verifier);
  if (!safeEq(computed, challenge)) throw new Error("invalid_grant: mismatch");
  return true;
};
check("正确 verifier 通过", verify(V, s256(V), "S256"));
check("错配 → invalid_grant", throws(() => verify(V.slice(0, 42) + "X", s256(V), "S256")) === "invalid_grant");
check("长度不足 → invalid_grant", throws(() => verify("short", s256("short"), "S256")) === "invalid_grant");

// --- 授权请求 URL 构造 + redirect_uri 精确匹配(RFC 9700 §2.1/§2.6)---
const REGISTERED = ["https://client.example.com/cb", "http://127.0.0.1:8123/cb"];
const authorize = ({ clientId, redirectUri, state, verifier, method = "S256" }) => {
  if (!REGISTERED.some((u) => u === redirectUri))
    throw new Error("invalid_request: redirect_uri not registered exactly");
  if (!redirectUri.startsWith("https://") && !redirectUri.startsWith("http://127.0.0.1"))
    throw new Error("invalid_request: insecure redirect_uri");
  if (!verifier) throw new Error("invalid_request: code challenge required");
  const u = new URL(redirectUri);          // WHATWG URL:端口默认值会被规范化掉
  u.searchParams.set("code", "splxlOBeZQQYbYS6WxSbIA");
  u.searchParams.set("state", state);
  u.searchParams.set("iss", "https://as.example");
  return { url: u.toString(), challenge: s256(verifier), method };
};

const req = authorize({ clientId: "s6BhdRkqt3", redirectUri: REGISTERED[0], state: "xyz", verifier: V });
check("挑战用 S256", req.challenge === "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM" && req.method === "S256");
check("重定向带 code/state/iss", req.url.includes("code=splxlOBeZQQYbYS6WxSbIA")
  && req.url.includes("state=xyz") && req.url.includes("iss=https%3A%2F%2Fas.example"));
check("verifier 不出现在授权请求里", !req.url.includes(V));
check("未注册 URI → 拒绝",
  throws(() => authorize({ clientId: "c", redirectUri: "https://client.example.com/cb/", state: "s", verifier: V }))
  === "invalid_request");
check("大小写差异 → 拒绝",
  throws(() => authorize({ clientId: "c", redirectUri: "https://Client.example.com/cb", state: "s", verifier: V }))
  === "invalid_request");
check("http scheme → 拒绝",
  throws(() => authorize({ clientId: "c", redirectUri: "http://client.example.com/cb", state: "s", verifier: V }))
  === "invalid_request");

// --- 客户端回调:state 单次 + iss 比对(RFC 9700 §4.2.4/§4.4.2.1)---
const store = new Map();
const start = (state, issuer) => { store.set(state, { issuer, used: false }); };
const callback = (params) => {
  const s = store.get(params.state);
  if (!s || s.used) throw new Error("state_mismatch: CSRF / replay");
  s.used = true;
  if (params.iss && params.iss !== s.issuer) throw new Error("mix_up: issuer changed");
  return params.code;
};
start("st-1", "https://as.example");
check("首次回调通过", callback({ state: "st-1", iss: "https://as.example", code: "c1" }) === "c1");
check("state 复用 → 中止", throws(() => callback({ state: "st-1", code: "c1" })) === "state_mismatch");
start("st-2", "https://as.example");
check("iss 被改写 → mix_up 中止",
  throws(() => callback({ state: "st-2", iss: "https://evil.example", code: "c2" })) === "mix_up");

console.log(`\n${pass} 项断言全部通过 (OAuth 2.0 + PKCE / JavaScript)`);

// X-Frame-Options 判定(JavaScript 侧):复刻 HTML 标准 §7.7 的处理模型与官方结果表。
// 运行: node frame_policy.mjs
const VALID = new Set(["deny", "sameorigin", "allowall"]);

const splitValues = (values) => values.flatMap((v) => v.split(",").map((s) => s.trim()));

// 返回 [allowed, reason]
const checkXfo = (values, { isChild = true, ancestors = [], destination = "" } = {}) => {
  if (!isChild) return [true, "top-level"];                       // step 1
  const opts = new Set(splitValues(values).map((v) => v.toLowerCase()));
  if (opts.size > 1 && [...opts].some((v) => VALID.has(v))) return [false, "confused-multiple-values"];
  if (opts.size > 1) return [true, "all-invalid"];                // 多个非法值 = 没有这个头
  if (opts.size === 0) return [true, "absent"];
  const only = [...opts][0];
  if (only === "deny") return [false, "deny"];
  if (only === "sameorigin") {
    for (const origin of ancestors) if (origin !== destination) return [false, "sameorigin-ancestor-mismatch"];
    return [true, "sameorigin"];
  }
  return [true, "lone-invalid"];                                  // 孤立非法值(含 ALLOW-FROM/ALLOWALL 单值)
};

const parseCspPolicies = (headers) => {
  const out = [];
  for (const [name, disposition] of [["content-security-policy", "enforce"],
    ["content-security-policy-report-only", "report"]]) {
    for (const raw of headers[name] || []) {
      const directives = {};
      for (const part of raw.split(";")) {
        const t = part.trim().split(/\s+/).filter(Boolean);
        if (t.length) directives[t[0].toLowerCase()] = t.slice(1);
      }
      out.push({ disposition, directives });
    }
  }
  return out;
};

const ancestorsMatch = (sources, ancestor, self) => {
  if (!sources.length) return true;
  if (sources.length === 1 && sources[0].toLowerCase() === "'none'") return false;
  const [aScheme, aHost] = [ancestor.split("://")[0], (ancestor.split("://")[1] || "")];
  for (const src of sources) {
    const s = src.toLowerCase();
    if (s === "*") return true;
    if (s === "'self'") { if (ancestor === self) return true; continue; }
    if (s === "'none'") continue;
    const hasScheme = s.includes("://");
    const scheme = hasScheme ? s.split("://")[0] : self.split("://")[0];
    const host = (hasScheme ? s.split("://")[1] : s).split(":")[0];
    if (scheme !== aScheme) continue;
    if (host.startsWith("*.")) {
      const base = host.slice(2);
      if (aHost === base || aHost.endsWith("." + base)) return true;
    } else if (aHost === host) return true;
  }
  return false;
};

const decide = (headers, cand) => {
  for (const pol of parseCspPolicies(headers)) {
    if (pol.disposition !== "enforce") continue;
    if ("frame-ancestors" in pol.directives) {
      const srcs = pol.directives["frame-ancestors"];
      for (const origin of [...cand.ancestors].reverse())
        if (!ancestorsMatch(srcs, origin, cand.destination)) return [false, "csp:frame-ancestors"];
      return [true, "csp:frame-ancestors"];
    }
  }
  return checkXfo(headers["x-frame-options"] || [], cand);
};

let pass = 0;
const check = (label, cond, detail = "") => {
  if (!cond) throw new Error(`FAIL ${label} ${detail}`);
  pass++; console.log(`  ok  ${label}`);
};
const SELF = "https://bank.example", EVIL = "https://evil.example";

console.log("HTML 标准 §7.7 官方结果表");
const table = [
  [["SAMEORIGIN", "SAMEORIGIN"], true, "同源嵌入允许"],
  [["SAMEORIGIN", "DENY"], false, "含合法值且多值"],
  [["SAMEORIGIN", ""], false, "空串也算一个值"],
  [["SAMEORIGIN", "ALLOWALL"], false, "含合法值且多值"],
  [["SAMEORIGIN", "INVALID"], false, "含合法值且多值"],
  [["ALLOWALL", "INVALID"], false, "allowall 属『困惑』集合"],
  [["ALLOWALL", ""], false, "同上"],
  [["INVALID", "INVALID"], true, "去重后单值 → 等同没有"],
];
for (const [values, want, why] of table) {
  const got = checkXfo(values, { ancestors: [SELF], destination: SELF })[0];
  check(`${values.map((v) => v || "<空>").join(",")} → ${want ? "允许" : "拒绝"}(${why})`, got === want, got);
}
check("DENY 拒绝", checkXfo(["DENY"], { ancestors: [SELF], destination: SELF })[0] === false);
check("顶层文档不受约束", checkXfo(["DENY"], { isChild: false })[0] === true);
check("无头 → 允许(危险默认值)",
  checkXfo([], { ancestors: [EVIL], destination: SELF })[0] === true);
check("大小写不敏感", checkXfo(["dEnY"], { ancestors: [SELF], destination: SELF })[0] === false);
check("SAMEORIGIN 沿祖先链全查(父同源、祖父跨源 → 拒绝)",
  checkXfo(["SAMEORIGIN"], { ancestors: [EVIL, SELF], destination: SELF })[0] === false);
check("ALLOW-FROM 已废弃,孤立值放行",
  checkXfo(["ALLOW-FROM https://partner.example"], { ancestors: [EVIL], destination: SELF })[0] === true);

console.log("CSP frame-ancestors 与 XFO 的关系");
check("enforce 的 frame-ancestors 让 XFO 被忽略(更宽松也照样忽略)",
  decide({ "x-frame-options": ["DENY"], "content-security-policy": ["frame-ancestors 'self'"] },
    { ancestors: [SELF], destination: SELF })[0] === true);
check("enforce 的 frame-ancestors 让 XFO 被忽略(更严格也一样)",
  decide({ "x-frame-options": ["DENY"], "content-security-policy": ["frame-ancestors *"] },
    { ancestors: [EVIL], destination: SELF })[0] === true);
check("Report-Only 不算 enforce → 回落 XFO",
  decide({ "x-frame-options": ["DENY"], "content-security-policy-report-only": ["frame-ancestors *"] },
    { ancestors: [EVIL], destination: SELF })[1] === "deny");
check("frame-ancestors 'none' 拒绝",
  decide({ "content-security-policy": ["frame-ancestors 'none'"] },
    { ancestors: [SELF], destination: SELF })[0] === false);
check("'self' 允许同源、拒绝跨源",
  decide({ "content-security-policy": ["frame-ancestors 'self'"] }, { ancestors: [SELF], destination: SELF })[0] === true
  && decide({ "content-security-policy": ["frame-ancestors 'self'"] }, { ancestors: [EVIL], destination: SELF })[0] === false);
check("*.somesite.com 通配子域",
  ancestorsMatch(["*.somesite.com"], "https://a.somesite.com", SELF) === true
  && ancestorsMatch(["*.somesite.com"], "https://notsomesite.com", SELF) === false);
check("有 frame-ancestors 时判定依据是 csp",
  decide({ "content-security-policy": ["frame-ancestors 'none'"] },
    { ancestors: [EVIL], destination: SELF })[1] === "csp:frame-ancestors");

console.log(`\n${pass} 项断言全部通过 (X-Frame-Options / CSP frame-ancestors · JavaScript)`);

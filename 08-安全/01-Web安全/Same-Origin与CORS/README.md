# Same-Origin Policy 与 CORS(Cross-Origin Resource Sharing)

## 简介

浏览器的**同源策略**(Same-Origin Policy)是 web 安全基石:脚本只能读取与自身**同源**(协议+域名+端口)的资源,跨源请求默认被禁止。但实际场景又需要合法跨源(CDN、API、子域),**CORS**(Cross-Origin Resource Sharing)就是服务端通过一组 HTTP 响应头**显式授权**特定跨源请求的机制。OWASP 不把 CORS 列为单独漏洞,但 CORS 配置错误(`Access-Control-Allow-Origin: *` + 凭据、`Access-Control-Allow-Origin` 反射 Origin)是常见高危漏洞来源。

## 关键概念清单

| 术语 | 一句话 |
| --- | --- |
| **Origin(源)** | `scheme://host:port` 三元组,任一不同即跨源 |
| **Same-Origin Policy** | 浏览器默认阻止跨源读取响应 |
| **Simple Request** | GET/HEAD/POST + CORS 安全列表头 + 3 种 MIME,不触发预检 |
| **Preflight Request** | 浏览器先用 OPTIONS 询问"实际请求是否允许" |
| **Pre-flighted Request** | 非简单请求都需预检 |
| **CORS-safelisted headers** | `Accept`/`Accept-Language`/`Content-Language`/`Content-Type`(限 3 种)/`Range` |
| **Credentialed Request** | 带 cookie / HTTP auth,需 `Access-Control-Allow-Credentials: true` |
| **`*` 通配符** | 无凭据时可,有凭据时**禁止**(必须具体源) |
| **Vary: Origin** | 动态 Origin 响应必须带 Vary 防缓存串味 |

## 历史背景

- 1995 年 Netscape Navigator 2 引入 **Same-Origin Policy**(JavaScript 1.0 同源检查)
- 2005-2006 年 XMLHttpRequest 推动跨源限制普及
- 2009 年 W3C WebApps WG 开始 CORS 草案
- 2014 年 Fetch 规范合并 CORS(替代早期 XMLHttpRequest Level 2 CORS)
- 2019 年 Chrome 76 默认 SameSite=Lax 配合 CORS 收紧跨站 cookie
- 2020-2023 年 Chrome 80+ Safari/Firefox 跟进 third-party cookie 限制 → CORS 凭据请求更严格

## 原理详解

### 同源判定 — 三元组

```
https://app.example.com:443/foo/bar
        ↑          ↑      ↑
       scheme     host   port(隐含 443)
```

| URL1 | URL2 | 是否同源 |
| --- | --- | --- |
| `https://app.example.com/a` | `https://app.example.com/b` | ✅ 同源(仅 path 不同) |
| `http://app.example.com/a` | `https://app.example.com/a` | ❌ 跨源(scheme 不同) |
| `https://app.example.com/a` | `https://api.example.com/a` | ❌ 跨源(host 不同) |
| `https://app.example.com:80/a` | `https://app.example.com/a` | ❌ 跨源(port 不同:80 ≠ 443) |
| `https://app.example.com/a` | `https://APP.example.com/a` | ✅ 同源(host 大小写不敏感) |

### 简单请求 vs 预检请求

**简单请求**(不预检,直接发送):
- 方法 ∈ {`GET`, `HEAD`, `POST`}
- 请求头仅含 **CORS-safelisted** 头:`Accept`、`Accept-Language`、`Content-Language`、`Content-Type`(限 `application/x-www-form-urlencoded`、`multipart/form-data`、`text/plain`)、`Range`
- 未注册 `XMLHttpRequest.upload` 事件监听器
- 未使用 `ReadableStream`

**非简单请求**触发预检:
- 方法 `PUT`/`DELETE`/`PATCH`/自定义 → 浏览器先发 `OPTIONS`
- 含自定义头(如 `X-Auth-Token`、非 safelisted `Content-Type: application/json`)
- 用 `ReadableStream` 对象

### 预检请求流程

```
1. 浏览器: OPTIONS /api/transfer
           Origin: https://app.example.com
           Access-Control-Request-Method: POST
           Access-Control-Request-Headers: X-Auth-Token, Content-Type
           
2. 服务端: 200 OK
           Access-Control-Allow-Origin: https://app.example.com
           Access-Control-Allow-Methods: POST, GET
           Access-Control-Allow-Headers: X-Auth-Token, Content-Type
           Access-Control-Max-Age: 86400
           
3. 浏览器: 实际 POST /api/transfer + X-Auth-Token + Content-Type: application/json
4. 服务端: 200 OK + Access-Control-Allow-Origin: https://app.example.com
```

**预检响应缓存**:`Access-Control-Max-Age` 决定浏览器多久不重发 OPTIONS(默认 5s)。

### CORS 响应头全集

| 响应头 | 用途 |
| --- | --- |
| `Access-Control-Allow-Origin` | 允许的源(`*` 或具体 origin,**凭据请求必须具体**) |
| `Access-Control-Allow-Credentials` | `true` 表示允许带 cookie/Authorization |
| `Access-Control-Allow-Methods` | 预检响应:允许的方法列表 |
| `Access-Control-Allow-Headers` | 预检响应:允许的请求头列表 |
| `Access-Control-Expose-Headers` | 允许 JS 读取的非 safelisted 响应头(如 `X-Request-Id`) |
| `Access-Control-Max-Age` | 预检结果缓存秒数 |
| `Vary: Origin` | 动态 Origin 必须带(否则 CDN 会缓存错响应) |

### 凭据请求硬约束

**MDN 原文警告**:
> When responding to a credentialed request, the server **must not** specify the `*` wildcard for the `Access-Control-Allow-Origin` response-header value, but **must** instead specify an explicit origin.
> If a request includes a credential (Cookie) and the response includes `Access-Control-Allow-Origin: *`, the browser **blocks** access and reports a CORS error.

`Set-Cookie` 在 `Access-Control-Allow-Origin: *` 时**不会写入**,浏览器直接丢弃。

### 预检 + 重定向陷阱

> "Not all browsers currently support following redirects after a preflighted request… The request was redirected to https://example.com/foo, which is disallowed for cross-origin requests that require preflight."

规避:服务端避免 3xx 跨域重定向,或改为简单请求。

## 演示

### Demo 1: 同源 vs 跨源判定

```python
is_same_origin("https://app.com/a", "https://app.com/b")  # True
is_same_origin("https://app.com/a", "https://api.com/a")  # False
```

### Demo 2: Simple Request vs Preflight

```python
classify_request(
    method='POST',
    content_type='application/json',  # 不在 safelist
    custom_headers=['X-Auth-Token']   # 不在 safelist
)
# → 'preflight_required: custom headers / non-safelisted Content-Type'
```

### Demo 3: CORS 响应头校验(凭据请求 `*` 拒绝)

```python
validate_cors_response(
    request_origin='https://app.com',
    allow_origin='*',
    allow_credentials='true',
)
# → 🛑 BLOCK: 凭据请求 + ACAO=* → 浏览器拒绝
```

### Demo 4: 预检响应构造(OPTIONS)

```
服务端响应 OPTIONS:
  Access-Control-Allow-Origin: https://app.com
  Access-Control-Allow-Methods: POST, DELETE
  Access-Control-Allow-Headers: X-Auth-Token, Content-Type
  Access-Control-Max-Age: 86400
  Vary: Origin
```

### Demo 5: Vary 头必要性

```
服务端不写 Vary: Origin:
  GET /api/data → ACAO: https://app-a.com (响应被 CDN 缓存)
  后续 GET /api/data from app-b.com → 命中 CDN,ACAO 还是 https://app-a.com
  → app-b.com 读到允许 app-a.com 的响应 → CORS 错误

正确做法:
  响应加 Vary: Origin → CDN 按 Origin 分桶缓存
```

## 环境准备

- Python 3.8+(标准库)
- Node.js 14+(运行 JS demo)

## 运行方式

```bash
cd Same-Origin与CORS/python
python3 cors_demo.py
# 5 demo 输出:同源判定 / Simple vs Preflight / 凭据校验 / 预检响应构造 / Vary 必要性
```

```bash
cd Same-Origin与CORS/javascript
node cors_flow.js
```

## 关键代码片段

```python
# 1. 同源判定
from urllib.parse import urlparse

def is_same_origin(url_a: str, url_b: str) -> bool:
    a, b = urlparse(url_a), urlparse(url_b)
    return a.scheme == b.scheme and a.hostname == b.hostname \
           and (a.port or default_port(a.scheme)) == (b.port or default_port(b.scheme))

# 2. 凭据请求 CORS 校验(MDN 强制约束)
def cors_response_ok(req_origin: str, acao: str, acac: str) -> bool:
    if acac == 'true' and acao == '*':
        return False   # 凭据 + 通配符 → 浏览器拒绝
    return req_origin == acao
```

## 性能与边界
- **预检缓存**(`Access-Control-Max-Age`):Chrome 默认上限 7200s(2h),Firefox 86400s。生产可设 86400s
- **Vary: Origin** 增加缓存键基数,CDN 命中率下降;生产常用子集
- **CORS 错误信息**对 JS 不可见(只 `console.error`),调试只能看 Network 面板

## 注意事项与常见坑
| 现象 | 原因 | 规避 |
| --- | --- | --- |
| `ACAO: *` + `credentials: 'include'` 被拒绝 | 浏览器硬性约束 | 凭据请求必须具体源 |
| 动态 Origin 反射 → 漏洞 | 服务端读 Origin 直接回写 | **白名单** + 精确匹配 |
| `Set-Cookie` 不生效 | `ACAO: *` 时浏览器丢弃 | 改为具体 origin + `SameSite=None; Secure` |
| CDN 缓存串味 | 响应未带 `Vary: Origin` | 动态源必须加 `Vary: Origin` |
| 预检请求 404 | OPTIONS 路由没注册 | 显式 `app.options('*', ...)` 或中间件 |
| 跨域 `Authorization` 头丢失 | 不在 CORS safelist | 显式 `Access-Control-Allow-Headers: Authorization` |
| `application/json` POST 触发预检 | 不在 safelist(只有 form/多部分/text/plain) | 接受预检开销 或用 `text/plain` + 服务端解析 |

## 参考资料(实际阅读)
- [MDN Cross-Origin Resource Sharing (CORS)](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CORS) — 完整规范与浏览器实现
- [MDN CORS Headers 列表](https://developer.mozilla.org/en-US/docs/Web/HTTP/CORS) — 每个响应头完整定义
- [Fetch Standard CORS 协议](https://fetch.spec.whatwg.org/#http-cors-protocol) — W3C/IETF 现行规范
- [WHATWG Fetch CORS Protocol 章节](https://fetch.spec.whatwg.org/#cors-protocol) — 凭据请求 + 预检流程
- [OWASP HTML5 Security Cheat Sheet(CORS)](https://cheatsheetseries.owasp.org/cheatsheets/HTML5_Security_Cheat_Sheet.html#cross-origin-resource-sharing) — 漏洞模式 + 安全配置
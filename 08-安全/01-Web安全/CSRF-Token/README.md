# CSRF Token 机制(Synchronizer / Double-Submit / SameSite / Fetch Metadata)

## 简介

CSRF(Cross-Site Request Forgery)是一种**身份冒用**攻击:受害者浏览器在已登录目标站(`example.com`)的情况下,访问攻击者站点(`evil.com`)时,**自动携带**目标站的 session cookie,发起一个 GET/POST 请求。目标站无法区分这是受害者的真实意图还是攻击者伪造。OWASP 把 CSRF 列入历史 Top 10,核心防御是 **token** + **SameSite** + **Fetch Metadata** 三层纵深。

## 关键概念清单

| 术语 | 一句话 |
| --- | --- |
| **CSRF** | 浏览器自动附带 cookie,跨站伪造状态变更请求 |
| **Synchronizer Token** | 服务端在 session 生成随机 token,每次状态变更请求必须携带并比对 |
| **Double Submit Cookie** | cookie + 表单/header 双发,服务端比对一致性 |
| **Signed Double-Submit** | 把 token 用 HMAC(session_id, secret) 签名后下发,服务端无需 session 存储 |
| **SameSite=Lax/Strict/None** | cookie 跨站发送策略(Lax 是 2020 起 Chrome 默认) |
| **Sec-Fetch-Site** | `same-origin`/`same-site`/`cross-site`/`none` 浏览器自动加的元数据头 |
| **Origin/Referer 校验** | 服务端二次防御,精确字符串匹配防 `example.org.attacker.com` 绕过 |
| **CSPRNG** | Cryptographically Secure Pseudo-Random Number Generator(token 必须用) |

## 历史背景

- 1988 年 Norm Hardy 在 *The Confused Deputy* 中首次描述"跨域身份冒用"语义
- 2000 年代初 webmail/CSRF 流行,2007 年 OWASP CSRFGuard 是首批开源 Java 防御
- 2016 年 OWASP 公布 **CSRF Prevention Cheat Sheet**,正式把 Synchronizer Token Pattern 列为首选
- 2016 年 Google Chrome 51 起实验 SameSite,2020 年 Chrome 80 默认 **Lax**
- 2020 年 W3C Fetch Metadata 工作草案发布,2022 年起被 OWASP 推荐作为现代浏览器主防御
- 2025 年 Go 1.25 内置 `net/http.CrossOriginProtection` 类型(基于 Fetch Metadata)

## 原理详解

### 攻击模型

```
1. 用户在浏览器登录 https://bank.com  → Set-Cookie: SID=abc123 (无 SameSite)
2. 用户被诱导访问 https://evil.com
3. evil.com 返回 HTML: <img src="https://bank.com/transfer?to=evil&amount=100">
4. 浏览器自动 GET https://bank.com/transfer?to=evil&amount=100
   自动带 Cookie: SID=abc123 → bank.com 认为合法请求 → 转账
```

> **关键**:GET 也可被攻击(`<img>` / `<iframe>` 都会触发 GET)。**状态变更绝不能用 GET**(HTTP 语义也禁止)。

### Synchronizer Token Pattern(OWASP 推荐,Stateful 服务)

服务端流程:

```
1. 用户登录 → 创建 session → session['csrf_token'] = CSPRNG.next()
2. 渲染表单时 → <input type="hidden" name="csrf_token" value="...">
3. POST 提交 → 服务端比对 session['csrf_token'] == POST['csrf_token']
   不一致 → 403 + 审计日志(潜在 CSRF)
```

**关键要求**(OWASP 原文):
- token 必须由 **服务端** 生成(CSPRNG,如 Python `secrets` / Java `SecureRandom`)
- token 必须 **per-session**(per-request 会破坏浏览器"返回上一页"功能)
- token **不可预测**(高熵,≥ 128 bits)
- **不能**通过 cookie 传递(否则攻击者也能读)
- **不能**写入 URL 或 server log(防泄漏)

### Double-Submit Cookie(OWASP Stateless 软件推荐)

```
1. 服务端 Set-Cookie: csrf=<random>; Secure; SameSite=Lax   ← 不可 HttpOnly
2. 前端 JS 读取 cookie,放入 X-CSRF-Token header 或表单隐藏域
3. 提交:POST /api/action  Header: X-CSRF-Token: <同 cookie 值>
4. 服务端比对 cookie 值 == header 值
```

**Naive 缺陷**:攻击者可在子域 `evil.example.com` 写入同名 cookie 覆盖(若 Domain=.example.com 错误配置)。**Signed Double-Submit** 改进:用 HMAC 签名 token = `HMAC(secret, session_id || random)`,服务端无需 session 表。

### SameSite Cookie 三态精确语义

| 值 | 跨站请求发送? | 适用场景 |
| --- | --- | --- |
| **Strict** | **任何跨站都不发**(连外链点击都不发) | 银行后台 |
| **Lax**(Chrome 默认) | 仅**顶级导航 + 安全方法**(GET)时发 | 普通网站 |
| **None** | **永远发**(必须配合 `Secure`) | 第三方 widget |

**关键陷阱**:`Lax` 不保护 GET 状态变更!OWASP 明确警告:"If any state-changing operation is reachable via a `GET` request, `SameSite=Lax` will not stop it."

### Fetch Metadata(现代浏览器主防御)

OWASP 推荐三层 fallback:

```
Sec-Fetch-Site: cross-site / same-site / same-origin / none
Sec-Fetch-Mode: cors / navigate / no-cors / same-origin / websocket
Sec-Fetch-Dest: document / script / image / empty / ...
```

**主策略**:`Sec-Fetch-Site: cross-site && method ∈ {POST, PUT, DELETE, PATCH}` → **拒绝**。
**例外**:顶级导航 + GET(用户主动链接/书签)。
**强制要求**:**必须配置 Origin/Referer 兜底**,因为旧浏览器不发送 `Sec-Fetch-*`。

### Origin/Referer 校验(强制兜底)

```
Origin: https://app.example.com  (POST/PUT 时一定存在)
Referer: https://app.example.com/page  (降级备用)

校验:精确字符串相等(必须带尾 /)
陷阱: app.example.com.attacker.com  ← 必须拒绝(尾匹配而非前缀匹配)
```

### Token 生成 — CSPRNG 跨语言对照

| 语言 | 推荐 API | 禁用 |
| --- | --- | --- |
| Python | `secrets.token_urlsafe(32)` | `random` |
| Go | `crypto/rand.Read(buf)` | `math/rand` |
| Java | `java.security.SecureRandom` | `java.util.Random` |
| Node.js | `crypto.randomBytes(32)` / `crypto.randomUUID()` | `Math.random()` |
| .NET | `RandomNumberGenerator.GetBytes(32)` | `System.Random` |

## 演示

### Demo 1: 朴素 Cookie 认证被 CSRF

```python
# 服务端仅靠 session cookie 鉴权 → 无 CSRF token
@app.route('/transfer', methods=['POST'])
def transfer():
    if 'user' not in session: abort(401)
    transfer_money(session['user'], request.form['to'], request.form['amount'])
    return 'OK'
# evil.com 的 <form action=https://bank.com/transfer> 自动带 cookie → CSRF 成功
```

### Demo 2: Synchronizer Token 防御

```python
# 服务端每会话生成 CSPRNG token,比对表单 token
# ...(见 demo_csrf.py)
```

### Demo 3: SameSite 模拟器

```python
# 给定请求方法 + Sec-Fetch-Site,判定 cookie 是否发送
```

### Demo 4: Fetch Metadata 判定

```python
# 给定 headers,判定跨站状态变更是否拒绝
```

## 环境准备

- Python 3.8+(纯标准库,无第三方依赖)
- Node.js 14+(可选,运行 JS 版)

## 运行方式

```bash
cd CSRF-Token/python
python3 csrf_demo.py
# 5 demo 输出 token 生成 / session 校验 / SameSite 判定 / Fetch Metadata / Origin 校验
```

```bash
cd CSRF-Token/javascript
node fetch_metadata.js
```

## 关键代码片段(`csrf_demo.py`)

```python
# 1. CSPRNG token(Python secrets 模块,OWASP 推荐)
import secrets
csrf_token = secrets.token_urlsafe(32)   # 256 bits 熵,base64-urlsafe 编码

# 2. 校验必须用 constant-time 比较(防 timing attack)
import hmac
ok = hmac.compare_digest(session_token, submitted_token)

# 3. SameSite Lax 缺陷演示
# 假设 GET /admin/delete?id=123 → Lax 允许跨站链接请求带 cookie
# → 必须改 POST + 配合 token,或 SameSite=Strict
```

## 性能与边界

- **CSPRNG**:Linux 上 `secrets` 模块底层为 `/dev/urandom`,128 bytes 约 1-2 μs
- **constant-time compare**:`hmac.compare_digest` O(n),n=44 字符时约 100 ns
- **Sec-Fetch-* 解析**:浏览器原生,服务端 0 成本(只是 header 读取)
- **HMAC token 签名**:32 字节输入 SHA-256 约 1 μs(Python hmac)

## 注意事项与常见坑

| 现象 | 原因 | 规避 |
| --- | --- | --- |
| `<script>fetch('/api/transfer', {credentials:'include'})</script>` 绕过 token | XSS 偷到 cookie 同时读 DOM 上的 token | **XSS 是 CSRF 防御的前提** — 先防 XSS |
| `<a href="https://app.com/logout">logout</a>` 跨站点击,`Strict` cookie 不发 | Strict 太过严格,影响外链 UX | 注销/退出常用 GET 用 `Strict`,其他场景用 `Lax` |
| `Domain=.example.com` cookie 被 `evil.example.com` 写覆盖 | Domain 设置过宽 | 用 `__Host-` 前缀(必须 `path=/` + `Secure` + 无 `Domain`) |
| `Origin: null` 被允许 | 旧版 Chrome 在某些 sandbox iframe 发送 null Origin | 仅在 sandbox/redirect 场景白名单 null |
| Per-request token 失败 | 用户点"上一页"按钮,旧 token 已失效 | 用 per-session;per-request 仅在金融级场景 |
| `Sec-Fetch-Site: same-site` 信任 | 兄弟子域可能受攻击者控制 | 同站策略必须**显式列白名单子域**,默认拒绝 |

## 参考资料(实际阅读)

- [OWASP Cross-Site Request Forgery Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html) — Synchronizer / Double-Submit / Origin / Fetch Metadata 全策略
- [OWASP Cross-Site Request Forgery (CSRF)](https://owasp.org/www-community/attacks/csrf) — 攻击模型 + 失败方案列表(secret cookie / only-POST 等)
- [OWASP SameSite Cookie Attribute](https://owasp.org/www-community/SameSite) — Strict/Lax/None 浏览器实际行为对照表
- [OWASP Anti CSRF Tokens ASP.NET](https://owasp.org/www-community/Anti_CRSF_Tokens_ASP-NET) — token 生成 + 校验 + 集成示例
- [MDN SameSite cookies](https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Set-Cookie/SameSite) — 浏览器支持矩阵
- [MDN Sec-Fetch-Site header](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Sec-Fetch-Site) — 取值 + 浏览器支持
- [W3C Fetch Metadata Request Headers](https://www.w3.org/TR/fetch-metadata/) — 标准规范
- [Stanford CSRF Robust Defenses](https://seclab.stanford.edu/websec/csrf/csrf.pdf) — 学术论文,SameSite Origin 起源
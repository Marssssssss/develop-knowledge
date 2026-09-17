# Cookie 存储模型、SameSite 与 HSTS

> 多数"Cookie 安全"材料讲的是**服务端该设什么属性**;本 demo 讲**浏览器拿到属性之后怎么处理** ——
> 后者才是 `__Host-` 前缀、SameSite 例外、cookie-fixing 这些机制真正落地的地方。

## 简介

四个真实的失效场景,都由"UA 侧算法"而非"服务端配置"决定:

| 场景 | 根因 | 规范位置 |
| --- | --- | --- |
| `__Host-` 前缀没起作用 | 只检查了前缀大小写敏感的精确拼写 | 6265bis §5.4:UA **MUST** 大小写不敏感匹配前缀 |
| http 页面把 https 的同名 cookie 改掉 | 忘了非安全 cookie 不得覆盖同名 Secure cookie | 6265bis §5.7 step 21(且路径比较**不对称**) |
| `SameSite=Lax` 挡不住表单 POST | 忽略 `Lax-allowing-unsafe` 的 2 分钟例外 | 6265bis §5.6.7.2 |
| 子域被 HSTS 遗漏 | 只做了完全一致的域名匹配 | RFC 6797 §5.4/§8.2:父域匹配须带 `includeSubDomains` |

## 原理详解

**1. 存储键与检索键不是同一个东西(RFC 6265 §5.3–§5.4)**

接收时算的是 `(name, domain, host_only, path)` 四元组(同名同域同路径 → 覆盖,且**继承原 creation-time**);发送时按三个条件过滤再排序:

```
host-only ? host === domain : domain-match(host, domain)
path-match(request-path, cookie-path)          # §5.1.4:点边界,不是字符串前缀
secure-only ⇒ 请求必须是安全协议
排序:路径长者在前;等长时 creation-time 早者在前
```

`domain-match` 的关键是**点边界**:`notexample.com` 不匹配 `example.com`;`path-match` 的关键是**不跨词边界**:`/docs` 匹配 `/docs/x`,不匹配 `/docsets`。

**2. `default-path` 有一个流传很广的口径差异**

RFC 6265 §5.1.4 与 6265bis 的原文是"输出到**最右斜杠之前**(不含该斜杠)",所以从 `/docs/Web/HTTP/index.html` 设置 cookie 的默认路径是 **`/docs/Web/HTTP`**;而 MDN 的 `Set-Cookie` 文档写成 `/docs/Web/HTTP/`(带尾斜杠)。本 demo 以 RFC 原文为准 —— 两种写法在 `path-match` 下**行为等价**,但断言必须钉死一个口径。

**3. SameSite 是"检索算法的一步",不是过滤器**

```
SameSite=None   → 总是发(但 UA 只在带 Secure 时才收,step 22)
SameSite=Strict → 仅同站
SameSite=Lax    → 同站,或"跨站 + 顶层导航 + 安全方法"
未写(Default)   → 等同 Lax;若 UA 启用 Lax-allowing-unsafe,
                  则"顶层导航 + 创建后 ≤2 分钟"的 POST 也放行
```

最后一个分支是"Flask/Django 默认 session cookie 突然能用"的原因。注意它**只对没显式写 SameSite 的 cookie 生效**,显式 `Lax` 不享受该例外。

**4. `__Secure-` / `__Host-`:UA 侧大小写不敏感**

服务端若用小写比较,`__SeCuRe-SID=evil` 会被当成普通 cookie 接受;而 UA 大小写不敏感地匹配前缀 —— 于是同一站点上会同时存在 `__Secure-SID` 与 `__SeCuRe-SID` 两条,而后端(大小写不敏感)无法区分。6265bis §5.4 因此把"UA MUST 大小写不敏感"写成了硬要求。

```text
__Secure-*  需要:Secure + 由安全源设置
__Host-*    需要:Secure + 由安全源设置 + 无 Domain(host-only)+ Path=/(显式)
```

**5. HSTS 是"按发出主机索引的策略库"(RFC 6797 §8)**

```text
只在安全传输上接受 STS 头;非安全传输上收到 → 完全忽略(§8.1)
同一响应多个 STS 头 → 只处理第一个;指令重复 → 整个头作废(§6.1)
max-age=0 → 删策略,并连带清掉 includeSubDomains(§6.1.1)
命中判定:父域匹配须带 includeSubDomains;否则只有"一致匹配"才算(§5.4/§8.2)
命中后:scheme http→https;显式 80→443;其他端口保留;无端口不加(§8.3)
IP 字面量不得被标记;`<meta http-equiv>` 一律不理会(§8.1.1/§8.5)
```

## 对比:三种"把攻击面收窄"的手段

| 手段 | 作用点 | 能防 | 不能防 |
| --- | --- | --- | --- |
| `HttpOnly` | JS 读不到值 | XSS 窃取会话 | XSS 冒用会话(请求仍带 cookie) |
| `SameSite` | 跨站请求带不带上 | CSRF(尤其 POST) | 同站 XSS、顶层导航型 CSRF(§8.8.2 明说是"路障") |
| `__Host-` 前缀 | 锁到单主机 + 全路径 | 子域/路径注入(子域被控、路径覆盖) | 同主机上的其它漏洞 |
| HSTS | 强制加密 + 拒绝继续 | 降级/SSL Strip、Secure cookie 被嗅探 | bootstrap 首次明文访问(§14.6) |

## 环境

- Python 3.9+(标准库 `re`/`urllib.parse`)
- Node.js 18+(`URL`)

## 运行方式

```bash
python cookie_check.py    # Python 侧 80 项断言(存储模型 + SameSite + HSTS)
node   cookie_store.mjs   # JavaScript 侧 18 项断言(同一批规则的精简版)
```

## 关键代码

```python
# 6265bis §5.7 step 21:非安全 cookie 不得覆盖同名 Secure cookie(路径比较不对称)
if not c.secure_only and not secure_conn:
    for old in self.jar:
        if (old.name == c.name and old.secure_only
                and (domain_match(c.domain, old.domain) or domain_match(old.domain, c.domain))
                and path_match(c.path, old.path)):
            return None

# §5.4:UA 必须大小写不敏感地匹配前缀
low = c.name.lower()
if low.startswith("__host-"):
    if not (c.secure_only and c.host_only and explicit_path_slash):
        return None
```

## 性能边界

- 存储是线性列表,`receive`/`cookie_header` 都是 O(n);浏览器实际用索引 + 上限 50/域、3000 总,本 demo 复刻了这两个上限(超限按 last-access 淘汰)。
- 单条 `Set-Cookie` 超 4096 字节、属性值超 1024 字节直接丢弃 —— 这是"cookie 值别塞大 JSON"的规范依据。
- 本 demo 不解析 `Expires` 的真实日期(用固定 +1 天代替),也不做 IDNA 规范化;这两处已标注在代码注释里。

## 注意事项与常见坑

- **`Max-Age` 优先于 `Expires`**,且 6265bis §5.5 要求把生命周期**截断到 400 天**。写 `Max-Age=9999999999` 不会真的永久有效。
- **`SameSite=None` 必须配 `Secure`**,否则整条被丢弃(step 22)——"我明明写了 None 却没生效"多半是这个。
- **非安全 cookie 覆盖判断是单向的**:新 cookie 的路径必须 path-match 旧 Secure cookie 的路径才拦截。所以攻击者仍可在更短路径(`/`)上种一个同名 cookie —— 两个同名 cookie 会**同时**进入 Cookie 头,顺序是路径长者在前。真正的解法是 `__Host-` 前缀。
- **HSTS 的 `preload` 不是 RFC 6797 的指令**:RFC 里只有 `max-age` 与 `includeSubDomains`,§12.3 讲的是"预加载列表"这个**设施**;`preload` 是浏览器厂商的扩展。实现里把未知指令忽略即可,但别以为写了 `preload` 就进了预加载表。
- **HSTS 一旦被误设就是 DoS**:§14.5 明确列出"仅 HTTP 服务的主机被设上策略后对用户不可用",`includeSubDomains` 更是会把不支持 TLS 的子域一起打进不可达状态。
- **`max-age=0` 与 `max-age=0; includeSubDomains` 效果相同**(后者被忽略)—— 清理策略不需要写 `includeSubDomains`。

## 参考资料(实际联网读过)

- RFC 6265 *HTTP State Management Mechanism* — <https://www.rfc-editor.org/rfc/rfc6265.txt>(§4.1.2 属性、§5.1.3 域匹配、§5.1.4 default-path/path-match、§5.3 存储模型、§5.4 Cookie 头与排序)
- draft-ietf-httpbis-rfc6265bis-20 *Cookies* — <https://www.ietf.org/archive/id/draft-ietf-httpbis-rfc6265bis-20.txt>(§4.1.2.7 SameSite、§4.1.3 前缀、§5.4 UA 侧前缀大小写不敏感、§5.5 400 天、§5.6.7.2 Lax-allowing-unsafe、§5.7 step 16/21/22)
- RFC 6797 *HTTP Strict Transport Security* — <https://www.rfc-editor.org/rfc/rfc6797.html>(§6.1 指令语法、§8.1–§8.3 处理模型、§8.5 meta 无效、§14.4/§14.5/§14.6 安全考量)
- MDN `Set-Cookie` — <https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie>(属性语义对照;default-path 口径与 RFC 原文不同,已在上文标注)

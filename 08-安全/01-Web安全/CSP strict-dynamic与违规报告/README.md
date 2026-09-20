# CSP `'strict-dynamic'` 与违规报告

> 基于 host 白名单的 CSP 有个绕不过去的现实:**现代前端会运行时插脚本**。
> 你没法在响应头里穷举出打包器会加载的每一个 URL,于是很多人写上了
> `'unsafe-inline' https:` —— 而这两项恰好又放开了大部分 XSS。
>
> `'strict-dynamic'` 的思路是"**信任传递**":已经被 nonce/hash 放行的脚本,
> 它自己再创建的脚本元素,一律放行;而 HTML 解析器插进来的(攻击者注入的)不放行。

## 1. 简介

本 demo 按 W3C CSP3 的原文实现:

- `§6.7.3.2` / `§6.7.3.3` —— 内联脚本/样式的元素匹配算法
- `§6.7.1.1` —— script 指令的 pre-request check
- `§6.7.2.6` —— URL 与源列表的匹配(含 `'none'` 的特殊语义)
- `§2.4` / `§5` —— 违规对象与报告
- `§8.5` —— Strict CSP 的判据

## 2. 原理详解

### 2.1 `'strict-dynamic'` 做的三件事(§8.2 原文归纳)

```text
① host-source / path / scheme-source 以及 'self'、'unsafe-inline'
   在加载 script 时**被忽略**；只有 nonce-source 与 hash-source 被尊重
② 由非 "parser-inserted" script 元素触发的脚本请求 → 放行
③ 因此可以做向后兼容部署：
   'unsafe-inline' https: 'nonce-X' 'strict-dynamic'
     ├─ CSP1 浏览器（不认 nonce）：等价于 'unsafe-inline' https:
     ├─ CSP2 浏览器（不认 strict-dynamic）：等价于 https: 'nonce-X'
     └─ CSP3 浏览器：等价于 'nonce-X' 'strict-dynamic'
```

### 2.2 判定"允许全部内联"时的短路顺序很关键(§6.7.3.2)

```text
for expr in source_list:
    if expr 是 nonce-source 或 hash-source:      return "Does Not Allow"   ← 立即
    if type ∈ {script, script attribute, navigation} 且 expr == 'strict-dynamic':
                                                  return "Does Not Allow"   ← 立即
    if expr == 'unsafe-inline':                  allow = True
return allow ? "Allows" : "Does Not Allow"
```

两个反直觉的推论(自检都覆盖了):

| 源列表 | type = `script` | type = `style` |
| --- | --- | --- |
| `'unsafe-inline' 'strict-dynamic'` | **Does Not Allow** | Allows |
| `'strict-dynamic' 'unsafe-inline'` | **Does Not Allow** | Allows |
| `http://example.com 'strict-dynamic'` | **Does Not Allow** | Allows |

因为 `'strict-dynamic'` 那一支是**立即 return**,不看前面的 `allow`;
而它只作用于 `script` / `script attribute` / `navigation` 三种 type。

### 2.3 元素匹配:`document.write` 与 `createElement` 的分水岭(§6.7.3.3)

```text
① 上一步 "Allows"                                   → Matches
② type ∈ {script, style} 且元素 nonceable,且元素 nonce == 某个 nonce-source → Matches
   原文 note: nonce **只作用于 inline script / inline style**,
              不作用于元素属性,也不作用于 javascript: 导航
③ 遍历源列表:
   - 遇 'strict-dynamic' 且 type == "script" 且元素**不是** parser-inserted → Matches
   - 遇 hash-source:把 base64-value 的 '-'→'+'、'_'→'/' 后与实际的 base64 摘要比对
```

§8.2 给的例子被本 demo 直接做成断言:

```text
dependency.js  ← createElement() 造的元素,不是 parser-inserted  → 放行
sadness.js     ← document.write() 造的元素,是 parser-inserted   → 拦下
```

### 2.4 请求侧:pre-request check(§6.7.1.1)

```text
① 源列表为空或只有 'none'                       → Blocked
② SRI 完整性元数据匹配源列表                     → Allowed
③ 源列表含 'strict-dynamic':
     request.parser_metadata == "parser-inserted" → Blocked
     否则                                        → Allowed
④ 否则走普通的 URL 匹配
```

### 2.5 `'none'` 的三个细节(§6.7.2.6)

- **空源列表等价于 `'none'`**(`script-src` 后面什么都不写 ≠ 允许一切)
- 只有**一项且是 `'none'`** 时才"不匹配任何 URL"
- **`'none'` 与其它表达式并存时不起作用** —— «`'none'`, `https://example.com`» 照样匹配 example.com

### 2.6 违规报告

`§2.4` 的违规对象字段:`documentURL` / `referrer` / `blockedURL` /
`effectiveDirective` / `originalPolicy` / `sourceFile` / `sample` /
`disposition` / `statusCode` / `lineNumber` / `columnNumber`。

两条容易忽略的规则:

- **`sample` 只取前 40 个字符**,且**只有内联脚本/事件处理器/样式才有**;
  来自外部文件的违规**不带 `sample`**(避免把别人服务器上的内容搬进报告)。
- §1 的变更记录写明:**`report-uri` 已弃用**,取而代之的是基于 [REPORTING] 的
  `report-to`(端点由 `Reporting-Endpoints` 响应头声明)。§5 的 csp hash report
  (type = `"csp-hash"`)还特别注明**对 `ReportingObserver` 不可见**。

## 3. 关键代码

```python
def allows_all_inline(source_list, element_type):
    allow = False
    for expr in source_list:
        if is_nonce_source(expr) or is_hash_source(expr):
            return False
        if element_type in ("script", "script attribute", "navigation") \
                and is_keyword(expr, "strict-dynamic"):
            return False
        if is_keyword(expr, "unsafe-inline"):
            allow = True
    return allow
```

## 4. 运行方式

```bash
cd "CSP strict-dynamic与违规报告"
python selfcheck_csp_strict.py     # 44 条断言,输出 "ALL OK"
go run .
```

## 5. 性能与工程边界

- **报告洪泛**:Report-Only 上线第一天的报告量可能是 QPS 的若干倍。
  `sample` 只有 40 字符正是为了控制体积,但 `originalPolicy` 会原样回传,
  策略很长时单条报告可达 1 KB 以上 —— 报告端点要有配额与采样。
- **`'strict-dynamic'` 会掩盖供应链风险**:§8.2 原文警告 —— 若运行时脚本的
  URL 可被攻击者控制(常见于"按配置动态拼 CDN 地址"的框架),该策略就会
  **放行任意脚本**。本 demo 把它做成了一条显式断言(`https://attacker.example`
  在 strict-dynamic 下是 `Allowed`)。
- **nonce 必须每次响应都不一样**:复用 nonce 等于把策略退化成白名单。

## 6. 注意事项与常见坑

1. **nonce 不会自动传播给子脚本**。用 `createElement` 造 `<script>` 时必须
   **显式拷贝 nonce 属性**,或者干脆依赖 `'strict-dynamic'`。二者选其一,
   混用时 nonce 那一支其实永远轮不到。
2. **nonce 只管 inline script / inline style**:想用它放行
   `<div onclick="...">` 或 `location.href="javascript:..."` 是无效的
   (§6.7.3.3 的 note),那两类要靠 `'unsafe-hashes'` + hash-source。
3. **哈希的 base64url 归一化**:规范只规定了把 `-`→`+`、`_`→`/`,
   **没有规定 padding 怎么处理**。`base64-value` 语法允许 0~2 个 `=`,
   本模型不做补齐,因此**去掉 padding 的写法在本模型下不匹配** —— 这是标注的
   口径,不是规范要求。
4. **`'unsafe-inline'` 与 nonce/hash 同时存在时会被忽略** —— 不是"或"的关系。
   这正是向后兼容部署能成立的原因。
5. **Strict CSP 还要求 `base-uri`**(§8.5):漏了 `base-uri 'self'` 或 `'none'`,
   攻击者可以用 `<base href>` 把相对路径的脚本指到自己的域名上。
6. **Report-Only 不是"演练"而是"观察"**:它不阻断任何东西,因此也**不会**
   阻止攻击;它只告诉你"如果切强制,会发生什么"。

## 7. 参考资料(本 demo 实际读取)

- W3C — *Content Security Policy Level 3* <https://www.w3.org/TR/CSP3/>
  (§1 变更记录、§2.4 违规对象、§5 Reporting、§6.7.1.1、§6.7.2.6、
  §6.7.3.2、§6.7.3.3、§8.2 Usage of `'strict-dynamic'`、§8.5 Strict CSP)
- Cure53 — *H5SC Minichallenge 3: "Sh\*t, it's CSP!"*（§8.2 引用的绕过案例集）

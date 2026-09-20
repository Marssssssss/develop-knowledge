# SRI 子资源完整性与 Integrity-Policy

> TLS + HSTS + 证书固定解决的是"**我在跟谁说话**",解决不了"**我收到的是什么**"。
> CDN 被入侵、构建流水线被投毒、运维手滑改了一个字节 —— 这些情况下传输层
> 一切正常。SRI 要做的是把**内容**也钉住。

## 1. 简介

`<script src="https://cdn.example/a.js" integrity="sha384-…" crossorigin="anonymous">`
这一行里藏着三个必须同时成立的约定:算法、摘要、**以及 CORS**。
本 demo 按 W3C SRI 规范原文实现解析、选强、比对与 §3.8 的 Integrity-Policy。

## 2. 原理详解

### 2.1 摘要用的是**标准 base64**,不是 base64url

§2 原文:"A base64 encoding is defined in Section 4 of RFC 4648."
官方例子里 `sha384-H8BRh8j48O9oYatfu5AZzq6A9RINhZO5H16dQZngK7T62em8MUt1FLm52t+eX6xO`
含 `+`,`sha512-Q2bFTOhEALkN8hOms2FKTDLy7eugP2zFZ1T8LCvX42Fp3WoNr3bjZSAHeOsHrbV1Fu9/A0EzCinRE7Af1ofPrw==`
含 `/` 与 `==`。本 demo 用这两个官方值**反向验证**了摘要实现。

### 2.2 三算法是有序的,且"最强者说了算"(§3.3.3)

```text
valid SRI hash algorithm token set = «"sha256", "sha384", "sha512"»  （更强的在后）

取最强：下标更大者胜出并**清空**结果集；下标相同则**都保留**。
```

由此产生一个非常反直觉、但规范写得明明白白的结论:

| integrity 属性 | 结果 | 原因 |
| --- | --- | --- |
| `sha384-正确 sha512-错误` | **失败** | 只验最强的 sha512,弱的那份**根本不看** |
| `sha384-错误 sha512-正确` | 通过 | 同上 |
| `sha384-A sha384-B`(两个都对一份) | 通过 | 同强度都保留,命中任意一份即可 |

所以"再加一个弱一点的摘要做兼容"是**无效**的,弱的那份不会成为退路。

### 2.3 §3.3.4 的一个安全上很要紧的默认值

```text
Do bytes match metadataList?
    parsedMetadata = parse(metadataList)
    if parsedMetadata 是空集: return true        ← 没有 integrity 属性 = 不做校验
```

**没有 `integrity` 属性就等于不校验**。这正是 §3.8 的 `Integrity-Policy`
存在的理由 —— 它把"必须有 integrity"变成一条可强制的策略。

### 2.4 SRI 必须配 CORS(§3.3.4 note)

原文:"Subresource Integrity requires CORS and it is a logical error to attempt
to use it without CORS." 跨源 CDN 上必须写 `crossorigin="anonymous"`
(或 `use-credentials`),否则跨源响应对页面不可读,校验必然失败。

### 2.5 失败时发生什么(§3.7)

UA **拒绝渲染或执行**,返回 **network error**,并触发元素的 `error` 事件。
规范专门提示:可以在 `error` 事件里回退到自建的、较慢但可信的副本。

### 2.6 Integrity-Policy(§3.8,较新的机制)

```http
Integrity-Policy: blocked-destinations=(script), endpoints=(integrity-endpoint)
```

- 值是 **RFC 9651 的 Dictionary**;成员值是 token 的 inner list
- `sources` 唯一可能的值是 `"inline"`,**缺省即 inline**
- `blocked-destinations` 取 `"script"` / `"style"`
- 阻断时向 `endpoints` 报告,JS 侧可用 `ReportingObserver` 收 `"integrity-violation"`

§3.8.2 的判定顺序(本 demo 逐条实现):

```text
① 有 integrity 元数据 且 mode ∈ {cors, same-origin}   → Allowed
② url 是 local（about:/blob:/data:/filesystem:）      → Allowed
③ 强制策略与 report-only 策略都为空                    → Allowed
④ sources 含 inline 且 blocked destinations 含本次 destination → Blocked
⑤ 只有 report-only 命中 → 报 reportOnly=true 的违规,但**不阻断**
```

注意 ① 的 `mode` 条件:**带 integrity 但 mode 是 `no-cors` 的请求拿不到豁免** ——
因为 §3.3.4 的 note 已经说明无 CORS 的 SRI 是逻辑错误。

## 3. 关键代码

```python
def get_strongest_metadata(parsed):
    result, strongest = [], None
    for item in parsed:
        if not result:
            result.append(item); strongest = item; continue
        cur, new_ = VALID_ALGOS.index(strongest["alg"]), VALID_ALGOS.index(item["alg"])
        if new_ < cur:  continue
        if new_ > cur:  strongest, result = item, [item]
        else:           result.append(item)
    return result
```

## 4. 运行方式

```bash
cd SRI与完整性策略
python selfcheck_sri.py     # 44 条断言,输出 "ALL OK"
go run .
```

## 5. 性能与工程边界

- **摘要只算一次、只算最强那批**:给一个 1 MB 的 bundle 配 `sha384 + sha512`
  会算两遍 —— 但按 §3.3.3,只有 sha512 会被用于比对,sha384 那份是纯浪费。
  **只写最强的那一个**。
- **构建期生成 integrity 是硬要求**:任何"运行时动态生成脚本内容"的方案
  都与 SRI 不兼容(摘要必须提前算好写进 HTML)。这让 SRI 天然不适合
  按用户/A-B 分桶动态拼装的脚本。
- **字节级敏感**:任何构建期之外的改动(压缩、加 banner、BOM、换行符差异)
  都会让摘要失配。**CI 里必须把"生成 HTML"放在"最终产物"之后**。

## 6. 注意事项与常见坑

1. **别忘了 `crossorigin`**。这是 SRI 落地失败最常见的原因,而且现象是
   "控制台一条警告 + 资源静默不执行",很容易被误判成 CDN 挂了。
2. **弱摘要不是退路**(见 2.2)。想做兼容只能靠"UA 不认识该算法就跳过"
   (§3.2.1 原文:不支持的算法视同没有 integrity 校验),而不是靠叠加多种强度。
3. **SRI 不防"第一次投递就是恶意的"**:摘要是你自己写进 HTML 的,
   如果 HTML 本身就是从被入侵的源站来的,SRI 无能为力。它防的是
   **CDN/中间环节在你之后改动内容**。
4. **Integrity-Policy 的 `sources` 目前只有 `inline`**,语义是"非内联的外部
   子资源也要有 integrity";不要指望它能表达"允许某几个域名例外"。
5. **report-only 的价值**:`Integrity-Policy-Report-Only` 可以先跑一轮
   收集"哪些外部脚本没带 integrity",再切强制 —— 直接上强制很容易把
   第三方统计/埋点脚本全打断。

## 7. 参考资料(本 demo 实际读取)

- W3C — *Subresource Integrity* <https://www.w3.org/TR/SRI/>
  (§2 术语与有序算法集合、§3.1 完整性元数据、§3.2.1 算法敏捷性、
  §3.3.1–§3.3.4 解析/选强/比对、§3.5 integrity 属性 ABNF、
  §3.6 Link 头的 integrity 参数、§3.7 违规处理、§3.8 Integrity-Policy)
- RFC 4648 — *The Base16, Base32, and Base64 Data Encodings*
- RFC 9651 — *Structured Field Values for HTTP*(Dictionary 结构)

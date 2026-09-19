# 幂等性与 Idempotency-Key：让 POST 也敢重试

## 简介

HTTP 给 PUT / DELETE 送了"幂等"这个礼物，但**创建类操作几乎只能用 POST**，而 POST 不幂等 ——
于是"请求超时了，到底要不要重试"成了分布式系统里最常见的两难。重试怕重复下单，不重试怕订单丢失。

`Idempotency-Key` 就是把幂等性**外包给一条请求头**：客户端生成一个唯一键，服务端用它识别"这是同一个请求的又一次到达"。
Stripe、Adyen、PayPal、Square、WorldPay 等支付类 API 早已各自实现，IETF HTTPAPI 工作组正在把它标准化
（本文按 `draft-ietf-httpapi-idempotency-key-header-07` 撰写 —— **注意这仍是工作草案，未成为 RFC**）。

本 demo 用 **Python（35 断言，可实跑）+ Go** 复刻草案里的状态机、错误分派与安全要求。

## 原理详解

### 1. 头长什么样

它是 **RFC 8941 Structured Field 的 Item，且值 MUST 是 String** —— 所以线上形态**必须带引号**：

```http
Idempotency-Key: "8e03978e-40d5-43e8-bc93-6894a57f9324"
Idempotency-Key: "clkyoesmbgybucifusbbtdsbohtyuuwz"
```

草案给的两个例子分别是 UUID（§2.2 RECOMMENDED）和 32 位随机串。不带引号的裸 UUID 是**非法**的，
这是最容易踩的坑（很多人照着各厂商的非标准实现写成裸值）。

### 2. 三种情形（§2.6）

| 情形 | 判定 | 服务端行为 |
| --- | --- | --- |
| **首次** | 键 + fingerprint 都没见过 | 正常处理，把结果（成功或错误）存下来 |
| **重试** | 键 + fingerprint 都见过，且原请求已完成 | **返回先前已完成操作的结果**（成功或错误） |
| **并发重试** | 键 + fingerprint 都见过，但原请求还在处理 | 回资源冲突错误（**409**） |

"返回先前结果"这件事值得强调：它包含**错误**。原请求返回了 500，重试也拿到同一个 500，
而不是让客户端误以为这次是新请求。

并发返回 409 而不是阻塞等待，是因为等待会把"客户端超时重试"变成"服务端线程池堆积"。
草案特别说明 **409 是唯一一个"客户端无需修正请求就能重试"的错误**。

### 3. 错误分派（§2.7）

| 触发 | 状态码 | 客户端该做什么 |
| --- | --- | --- |
| 需要键的操作缺了 `Idempotency-Key` | **400** | 补上键 |
| 键的格式不合规 | **400** | 按公布的规范重新生成 |
| 同一个键配了不同的 payload | **422** | 换键（或修正 payload） |
| 原请求还在处理中 | **409** | 直接重试，**无需修正** |
| 其他 4xx/5xx（401/403/500/502/503/504/429…） | 按资源文档 | 自便 |

错误响应可以用 **RFC 9457 problem+json** 描述，也可以用 `Link: <...>; rel="describedby"` 指向文档 ——
草案两种都给了示例。

### 4. fingerprint（§2.4）

键相同但 payload 不同，说明客户端用错了键（§2.2 明令 MUST NOT 这样复用）。服务端怎么知道 payload 变了？
草案给了几种 fingerprint 生成方式：整个 payload 的校验和、选中字段的校验和、逐字段比对、请求签名/摘要。
本 demo 用**规范化 JSON 的 SHA-256**（键序无关，所以 `{"a":1,"b":2}` 与 `{"b":2,"a":1}` 同指纹）。

### 5. 键的生命周期（§2.3）

资源 MAY 要求基于时间的键以便过期清理，且 **SHOULD 公布过期策略**。过期后的同一键会被当成新键 ——
这意味着"幂等窗口"不是无限的：窗口之外重试，仍可能重复创建。窗口多长是业务决策
（Stripe 是 24 小时）。

### 6. 安全（§5）—— 最容易被忽略的一节

草案点名两类攻击：

- **注入**：不校验键就拿去做幂等缓存查找，可能把恶意内容带进查询；
- **数据泄漏**：键的**熵太低**时，攻击者可以猜到其他客户端的键，从而读到别人缓存的响应条目。

对策三条：

1. 建立固定的键格式并公布，处理任何请求之前**先校验**；
2. 用**复合键**做缓存查找键 —— 客户端传来的键 **+ 只有服务端知道的客户端属性**（如账号 ID）；
3. 让键的熵足够高（UUID v4 / 32 位随机串）。

本 demo 断言了复合键的效果：两个不同客户端传同一个 `Idempotency-Key`，各自视为首次请求、
各自创建订单，互不可见。

### 7. 哪些方法需要它

按 RFC 9110，GET / HEAD / OPTIONS / TRACE / PUT / DELETE 本来就幂等，**只有 POST 与 PATCH 才需要** `Idempotency-Key`。
本 demo 的中间件对幂等方法直接放行、不查键。

## 对比

| 方案 | 谁生成标识 | 重复请求的结果 | 代价 |
| --- | --- | --- | --- |
| 什么都不做 | — | 重复创建 | 数据不一致 |
| 客户端先查后写（read-before-write） | 客户端 | 视竞态而定 | 仍有竞态窗口、多一次 RTT |
| `Idempotency-Key` | 客户端 | 返回首次结果 | 服务端要存键与响应、要定过期策略 |
| 服务端去重（业务唯一键，如订单号） | 服务端 | 报重复 | 要求业务自带天然唯一键 |
| 事务性发件箱（outbox） | 服务端 | 靠数据库唯一约束 | 侵入数据层 |

`Idempotency-Key` 的定位是**通用的、与业务无关的**：业务层没有天然唯一键时也能用。

## 环境

- Python 3.8+（仅标准库）
- Go 1.18+（仅标准库；本机无 Go 工具链，人工审查 + 括号配平校验）

## 运行方式

```bash
cd python && python check.py     # 35 项断言，全绿
cd go     && go run idempotency.go
```

## 关键代码

```python
def parse_structured_string(raw):
    """§2.1：值 MUST 是 RFC 8941 的 String —— 不带引号即非法。"""
    m = SF_STRING_RE.match(raw.strip())
    return m.group(1) if m else None
```

```python
ck = (client_id, key)   # §5 复合键：客户端键 + 只有资源知道的客户端属性
rec = self.store.get(ck)
...
if rec.fingerprint != fp:
    return Response(422, problem(422, "Idempotency-Key is already used", ...))
if rec.state == "in_progress":
    return Response(409, problem(409, "A request is outstanding ...", ...))
return Response(rec.status, rec.body, note="replayed")   # §2.6 成功或错误都重放
```

## 性能边界

- 每次非幂等请求多一次键查找（O(1) 哈希）+ 一次响应落盘；**响应体越大，存储代价越高**，
  通常只存必要的响应摘要而不是完整大对象。
- 幂等窗口 = TTL。窗口越长存储越多，窗口越短越可能"窗口外重试导致重复创建"。
- 并发 409 会把重试压力**还给客户端**，客户端需要退避（否则就是重试风暴）。
- 复合键让缓存按客户端分片，**缓存命中率不变但隔离性大幅提升**，代价是存储里多一个维度。

## 注意事项与常见坑

1. **值必须带引号**（RFC 8941 String），裸 UUID 非法 —— 各厂商的历史实现常不带引号，别照抄。
2. **409 是唯一"无需修正即可重试"的错误**，其余 400/422 都要求客户端先改请求。
3. **错误结果也会被缓存并重放**，别指望"上次 500 了，这次重试能成功"。要让它重试就别存失败结果。
4. **键必须全局唯一且不与不同 payload 复用**：复用同一键发不同的下单请求会拿到 422，
   而"看着像重试其实改了参数"是最常见的误用。
5. **低熵键 = 数据泄漏**：自增 ID、短字符串都不能当键。
6. **必须用复合键**（键 + 客户端身份），否则猜到键就能读别人的响应。
7. **这是草案不是 RFC**：字段语义在 -01..-07 之间变过（例如错误响应示例仍引 RFC 7807 而非 9457），
   落地前以最新 draft 与目标厂商文档为准。
8. **幂等 ≠ 并发安全**：两个**不同**键的并发请求仍会各自创建资源，幂等性只对同一键生效。
9. **存储必须与实际业务操作在同一个事务里提交**，否则"业务成功但键没存"或反之，都会破坏幂等。

## 参考资料

- Jena & Dalal, *The Idempotency-Key HTTP Header Field*,
  draft-ietf-httpapi-idempotency-key-header-07, 2025-10-15
  — <https://www.ietf.org/archive/id/draft-ietf-httpapi-idempotency-key-header-07.txt>
  （全文实读：§1–§2.7、§3 IANA、§4 实现现状、§5 安全、§6 示例、附录 A）
  注：-08 及以上版本在本轮抓取时为 404，**-07 为当前可用最新版**。
- RFC 8941, *Structured Field Values for HTTP*（§3.3.3 String —— 值必须带引号）
- RFC 9457, *Problem Details for HTTP APIs*（草案 §2.7 的示例仍引 RFC 7807，9457 已将其废止）
- RFC 9110 §9.2.2, *HTTP Semantics*（POST/PATCH 非幂等的出处）
- Stripe, *Advanced idempotency* — <https://stripe.com/docs/idempotency>（草案 §4 实现现状引用）

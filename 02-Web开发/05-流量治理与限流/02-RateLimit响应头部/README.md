# RateLimit 响应头部

HTTP 限流「怎么告诉客户端」的标准化方案：`RateLimit-Policy`（策略，稳定）+ `RateLimit`（当前服务限额，逐请求变化）+ 三个 problem type。依据 **draft-ietf-httpapi-ratelimit-headers-11**（2026-05，全文 67 KB 实读）与 **RFC 6585 §4**。

## 一、字段形状

两个字段都是 RFC 9651 Structured Fields 的 **List of Items**，Item 值必须是 String：

```http
RateLimit-Policy: "burst";q=100;w=60,"daily";q=1000;w=86400
RateLimit:        "default";r=50;t=30
```

`RateLimit-Policy` 的 `SHOULD` 在一串响应里保持不变——这正是它与 `RateLimit`（`MAY` 逐请求变化）的分工。两者都是 List，所以**多策略天然可以并列**，也可以拆成多个同名字段。

## 二、参数与硬性约束

| 字段 | 参数 | 约束 |
| --- | --- | --- |
| Policy | `q` | **REQUIRED**，非负整数（§3.1.1） |
| Policy | `qu` | String，取值来自配额单位注册表；规范定义 `requests` / `content-bytes` / `concurrent-requests`，缺省 `requests` |
| Policy | `w` | 非负**且非零**整数，单位秒，不支持亚秒（§3.1.3） |
| Policy / Limit | `pk` | Byte Sequence（`:base64:`），配额按分区键分配 |
| Limit | `r` | **REQUIRED**，非负整数，剩余配额（§4.1.1） |
| Limit | `t` | 非负整数，**0 合法**（与 `w` 不同） |

注意 `w` 与 `t` 的「零」语义不对称：`w=0` 非法（窗口无意义），`r=0;t=0` 合法（配额确实耗尽）。自检里两条断言分别钉住。

`t` 用 delay-seconds 而非时间戳的理由，规范写得很明确：不依赖时钟同步、对时钟调整与偏移免疫；同时能缓解大量客户端拿到同一时间戳造成的惊群。

## 三、服务端行为（§6）

- `MAY` 独立于状态码返回，包括被限流的响应；规范**不要求**字段值与状态码有任何相关性。
- 3xx 上要谨慎：`r=0;t=10` 配 `Location` 会让客户端先等 10 秒才敢跟跳转。
- 同时给 `Retry-After` 时，`Retry-After` 的时点 `SHOULD NOT` 早于有效窗口结束。
- `MUST NOT` 通过字段值暴露「还能打多少」的过大数字，`SHOULD` 给 `r/t` 比值设上限（§8.5 DoS 考量）；特殊情况下 `MAY` 主动压低字段值。

## 四、客户端行为（§7）

优先级链，实现时必须照抄：

1. **畸形字段 MUST 被忽略**（不是报错、不是回退到上一次的值）。
2. `Retry-After` 存在时 **MUST 优先**，`t` MAY 被忽略——即使 `Retry-After=1` 小于 `t=7` 也照用。
3. 否则 `SHOULD NOT` 在 `t` 内超过 `r`。
4. `MUST NOT` 假设后续响应里还会有这些字段。
5. `r` 为正**不等于**后续请求一定被服务（§4.1.1 明确写了）。

多条 Item 并存时，规范没说怎么合并；本 demo 取**最保守**（按最小 `r/t` 换算等待），README 里标明这是本实现的推导而非规范条文。

## 五、问题类型（§5）

| type 片段 | 状态码 | 扩展成员 |
| --- | --- | --- |
| `quota-exceeded` | 429 | `violated-policies: [string]` |
| `temporary-reduced-capacity` | 503 | 同上 |
| `abnormal-usage-detected` | 429 | 同上 |

三种都以 `application/problem+json` 承载（RFC 9457 的问题详情模型），扩展成员 `violated-policies` 是**字符串数组**，列出被违反的策略名。

**规范原文的笔误（照录并说明）**：§5.1 示例写的是 `HTTP/1.1 429 Bad Request`，而 RFC 6585 §4 定义的 429 短语是 `Too Many Requests`——§5.3 示例里写的才是 `429 Too Many Requests`。§5.2 则写 `503 Server Unavailable`（常见写法是 `Service Unavailable`）。自检里有四条断言把这三处原文钉住，避免后续轮次误以为是自己读错。

## 六、代码结构

| 文件 | 内容 |
| --- | --- |
| `python/ratelimit_headers.py` | SF list 切分、Item/参数解析、两类 Item 校验、`safe_wait` 优先级链、`is_throttled_problem` |
| `python/selfcheck_ratelimit_headers.py` | 35 条断言（实跑全绿） |
| `python/main.py` | 四组真实响应头部 → 等待决策的对照输出 |
| `go/ratelimit_headers.go` | Go 侧同构实现（`ParsePolicy` / `ParseLimit` / `SafeWait`） |

## 参考资料

- draft-ietf-httpapi-ratelimit-headers-11, *RateLimit header fields for HTTP*（2026-05）— <https://www.ietf.org/archive/id/draft-ietf-httpapi-ratelimit-headers-11.txt>（§3/§3.1.x/§4/§4.1.x/§5.x/§6/§7 全文实读）
- RFC 6585 §4, *429 Too Many Requests*（MAY 带 Retry-After；响应 `MUST NOT` 被缓存）— <https://www.rfc-editor.org/rfc/rfc6585.txt>
- RFC 9457, *Problem Details for HTTP APIs*（问题类型与扩展成员的承载模型）

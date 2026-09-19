# 条件请求与乐观并发：If-Match / If-None-Match / If-Range

## 简介

"条件请求"是 HTTP 里唯一被标准化了的**乐观并发控制**手段。它同时服务两个目标：

1. **省流量** —— 条件 GET 让缓存更新只要一个 304；
2. **防丢更新（lost update）** —— 把 `If-Match` 挂在 PUT/DELETE/POST 上，让"我改的是我刚才看到的那份"
   成为协议级断言，而不是应用层约定。

本 demo 按 RFC 9110 §13 逐条复刻：校验器的强/弱比较、四个条件头的求值、**六步优先级表**，
以及最容易被忽略的"**什么时候根本不该求值**"。

## 原理详解

### 1. 强比较 vs 弱比较（§8.8.3.2）

ETag 形如 `"1"`（强）或 `W/"1"`（弱，`W/` 大小写敏感）。两种比较函数：

| ETag 1 | ETag 2 | 强比较 | 弱比较 |
| --- | --- | --- | --- |
| `W/"1"` | `W/"1"` | no match | match |
| `W/"1"` | `W/"2"` | no match | no match |
| `W/"1"` | `"1"` | no match | **match** |
| `"1"` | `"1"` | match | match |

强比较要求 **both are not weak**。这条规则的直接后果常被忽略：

> **服务器给出弱 ETag 时，`If-Match` 永远不可能成立。**

因为强比较要求双方都不弱，而服务器的那一侧已经是弱的了。所以想用 `If-Match` 做乐观并发，
服务器**必须**发强 ETag。

### 2. 四个条件头各用哪种比较（§13.1）

| 头 | 用途 | 比较函数 | 失败时 |
| --- | --- | --- | --- |
| `If-Match` | 防丢更新（state-changing） | **强**（MUST） | 412；MAY 回 2xx 若变更已应用 |
| `If-None-Match` | 缓存校验 / 防重复创建 | **弱**（MUST） | GET/HEAD → **304**；其他方法 → **412** |
| `If-Modified-Since` | 没有 ETag 时的缓存校验 | 日期 | 304（SHOULD） |
| `If-Unmodified-Since` | 没有 ETag 时的防丢更新 | 日期 | 412；MAY 回 2xx 若变更已应用 |
| `If-Range` | 续传时"要么续、要么重来" | **精确匹配** | 忽略 Range，回 200 |

`If-None-Match: *` 有个巧妙用法：客户端"以为资源还不存在"，用 PUT 创建时带上它，
资源若已存在条件即为假 → 412，正好挡住两个客户端抢着做初始创建。

### 3. 日期判据的方向（最易搞反）

`If-Modified-Since` 的判据是：

> If the selected representation's last modification date is **earlier or equal to** the date
> provided in the field value, the condition is **false**.

即 `last_modified <= IMS → false → 304`。所以 **IMS 越靠后（越新），越容易拿到 304**；
反过来 `If-Unmodified-Since` 是 `last_modified <= IUS → true`，越靠后越容易**通过**。
本 demo 对两个方向各给了断言（开发期就是把这两条写反过）。

### 4. 谁屏蔽谁

- 有 `If-None-Match` → **MUST ignore** `If-Modified-Since`（前者更精确，同时发只是为了兼容老中间盒）
- 有 `If-Match` → **MUST ignore** `If-Unmodified-Since`
- 方法不是 GET/HEAD → **MUST ignore** `If-Modified-Since`
- 日期非法 / 资源没有修改时间 → **MUST ignore** 该头
- 请求里没有 `Range` → 服务器 **MUST ignore** `If-Range`（客户端也 MUST NOT 发）

### 5. 六步优先级（§13.2.2）

多个条件头同时出现时的求值顺序是**写死的**：

```text
1. If-Match（仅源服务器）        失败 → 412
2. If-Unmodified-Since（无 If-Match 时） 失败 → 412
3. If-None-Match                 失败 → GET/HEAD: 304 / 其他: 412
4. If-Modified-Since（GET/HEAD 且无 INM）  失败 → 304
5. If-Range（GET 且带 Range）     成立 → 206 / 不成立 → 忽略 Range 回 200
6. 执行方法
```

顺序背后的理由规范也给了：**"lost update" 条件比缓存校验更严格、校验过的缓存比部分响应更高效、
ETag 被认为比日期更准确**。所以 `If-Match` 失败时**一定**是 412，即便同时有一个会给出 304 的
`If-None-Match`。

### 6. 什么时候根本不该求值（§13.2.1）

这是最实用的一条：

> A server MUST ignore all received preconditions if its response to the same request without
> those conditions ... would have been a status code other than a 2xx or 412.

即 **404 / 401 / 403 / 301 优先于一切条件求值**。所以"资源不存在"不会被 `If-None-Match: *`
的语义搅成 412。另外 `CONNECT` / `OPTIONS` / `TRACE` 与"选择某个表示"无关，条件头一律忽略；
非源服务器且不能作为 cache 的节点 MUST NOT 求值、MUST 转发。

### 7. lost update 场景

```text
A 读到 /doc，ETag = "v1"
B 也读到 /doc，ETag = "v1"
A 先写成功 → ETag 变成 "v2"
B 写：
   不带 If-Match         → 200，A 的修改被静默覆盖
   带 If-Match: "v1"     → 强比较失败 → 412，B 知道要重新读
```

## 对比

| 方案 | 谁负责 | 失败语义 | 代价 |
| --- | --- | --- | --- |
| 无保护 | — | 静默覆盖 | 数据丢失 |
| 版本号字段（body 内） | 应用层 | 自定义 | 每个 API 各写一遍，且对缓存/中间盒不可见 |
| `If-Match`（本协议） | HTTP 层 | 412（标准化） | 服务器 MUST 发强 ETag |
| 悲观锁 | 服务器 | 排队/超时 | 吞吐下降、需要锁超时处理 |

## 环境

- Python 3.8+（标准库：`email.utils` 解析 HTTP-date）
- Go 1.18+（仅标准库；本机无 Go 工具链，人工审查 + 括号配平校验）

## 运行方式

```bash
cd python && python check.py     # 50 项断言，全绿
cd go     && go run conditional.go
```

## 关键代码

```python
def strong_compare(a: str, b: str) -> bool:
    """强比较：两者都**不**弱，且 opaque-tag 逐字符相同。"""
    pa, pb = parse_entity_tag(a), parse_entity_tag(b)
    if pa is None or pb is None:
        return False
    return (not pa[0]) and (not pb[0]) and pa[1] == pb[1]
```

```python
# §13.2.1：基线响应不是 2xx/412 → 条件全部忽略
if not (200 <= self.base_status < 300 or self.base_status == 412):
    return Response(self.base_status,
                    "baseline failure/redirect takes precedence",
                    sorted(headers.keys()))
```

## 性能边界

- 每个条件头求值 O(标签数)，无回溯，无正则；服务端开销可忽略。
- 收益端：`304` 只有头部没有 body，条件 GET 的带宽节省与表示大小成正比。
- 弱 ETag 的生成成本远低于强 ETag（后者常需对表示数据做抗碰撞哈希），
  但**换不来** `If-Match` 的并发保护 —— 这是"成本 vs 能力"的取舍点。
- 日期校验器只有 1 秒分辨率（§8.8.2.2 明说它"implicitly weak"），一秒内两次修改无法区分。

## 注意事项与常见坑

1. **弱 ETag + `If-Match` = 永远 412**。想做乐观并发就必须发强 ETag。
2. **`If-None-Match` 在非安全方法上失败是 412 不是 304** —— 只有 GET/HEAD 才有 304 语义。
3. **日期方向容易写反**：IMS 是 `last_modified <= date → false`；IUS 是 `last_modified <= date → true`。
4. **`If-Range` 用精确匹配**，不是 `If-Unmodified-Since` 那种 `<=`；且它只用强比较。
5. **404 优先于条件求值**，别指望用条件头把"不存在"变成 412。
6. **`If-Match` / `If-None-Match` 里 `*` 与实体标签不能混写**（§13.1.1/§13.1.2 明确 syntactically invalid）。
7. **`If-Match` 失败时 MAY 回 2xx**（变更看起来已被应用），但对"多客户端把资源当信号量用"
   的场景规范建议**严格回 412** —— 非原子自增这类场景会被这个 MAY 坑到。
8. **缓存/中间盒 MAY 忽略 `If-Match` / `If-Unmodified-Since`**（§13.1.1/§13.1.4 末段），
   因为它们的互操作价值只对源服务器成立。
9. 实体标签是**不透明**的：客户端不该解析它的结构，也不同资源间唯一性无保证
   （§8.8.1：同一个强校验器可以同时用在多个资源的表示上）。

## 参考资料

- RFC 9110, *HTTP Semantics*（Fielding / Nottingham / Reschke, 2022-06）
  — <https://www.rfc-editor.org/rfc/rfc9110.txt>
  实读章节：§8.8 Validator Fields（含 §8.8.1 强弱定义、§8.8.3.2 比较表 Table 3）、
  §9.2 Common Method Properties、§13 Conditional Requests（§13.1.1–§13.1.5、§13.2.1、§13.2.2 六步表）
- RFC 9111, *HTTP Caching*（条件 GET 的缓存侧语义）

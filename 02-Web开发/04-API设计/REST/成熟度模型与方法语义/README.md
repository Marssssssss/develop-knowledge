# Richardson 成熟度模型与 HTTP 方法语义

## 简介

"我们的 API 算 REST 吗"是个没有答案的问题，但"它离 REST 还差哪几步"有答案 —— 这就是
Leonard Richardson 提出的成熟度模型（RMM，Martin Fowler 2010 年整理阐述）。它把通往 REST 的路拆成
**资源 → 动词 → 超媒体**三步，加起点的"隧道"共四级。

本 demo 做两件事：

1. 把 RMM 变成**可从报文反推**的判据（不是主观打分）：给定一组请求/响应特征，算出等级并给出依据；
2. 把 RMM Level 2 依赖的 HTTP 方法语义（safe / idempotent / cacheable）按 RFC 9110 §9.2 落实成
   可断言的函数，尤其是"**谁可以自动重试谁**"这条工程上最有用的推论。

## 原理详解

### 1. 四个层级

| 层级 | 加了什么 | 解决的问题 | 报文特征 |
| --- | --- | --- | --- |
| L0 | — | HTTP 只是隧道（RPC / POX / XML-RPC / SOAP 都是这一层） | 所有请求打同一个端点，一律 POST，一律 200 |
| L1 | 资源 | 复杂性：把大端点拆成多个可寻址资源（分而治之） | 出现多个 URI，但仍一律 POST / 一律 200 |
| L2 | 动词 + 状态码 | 变化：同类情况用同样方式处理（消除不必要的变化） | 查询用 `GET`，创建返 `201 + Location`，冲突返 `409` |
| L3 | 超媒体控制 | 可发现性：协议自描述（HATEOAS） | 响应里带 `link rel`，客户端不必预知该往哪儿发 |

Level 3 的实际收益 Fowler 说得最实在：**服务器可以随便改 URI 方案而不破坏客户端**，
只要客户端按 `rel` 去查链接；代价是客户端必须"顺着链接走"，不能硬编码 URI。

> Fowler 原文特别提醒：RMM "is not a definition of levels of REST itself"，
> Roy Fielding 明确 **Level 3 是 REST 的前置条件而非 REST 本身**；
> 而且它 "should not be used in some kind of assessment mechanism"（别拿去当 KPI 打分）。

### 2. safe / idempotent / cacheable（RFC 9110 §9.2）

```text
safe        = GET, HEAD, OPTIONS, TRACE                 §9.2.1
idempotent  = PUT, DELETE + 全部 safe 方法              §9.2.2
cacheable   = 规范只为 GET, HEAD, POST 定义了缓存语义    §9.2.3
```

三条定义各自的内涵常被混为一谈，规范其实区分得很清楚：

- **safe 是"客户端没有请求状态变更"**。它不是"实现不能有副作用" —— 服务器写访问日志、
  展示广告扣广告主的钱，都算 safe。关键是客户端**没有请求**、也**不该为此负责**。
- **idempotent 是"多次相同请求的效果 = 一次的效果"**。同样只约束**被请求的那一层**：
  服务器照样可以每条请求记一次日志、留一条版本历史。
- **cacheable 是方法定义里必须显式说明缓存条件**，否则不能缓存（§9.2.3）。
  规范虽然给 POST 定义了缓存语义，但"绝大多数实现只支持 GET 和 HEAD"。

### 3. 谁可以自动重试（工程上最有用的一条）

从 idempotent 直接推出：

| 角色 | 幂等方法 | 非幂等方法（POST / PATCH） |
| --- | --- | --- |
| 客户端 | 可以自动重试 | **SHOULD NOT**，除非「知道该请求语义上确实幂等」或「能检测原请求从未被应用」 |
| 代理 | 可以自动重试 | **MUST NOT**（硬禁止） |

规范还补了一句：**客户端 SHOULD NOT 自动重试"一次已经失败的自动重试"**（别嵌套重试）。

### 4. safe 方法上的不安全动作是 MUST disallow

§9.2.1 点名了 `page?do=delete` 这类写法：

> If the purpose of such a resource is to perform an unsafe action, then the resource owner
> **MUST** disable or disallow that action when it is accessed using a safe request method.

否则爬虫、链接检查、预取、建搜索索引顺着 URI 爬一遍就会把数据删光 —— 这不是"可能被滥用"，
而是**必然**被触发。本 demo 提供 `safe_method_violation()` 做静态检查。

### 5. 从报文反推等级

判据（逐级短路，缺哪一级就停在哪一级）：

```text
多个不同资源路径？       否 → L0
读取用了 safe 方法 且 用了 200 以外的状态码？  否 → L1
响应带超媒体控制？       否 → L2
                        是 → L3
```

注意 L2 需要**两个条件同时成立**：只把状态码用起来（比如 POST 返 409）但查询仍走 POST 隧道，
仍然停在 L1 —— 因为"用 GET 做只读"才是让缓存、预取、爬虫这些 Web 基础设施生效的关键。

## 对比

| 维度 | L0/L1（RPC 风格） | L2（多数"RESTful API"的落点） | L3（HATEOAS） |
| --- | --- | --- | --- |
| URI 变更 | 无 URI 概念 | 破坏性变更 | 客户端跟 `rel` 走，可随意改 |
| 缓存 | 缓存不了 | GET 可被 Web 基础设施缓存 | 同 L2 |
| 客户端耦合 | 耦合到方法名 | 耦合到 URI 模板 | 只耦合到入口 + 关系名 |
| 实现成本 | 低 | 中 | 高（服务端要维护链接关系） |

L3 的现实困境：Fowler 也承认"还没有足够多的例子能确信 restful 路线是对的"，
且"**hypermedia controls 没有绝对标准**" —— 他的示例用 ATOM（RFC 4287）的 `<link rel uri>`，
实际工程里还有 HAL、Siren、JSON:API 等多种选择，这本身又制造了分裂。

## 环境

- Python 3.8+（仅标准库）
- Go 1.18+（仅标准库；本机无 Go 工具链，人工审查 + 括号配平校验）

## 运行方式

```bash
cd python && python check.py     # 57 项断言，全绿
cd go     && go run maturity.go
```

## 关键代码

```python
def richardson_level(ex: Sequence[Exchange]) -> int:
    """从可观测报文反推 RMM 等级（0..3）。逐级短路。"""
    if not uses_resources(ex):
        return 0
    if not (uses_http_verbs(ex) and uses_status_codes(ex)):
        return 1
    if not uses_hypermedia(ex):
        return 2
    return 3
```

```python
def proxy_may_auto_retry(method: str) -> bool:
    """§9.2.2：A proxy MUST NOT automatically retry non-idempotent requests."""
    return is_idempotent(method)
```

## 性能边界

- 分级是 O(报文数)，无回溯；适合放进 CI 当 API 设计的 lint。
- Level 2 的收益是**缓存命中率**，不是单次延迟：GET 可缓存意味着整条链路（浏览器 / CDN / 正向代理）
  都能挡掉请求，量级差异远大于单次 RTT。
- Level 3 的代价在服务端：每个表示都要生成链接关系，且"哪些 rel 该出现"取决于当前资源状态
  （这正是它的价值所在 —— 状态机被外化成了链接的有无）。

## 注意事项与常见坑

1. **safe ≠ 无副作用**。写日志、计费都算 safe；判据是"客户端有没有**请求**状态变更"。
2. **POST/PUT 与 create/update 不是对应关系**。Fowler 原文点名"some people incorrectly make a
   correspondence between POST/PUT and create/update" —— 两者的选择依据完全不同
   （谁决定 URI、幂等性要求）。
3. **幂等 ≠ 响应相同**。重复 PUT 的"效果"相同，但响应可以不同（§9.2.2 明说 "the response might differ"）。
4. **PATCH 不幂等**，别因为它"看起来像局部更新"就当它能重试。
5. **代理禁止重试非幂等请求是 MUST**，比客户端的 SHOULD NOT 更硬 —— 写网关/边车时要特别注意。
6. **L2 的两个条件缺一不可**：只加状态码不加 GET，仍然停在 L1。
7. **别把 RMM 当评分卡** —— Fowler 明确说它是"帮助理解 REST 思想的工具"，不是评估机制。
8. **Level 3 不是 REST 的充分条件**，只是前置条件；Fielding 的定义才是 REST 本身。
9. `201` 必须配 `Location` 才有意义 —— "新资源在哪儿"是 Level 2 的核心信息。

## 参考资料

- Martin Fowler, *Richardson Maturity Model: steps toward the glory of REST*, 2010-03-18
  — <https://martinfowler.com/articles/richardsonMaturityModel.html>（全文实读：L0–L3 报文示例、
  "The Meaning of the Levels"、Ian Robinson 的三条总结、关于 hypermedia 无绝对标准的说明）
- RFC 9110, *HTTP Semantics* — <https://www.rfc-editor.org/rfc/rfc9110.txt>
  实读：§9.2.1 Safe Methods、§9.2.2 Idempotent Methods、§9.2.3 Methods and Caching
- RFC 4287, *Atom Syndication Format*（Fowler 示例中 `<link rel uri>` 的来源）

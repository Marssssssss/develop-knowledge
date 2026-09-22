# 607 · API 版本化与弃用：Deprecation / Sunset / 主动协商

> 本轮实际抓下来读过的原文（不是凭记忆复述）：
> - `draft-ietf-httpapi-deprecation-header-07`（22 226 字节）— <https://www.ietf.org/archive/id/draft-ietf-httpapi-deprecation-header-07.txt>
> - RFC 8594 *The Sunset HTTP Header Field*（23 588 字节）— <https://www.rfc-editor.org/rfc/rfc8594.txt>
> - RFC 9651 *Structured Field Values for HTTP* §3.3.7（74 086 字节）— <https://www.rfc-editor.org/rfc/rfc9651.txt>
> - RFC 9110 *HTTP Semantics* §5.6.7 / §12 / §15.5.7 / §15.5.16（502 941 字节）— <https://www.rfc-editor.org/rfc/rfc9110.txt>
> - RFC 9110 勘误 Errata ID 7306（2022-11-09 Verified）— <https://www.rfc-editor.org/errata/rfc9110>

---

## 一、两阶段模型：先 Deprecation，后 Sunset

RFC 8594 §1.4 把 API 退场拆成两个阶段，**只有第二阶段该用 Sunset**：

| 阶段 | 含义 | 该用什么 |
| --- | --- | --- |
| 一 | API 不再是推荐版本，但**仍然可用** | Deprecation 头 + `deprecation` 链接（RFC 8594 明确说 Sunset 不适合此阶段） |
| 二 | API 或某个版本**下线**，预期不再响应 | Sunset 头 + `sunset` 链接 |

### 1.1 两个头的日期格式**不一样**

这是最容易写错的一条：

| 头 | 类型 | 例子 |
| --- | --- | --- |
| `Deprecation` | **Item Structured Header**，取值 `sf-date` | `Deprecation: @1688169599` |
| `Sunset` | **HTTP-date**（IMF-fixdate） | `Sunset: Sun, 30 Jun 2024 23:59:59 GMT` |

草案自己都加了一句 "for historical reasons the Sunset HTTP header field uses a
different data type for date"。同一个时间点（`2023-06-30T23:59:59Z` 与
`2024-06-30T23:59:59Z`）在两个头里走两套解析，本 demo 各写了一遍。

`sf-date` 的规则（RFC 9651 §3.3.7）：`sf-date = "@" sf-integer`，表示自
1970-01-01T00:00:00Z 起的秒数，**可为负**、**不含闰秒**、解析器 MUST 支持 1..9999 年，
即 `-62135596800 .. 253402214400`。所以 `@16.5`（小数）和 `1688169599`（缺 `@`）都是非法的。

`HTTP-date` 的规则（RFC 9110 §5.6.7）：三种格式都可能出现，
**接收方 MUST 接受全部三种，发送方 MUST 生成 IMF-fixdate**：

```text
Sun, 06 Nov 1994 08:49:37 GMT    ; IMF-fixdate（首选）
Sunday, 06-Nov-94 08:49:37 GMT   ; rfc850（过时）
Sun Nov  6 08:49:37 1994         ; asctime（过时，日期两位、空格填充）
```

> 本 demo 口径：rfc850 的两位年份规范没给换算规则，这里按 `70-99 → 19xx、
> 00-69 → 20xx` 处理，与 `Sunset` 实际使用场景（近未来）一致。

### 1.2 三条约束

1. **§4：`Sunset` 的时间戳 MUST NOT 早于 `Deprecation` 的时间戳。**
   注意是 "earlier than"——两者**相等是允许的**，本 demo 显式断言了这一点。
2. **§5：弃用不改变资源行为。** 已弃用资源 SHOULD 照旧可用，只有过了 sunset 才预期
   不可用。所以 `usable()` 只在 `phase == "sunset"` 时为假。
3. **RFC 8594 §3：Sunset 只是 hint。** "It is safest to consider timestamps in the past
   mean the present time"；过了时间点仍返回 2xx **不算违规**。本 demo 用
   `hint_vs_status()` 把它分成 `before` / `honored` / `hint-not-kept` 三态，
   而不是简单判对错。

### 1.3 作用域是"被响应的那个资源"

两个头都只作用于返回它们的资源。服务端 MAY 定义更大作用域（比如只在 home document 上
宣告一次代表整个 API），但**不知情的消费者看不到**——这一点在 RFC 8594 §5 与草案 §2.2
里是同一段话。

配套的两个链接关系类型（不需要有对应的头也能先挂上，用于提前公布策略）：

```text
Link: <https://developer.example.com/deprecation>; rel="deprecation"; type="text/html"
Link: <https://developer.example.com/sunset>;      rel="sunset"
```

---

## 二、内容协商版本：`Accept` 的 q 值怎么算

RFC 9110 §12.5.1 的核心规则只有一句：**"Media ranges can be overridden by more
specific media ranges"**——同一类型上最具体的引用优先。

具体实现是给每个 media-range 定一个 specificity，取**匹配的**里面 specificity 最高的那个的 q：

| media-range | specificity |
| --- | --- |
| `*/*` | 0 |
| `text/*` | 1 |
| `text/plain` | 2 |
| `text/plain;format=flowed` | 3 |

### 2.1 参数不是"可选提示"，是匹配条件

带参数的 media-range **只匹配参数完全相同**的媒体类型；反过来，无参的 media-range
可以匹配带额外参数的媒体类型：

```text
Accept: text/plain;format=flowed;q=0.5
  → quality("text/plain")                 == 0.0   （参数不满足，不匹配）
Accept: text/plain
  → quality("text/plain;format=flowed")   == 1.0   （媒体类型可多带参数）
```

### 2.2 官方 Table 5 有一处勘误

§12.5.1 给的例子：

```text
Accept: text/*;q=0.3, text/plain;q=0.7, text/plain;format=flowed,
        text/plain;format=fixed;q=0.4, */*;q=0.5
```

| Media Type | 官方表 | 本 demo 按规则推出 |
| --- | --- | --- |
| `text/plain;format=flowed` | 1 | 1 ✅ |
| `text/plain` | 0.7 | 0.7 ✅ |
| `text/html` | 0.3 | 0.3 ✅ |
| `image/jpeg` | 0.5 | 0.5 ✅ |
| `text/plain;format=fixed` | 0.4 | 0.4 ✅ |
| **`text/html;level=3`** | **0.7** | **0.3** ❗ |

`text/html;level=3` 能匹配到的只有 `text/*;q=0.3`（specificity 1）与 `*/*;q=0.5`
（specificity 0），最具体的是前者 ⇒ **0.3**。官方印的 0.7 是抄 RFC 7231 §5.3.2 时的
遗留——那一版的 Accept 里含 `text/html;q=0.7`，0.7 才成立。
**Errata ID 7306（2022-11-09 Verified）已确认应改为 0.3。**

本 demo 不悄悄折中：两条断言同时存在——`quality(...) == 0.3` 且
`0.7 != quality(...)`。

### 2.3 协商失败怎么办

- 全部 q 为 0 且服务端不愿给默认表示 → **406**（§15.5.7）。
- 服务端也**可以无视** Accept 直接给默认表示（§12.4.1 明确允许）。
- **415**（§15.5.16）方向相反：是**请求**内容的格式不被支持，不是响应。

### 2.4 Vary

`Vary = #( "*" / field-name )`。

- 列出字段名：除非后续请求在这些头上的取值逐一相同，缓存 MUST NOT 复用
  （§12.5.5 用途 1）；两边都缺视为相同。
- `*`：方差无限，不转发请求就无法判定；代理 MUST NOT 生成 `*`。

版本化用 `Accept` 做内容协商时，**必须**配 `Vary: Accept`，否则中间缓存会把 v2 的
表示发给要 v1 的客户端。

---

## 三、三种版本载体的优先级（本 demo 口径）

| 载体 | 例子 | 可缓存性 |
| --- | --- | --- |
| URI 路径 | `/v2/orders` | 最好（URI 即缓存键） |
| 媒体类型 | `Accept: application/vnd.example.v2+json` | 需要 `Vary: Accept` |
| 自定义头 | `API-Version: 4` | 需要 `Vary: API-Version` |

**规范并未规定三者的优先级**，本 demo 的 `resolve_version` 采用工程惯例
「显式头 > 媒体类型 > 路径 > 默认」，并把它写成显式函数，好让
"三种载体可以并存且互相冲突"这件事能被断言——真正重要的是**选定一种并写进文档**，
而不是靠猜。

---

## 四、运行

```bash
cd python && python selfcheck_versioning.py   # 90 断言全绿
cd python && python main.py
cd go     && go run versioning.go negotiate.go  # 本机无 Go 工具链，人工审查 + 静态检查
```

文件：`python/sunset.py`（两个日期格式 + 生命周期）· `python/negotiate.py`
（Accept / qvalue / Vary / 三种版本载体）· `python/harness.py` +
`python/selfcheck_versioning.py` · `go/versioning.go` + `go/negotiate.go`。

> 实现踩坑记录：`media_range_matches` 初版先 `media_type.partition("/")` 再切参数，
> 于是 `text/plain;format=flowed` 的 subtype 被算成 `plain;format=flowed`，
> 带参数的 media-range 全部匹配不上（Table 5 第 1、5 行当场挂掉）。
> **必须先按 `;` 切参数、再按 `/` 切类型。**

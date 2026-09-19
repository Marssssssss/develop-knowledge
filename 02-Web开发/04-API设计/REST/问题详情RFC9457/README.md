# RFC 9457 Problem Details：HTTP API 的错误响应格式

## 简介

HTTP 状态码只能表达"大致错在哪一类"，表达不了"到底错在哪一项"。RFC 9457（obsoletes RFC 7807）定义了
一种机器可读的 **problem detail** 对象，媒体类型 `application/problem+json`（XML 变体为
`application/problem+xml`），让 API 不必再各自发明 `{"code":..., "msg":...}` 之类的错误格式。

本 demo 用 **Python（可实跑）+ Go** 两侧复刻规范里"可判定"的条款：成员类型校验、相对 URI 解析、
扩展成员规则、`about:blank` 的 title 推导、XML 数组约定，以及"多问题时只报最紧急"的建议。

## 原理详解

### 1. 五个保留成员与"类型不符即忽略"

| 成员 | 类型 | 语义 |
| --- | --- | --- |
| `type` | string（URI reference） | 问题**类型**的主标识符，缺省 `about:blank` |
| `status` | number（整数，100..599） | 仅**参考**；生成器 MUST 与真实状态码一致 |
| `title` | string | 问题类型的简短摘要，SHOULD **不随具体发生次数变化**（本地化除外） |
| `detail` | string | 本次发生的具体说明；消费者 SHOULD NOT 去解析它 |
| `instance` | string（URI reference） | 本次发生（occurrence）的标识 |

§3.1 的一句关键话决定了解析器的写法：

> If a member's value type does not match the specified type, the member MUST be ignored --
> i.e., processing will continue as if the member had not been present.

也就是说 `"status": "404"`（字符串）不是"报错"，而是**当作 status 成员不存在**。本 demo 的
`Problem.parse()` 把被丢掉的成员记进 `ignored` 列表，可直接断言。

注意一个真实实现里的坑：**JSON 的 `true`/`false` 不是 number**。Python 里 `isinstance(True, int)`
为 `True`，照直觉写 `isinstance(v, (int, float))` 会把 `"status": true` 当成数字 1 而放行；
本实现显式排除 `bool`。

### 2. `type` 是 URI，且相对引用会咬人

`type` 是 URI **reference**，相对引用按文档 base URI 解析。规范给的例子正是两个不同资源上的
同名相对 `type` 解析成两个不同的绝对 URI：

```text
https://api.example.org/foo/bar/123   + "example-problem" → https://api.example.org/foo/bar/example-problem
https://api.example.org/widget/456    + "example-problem" → https://api.example.org/widget/example-problem
```

所以规范 RECOMMENDED 用绝对 URI；确要用相对 URI 就带上完整路径（如 `/types/123`）。
本 demo 实现了 RFC 3986 §5.2.4 的 `remove_dot_segments`，相对解析可断言。

`type` 允许不可解析的 URI（`tag:example@example.org,2021-09-17:OutOfLuck`），但规范鼓励用可解析的：
一旦以后想做成"点进去看文档"，从 tag 换成 http URI 等于**换了一个身份**，是 breaking change。

### 3. `status` 是 advisory，但有个 MUST

`status` "is only advisory"，存在的价值是：响应内容被持久化到日志/队列、或状态码被中间盒改写之后，
消费者仍能知道源服务器当初给的是什么。但生成器 MUST 让两者一致 —— 否则代理、负载均衡、防火墙这类
**只看真实状态码**的通用 HTTP 软件会与 body 里的说法打架。§5 明确写了二者的优先级"并不清晰"。

### 4. 扩展成员

问题类型可以自定义成员（例如 `balance`、`accounts`）。两条规则：

- 消费者 **MUST ignore 不认识的扩展**（这是类型能演进的前提）；
- §4 对命名有 SHOULD：以 ALPHA 开头、只由 `ALPHA / DIGIT / "_"` 组成、**长度 ≥ 3**
  （原因很实在：要能序列化成 XML 的 Name）。所以 `a-b`、`_x`、`ab` 都不合规。

### 5. `about:blank` 与 title 推导

`about:blank` 是规范注册的唯一一个问题类型，含义是"除状态码本身之外没有附加语义"，
推荐状态码为 **N/A**。此时 `title` SHOULD 等于该状态码的推荐短语（404 → `Not Found`），
可再按 `Accept-Language` 本地化。因为它是 `type` 的缺省值，**任何不带 `type` 的对象都在隐式使用它**。

### 6. 多个问题怎么办

§3 的 RECOMMENDED：**只报最相关/最紧急的那个**。规范原话是"generic batch problem types ...
do not map well into HTTP semantics"。本 demo 用一个 urgency 表演示挑选逻辑。

### 7. XML 变体的数组约定（附录 B）

XML 里判断数组的规则很反直觉：一个元素**只含若干名为 `i` 的子元素**才算数组：

```xml
<accounts><i>https://example.net/account/12345</i><i>https://example.net/account/67890</i></accounts>
```

不是重复同名元素。命名空间固定 `urn:ietf:rfc:7807`，扩展也 MUST 落在这个命名空间里。

## 对比

| 维度 | RFC 7807（已废） | RFC 9457 |
| --- | --- | --- |
| 问题类型注册表 | 无 | §4.2 建了 "HTTP Problem Types" 注册表（Specification Required 策略） |
| 多问题处理 | 未澄清 | §3 明确 RECOMMENDED 只报最相关的一个 |
| 不可解析 type URI | 未给指导 | §3.1.1 给了 tag URI 方案与取舍分析 |
| 注册项 | — | `about:blank`（Title: "See HTTP Status Code"） |

## 环境

- Python 3.8+（仅标准库）
- Go 1.18+（仅标准库；本机无 Go 工具链，代码经人工审查 + 括号配平校验）

## 运行方式

```bash
cd python && python check.py     # 44 项断言，全绿
cd go     && go run problem_details.go
```

## 关键代码

```python
def _is_json_number(v: Any) -> bool:
    # 注意：Python 里 bool 是 int 的子类，而 JSON 的 true/false 不是 number。
    return isinstance(v, (int, float)) and not isinstance(v, bool)

def is_valid_extension_name(name: str) -> bool:
    if not isinstance(name, str) or len(name) < 3:
        return False
    if not name[0].isalpha():
        return False
    return all(ch.isalnum() or ch == "_" for ch in name)
```

```go
func isIntVal(v interface{}) (int, bool) {
	switch n := v.(type) {
	case int:
		return n, true
	case float64:
		if n == float64(int(n)) {   // encoding/json 把一切数字都解成 float64
			return int(n), true
		}
		return 0, false
	default:
		return 0, false
	}
}
```

## 性能边界

- 解析是 O(成员数)，无回溯；`remove_dot_segments` 对路径长度线性且有界。
- 扩展成员无数量上限，恶意 body 可以让 `extensions` 无限膨胀 —— 生产实现应给 body 大小与成员数设限。
- "类型不符即忽略"意味着**解析失败永远不会抛错**，错误会被静默吞掉：这是规范的选择，
  但也意味着 `detail` 里写错类型时你不会得到任何提示。

## 注意事项与常见坑

1. **`status` 用字符串/布尔/小数表示会被整个忽略**，而不是报错 —— 排障时看到"status 没生效"先查类型。
2. **相对 `type` 在不同资源上会解析成不同 URI**，客户端若拿字符串直接比 `type` 就会漏匹配。用绝对 URI。
3. **消费者 SHOULD NOT 自动 dereference `type` URI**（§3.1.1），除非是在给开发者看的调试工具里。
4. **不要把 `detail` 当协议字段解析**：它是给人看的，机器可读信息放扩展成员。
5. **`about:blank` 不是"没有类型"**，而是一个注册过的类型，且是 `type` 的缺省值。
   "没有 type 成员"与"type 为 about:blank"在语义上等价。
6. **错误内容会泄漏实现细节**（§5）：`instance` 指向的诊断资源不要放堆栈转储。
7. 真正通用的问题（例如对 PUT 回 403）**不该**发明新的 problem type，状态码本身就够了（§4）。
8. 客户端没在 `Accept` 里列 `application/problem+json` 时，服务器**照样可以**返回它（§3 末段）。

## 参考资料

- RFC 9457, *Problem Details for HTTP APIs*（Nottingham / Wilde / Dalal, 2023-07，obsoletes RFC 7807）
  — <https://www.rfc-editor.org/rfc/rfc9457.txt>（全文实读：§1–§7 + 附录 A/B/C/D）
- RFC 9110 §15.5/§15.6，*HTTP Semantics*（状态码与推荐短语）
  — <https://www.rfc-editor.org/rfc/rfc9110.txt>（实读）
- RFC 3986 §5.2.4，*URI Generic Syntax*（`remove_dot_segments`）
- IANA HTTP Problem Types 注册表 — <https://iana.org/assignments/http-problem-types>

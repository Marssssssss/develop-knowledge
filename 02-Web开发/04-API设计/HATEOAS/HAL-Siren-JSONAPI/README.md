# 606 · HATEOAS 的三种表示形态：HAL / Siren / JSON:API

> 参考资料都是本轮**实际抓下来读过**的原文，不是凭模型记忆复述：
> - `draft-kelly-json-hal-08`（IETF 存档全文，18 925 字节）— <https://www.ietf.org/archive/id/draft-kelly-json-hal-08.txt>
> - Siren 规范 README（GitHub `kevinswiber/siren` master，10 577 字节）— <https://github.com/kevinswiber/siren>
> - JSON:API v1.1（Archived Copy，191 457 字节 HTML）— <https://jsonapi.org/format/1.1/>

三者都在回答同一个问题：**除了数据本身，服务端还应该把"下一步能干什么"写进表示里吗？**
HAL 只给链接，Siren 给链接 + 动作，JSON:API 给链接 + 一整套查询协议。

---

## 一、HAL：把资源状态与超链接分开

`application/hal+json`，根对象**必须**是 Resource Object，只有两个保留属性
`_links` 与 `_embedded`，其余属性一律是资源当前状态（§3、§4）。

| 规则 | 出处 | 本 demo 落点 |
| --- | --- | --- |
| `href` 是 REQUIRED | §5.1 | `validate_resource` 缺 href 即报错 |
| `href` 可以是 URI 或 URI Template | §5.1 | `is_uri_template` 只认花括号 |
| 是模板时 `templated` SHOULD 为 true | §5.2 | 记一条 "URI Template 但 templated 不为 true" |
| `templated` 非 true 的一切取值视为 false | §5.2 | `is_templated` 用 `is True` 而不是真值判断 |
| `_links` / `_embedded` 的值可以是单对象或数组 | §4.1.1 / §4.1.2 | `as_link_list` / `as_resource_list` 归一 |
| CURIE 由根上 `rel=curies` 的链接建立 | §8.2 | `curies()` + `expand_rel()` |

最容易写反的两条：

1. **`templated` 不是"有这个键就算模板"**。规范原文是 "Its value SHOULD be considered
   false if it is undefined **or any other value than true**"——字符串 `"true"` 也算 false。
   所以 `is_templated` 必须写成 `link.get("templated") is True`；写成 `bool(link.get(...))`
   会把 `"true"` 判成真。
2. **CURIE 展开要按"已声明前缀"查表，不是看见冒号就切**。`zz:widgets` 在未声明 `zz` 的
   文档里必须原样返回；`expand_rel` 里加了这条负向断言。

### 超文本缓存模式（§8.3）

客户端**可以**优先读同 rel 的嵌入资源，省掉一次真实请求：

```text
Before:  _links.author = /people/alan-watts        → ("link", href, 1 次请求)
After :  _embedded.author = {name: "Alan Watts"}   → ("embedded", 资源, 0 次请求)
```

规范同时要求服务端 **SHOULD NOT** 把链接整体换成嵌入——因为客户端是否支持这个技巧是
OPTIONAL 的。所以 demo 里"有嵌入"和"有链接"是**并存**的两种写法，不是二选一。

---

## 二、Siren：在链接之上再加"动作"

`application/vnd.siren+json`。与 HAL 最大的差别是 `actions`：HAL 明确说
"Why does HAL have no forms?"，Siren 则把状态迁移直接写进表示。

| 元素 | 约束（原文章句） | 本 demo 落点 |
| --- | --- | --- |
| `class` | MUST 是字符串数组（实体/链接/动作/字段都有） | `validate_entity` |
| `links[].rel` | MUST 是非空字符串数组，Required | 负向用例传字符串 `"self"` 应被拒 |
| `entities[]` | 含 `href` 即嵌入链接；否则是嵌入表示且 MUST 含 `rel` | `partition_entities` |
| `actions[].name` | Required，且在同一实体内 MUST 唯一 | 重名报"行为未定义" |
| `method` | 省略时按 GET | `EffectiveMethod` |
| `type` | 省略且存在 `fields` 时默认 `application/x-www-form-urlencoded` | `EffectiveType` |
| `fields[].type` | 取值域是 HTML5 input type（19 种） | `FIELD_INPUT_TYPES` |

**最实用的一条**：`type` 的默认值只在"有 fields"时成立。规范原文是 "When omitted
**and the `fields` attribute exists**, the default value is ..."。所以
`{"name": "g", "href": "/g"}`（无 fields）时本 demo **不臆造** Content-Type，返回 `None`
并写成断言——这是刻意的，不是漏实现。

序列化时 hidden 字段照样进请求体：

```text
POST http://api.x.io/orders/42/items
Content-Type: application/x-www-form-urlencoded
orderNumber=42&productCode=X-1&quantity=2
```

---

## 三、JSON:API：链接 + 一整套查询协议

`application/vnd.api+json`。它的约束密度远高于前两者，这里只钉住最容易出错的几处。

### 3.1 媒体类型参数只有两个

"MUST NOT be specified with any media type parameters **other than ext and profile**"。
带 `charset` 之类 → **415**；Accept 里所有 JSON:API 实例都被别参数修饰 → **406**。

### 3.2 顶层三选一 + 两条互斥

至少含 `data` / `errors` / `meta` 之一；`data` 与 `errors` **MUST NOT 共存**；
没有顶层 `data` 时 `included` **MUST NOT** 出现。
（规范还允许"已应用扩展定义的成员"，但那要知道扩展才能判定，本 demo 不覆盖。）

### 3.3 成员名：首尾必须是"全局允许字符"

允许出现在中间但不能在首尾的只有三个：`-`、`_`、空格。
保留字符一长串（`+ , . [ ] ! " # $ % & ' ( ) * / : ; < = > ? @ \ ^ \` { | } ~`、DEL、C0 控制符），
其中 **`@` 唯一例外：只能作首字符**（@-Members）。

| 名字 | 判定 | 原因 |
| --- | --- | --- |
| `first-name` | 合法 | 连字符在中间 |
| `-first` | 非法 | 连字符不能开头 |
| `first name` | 合法（不推荐） | 空格在中间 |
| `first.name` | 非法 | 点号是保留字符（关系路径分隔符） |
| `@member` | 合法 | @ 作首字符 |
| `a@b` | 非法 | @ 不得在非首位 |

### 3.4 查询参数：纯小写名字被规范保留

实现自定义参数的基名 **MUST** 是合法成员名 **且至少含一个非 a-z 字符**，否则 400。
规范明说了动机："By forbidding the use of query parameters that contain only the
characters [a-z], JSON:API is reserving the ability to standardize additional query
parameters later."

参数族的形状是「基名 + 若干 `[]` / `[合法成员名]` / `[点分隔合法成员名列表]`」，
所以 `filter[_]` **非法**——`_` 不是合法成员名（下划线不能作首尾）。

### 3.5 排序、稀疏字段集、include

- `sort=-created,title`：减号前缀为降序，其余 MUST 升序，多个字段按给出顺序依次应用。
  实现上用"从后往前稳定排序"，让首位字段成为主键。
- `fields[articles]=title,body`：未请求的字段 MUST NOT 出现在响应里；`type`/`id` 不是
  "字段"，不在裁剪范围内。
- `include=comments.author,ratings`：逗号分隔路径、路径内点分隔；空值表示不返回相关资源。

---

## 四、三形态对照

| 维度 | HAL | Siren | JSON:API |
| --- | --- | --- | --- |
| 媒体类型 | `application/hal+json` | `application/vnd.siren+json` | `application/vnd.api+json` |
| 链接容器 | `_links`（对象，rel → 对象或数组） | `links`（数组，每项含 `rel` 数组） | 顶层 / 资源 / 关系各自 `links` |
| 嵌入 | `_embedded` | `entities` | `included`（扁平数组） |
| 动作/表单 | 无（规范明确不做） | `actions` + `fields` | 无（靠文档与扩展） |
| 关系语义 | 只给 rel | `rel` + `class` 分离 | `relationships` + resource linkage |
| 查询协议 | 无 | 无 | include / fields / sort / page / filter |
| 扩展机制 | `profile` 媒体类型参数 | 无 | `ext` 与 `profile` 两个媒体类型参数 |

一句话选型：**只要链接**用 HAL；**要把"能发什么请求"写进表示**用 Siren；
**还要约定一套通用查询/写入协议**用 JSON:API。

---

## 五、运行

```bash
cd python && python selfcheck_hateoas.py   # 118 断言全绿
cd python && python main.py                # 三形态并排演示
cd go     && go run hateoas.go jsonapi.go  # 本机无 Go 工具链，人工审查 + 静态检查
```

文件：`python/hal.py`（HAL 模型）· `python/siren.py`（Siren 模型）·
`python/jsonapi.py`（文档结构）+ `python/jsonapi_query.py`（查询参数族）·
`python/harness.py` + `python/selfcheck_hal_siren.py` + `python/selfcheck_jsonapi.py` ·
`go/hateoas.go` + `go/jsonapi.go`。

自检按"误报集 + 漏报集"成对构造：每条"应该合法"的用例都配一条"应该被拒"的用例，
`expect_errors` 同时校验该报的报了、不该报的没报。

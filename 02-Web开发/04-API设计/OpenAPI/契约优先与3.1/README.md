# 608 · OpenAPI 3.1 契约优先：从契约到校验

> 本轮实际抓下来读过的原文：
> - OpenAPI Specification **v3.1.1**（`spec.openapis.org/oas/v3.1.1` 全文 576 103 字节）— <https://spec.openapis.org/oas/v3.1.1>
> - JSON Schema Draft 2020-12 Core（342 931 字节）— <https://json-schema.org/draft/2020-12/json-schema-core>

3.1 相对 3.0 最大的变化只有一句话：**Schema Object 变成了 JSON Schema 2020-12 的超集**。
契约不再是一套"长得像 JSON Schema"的自有方言，而是真的 JSON Schema。

---

## 一、文档级：一份 OAD 的下限与版本号

| 规则 | 出处 | 落点 |
| --- | --- | --- |
| `openapi` REQUIRED，与 `info.version` 无关 | §4.8.1 | `validate_document` |
| OAD **MUST 至少含 `paths` / `components` / `webhooks` 之一** | §1.1 | 三条路径各一条断言 |
| 版本用 major.minor.patch；**patch 只纠错与澄清** | §1.2 | `same_feature_set` 只比 major.minor |
| `jsonSchemaDialect` MUST 是 URI 形式 | §4.8.1 | `is_uri` |
| `license.identifier`（SPDX）与 `license.url` **互斥** | §4.8.2.1 | `LicenseIssue` |
| `tags` 里每个名字 MUST 唯一 | §4.8.1 | `validate_document` |
| `operationId` MUST 全局唯一且**大小写敏感** | §4.8.9.1 | `List ≠ list` 是两条不同 id |
| Reference Object 只允许 `$ref` / `summary` / `description` | §4.8.23 | 额外成员 SHALL 被忽略 |

### 1.1 方言怎么定

```text
$schema（Schema Object 作为资源根时）  ← 永远覆盖
  ↓ 未给
jsonSchemaDialect（OpenAPI Object）     ← 文档级默认
  ↓ 未给
https://spec.openapis.org/oas/3.1/dialect/base   ← OAS 方言，工具 MUST 支持
```

规范明说 "Tooling MUST support the OAS dialect schema id, and MAY support additional
values of `$schema`"，而 `$schema` 只在**资源根**上生效。

### 1.2 security 是"备选"，不是"全部满足"

这是最常被写反的一条。§4.8.1 原文："**Only one** of the Security Requirement Objects
need to be satisfied to authorize the request." 要显式声明"可不鉴权"，就往数组里
塞一个空对象 `{}`：

```python
security_satisfied([{"oauth": ["admin"]}, {"apiKey": []}], have)  # → (True, "apiKey")
security_satisfied([{}], have)                                    # → (True, "empty")
security_satisfied([{"oauth": ["admin"]}], have)                  # → (False, None)
```

---

## 二、Schema Object：3.1 与 3.0 的几处 breaking change

### 2.1 `nullable` 没了，改用 `type` 数组

| 3.0 | 3.1 |
| --- | --- |
| `{"type": "string", "nullable": true}` | `{"type": ["string", "null"]}` |

`deprecated_30_keywords()` 会把 `nullable` 与**布尔形式的** `exclusiveMinimum` /
`exclusiveMaximum` 一起报出来——后者在 2020-12 里是数值，不是配 `minimum` 用的开关。

### 2.2 整数是"数学上"定义的

§4.4："JSON itself does not make that distinction... both 1 and 1.0 are equivalent,
and are both considered to be integers."

所以 `is_integer(1.0)` 必须为 `True`，而 `is_integer(True)` 必须为 `False`
（Python 里 `bool` 是 `int` 的子类，不显式挡掉就会漏）。

### 2.3 关键字与 format **不会隐式要求类型**

§4.4："JSON Schema keywords and formats do **NOT** implicitly require the expected
type." `pattern`、`date-time` 对非字符串一律自动通过。要约束类型必须显式写 `type`。

`format` 在默认词汇表下更是**注解而非断言**（§4.4.1）：
`{"type": "string", "format": "email"}` **不会**拒绝 `"nope"`。
OAS 自带的 format 里 `int32/int64/float/double` 的 JSON Data Type 都是 `number`，
只有 `password` 是 `string`。

### 2.4 二进制描述换写法

| 场景 | 3.0 | 3.1 |
| --- | --- | --- |
| raw | `type: string` + `format: binary` | `contentMediaType: image/png`（`type` **省略**） |
| encoded | `type: string` + `format: byte` | `type: string` + `contentMediaType` + `contentEncoding: base64` |

还有一条容易被忽略的：`contentMediaType` 如果与所在 Media Type Object 的 key
（或 Encoding Object 的 `contentType`）**矛盾，`contentMediaType` SHALL 被忽略**。

---

## 三、discriminator：只是 hint，不得改变结论

§4.8.24.4.1 三条硬约束：

1. `discriminator` 指向的属性 **MUST 是 required**。
2. **MUST NOT 改变校验结论** —— 它只是帮反序列化挑分支的提示。
   本 demo 的断言方式是"删掉 `discriminator` 后重新校验，结论必须逐条相同"，
   而不是"选中哪个分支就算通过"。
3. 用在 `oneOf` / `anyOf` 上时，所有可能的 schema **MUST 显式列出**；
   `allOf` 形式只服务于非校验用途（因为没有任何标准关键字能把父 schema 连到子 schema）。

判别值的两种来源（规范原文）：
- 用 schema 名；
- **用该属性在分支里被覆盖的新值**（若存在，优先于 schema 名）。

本 demo 里 `Cat` 写了 `properties.petType.const = "cat"`，于是判别值取 `"cat"`
而不是 schema 名；`mapping` 存在时则走 mapping。

```text
{"petType": "dog", "bark": true}  → 选中 Dog（下标 1），整体 True，选中分支 True
{"petType": "cat", "meow": true}  → 选中 Cat（下标 0），整体 True，选中分支 True
{"petType": "dog", "meow": true}  → 选中 Dog（下标 1），整体 False，选中分支 False
{"petType": "bird"}               → 无选中，整体 False
```

注意第三行：**选中了分支但结论仍是 False**，这正是"discriminator 不改变结论"的体现——
它挑错了方向也不会让校验变宽松。

---

## 四、契约优先能带来什么

把契约当唯一事实来源后，下面几件事都能从同一份文档里推出来：

- 请求/响应体校验（本 demo 的 `validate`）；
- 反序列化时的多态分派（`discriminator`）；
- 客户端 / 服务端骨架代码生成（`operationId` 唯一且大小写敏感，正好当函数名）；
- 文档、Mock、契约测试。

反过来，`operationId` 重复、`tags` 重名、`license.identifier` 与 `url` 同时出现这类
"文档能解析但语义上是错的"问题，只有在**显式校验契约**时才会暴露。

---

## 五、运行

```bash
cd python && python selfcheck_openapi.py   # 114 断言全绿
cd python && python main.py
cd go     && go run oas31.go schema.go     # 本机无 Go 工具链，人工审查 + 静态检查
```

文件：`python/oas31.py`（文档级）· `python/schema.py`（Schema Object 级 +
discriminator + 二进制）· `python/harness.py` + `python/selfcheck_openapi.py` ·
`go/oas31.go` + `go/schema.go`。

> 实现踩坑：`pick_by_discriminator` 初版只认未解析的 `$ref` 末段，
> 于是 `$ref` 展开后（分支变成完整 schema）**所有实例都选不中**。
> 改成 `_branch_value()` —— 先看分支里该属性是否被 `const` / `enum[0]` 覆盖，
> 没有再退回 schema 名（已解析时取 `title`）。

# SBOM 格式对比与漏洞关联：PURL、VEX 与两套依赖图

## 简介

- SBOM（软件物料清单）的两种主流格式 **CycloneDX** 与 **SPDX** 解决的是同一个问题（把「这个制品里有什么」机器可读地写出来），但**数据模型不同**：CycloneDX 是「组件 + `bom-ref` 引用图 + 内联漏洞」，SPDX 是「元素 + `relationship` 关系表 + externalRef」。
- 二者唯一的**稳定交集**是 **PURL**（Package URL）：它是跨格式关联 CVE 的实际主键。
- 本 demo 做三件事：PURL 的规范化、CycloneDX VEX 的判定语义、两套依赖图（CycloneDX `dependencies` ↔ SPDX `DEPENDS_ON`）的互转。

## 原理详解

### 1. PURL 的七个组件

```
scheme:type/namespace/name@version?qualifiers#subpath
```

规范（purl-spec 第 5 章）逐条规定：

| 组件 | 关键规则 |
| --- | --- |
| scheme | 恒为 `pkg`；`pkg://` 这种「冒号后跟若干斜杠」的写法解析器**应剥离**这些斜杠 |
| type | 只允许 ASCII 字母数字与 `.` `-`，必须以字母开头；**不做百分号编码**；大小写不敏感，规范形式小写 |
| namespace | 可选；段之间用 `/`；前后多余斜杠无意义；每段都要百分号编码 |
| name | 百分号编码；namespace 非空时用 `/` 前缀 |
| version | 百分号编码；`@` 前缀；**是「不透明字符串」，规范不做任何归一化** |
| qualifiers | `key=value` 用 `&` 连接；**key 必须小写**、**空值等同于该 key 不存在** |
| subpath | `#` 前缀；丢弃空段与 `.`、`..` |

### 2. 百分号编码的豁免集

规范 5.4 定义得很死：**字母数字 + `.-_~`** 属于 "allowed set"；`:/@?=&#` 是 "delimiters"，在组件内部出现时必须编码；但**冒号 `:` 无论是否作为分隔符都不编码**；空格必须编码为 `%20`。

所以 `PctEncode("a:b") == "a:b"`，而 `PctEncode("my pkg") == "my%20pkg"`。

### 3. 构造 PURL 的顺序（how-to-build）

关键一步在 qualifiers：**先**把每个 `key=value` 拼成字符串（key 小写、value 编码、空值丢弃），**再按字符串字典序排序**，最后用 `&` 连接。

```
pkg:npm/foo@1.0.0?b=2&a=1   →   pkg:npm/foo@1.0.0?a=1&b=2
pkg:npm/foo?A=1             →   pkg:npm/foo?a=1
pkg:npm/foo?a=              →   pkg:npm/foo
```

排序的是**整个 `key=value` 串**而不是 key，因此 `a=10` 会排在 `a=9` 前面。

### 4. CycloneDX 的图模型

- 每个 component 有 `bom-ref`，**同一 BOM 内必须唯一**。
- 依赖不是嵌在组件里，而是顶层的 `dependencies[]`：`{ ref, dependsOn: [bom-ref] }`。
- 漏洞是**内联**在 BOM 里的 `vulnerabilities[]`，通过 `affects[].ref` 指回 `bom-ref`。`ref` 既可以是 bom-ref，也可以是 BOM-Link（`urn:cdx:...`）。
- `affects[].versions[]` 每一项必须**恰好**给出 `version` 或 `range` 之一（schema 是 `oneOf`），`status` 缺省为 `affected`，枚举只有 `affected` / `unaffected` / `unknown` 三项。
- `range` 用的是 **vers** 语法（`vers:<type>/<constraint>`，多个约束用 `|` 分隔）。

### 5. VEX：`analysis.state` 与 `justification`

`analysis.state` 六值枚举：

```
resolved | resolved_with_pedigree | exploitable | in_triage | false_positive | not_affected
```

`justification` 九值枚举（解释「为什么不受影响」）：

```
code_not_present | code_not_reachable | requires_configuration | requires_dependency
requires_environment | protected_by_compiler | protected_at_runtime
protected_at_perimeter | protected_by_mitigating_control
```

注意 **`not_affected` 与 `affects[].versions[].status=unaffected` 是两层**：前者是「人工/自动分析的结论」，后者是「某个版本区间的事实声明」。判定「这个版本受不受影响」只看后者。

### 6. SPDX 的关系模型

SPDX 用 `relationships` 表表达一切：

| 关系 | 含义 |
| --- | --- |
| `DESCRIBES` | 文档描述该包；**当文档含多于一个 package 时是必需的关系** |
| `CONTAINS` | 包含（文件/包） |
| `DEPENDS_ON` | A 依赖 B |
| `DEPENDENCY_OF` | A 是 B 的依赖 —— 与 `DEPENDS_ON` **方向相反** |

`SPDXID` 必须以 `SPDXRef-` 开头。PURL 放在 package 的 `externalRefs` 里，`referenceCategory` 为 `SECURITY`（规范 F.2 节明确建议用 CPE / PURL / SWID 做漏洞关联）。

### 7. 两套依赖图互转

| CycloneDX | SPDX |
| --- | --- |
| `dependencies[].ref → dependsOn[]` | `(ref) DEPENDS_ON (each)` |
| `metadata.component` | ` DESCRIBES` 的起点 |

`DEPENDENCY_OF` 是 `DEPENDS_ON` 的**逆边**，归一化时应翻转，否则依赖方向会整体反过来。

## 为什么需要 PURL 而不是「名字 + 版本」

| 方案 | 问题 |
| --- | --- |
| 名字 + 版本 | 同一名字在不同生态里是不同东西（`log4j` 只有 Java，`foo` 到处都是） |
| CPE | 语法复杂、需要人工维护字典、对现代包管理器覆盖不足 |
| **PURL** | 自带 type（生态）+ namespace + name + version，可机械推导、可规范化比较 |

**同一组件的两种写法必须落到同一个 key**——这正是「规范化」存在的意义：`pkg:NPM/foo@1.0.0?arch=x86` 与 `pkg:npm/foo@1.0.0?arch=x86` 是同一条记录。

## 环境准备与运行

```bash
cd python && python selfcheck_sbom.py     # 46 条断言，全绿打印 ALL OK
cd go && go run .
```

## 关键代码

| 文件 | 作用 |
| --- | --- |
| `python/purl.py` | PURL 解析、百分号编码、规范化构造、跨格式主键 |
| `python/main.py` | vers 区间判定、CycloneDX VEX 判定、SPDX 关系归一化、依赖闭包 |
| `python/selfcheck_sbom.py` | 46 条断言 |
| `go/sbom.go` | 同算法的 Go 转写 |

## 性能边界与注意事项

- **版本不能用字符串比较**：`"1.10" < "1.9"` 为真，但语义上 `1.10 > 1.9`。必须按数值段比较（本 demo 的 `_cmp` / `CmpVersion`）。
- **`range` 是 vers 语法**，本 demo 只实现了 `>=` `>` `<=` `<` `!=` `==` 与裸版本六种约束且多约束取合取；未支持的写法**抛异常而不是静默放行**——静默放行会让「扫不出漏洞」看起来像「没有漏洞」。
- **VEX 判定是「首个匹配胜出」**，因此同一 `affects` 里条目的**顺序会影响结论**。
- `analysis.state` 与 `affects[].status` 是两个不同维度，混用会导致把「已修复」当成「不受影响」。
- 完整注意事项见 [`NOTES.md`](./NOTES.md)。

## 参考与展望

- 未完成：`vers` 语法的完整实现（本 demo 只是子集）；CycloneDX 的 `compositions`（声明 BOM 完整性：`complete` / `incomplete_*`）与 `component.components` 嵌套子组件的建模。
- 可继续：把 SPDX 的 `PackageVerificationCode` 与 `filesAnalyzed` 接进来做「SBOM 与源码是否一致」的校验。

## 参考资料（实际阅读过的权威来源）

- [package-url/purl-spec — Clause 5 Package URL Specification](https://github.com/package-url/purl-spec/blob/main/docs/specification/standard/Clause-5-Package-URL-Specification.md) — 七组件的逐条规则、百分号编码豁免集、大小写折叠
- [package-url/purl-spec — How to build a PURL](https://github.com/package-url/purl-spec/blob/main/docs/specification/how-to-build.md) — qualifier 排序与空值丢弃、subpath 段过滤
- [CycloneDX specification — `schema/bom-1.6.schema.json`](https://github.com/CycloneDX/specification/blob/master/schema/bom-1.6.schema.json) — `vulnerability` / `affects` / `affectedStatus` / `impactAnalysisState` / `impactAnalysisJustification` / `aggregateType` 的枚举与 required
- [SPDX 2.3 — Relationships between SPDX elements](https://github.com/spdx/spdx-spec/blob/v2.3/chapters/relationships-between-SPDX-elements.md) — `DESCRIBES` / `CONTAINS` / `DEPENDS_ON` / `DEPENDENCY_OF` 的定义与示例
- [SPDX 2.3 — External repository identifiers](https://github.com/spdx/spdx-spec/blob/v2.3/chapters/external-repository-identifiers.md) — F.2 Security 节关于用 CPE/PURL/SWID 做漏洞关联的建议、F.3.5 purl 的定位格式
- [SPDX 2.3 — Composition of an SPDX document](https://github.com/spdx/spdx-spec/blob/v2.3/chapters/composition-of-an-SPDX-document.md)
- [SPDX 2.3 — Package information](https://github.com/spdx/spdx-spec/blob/v2.3/chapters/package-information.md)

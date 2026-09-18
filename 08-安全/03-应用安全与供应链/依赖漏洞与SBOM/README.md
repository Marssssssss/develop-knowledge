# 依赖漏洞判定：版本区间 → 传递闭包 → 可达性剪枝 → SBOM

一个包「有 CVE」和「你的程序会被这个 CVE 打到」之间隔着**两级过滤**：

1. **版本区间匹配**（纯语法）：解析出来的版本落在 CVE 影响区间内吗？
2. **可达性**（语义）：从程序入口出发，沿调用图**走得到**那个有洞的函数吗？

只做第 1 步的工具会把「装了但一行都没调用」的依赖也报成高危——这是告警疲劳的主要来源。
本 demo 用一个 5 包 / 4 CVE 的 fixture 把两级过滤各自剪掉了什么量化出来。

## 原理详解

### 1. SemVer 的优先级不是字典序（semver.org §11）

`X.Y.Z` 三段按**数值**比较；`X/Y/Z` 相等时，**预发布版本优先级低于正式版**；
两个预发布版本逐段比较，规则是：

- 纯数字段按**数值**比 → `beta.2 < beta.11`（字符串比较会得出相反结论）
- 含字母/连字符的段按 **ASCII 字典序**比
- **数字段优先级低于非数字段** → `1.0.0-alpha.1 < 1.0.0-alpha.beta`
- 前面都相等时，段数更多的优先级更高

还有一条常被忽略：**build metadata（`+` 之后的部分）完全不参与优先级比较**（§10），
所以 `1.0.0+build1` 与 `1.0.0+build2` 优先级相同。

### 2. `^` 在 0.x 上是陷阱（semver.org §4）

规范原文：major 为 0 时（`0.y.z`）是**初始开发期**，任何东西都可能变，
公开 API **不应视为稳定**。所以：

| 写法 | 等价区间 | 说明 |
| --- | --- | --- |
| `^1.2.3` | `>=1.2.3 <2.0.0` | 只锁 major |
| `^0.2.3` | `>=0.2.3 <0.3.0` | **0.x 时锁到 minor** |
| `^0.0.3` | `>=0.0.3 <0.0.4` | 几乎等于锁死 |
| `~1.2.3` | `>=1.2.3 <1.3.0` | 只允许 patch |

直觉上「`^` 允许兼容升级」在 `0.x` 依赖上**不成立**——它连 minor 都不让你升。
于是 0.x 依赖的安全补丁如果只发在 minor 上，`^` 会把它挡在外面。

### 3. 解析取「区间内最高版本」本身就会修掉一部分 CVE

fixture 里 `logfmt` 的 CVE 影响 `>=1.0.0 <1.0.2`，而解析结果是 **1.0.2**——
因为根约束 `^1.0.0` 允许升到 1.x 最新，补丁包被自动选中，这条 CVE **在版本层就被排除**了。

反过来 `compress` 上游只在 registry 里发布了 `0.2.7`（没有修复版），
所以只能停在受影响版本上。这就是「能不能升」和「有没有洞」是两件事。

### 4. 可达性剪掉的是「装了但调不到」

调用图（入口 `app.main`）：

```
app.main ──┬─→ httpkit.Handler ─→ codec.Decode
           ├─→ logfmt.Format
           └─→ orm.Find ─→ orm.RawQuery

compress.Inflate        ← 没有任何入边，不可达
```

`CVE-COMPRESS` 版本判定命中（`0.2.7 ∈ [0.2.0, 0.2.8)`），但 `compress.Inflate`
从 `app.main` 走不到——本 fixture 里 `codec.Decode` 并没有调用它。
所以最终告警从 3 条降到 2 条。

### 5. 结果总表

```
resolved: codec 1.4.3 / compress 0.2.7 / httpkit 2.4.0 / logfmt 1.0.2 / orm 3.1.0

CVE            pkg       ver     by_version  reachable
CVE-COMPRESS   compress  0.2.7   True        False    ← 被可达性剪枝
CVE-CODEC      codec     1.4.3   True        True     ← 真告警
CVE-ORM        orm       3.1.0   True        True     ← 真告警
CVE-LOGFMT     logfmt    1.0.2   False       False    ← 被版本过滤（已升到修复版）
```

两级过滤**各自都剪掉了东西**：版本过滤砍掉 1 条，可达性再砍掉 1 条。
如果只做版本匹配，告警数会是 3 而真实风险是 2。

## SBOM 输出

`sbom()` 产出 **SPDX 2.3** 风格文档骨架（字段名与关系类型取自 SPDX 规范 Clause 目录）：

- `documentDescribes` + `DESCRIBES` 关系：文档 → 根包
- `CONTAINS` 关系：根包 → 每个传递依赖（表达 SBOM 的层级）
- `DEPENDS_ON` 关系：包 → 它的直接依赖（**影响范围分析的关键边**）
- `externalRefs` 里的 **PURL**（`pkg:generic/<name>@<version>`）：跨仓库唯一定位组件
- `checksums`：SHA256，用于完整性校验

「`DEPENDS_ON` 边」正是第 4 节可达性分析的输入——SBOM 不只是合规清单，
它同时是**漏洞影响分析的图**。

## 代码结构

| 文件 | 内容 |
| --- | --- |
| `dep_reachability.py` | SemVer 解析/比较、`^`/`~` 求值、传递解析、可达性 BFS、SPDX 输出 |
| `dep_fixture.py` | 注册表 / 依赖边 / 根约束 / 调用图 / CVE 表（单独成模块以守 ≤300 行） |
| `dep_reachability_selftest.py` | 41 项断言（规范优先级链、前导零拒绝、0.x caret、解析结果、剪枝计数、SBOM 结构） |
| `dep_reachability.go` + `dep_fixture.go` | Go 版；fixture 单独成文件以满足 ≤300 行 |
| `dep_reachability.c` | C 版；为控制篇幅只做单条区间求值（不含多约束交集）与可达性 BFS |

## 运行

```bash
python dep_reachability.py            # 打印解析结果与 CVE 判定表
python dep_reachability_selftest.py   # 41 项断言
go run dep_reachability.go dep_fixture.go
cc -o dep dep_reachability.c && ./dep
```

## 参考资料

- <https://semver.org/> —— Semantic Versioning 2.0.0：§2 禁止前导零、§4 `0.y.z` 为初始开发期、§10 build metadata 不参与优先级、§11 优先级与预发布标识符比较规则
- <https://spdx.github.io/spdx-spec/v2.3/> —— SPDX 2.3 规范：Clause 6 `documentDescribes`、Clause 7 `packageVerificationCode`/`checksums`/`externalRefs`+PURL（Annex F）、Clause 11 `DESCRIBES`/`CONTAINS`/`DEPENDS_ON` 关系类型

# 制品晋升与语义化版本（SemVer 2.0.0 优先级 + OCI 内容寻址与 tag/digest 语义）

## 简介

CD 流水线的最后一步是"把某个已经验证过的制品发布出去"——生产环境里这件事的正确做法是
**改引用**，而不是**重新构建**：把目标 channel 的 tag 指向已经在测试环境验证过的那个
**digest**。这个动作成立的前提有两块硬语义：

- **SemVer 2.0.0**：版本号不只是字符串，它有一条严格的优先级全序，是"能不能进 prod 通道"
  这类闸门的判据（`1.0.0-rc.1 < 1.0.0`，`1.0.0 < 1.1.0`）。
- **OCI 分发规范**：`digest = '<alg>:' + Encode(H(C))` 是内容的函数，仓库**必须逐字节**
  保存客户端推上来的 manifest，因此"digest 没变"就等于"制品没被改过"。

本 demo 把这两半都写成可执行模型（Python + Go），用断言钉住官方明确写出的规则：

| 主题 | 官方要点 |
| --- | --- |
| SemVer 形式 | `X.Y.Z`，**不得有前导零**；`1.9.0 -> 1.10.0` 是数值递增 |
| 预发布 | `-alpha.1`，数字标识符**不得**有前导零；预发布**低于**对应正规版本 |
| 构建元数据 | `+build.7` **不参与**优先级比较：只差它的两个版本优先级相同 |
| 优先级 | 数字标识符按数值、含字母按 ASCII 序、**数字恒低于非数字**、前缀相等时**多者更高** |
| digest 语法 | `<algorithm>:<encoded>`；`sha256` 的 encoded **必须**小写十六进制 `[a-f0-9]{64}` |
| tag | 至多 128 字符，`[a-zA-Z0-9_][a-zA-Z0-9._-]{0,127}`；**tag 可变、digest 不可变** |
| push 顺序 | 先 blobs 后 manifest；引用缺失报 `MANIFEST_BLOB_UNKNOWN`，但 `subject` 例外 |
| tags 列表 | 必须按 ASCIIbetical 序；`n`/`last` 分页 + `Link: rel="next"`；`n=0` 返回空且无 Link |
| referrers | 支持的仓库**不得**返回 404；不支持时回退 **referrers tag 方案**（截断 + 替换） |

## 原理详解

### 1. SemVer 的优先级是全序，不是字符串序

官方给的判定链（本 demo 直接断言这一串）：

```
1.0.0-alpha < 1.0.0-alpha.1 < 1.0.0-alpha.beta < 1.0.0-beta
        < 1.0.0-beta.2 < 1.0.0-beta.11 < 1.0.0-rc.1 < 1.0.0
```

三段规则容易被忽略：

- **数字标识符按数值比**：`beta.2 < beta.11`（字符串序会得出相反结论）。
- **数字恒低于非数字**：`alpha.1 < alpha.beta`。
- **前缀相等时标识符多者更高**：`alpha < alpha.1`。

再加两条：major/minor/patch 全等时"有预发布 < 无预发布"；**构建元数据完全不参与**，
所以 `1.0.0+001` 与 `1.0.0+20130313144700` 优先级相同（本 demo 连哈希都对齐了这一点）。

### 2. 为什么"版本闸门"必须用 SemVer 而不是字符串比较

`latest`/`prod` 这类通道名不是版本，**不能**当闸门依据（本 demo 会明确拒绝）。版本闸门
应当来自**发布记录**里的当前版本（本 demo 用一个 `release_tag -> version` 映射建模）：

1. 来源 tag 必须能解析成 SemVer，否则拒绝；
2. 预发布默认不许进 prod（`allow_prerelease` 可显式放开，用于灰度）；
3. 通道当前版本不低于待晋升版本 → 拒绝降级（`allow_downgrade` 可放开）；
4. 目标 tag 已经指向同一 digest → 幂等，直接"无需动作"。

### 3. digest 是内容的函数，所以晋升不改字节

```
ID(C) == D == '<alg>:' + Encode(H(C))
```

于是有两条可直接利用的工程性质：

- **跨仓库零成本校验**：同一个 digest 在 A 仓库和 B 仓库必然是同一份字节；
- **晋升 = 移动 tag**：目标 tag 从旧 digest 指到新 digest，manifest 与 blobs 一个字节都不重传。

本 demo 的仓库模型强制**逐字节保存**（不重新序列化 JSON）：同一逻辑对象的两种字节表示
会算出两个不同的 digest——这不是 bug，而是规范要求的行为（`PUT` 的 `Docker-Content-Digest`
必须等于客户端提供的 digest）。

### 4. referrers：起 sbom / 签名的标准姿势

`GET /v2/<name>/referrers/<digest>` 返回一个 image index，列出所有 `subject` 指向该 digest
的 manifest（sbom、attestation、签名）。两处细节值得记住：

- **`subject` 例外**：注册表必须接受 `subject` 指向尚不存在的 manifest，这样推 sbom 和推
  主体可以任意顺序（其它字段引用缺失 blob 则必须报 `MANIFEST_BLOB_UNKNOWN`）。
- **回退 tag 方案**：仓库不支持 referrers API 时返回 404，客户端改读 tag
  `<算法截断32>-<encoded截断64>`（非法字符换成 `-`）。该 tag 的内容由**客户端**维护，
  并发更新会互相覆盖——规范明确说这是客户端/用户的责任，根治办法是换支持 referrers API
  的仓库。

### 5. tags 列表的顺序 ≠ 版本顺序

`/v2/<name>/tags/list` 必须按 **ASCIIbetical** 返回，于是 `1.10.0` 排在 `1.9.0` **之前**、
`latest` 排在数字之后（`'l' > '9'`）。所以"取列表最后一个当最新版"是错的，必须自己按
SemVer 比较（本 demo 的 `highest()` 就是干这个的）。

## 对比 / 选型

| 维度 | 用 tag 部署 | 用 digest 部署 |
| --- | --- | --- |
| 可变性 | 可变（可被再次 push 覆盖） | 不可变（内容摘要） |
| 可审计 | 需查 tag→digest 映射历史 | 直接就是内容指纹 |
| 晋升 | 移动 tag（本 demo 的 `promote`） | 改 deployment 里的 digest |
| 回滚 | 把 tag 指回旧 digest | 改回旧 digest |
| 风险 | tag 被覆盖导致"同名不同物" | 无（除非仓库被删） |

## 环境准备

- Python 3.11+（本 demo 用 3.13），**零第三方依赖**，只用标准库（`hashlib`/`json`/`re`）。
- Go 1.18+（仅用于阅读/编译 Go 对照实现）。
- 本机**没有 Go 工具链**：Go 版走人工审查 + 结构自检（括号配平、未用 import、`check()` 实参个数）。

## 运行方式

```bash
cd python && python semver_check.py     # Python：108 条断言全绿
# cd go && go run .                      # 需本机有 Go 工具链（本机未装）
```

文件分工（每个源文件 ≤ 300 行）：

```
python/semver2.py        SemVer 解析/比较/递增（官方正则原样照抄）
python/oci_registry.py   digest 语法与计算、最小仓库 API、referrers
python/promotion.py      晋升闸门、tag 移动、跨仓库搬运
python/harness.py        零依赖断言 harness
python/semver_check.py   断言 1-3 组 + 入口
python/oci_check.py      断言 4-7 组（digest / 仓库 / 发现 / 晋升）
go/semver2.go / oci_registry.go / oci_discovery.go / promotion.go  Go 对照实现
go/main.go + go/oci_check.go                                       Go 断言与入口
```

## 关键代码片段（Python）

```python
def _compare_identifier(a: str, b: str) -> int:
    """官方规则: 纯数字按数值比; 数字恒低于非数字; 否则按 ASCII 字典序。"""
    a_num, b_num = a.isdigit(), b.isdigit()
    if a_num and b_num:
        return (int(a) > int(b)) - (int(a) < int(b))
    if a_num:
        return -1          # 数字标识符恒低于非数字标识符
    if b_num:
        return 1
    return (a > b) - (a < b)
```

```python
def referrers_tag(subject_digest: str) -> str:
    """Referrers Tag 方案: 算法截断 32、encoded 截断 64、非法字符换成 -。"""
    alg, _, encoded = subject_digest.partition(":")
    raw = "%s-%s" % (alg[:32], encoded[:64])
    return re.sub(r"[^a-zA-Z0-9._-]", "-", raw)
```

## 性能与边界

- 模型是**语义模型**：不做真实 HTTP 往返、不做认证（Bearer/token）、不实现分块上传时序
  （`PATCH` + `Content-Range` + `416`）、不实现 `Range` 下载与 `Content Management`
  （`DELETE`）的完整语义。
- 只实现单平台 manifest；多平台 index、artifact manifest 的完整 `artifactType` 规则、
  combination 校验、镜像签名（cosign/notation）不在范围内。
- digest 只支持 `sha256`/`sha512` 的计算；其它算法按"未注册但语法合法"处理（与规范一致）。
- Go 版与 Python 版的**字节表示不同**（Go 的 `encoding/json` 对 map 键排序，Python 保持
  插入序），因此同一逻辑 manifest 两边算出的 digest 不同——这是**有意的口径差异**，
  不影响各自内部的断言。

## 注意事项与常见坑

- **前导零非法**：`01.2.3`、`1.2.3-01` 都不是合法版本，解析必须报错而不是 `int()` 掉。
- **构建元数据不进比较**：`1.0.0+build.1` 与 `1.0.0+build.2` 优先级相同，别拿它排序。
- **`v1.2.3` 不是 SemVer**：`v` 只是 tag 前缀习惯，比较前必须先剥掉。
- **tags 列表顺序不是版本顺序**：ASCII 序下 `1.10.0 < 1.9.0`，`latest` 在最后。
- **sha256 的 encoded 大写非法**（原文 `[A-F]` MUST NOT be used），校验要卡死大小写。
- **tag 是可变指针**：同名 tag 被重新 push 就是"同名不同物"；生产部署请用 digest。
- **`MANIFEST_BLOB_UNKNOWN` 与 `subject` 的区别**：前者必须拒绝，后者必须接受。
- **referrers 回退 tag 有并发写风险**：多个客户端同时更新同一个 tag 会互相覆盖，规范
  明确把责任交给客户端；能换支持 referrers API 的仓库就换。
- **`n=0` 与"不分页"是两回事**：`n=0` 必须返回空列表且不带 `Link`。
- 本 demo 的 Go 版**未经编译器验证**（本机无 Go 工具链），已人工复核签名并用脚本统计
  97 个 `check()` 调用的实参个数；Python 版 108 条断言全部通过。

## 参考资料（实际阅读过的权威来源）

- Semantic Versioning 2.0.0 规范原文（`semver/semver` 仓库的 `semver.md`）：
  <https://github.com/semver/semver/blob/master/semver.md>
  （前导零禁令、预发布与构建元数据定义、11 条优先级规则与官方例子链、BNF 语法、
  官方给的两个校验正则、FAQ「`v1.2.3` 不是语义化版本」）
  —— 本机直连 GitHub 被重置，实际经镜像读取：`https://cdn.jsdelivr.net/gh/semver/semver@master/semver.md`
- <https://semver.org/>（同一规范的官方站点，本 demo 亦实读其 HTML 正文作交叉确认）
- OCI Distribution Specification（`opencontainers/distribution-spec`，main 分支 `spec.md`）：
  <https://github.com/opencontainers/distribution-spec/blob/main/spec.md>
  （Pull/Push/Content Discovery/Content Management 四类 API、`<tag-or-digest>` 与
  `<name>` 正则、`Docker-Content-Digest`、`MANIFEST_BLOB_UNKNOWN` 与 `subject` 例外、
  tags 的 ASCIIbetical 序与 `n`/`last` 分页、referrers API 与回退 tag 方案、错误码表）
  —— 实际经镜像读取：`https://cdn.jsdelivr.net/gh/opencontainers/distribution-spec@main/spec.md`
- OCI Image Format Specification — Content Descriptors（`descriptor.md`，v1.1.0）：
  <https://github.com/opencontainers/image-spec/blob/v1.1.0/descriptor.md>
  （digest 语法 EBNF、内容寻址与 `ID(C) == D == '<alg>:' + Encode(H(C))`、
  `sha256` 的 encoded 必须 `[a-f0-9]{64}`、`sha512` 为 `[a-f0-9]{128}`、
  未注册算法应当放行）—— 实际经镜像读取：`https://cdn.jsdelivr.net/gh/opencontainers/image-spec@v1.1.0/descriptor.md`

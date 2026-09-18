# SLSA 来源证明：制品凭什么被信任

扫描源码找漏洞只能说明「**代码**看起来没问题」，说明不了「**你手上这个二进制**
就是那份代码编出来的」。SLSA（Supply-chain Levels for Software Artifacts）
用 **Provenance（来源证明）** 补上这一段：它记录制品是**在哪里、何时、如何**
被生产出来的。

本 demo 的重点不是生成证明，而是**验证**——规范里那些 MUST / SHOULD 到底在防什么。
每条规则都对应一个可运行的攻击场景。

## 原理详解

### 1. Provenance 的结构（SLSA v1.0）

它是 [in-toto attestation](https://github.com/in-toto/attestation) 框架里的一种 predicate：

```jsonc
{
  "_type": "https://in-toto.io/Statement/v1",
  "subject": [ { "name": "hello-world", "digest": { "sha256": "..." } } ],  // 顶层字段
  "predicateType": "https://slsa.dev/provenance/v1",
  "predicate": {
    "buildDefinition": {
      "buildType": ".../workflow/v1",
      "externalParameters": { ... },      // 不可信，MUST 下游验证
      "internalParameters": { ... },      // 可信平台设置，无需验证
      "resolvedDependencies": [ ... ]     // best effort 完整
    },
    "runDetails": {
      "builder": { "id": "..." },         // 级别的唯一决定因素
      "metadata": { "invocationId": "...", "startedOn": "...", "finishedOn": "..." },
      "byproducts": []
    }
  }
}
```

两个易错点：**`subject` 是 Statement 的顶层字段，不在 `predicate` 里面**；
`predicateType` 必须**原样**使用 `https://slsa.dev/provenance/v1`（主版本不兼容时才变）。

### 2. `builder.id` 是 SLSA Build level 的**唯一**决定因素

规范原话：`builder.id` *"is intended to be the sole determiner of the SLSA Build level"*。
级别**不能**从扩展字段、不能从 subject、不能从产物标签推断。
所以本 demo 的 S7 里，攻击者在证明里塞一个 `x_slsaBuildLevel: 3` 自称 L3，
而 `builder.id` 实际只有 L2——要求 L3 时必须**拒绝**。

与它配套的是 **signer-builder 配对**：

> Consumers MUST accept only specific signer-builder pairs.

GitHub 可以为 GitHub Actions 签名，Google 可以为 Google Cloud Build 签名，
但 **GitHub 不能为 Google Cloud Build 签名**（S4）。规范还要求：同一构建平台若有
多种安全属性不同的模式，**每种模式必须有不同的 `builder.id`**，避免低安全模式拖累高安全模式。

### 3. `externalParameters` 不可信，`internalParameters` 可信

这是最反直觉的一条：

| 字段 | 谁设置的 | 可信吗 | 验证要求 |
| --- | --- | --- | --- |
| `externalParameters` | 构建平台的**用户/租户** | **不可信** | MUST 包含在证明里，MUST 下游验证；验证者 SHOULD 拒绝未识别字段 |
| `internalParameters` | 可信构建平台 | 可信 | **无需验证**（"no need to verify these parameters"） |

所以 S5 里 `externalParameters` 被塞了一个 `entryPoint: attacker-supplied.yml`
——这是用户可以影响的字段，验证者必须拒绝。

### 4. 未识别字段必须忽略，且要满足单调性

> Consumers MUST ignore unrecognized fields.

S6 里证明带了一个 `x_customHint` 扩展，验证必须**照常通过**（不能因为不认识就报错）。
但规范同时给了**单调性原则**：删除/忽略一个扩展 **SHOULD NOT** 把 DENY 变成 ALLOW。
换句话说——**策略永远不能依赖某个扩展字段来满足强制要求**，否则攻击者只要让验证者
忽略该扩展就能绕过。S7 正是这条原则的反面教材。

### 5. 场景与结果

| 场景 | 攻击 | 命中规则 | 结论 |
| --- | --- | --- | --- |
| S1 | —— | —— | ALLOW |
| S2 | 构建后换包 | `subject` 摘要不匹配 | DENY |
| S3 | 自建构建平台 | builder 不在可接受集合 | DENY |
| S4 | GitHub 给 Cloud Build 签名 | signer-builder 配对 | DENY |
| S5 | 注入 `entryPoint` | `externalParameters` 有未预期字段 | DENY |
| S6 | 未知扩展字段 | MUST 忽略 | **ALLOW** |
| S7 | 扩展自称 L3 | 级别只由 `builder.id` 定 | DENY |

注意 S6 是唯一一个「证明里有多余东西但仍然通过」的场景——这正是规范设计成
「忽略未识别字段」的目的：**让扩展可以演进而不破坏旧验证者**。

### 6. 还有一层：依赖的递归分析

`resolvedDependencies` 记录了构建期间取到的制品与其 digest（如把 `refs/heads/main`
解析成具体 git commit）。规范说它的完整性是 **best effort**（至少到 L3 如此），
用途是支撑**递归分析**：依赖自身也可以带一份 provenance，消费者逐级验证下去。
SLSA 明确说 **level 不是传递的**——一个 L3 制品的 L1 依赖仍然是 L1。

## 代码结构

| 文件 | 内容 |
| --- | --- |
| `provenance_verify.py` | 证明生成、HMAC 签名、7 步验证、7 个场景 |
| `provenance_verify_selftest.py` | 26 项断言 |
| `provenance_verify.go` | Go 版；扩展字段用 `ExtraPredicate` / `ClaimedLevel` 显式表达 |
| `provenance_verify.c` | C 版；**签名与摘要用 FNV-1a 占位**（不引入密码学依赖），只演示验证规则 |

签名本身用 HMAC-SHA256（Python/Go）模拟，真实系统是 DSSE 信封 + 公钥签名；
重点是**验证顺序**：先绑 `subject` 摘要、再查配对、再定级别、最后才校验外部参数。

## 运行

```bash
python provenance_verify.py             # 打印 7 个场景的判定与理由
python provenance_verify_selftest.py    # 26 项断言
go run provenance_verify.go
cc -o prov provenance_verify.c && ./prov
```

## 参考资料

- <https://slsa.dev/spec/v1.0/provenance> —— SLSA Provenance v1.0：`predicateType` 固定串、`buildDefinition` / `runDetails` 字段树、`externalParameters` 不可信且 MUST 下游验证、`internalParameters` 无需验证、`builder.id` 是级别的 sole determiner、signer-builder 配对、忽略未识别字段与扩展单调性原则
- <https://slsa.dev/spec/v1.0/about> —— SLSA 是什么：tracks/levels、Build Track L1–L3、防的是代码篡改 / 非预期构建平台上载 / 构建平台受攻击三类威胁、level 不传递

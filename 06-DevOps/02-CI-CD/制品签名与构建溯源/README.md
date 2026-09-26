# 制品签名与构建溯源:in-toto 证明链 + SLSA provenance + cosign

> 供应链安全的三件套:**证明(attestation)说清楚制品从哪来、谁签的名**。
> in-toto 定义证明的四层结构,SLSA 定义 provenance 该长什么样、
> 到什么等级要满足什么要求,cosign 负责把签名这件事变成"不用管密钥"。

## 1. in-toto 证明框架的四层(spec v1.2)

| 层 | 职责 |
| --- | --- |
| **Predicate** | 类型化元数据(可插拔:SLSA/link/SBOM 各一种 predicateType) |
| **Statement** | 把证明**绑定到 subject**(加密摘要)+ 声明谓词类型 |
| **Envelope** | 认证与序列化(DSSE:对规范化 payload 签名) |
| **Bundle** | 聚合多条证明 |

消费者是**策略引擎**(in-toto-verify / Binary Authorization):
验签(Envelope)→ subject 摘要比对(Statement)→ 谓词类型与内容比对(Predicate),
任一环失败即拒收。

## 2. SLSA provenance(v1 谓词)

> provenance = "verifiable information about software artifacts describing
> **where, when, and how** something was produced"

- L1 REQUIRED 字段:`buildType`(封装跑了什么流程,与谁触发无关)、
  `externalParameters`(外部输入)、`builder.id`(**受信构建平台的传递闭包**——
  同一 builder.id 下还有别的签名身份时要拆分);
- `resolvedDependencies` 记录材料(源码/依赖的摘要);
- subject 用**加密摘要**标识——文件名/tag 可变,digest 不可变,
  签名挪到别的制品上会被摘要比对拦下。

## 3. SLSA Build 等级(requirements)

| 等级 | 要求 | 三维刻画 |
| --- | --- | --- |
| L1 | **Provenance Exists**:MUST 无歧义标识输出(摘要) | 真实性/准确性无要求 |
| L2 | **Provenance is Authentic**:消费者 MUST 能验签 | 完整性+签名者身份 |
| L3 | 再加构建**隔离**、不可伪造等 | 精确性对抗构建内篡改 |

三问:**Completeness**(内容多全)/ **Authenticity**(能否归因到 builder)/
**Accuracy**(构建内篡改的抵抗力)。

## 4. cosign 无密钥签名(sigstore)

- **身份而非密钥**与签名关联:OIDC 身份令牌 → Fulcio CA 签发**短期证书**
  绑定临时密钥与身份;
- 签名事件写入 **Rekor 透明日志**(可审计"何时签的名",防抵赖);
- 私钥**事后销毁**、证书很快过期——验证走透明日志条目,
  不依赖签名者长期保管私钥(密钥泄漏的传统攻击面被拆掉);
- 根信任(Fulcio 根证书/Rekor 公钥)经 TUF 分发。

## 自检

`python python/provenance.py` —— 5 项断言:Statement 摘要绑定与 L1 字段齐备 /
Envelope 验签三段拒绝链 / 防偷换 subject / keyless 流程要点 /
四层职责。Go 侧 `go/provenance.go` 为同语义复刻(静态审查)。

## 参考资料(实读)

- [in-toto Attestation Framework Spec v1.2(四层模型)](https://github.com/in-toto/attestation/blob/main/spec/README.md)
- [SLSA — Build provenance(谓词字段与 REQUIRED 清单)](https://github.com/slsa-framework/slsa/blob/main/spec/build-provenance.md)
- [SLSA — Build Track Requirements(L1/L2/L3)](https://github.com/slsa-framework/slsa/blob/main/spec/build-requirements.md)
- [Sigstore — Identity-based ("keyless") signing](https://github.com/sigstore/docs/blob/main/content/en/cosign/signing/overview.md)
- 本目录 [制品晋升与语义化版本/](../制品晋升与语义化版本/)(digest 不可变性前置)

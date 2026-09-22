# DSSE、PAE 与制品签名：从「签了什么」到「验了什么」

## 简介

- **DSSE**（Dead Simple Signing Envelope，in-toto 团队提出）回答一个看似简单的问题：签名到底覆盖哪些字节？它的答案是——把 `payloadType` 一起签进去，用一套叫 **PAE** 的编码把两者钉死。
- Sigstore / cosign / SLSA provenance 全部建在 DSSE 之上。**不理解 PAE 就无法理解「为什么换个 payloadType 签名就失效」**。
- 本 demo 用纯 Python 实现了 P-256 上的 ECDSA（含 RFC 6979 确定性 nonce），并**逐字节复现了 DSSE 官方测试向量**——这是本 demo 最强的证据。

## 原理详解

### 1. 签名对象：PAE

```
SIGNATURE = Sign(PAE(UTF8(PAYLOAD_TYPE), SERIALIZED_BODY))

PAE(type, body) = "DSSEv1" + SP + LEN(type) + SP + type + SP + LEN(body) + SP + body
SP     = ASCII 空格 0x20
LEN(s) = s 的**字节长度**的十进制，无前导零
```

三个容易错的点：

1. **`payloadType` 参与签名**。它的作用不是「标注格式」，而是**防止跨应用的类型混淆**：两个应用都用 JSON 编码但语义不同，如果只签 payload，攻击者可以把 A 应用的合法签名挪到 B 应用去用（这正是规范要求 payloadType 必须是「应用专属 media type」而不是泛泛的 `application/json` 的原因）。
2. **LEN 是字节数不是字符数**，type 要先做 UTF-8 编码。
3. **PAE 是前缀无歧义的**：因为长度写在内容前面，不同 (type, body) 组合不会拼出相同的字节串。

官方测试向量（protocol.md）：

```
payload     = hello world
payloadType = http://example.com/HelloWorld
PAE         = DSSEv1 29 http://example.com/HelloWorld 11 hello world
```

### 2. 信封与解析规则

```json
{
  "payload": "<Base64(SERIALIZED_BODY)>",
  "payloadType": "<PAYLOAD_TYPE>",
  "signatures": [{ "keyid": "<KEYID>", "sig": "<Base64(SIGNATURE)>" }]
}
```

| 字段 | 要求 |
| --- | --- |
| `payload` / `payloadType` / `signatures` / `signatures[].sig` | **必须存在，即使为空** |
| `signatures[].keyid` | 可选；**未设置与设为空串等价** |
| 未识别字段 | 生产者可加，**消费者必须忽略** |
| base64 | 标准与 URL-safe 两种编码都允许，签名者任选，**验签者必须两种都接受** |

### 3. keyid 不是安全凭据

规范写得很重：`keyid` 是 **unauthenticated hint**，只能用来**缩小候选密钥范围**，`MUST NOT` 用于安全决策。

后果：信封里写着 `keyid: "prod-key"` 并不代表它真是 prod 的密钥验过的；一个错误的实现若「看到 keyid 匹配就放行」，等于把安全性交给了未认证字段。

### 4. 验签的四步顺序

```
1. 解码（失败即拒绝）
2. 可选地用 keyid 过滤候选公钥
3. 用 PAE(payloadType, payload) 验签（失败即拒绝）
4. 检查 payloadType 是否受支持 → 才按该类型解析 payload
```

规范还特意强调一句实现要求：**验签用的 `SERIALIZED_BODY` 必须是交给应用层的那一份**，不允许「验完再重新解析信封去取 payload」——否则 TOCTOU。

### 5. (t, n) 多签

`(t,n)` 信封有效当且仅当**至少 t 个互不相同的受信任公钥**验签通过。注意是 **unique keys**：同一个密钥出现两次只算一个，因此「把同一签名复制两遍」凑不出 t=2。

### 6. cosign 的存储与发现

| 约定 | 值 |
| --- | --- |
| 发现方式 | tag-based：`sha256:<hex>` → tag `sha256-<hex>.sig` |
| payload mediaType | `application/vnd.dev.cosign.simplesigning.v1+json` |
| 签名注解键 | `dev.cosignproject.cosign/signature` |
| 证书注解键 | `dev.cosignproject.cosign/certificate` |
| 证书链注解键 | `dev.cosignproject.cosign/chain` |
| `critical.type` | `cosign container image signature` |
| 算法 | ECDSA-P256 + SHA256（且必须用 registry 用的同一哈希算法） |

签名与镜像的绑定是**两跳**：

```
Sign( sha256( SimpleSigningPayload( sha256(Image Manifest) ) ) )
```

payload 以 blob 形式存进 registry 并被内容寻址引用，因此**不用拉取 blob 就能验签**（manifest 里已经有 blob 的 digest）。规范还解释了为什么「换个更强的哈希」没有意义：内层已经是 sha256，外层换了也只保护外层。

### 7. Sigstore bundle

`Bundle` 的 `media_type` 现行必须产出 `application/vnd.dev.sigstore.bundle.v0.3+json`，客户端还要能接受 `;version=0.1 / 0.2 / 0.3` 的旧写法。

content 是 `message_signature` 与 `dsse_envelope` 的 oneof。这里有一条**与 DSSE 本身冲突的约束**：

> DSSE 规范允许多签，但 **bundle 里的 DSSE envelope 必须恰好一个签名**；客户端在签名数不等于 1 时 `MUST` 拒绝。

理由是简化验证逻辑，且 bundle 只能携带一份验证材料。此外：若 verification material 提供的是 public key identifier（key hint）而 content 是 DSSE envelope，**两处的 key hint 必须完全一致**。

Rekor 条目里：`integrated_time` 在 **`inclusion_promise` 缺失时 `MUST NOT` 被信任**；`canonicalized_body` 若设置，客户端必须校验其中的签名与 `Bundle.content` 的签名一致。

### 8. PublicKeyDetails 的一个坑

`sigstore_common.proto` 里有两项 P-256：

| 值 | 名称 | 状态 |
| --- | --- | --- |
| 5 | `PKIX_ECDSA_P256_SHA_256` | **现行** |
| 6 | `PKIX_ECDSA_P256_HMAC_SHA_256`（RFC 6979） | **已废弃** |

注意「RFC 6979」在这里指的是 **nonce 生成方式**，不是哈希算法。本 demo 用 RFC 6979 生成 nonce（这正是官方向量用的方式），但算法标识仍是 `PKIX_ECDSA_P256_SHA_256`。

## 环境准备与运行

```bash
cd python && python selfcheck_dsse.py     # 63 条断言，全绿打印 ALL OK
cd go && go run .
```

无需任何第三方依赖（只用标准库 `hashlib` / `hmac`）。

## 关键代码

| 文件 | 作用 |
| --- | --- |
| `python/p256.py` | P-256 点运算、RFC 6979 确定性 nonce、ECDSA 签名与验签 |
| `python/main.py` | PAE、DSSE 信封解析/验签/多签、cosign 常量与两跳哈希、bundle 校验 |
| `python/selfcheck_dsse.py` | 63 条断言，其中 C1 与官方向量逐字节对拍 |
| `go/p256.go` / `go/dsse.go` | 同算法的 Go 转写（标准库 `math/big` + `crypto/hmac`） |

## 性能边界与注意事项

- 纯 Python 的 P-256 标量乘是**教学级实现**，逐位展开、无恒定时间保证，**不可用于生产**（侧信道）。
- `verify` 里额外做了「公钥在曲线上」与「n·P == 无穷远」两项检查，缺了后者会接受小阶点（无效曲线攻击的一类）。
- **验签前不要重新解析信封取 payload**——规范明确禁止。
- `keyid` 只做过滤，不做决策。
- 完整注意事项见 [`NOTES.md`](./NOTES.md)。

## 参考与展望

- 未完成：Rekor 的 **inclusion proof 验证**（Merkle 树根哈希与树大小的一致性）、Fulcio 证书的 SAN 与签发时间校验、TUF 信任根轮换。这些是「验签名」之外同样重要的一半。
- 可继续：把 SLSA v1.0 provenance 的 `builder.id` / `externalParameters` 判定接在 DSSE payload 之上。

## 参考资料（实际阅读过的权威来源）

- [secure-systems-lab/dsse — protocol.md](https://github.com/secure-systems-lab/dsse/blob/master/protocol.md) — PAE 定义、签名/验签流程、`(t,n)` 多签、**官方测试向量**（含 X/Y/d 与 base64 签名）
- [secure-systems-lab/dsse — envelope.md](https://github.com/secure-systems-lab/dsse/blob/master/envelope.md) — JSON 信封结构、required/optional 字段、解析规则、安全考虑
- [sigstore/cosign — specs/SIGNATURE_SPEC.md](https://github.com/sigstore/cosign/blob/main/specs/SIGNATURE_SPEC.md) — tag-based discovery、注解键、Simple Signing payload、两跳哈希与取舍理由
- [sigstore/cosign — specs/BUNDLE_SPEC.md](https://github.com/sigstore/cosign/blob/main/specs/BUNDLE_SPEC.md) — bundle 的字段与离线验证
- [sigstore/protobuf-specs — `protos/sigstore_bundle.proto`](https://github.com/sigstore/protobuf-specs/blob/main/protos/sigstore_bundle.proto) — `media_type` 取值、content oneof、**DSSE envelope 必须恰好一个签名**、key hint 一致性
- [sigstore/protobuf-specs — `protos/sigstore_rekor.proto`](https://github.com/sigstore/protobuf-specs/blob/main/protos/sigstore_rekor.proto) — `TransparencyLogEntry` 各字段语义、`integrated_time` 的可信条件、`canonicalized_body`
- [sigstore/protobuf-specs — `protos/sigstore_common.proto`](https://github.com/sigstore/protobuf-specs/blob/main/protos/sigstore_common.proto) — `PublicKeyDetails` 枚举与废弃标记
- RFC 6979（Deterministic ECDSA）— 本 demo nonce 生成算法依据（经官方向量验证）

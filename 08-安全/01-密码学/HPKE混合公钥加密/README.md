# HPKE：一个 KEM、两个 suite_id 和一条 XOR 出来的 nonce

把 **RFC 9180（Hybrid Public Key Encryption）** 的 `DHKEM(X25519, HKDF-SHA256)`
+ `HKDF-SHA256` + `AES-128-GCM` 这套组合从零实现，并**逐条对齐附录 A.1 的官方向量**：
共享秘密、五个派生值、6 条密文、3 个导出秘密全部对得上。

## 事实来源

| 内容 | 位置 |
| --- | --- |
| `LabeledExtract` / `LabeledExpand` 与「KEM 用 `KEM` 前缀、其余用 `HPKE` 前缀」 | RFC 9180 §4 |
| `ExtractAndExpand`：`eae_prk` → `shared_secret` | §4.1 |
| `Encap` / `Decap`：`kem_context = enc ‖ pkRm` | §4.1 |
| `KeySchedule`：`psk_id_hash` / `info_hash` / `secret` / `key` / `base_nonce` / `exp` | §5.1 |
| `VerifyPSKInputs` 的判据 | §5.1 |
| `ComputeNonce = base_nonce XOR I2OSP(seq, Nn)`、`IncrementSeq` 的上界 | §5.2 |
| `Export` 用标签 `sec`、输入是 `exporter_secret` | §5.3 |
| 附录 A.1 全部数值（含 A.1.1.1 六条密文、A.1.1.2 三个导出值） | 附录 A.1 |
| X25519 的 clamp 规则与 Montgomery 阶梯 | RFC 7748 §5 |
| HKDF Extract / Expand | RFC 5869 |
| AES-128 与 GCM | FIPS 197、NIST SP 800-38D |

## 一、同一个 KDF，两个 suite_id

这是 §4 里最容易漏读的一条：`suite_id` 不是全局常量。

```text
KEM  内部：suite_id = "KEM"  ‖ I2OSP(kem_id, 2)                = 4b454d0020
HPKE 其余：suite_id = "HPKE" ‖ I2OSP(kem_id,2) ‖ kdf ‖ aead     = 48504b45002000010001
```

也就是说 `shared_secret` 的派生（KEM 内）与 `key`/`base_nonce`/`exp`（KEM 外）
用的是**不同的域分隔**。写成同一个常量，KEM 那步就会错，而错误信息只是「密钥对不上」。

## 二、nonce 是 XOR 出来的，不是拼出来的

```text
nonce = base_nonce XOR I2OSP(seq, 12)
```

`I2OSP` 是**大端**，所以序号增加时翻转的是**末尾**字节：

```text
seq   0 -> 56d890e5accaaf011cff4b7d
seq   1 -> 56d890e5accaaf011cff4b7c
seq   2 -> 56d890e5accaaf011cff4b7f
seq 255 -> 56d890e5accaaf011cff4b82
seq 256 -> 56d890e5accaaf011cff4a7d   <- 翻的是倒数第二字节
```

最后一行是这套设计里最容易写错的地方：如果按小端拼序号，`seq = 256` 之前一切正常，
**第 257 条消息才开始解不开**。RFC 特意给了 `seq = 256` 的向量，就是为此。

`IncrementSeq` 在 `seq >= 2^96 - 1` 时抛 `MessageLimitReachedError`——
一个 HPKE 上下文最多加密 `2^96 - 1` 条消息。

## 三、Export 与加密密钥是单向隔离的

`exporter_secret` 和 `key` 都由 `secret` 派生，但标签不同（`exp` vs `key`）。
`Export` 用的是 `exporter_secret`，标签是 `sec`：

```text
Export("", 32)            = 3853fe2b...5250ee
Export(0x00, 32)          = 2e8f0b54...8c21a5
Export("TestContext", 32) = e9e43065...af54931
```

这让上层协议（比如 ECH、MLS）能复用同一次 HPKE 握手去派生别的密钥，
而不会削弱 AEAD 密钥本身。注意 `Export` **不推进 seq**。

## 四、PSK 模式的输入校验判据是「不等于空串」

`VerifyPSKInputs` 判的是 `psk != default_psk`（其中 `default_psk = ""`），
**不是**「是否为 `None`」。本 demo 第一版写成 `is not None`，
于是给 `key_schedule` 的默认参数 `psk=b""` 被当成「提供了 PSK」，Base 模式直接抛错。

## 五、底座自证

为了能对上 A.1.1.1 的密文，AES-128-GCM 是从零实现的，并用两条独立向量自证：

| 检查 | 期望 |
| --- | --- |
| AES-128(000102…0f, 001122…ff) | `69c4e0d86a7b0430d8cdb78070b4c55a`（FIPS 197 C.1） |
| AES-128-GCM(K=0^128, N=0^96, P=空) | `58e2fccefa7e3061367f1d57a4e7455a` |
| AES-128-GCM(K=0^128, N=0^96, P=0^128) | `0388dace…` ‖ tag `ab6e47d4…` |

AES 的 S 盒是用 GF(2^8) 求逆 + 仿射变换**算出来**的，不是手抄 256 个常数。

## 运行

```bash
cd python && python main.py              # 演示：suite_id / KEM / KeySchedule / 序号 / Export
cd python && python selfcheck_hpke.py    # 断言版（55 条，对齐 RFC 9180 A.1）
cd go && go run x25519.go hpke.go main.go
```

Go 侧覆盖 X25519 + 标签化 KDF + KeySchedule + Export + 序号语义；
AES-GCM 只在 Python 侧实现（不影响上述任何数值的验证）。

## 自检覆盖的坑

1. **GHASH 的长度块必须作为最后一个数据块参与**（要再乘一次 H），
   只在末尾异或进去是错的。空输入时 `0 * H = 0` 恰好等于异或 0，
   所以只测空明文会**漏掉这个 bug**——必须同时测非空。
2. **AES 的 state 是列优先**：输入字节 `4c+r` 落在 `state[r][c]`。写反了密文静默错。
3. **AES 必须有 ShiftRows**；漏掉这一轮，前几轮看着像在收敛，结果完全对不上。
4. **`kem_context = enc ‖ pkRm`**，两个公钥的顺序不能反（RFC 明写）。
5. **`enc` 就是临时公钥 `pkEm`**，不是额外的一层编码。
6. 接收端 `seq` 与发送端错位时解不开——这是**特性**不是 bug，自检里作为负控断言。

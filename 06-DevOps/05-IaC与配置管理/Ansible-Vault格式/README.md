# Ansible Vault 载荷格式

## 简介

Ansible Vault 解决的是"**密钥要跟代码一起进版本库**"这个矛盾：playbook 需要密码、私钥、token，但这些东西不能明文提交。Vault 的做法是把它们加密成一段**文本装甲（text armor）**，随 playbook 一起 commit，只有拿到 vault 密码的人能解开。

本 demo 逐字节实现官方的 **payload format 1.1 / 1.2**：header 解析、hex 装甲与 80 列换行、PBKDF2 派生三把密钥、HMAC-SHA256 认证、AES-256-CTR 加解密、RFC 5652 填充。Python 版**从零手写 AES-256**（标准库无分组密码），Go 版直接用 `crypto/aes`——同一规范的两种落地方式正好可以互相对照。

关键概念：

| 概念 | 一句话解释 |
| --- | --- |
| 文本装甲 | 把二进制密文 hexlify 成 ASCII，每行 80 字符 |
| salt | 每次加密新生成的 32 字节随机量，保证同明文密文不同 |
| 80 字节派生量 | PBKDF2 输出被切成 32 字节密码钥 + 32 字节 HMAC 钥 + 16 字节 IV |
| HMAC | RFC 2104 式 HMAC，输入是**密文**，用来发现篡改/错密码 |
| AES-CTR | 流式模式，128 位计数器块由 IV 播种；明文需先填充 |
| vault-id | 1.2 格式 header 的第四个字段，用于"多套密码" |

## 原理详解

### 1. 文件结构（官方原文逐字对应）

```text
$ANSIBLE_VAULT;1.1;AES256                 ← header，`;` 分隔，最多四段
653162376237616131333031373365336533...   ← vaulttext，每行 80 字符
633331303938363830623131356335306264...
```

| header 字段 | 取值 | 说明 |
| --- | --- | --- |
| 格式 ID | `$ANSIBLE_VAULT` | 唯一合法值；用于识别"这是 vault 内容" |
| 格式版本 | `1.1` / `1.2` | 带 vault-id 时用 1.2；1.0 只读，写入时自动升级为 1.1 |
| 密码算法 | `AES256` | 当前唯一支持值（1.0 时代写 `AES`） |
| vault-id 标签 | 如 `dev` | 1.2 可选字段，来自 `--vault-id dev@prompt` |

### 2. vaulttext 的三段式

官方描述："vaulttext is a concatenation of the ciphertext and a SHA256 digest with the result hexlify'ied"。拆开看是：

```text
hexlify( salt )          + "\n"
 hexlify( hmac )         + "\n"
  hexlify( ciphertext )
```

**外层再整体 hexlify 一次**，然后按 80 列硬换行。所以 `unhexlify(拼接所有行)` 得到的**不是密文**，而是一段 ASCII 文本，需要再按 `\n` 切成三段、各自 hex 解码——这是自己实现时最容易搞错的一层（本 demo 的 `_unarmor` / `unarmor` 就是这一层）。

### 3. 密钥派生

```text
PBKDF2-HMAC-SHA256(password, salt, 10000 iterations) → 80 bytes
    ├─ [ 0:32] cipher key   (AES-256)
    ├─ [32:64] HMAC key
    └─ [64:80] IV           (128 bit counter block seed)
```

官方给出的三个参数缺一不可：**10000 次迭代**、**SHA256**、**总长 80 字节**。salt 是 32 字节随机量、明文存在文件里（就在第一段 hex 里），所以同样的密码 + 同样的明文，两次加密结果**必然不同**。

### 4. 加密与认证

```text
 padded = PKCS7(plaintext)              # RFC 5652，1..16 字节，总是填充
 ciphertext = AES-256-CTR(cipher key, IV, padded)
 hmac = HMAC-SHA256(hmac key, ciphertext)          ← 认证的是密文
```

**认证在前、解密在后**：CTR 是流密码，明文每一比特只与密钥流异或，翻转密文任意一位会让明文同位翻转——**没有任何内建完整性**。整个方案的安全性依赖"先验 HMAC 再解密"，因此任何实现都必须把校验放在解密之前（本 demo 的 tamper 分支就是这条性质的可执行证明）。

### 5. 为什么计数器块由 IV 播种

`SP 800-38A §6.5`：CTR 把 128 位计数器块当成整数，每加密一个块 +1，块内容用**加密方向**的 AES 变换成密钥流。因为 IV 已经是从 PBKDF2 里派生出来的 16 字节，实现上直接 `int.from_bytes(iv, "big")` 当初始计数值即可（Python 版 `ctr_keystream`）。

## 对比：Vault vs 其他"密钥随代码走"方案

| 方案 | 密钥放哪 | 完整性保护 | 离线可解密 |
| --- | --- | --- | --- |
| Ansible Vault | 派生自 vault 密码 | ✅ HMAC-SHA256 | ✅ 只需密码 |
| SOPS + age/KMS | 信封加密，数据密钥被 KMS 包裹 | ✅ MAC/AEAD | ❌ 依赖 KMS 可达 |
| git-crypt | git filter，透明加解密 | ✅ (AES-GCM 等) | ✅ 但需 .gitattributes 覆盖 |
| 环境变量 / CI Secret | 完全在代码之外 | 取决于平台 | ❌ 无法随仓库分发 |

## 环境准备

- 操作系统：Linux / macOS / Windows
- Python：3.10+（本 demo 用 `3.13`；只用 `hashlib`/`hmac`/`binascii`/`os`）
- Go：1.21+（只用 `crypto/aes`、`crypto/cipher`、`crypto/hmac`、`crypto/sha256`、`encoding/hex`）

## 运行方式

### Python

```bash
cd python
python3 aes.py            # AES-256 自测（S-box 生成 + FIPS-197 C.3 向量）
python3 vault_format.py   # Vault 载荷往返 + 篡改/错密码/填充演示
```

### Go

```bash
cd go && go run vault_format.go
```

## 关键代码片段

```python
def _derive(self, salt):
    dk = hashlib.pbkdf2_hmac("sha256", self.password, salt,
                             PBKDF2_ITERATIONS, dklen=DERIVED_LENGTH)  # 80
    return dk[:32], dk[32:64], dk[64:80]      # cipher key / HMAC key / IV

def decrypt(self, text):
    ...
    expected = hmac.new(hmac_key, ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, digest):     # ① 先认证
        raise VaultError("HMAC mismatch: wrong password or tampered ciphertext")
    plain = xor_bytes(ciphertext, ctr_keystream(AES256(cipher_key),
                                                int.from_bytes(iv, "big"),
                                                len(ciphertext)))   # ② 再解密
    return self._pkcs7_unpad(plain).decode("utf-8")
```

## 性能与边界

- **PBKDF2 是主要开销**：10000 次 SHA256。Python 用 C 实现的 `hashlib.pbkdf2_hmac` 约几毫秒；本 demo 的 AES 是纯 Python 实现，只适合学习（每块约几十微秒量级，生产请用 `cryptography`）。
- **单文件无大小限制**，但整份文件在内存里加解密；`ansible-vault` 官方也建议文件大时避免频繁 `edit`。
- 平台差异：`ansible-vault` 支持 `--vault-id` 多密码，靠 header 的第四个字段路由；本 demo 复刻了 header 解析，但只实现单密码。
- 安全边界（官方警告原文）：Vault **只保护"静态数据"（data at rest）**。内容一旦解密（data in use），就靠 playbook/插件作者自己避免泄露，比如用 `no_log` 屏蔽输出。

## 注意事项与常见坑

1. **两层 hexlify 别搞混**。`unhexlify(整个 vaulttext)` 得到的是"三段 hex 的 ASCII 文本"，不是密文。直接把它当密文喂给 AES 会解出乱码。
2. **HMAC 覆盖的是密文不是明文**。自己实现时如果 HMAC 输入写成明文，加密能跑通、解密也能跑通，但**认证语义变了**（且在"先解密后校验"的实现里会引入 padding oracle 风险）。
3. **必须先认证再解密**。CTR 无内建完整性，先解密再校验等于给攻击者一个可控的错误反馈通道。
4. **总是填充**。即使明文长度正好是 16 的倍数也要加满一整块（pad=16），否则解密端无法区分"最后一块是数据"还是"最后一块是填充"。
5. **salt 每次都要新生成**。官方实现每次加密都从随机源取 salt；如果偷懒复用 salt，同一个密码下的相同明文会产生相同密文。本 demo 用 4 次加密的去重计数验证了这一点。
6. **salt 不是秘密**。它明文存在文件第一段，作用是防止预计算（rainbow table）与跨文件复用，不承担保密职责。
7. **1.0 格式只读**。读得到、写不出：任何写入都会升级成 1.1。所以"1.0 兼容"只是过渡期承诺。
8. **vault-id 只是标签**，不是密钥材料。它决定"用哪把密码去试"，密码本身仍在 vault 密码文件/提示里。header 里的标签可以被攻击者看到。
9. **命令行直接写密码会进 shell history**。官方专门为此加了警告，推荐用 `--vault-password-file` 或 `--vault-id xx@prompt`。

## 参考资料（实际阅读过的权威来源）

- [Using encrypted variables and files — Ansible Community Documentation](https://docs.ansible.com/projects/ansible-core/devel/vault_guide/vault_using_encrypted_content.html) — "Format of files encrypted with Ansible Vault" 与 "Ansible Vault payload format 1.1 - 1.2" 两节逐字依据：header 四字段语义、80 字符行宽、vaulttext 的三段拼接顺序、PBKDF2 的 10000 次迭代/SHA256/80 字节切分（32+32+16）、RFC2104 式 HMAC 输入为密文、AES-CTR 的 128 位计数器块、RFC5652 填充（全文阅读）
- [Encrypting content with Ansible Vault — Ansible Community Documentation](https://docs.ansible.com/ansible/latest/vault_guide/vault_encrypting_content.html) — `encrypt_string` 的真实产物示例（含 `$ANSIBLE_VAULT;1.1;AES256` 与 1.2+vault-id 两种 header）、`!vault |` 标签、加密变量 vs 加密文件的差异、命令行直接输入密码的警告（全文阅读）
- [Ansible Vault — Ansible Community Documentation](https://docs.ansible.com/ansible/latest/vault_guide/vault.html) — "只保护 data at rest" 的官方警告、`no_log` 提示（全文阅读）
- [FIPS-197: Advanced Encryption Standard (AES) — NIST](https://csrc.nist.gov/pubs/fips/197/final) — AES-256 密钥扩展（Nk=8、Nr=14、60 个 word）、状态列主序、ShiftRows/MixColumns/AddRoundKey 定义、Appendix C.3 测试向量 `8ea2b7ca516745bfeafc49904b496089`
- [Rijndael S-box — Wikipedia](https://en.wikipedia.org/wiki/Rijndael_S-box) — 用 p/q 递推程序化生成 S-box 的算法（避免手抄 256 个常数）
- [SP 800-38A: Block Cipher Modes of Operation — NIST](https://csrc.nist.gov/pubs/sp/800/38/a/final) — §6.5 CTR 模式：计数器块递增、块密码工作在加密方向
- [RFC 2104 — HMAC: Keyed-Hashing for Message Authentication](https://www.rfc-editor.org/rfc/rfc2104) — HMAC 构造定义
- [RFC 5652 §6.3 — Cryptographic Message Syntax：填充](https://www.rfc-editor.org/rfc/rfc5652#section-6.3) — 文档中"基于 RFC5652 的填充"所指的规范

# SSH 传输层：二进制包协议、算法协商与密钥派生

## 简介

SSH 传输层（RFC 4253）负责在 TCP 之上建立一条加密且带完整性保护的通道。它有三个容易被忽略却决定安全性的细节：

1. **包的填充与对齐规则**（§6）—— 直接决定流量分析能做到什么程度，也是历史上若干攻击的入口；
2. **算法协商的两种不同选法**（§7.1）—— KEX 与 cipher/MAC 的选取规则**并不一样**，KEX 多一层主机密钥能力约束；
3. **会话标识符与重协商**（§7.2/§9）—— `session_id` 一旦算出就不随重协商改变，而所有密钥都随 `H` 改变。

RFC 8308 又补了一套扩展协商机制（`ext-info-c` / `ext-info-s` / `SSH_MSG_EXT_INFO`），用来在不破坏老实现的前提下交换能力。

本 demo 逐条实现这些规则，并对 mpint 编码、padding 最小性、两种协商选法、密钥派生链、交换哈希绑定、扩展协商做断言。

## 原理详解

### 1. 二进制包协议（§6）

```
uint32    packet_length       不含 mac，也不含 packet_length 自身这 4 字节
byte      padding_length
byte[n1]  payload             n1 = packet_length - padding_length - 1
byte[n2]  random padding      n2 = padding_length
byte[m]   mac                 m = mac_length
```

四条硬约束：

| 约束 | 值 |
| --- | --- |
| 对齐 | `(packet_length \|\| padding_length \|\| payload \|\| padding)` 的长度是 **max(cipher block size, 8)** 的倍数，**即使使用流密码也必须遵守** |
| padding 下界 | ≥ 4 字节 |
| padding 上界 | ≤ 255 字节 |
| 最小包长 | 16 字节（或 cipher block size，取大者），mac 不计 |

于是 padding 的最小取值为

```
pad = blk - ((5 + payload_len) % blk);  若 pad < 4 则 pad += blk      blk = max(cipher_block, 8)
```

实测：空载荷 + 8 字节块 → padding=11、整包 16 字节；payload=10 → padding=9、整包 24 字节。

`packet_length` 字段**本身也是加密的**（§6 末句提醒），所以接收方必须先解密前 8 字节（或 block size）才能知道包有多长 —— 这正是 SSH 不能直接套用"明文长度前缀"的原因。

### 2. 算法协商：KEX 与 cipher 不是同一条规则（§7.1）

**cipher / MAC / compression**：取客户端 name-list 里**第一个服务端也支持**的算法，仅此一条。

**KEX**：同样从客户端列表逐个试，但要同时满足三个条件：

1. 服务端也支持该算法；
2. 若该算法需要**可加密**的主机密钥，服务端与客户端必须同时支持某个具备加密能力的主机密钥算法；
3. 若需要**可签名**的主机密钥，同理。

差别在第 2/3 条。举例（RFC 4432 的 `rsa2048-sha256` 需要可加密的主机密钥）：

| 主机密钥 | 协商出的 KEX |
| --- | --- |
| `ssh-ed25519`（只能签名） | `curve25519-sha256`（跳过 `rsa2048-sha256`） |
| `ssh-rsa`（可签名也可加密） | `rsa2048-sha256`（首选被选中） |

`cookie` 那 16 字节随机值的作用被 RFC 写得很明确：**让任何一方都无法独自决定密钥与会话标识符**。

### 3. 密钥派生（§7.2）

```
Initial IV      client→server : HASH(K || H || "A" || session_id)
Initial IV      server→client : HASH(K || H || "B" || session_id)
Encryption key  client→server : HASH(K || H || "C" || session_id)
Encryption key  server→client : HASH(K || H || "D" || session_id)
Integrity key   client→server : HASH(K || H || "E" || session_id)
Integrity key   server→client : HASH(K || H || "F" || session_id)
```

需要的字节数超过哈希输出时链式扩展：

```
K1 = HASH(K || H || X || session_id)
K2 = HASH(K || H || K1)
K3 = HASH(K || H || K1 || K2)
key = K1 || K2 || K3 || ...
```

**扩展是追加式的**：`need_bytes=64` 的前 32 字节与 `need_bytes=32` 完全相同（已断言）。

`session_id` 是**首次** KEX 的 `H`，此后无论重协商多少次都不变；但六把密钥都随新的 `H` 改变。`session_id` 参与每一次派生，所以即使 `H` 相同、`session_id` 不同也会得到不同的密钥。

### 4. 交换哈希（§8）

```
H = hash(V_C || V_S || I_C || I_S || K_S || e || f || K)
```

`V_C`/`V_S`（版本串）与 `I_C`/`I_S`（完整的 KEXINIT 报文）都是 **string**（带 4 字节长度前缀），`e`/`f`/`K` 是 **mpint**。把整份 KEXINIT 报文哈希进去，就是为了**防降级**：攻击者改动任何一个算法列表都会改变 `H`，进而让签名校验失败。

`e` 或 `f` 必须落在 `[1, p-1]`，越界的一律不得发送也不得接受。

### 5. RFC 8308 扩展协商

- 客户端往自己的 `kex_algorithms` 里加 `ext-info-c`，服务端加 `ext-info-s`。加在这一个字段是因为它是 KEXINIT 里两个**不分方向**的 name-list 之一。
- **两个名字故意不同**，保证它们永远不会互相匹配成"被协商出来的 KEX 方法"，因此不影响算法选择（已断言）。
- 若 `ext-info-c`/`ext-info-s` 真的被协商成了 KEX 方法，双方**必须断开**。
- 一方给出了 indicator，对端才**可以**（非必须）发 `SSH_MSG_EXT_INFO`；客户端必须在第一个 `NEWKEYS` 之后立刻发，服务端可以在第一个 `NEWKEYS` 之后、或在 `USERAUTH_SUCCESS` 之前发（后者用于只对已认证客户端公开的能力，且会**替换**先前的 EXT_INFO）。
- 未知扩展名必须忽略；`extension-value` 里可能含任意字节（包括 NUL），实现不能假设它是 C 字符串。

## 对比表

| 维度 | KEX | cipher / MAC / compression |
| --- | --- | --- |
| 选择依据 | 客户端列表顺序 + 服务端支持 + **主机密钥能力匹配** | 客户端列表顺序 + 服务端支持 |
| 无匹配时 | 双方必须断开 | 双方必须断开 |
| RFC 8308 indicator 的位置 | `kex_algorithms` | 不放 |

## 环境

- Python 3.13（仅标准库 `hashlib`）
- Go 1.22（仅标准库 `crypto/sha256` / `math/big`）
- 纯协议模型：不做真实连接、不实现具体密码算法

## 运行方式

```bash
cd python && python selfcheck_sshtrans.py   # 401 条断言，全部实跑
cd python && python main.py                 # 打印七组对照表
cd go     && go run .
```

## 关键代码

```python
def padding_length(payload_len, cipher_block_size=8):
    """求满足全部约束的最小 padding。"""
    blk = block_size_for(cipher_block_size)
    pad = (blk - ((4 + 1 + payload_len) % blk)) % blk
    if pad < MIN_PADDING:      # 4
        pad += blk
    return pad
```

```python
def select_kex(client_kex, server_kex, client_host_keys, server_host_keys, host_key_caps):
    for k in client_kex:
        if k.name not in [x.name for x in server_kex]:
            continue                      # 条件①
        if k.needs == "any":
            return k
        ok = any(hk in server_host_keys and need_ok(hk, k.needs, host_key_caps)
                 for hk in client_host_keys)   # 条件②③
        if ok:
            return k
    return None
```

## 性能与边界

- **`padding` 最多 255 字节**：这条上界在实际配置下几乎不会触发（block=8 时 padding 恒在 4~11，block=16 时 4~19），但实现仍要校验。
- **单包上限**：未压缩载荷 ≤ 32768 字节、含 `packet_length`/padding/payload/mac 的总包 ≤ 35000 字节（§6.1）。文档说 35000 是"随手选的比上面的数更大的值"。
- **`derive_key` 会丢熵**：§7.2 末句提醒，若 `K` 的熵超过 HASH 的内部状态大小，链式扩展会损失熵。
- **mpint 的负数表示**：本 demo 用 `math/big` 求"最小字节数"，`-1` 是 `0xff` 一字节、`-129` 是 `0xff7f` 两字节。自己实现时最容易漏的是"正数最高位为 1 要前置 0x00"而负数**不能**前置。

## 注意事项与常见坑

1. **`packet_length` 不含 mac、也不含自身**：`packet_length = padding_length + 1 + payload_len`。把它算成"整包长度"会让接收方少读或多读 4 字节。
2. **对齐单位取 max(block, 8) 而不是 block**：流密码（如 ChaCha20）的 block 是 1，但 RFC 要求**即使使用流密码也必须按 8 字节对齐**。写成 `cipher_block_size` 会让 ChaCha20 的包不对齐。
3. **padding 的"最小性"靠 `pad - blk < 4` 判定**：这只是"再减一个块就会违反下界"。别误以为 padding 恒等于 4。
4. **KEX 协商的主机密钥能力检查不能省**：`rsa2048-sha256`（RFC 4432）这类 RSA 加密型 KEX 要求主机密钥**可加密**；`ssh-dss`、`ssh-ed25519` 只能签名。少了这层检查，协商出的组合在实际签名/加密时会失败。
5. **`session_id` 不等于当前的 `H`**：首次 KEX 后两者相等，重协商后 `H` 变、`session_id` 不变。把它俩混起来会让重协商后的密钥派生全错。
6. **扩展密钥是追加而非重算**：`DeriveKey(..., need_bytes=64)[:32] == DeriveKey(..., need_bytes=32)`。若实现成"每次重算 K1"就会破坏这个等式。
7. **RFC 8308 的 indicator 名字必须按角色用**：客户端发 `ext-info-c`、服务端发 `ext-info-s`；发错角色时实现**可以**断开（MAY），而 indicator 被协商成 KEX 方法时**必须**断开（MUST）。
8. **`EXT_INFO` 的 `extension-value` 可能含 NUL**：§2.3 明确要求容忍任意字节序列，不要当成 C 字符串处理。

## 参考资料

- RFC 4253《The Secure Shell (SSH) Transport Layer Protocol》§6（二进制包协议与 padding/对齐/最小包长）、§6.1（最大包长）、§7.1（算法协商与三条件）、§7.2（密钥派生与扩展、session_id）、§7.3（NEWKEYS）、§8（DH 交换与 exchange hash）—— https://www.rfc-editor.org/rfc/rfc4253.txt
- RFC 8308《Extension Negotiation in the Secure Shell (SSH) Protocol》§2.1（indicator 的放置与为什么两个名字不同）、§2.2（启用条件与必须断开的情形）、§2.3/§2.4（EXT_INFO 结构与发送时机）、§2.5（扩展名与值的解释规则）—— https://www.rfc-editor.org/rfc/rfc8308.txt

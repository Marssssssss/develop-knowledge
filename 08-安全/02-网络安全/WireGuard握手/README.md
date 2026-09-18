# WireGuard 握手（Noise_IKpsk2）

## 简介

WireGuard 用约 4000 行内核代码实现了现代 VPN：握手是 **Noise Protocol Framework 的 IKpsk2 模式**，
数据面是 ChaCha20-Poly1305 的无状态加密。它的安全性不来自"协议更复杂"，恰恰相反 —— 密码学
套件**没有任何协商**（算法都写死），从而消灭了降级与不一致配置攻击面。

关键概念：

- **Noise_IKpsk2**：`I` = 发起方静态公钥立即传输；`K` = 响应方静态公钥为发起方先知；
  `psk2` = 预共享密钥在第二条消息末尾混入（对称密钥退化为"额外一层口令"）。
- **ck / h / k 三状态**：chaining key（密钥链）、handshake hash（转录哈希，同时充当 AEAD 的 AD）、
  CipherState 密钥。
- **TAI64N 时间戳**：12 字节（8 字节秒 + 4 字节纳秒）抗握手重放；内核把纳秒向下对齐到 `2^24`
  以削弱精确计时侧信道。
- **MAC1 / MAC2 与 cookie**：无状态 DoS 防护 —— 消息末尾两个 16 字节 MAC，让服务器不做任何
  DH 运算就能丢弃绝大多数伪造包。

## 原理详解

### 1. 初始化：WireGuard 与纯 Noise 的第一处差异

```
Noise:      h  = HASH(protocol_name)          # 不足 HASHLEN 则补零
WireGuard:  ck0 = BLAKE2s("Noise_IKpsk2_25519_ChaChaPoly_BLAKE2s")   # 37 字节, 无结尾 NUL
            h0  = BLAKE2s(ck0 ‖ "WireGuard v1 zx2c4 Jason@zx2c4.com") # 34 字节
```

多出的标识串把"具体实现"也绑进转录哈希，避免同名构造串的不同实现互通。随后 IK 的 pre-message
`<- s` 执行 `mix_hash(rs)`：发起方用**响应方的静态公钥**，响应方用**自己的静态公钥**。

### 2. 消息 1（发起方 → 响应方）：e, es, s, ss

| 步骤 | 动作 |
| --- | --- |
| `e` | 生成临时密钥对；`MixHash(e.pub)`；**PSK 模式额外 `MixKey(e.pub)`（只更新 ck）** |
| `es` | `MixKey(DH(e, rs))` —— 临时 × 静态，提供响应方认证并抗 KCI |
| `s` | `EncryptAndHash(initiator_static)`，比明文长 16 字节 |
| `ss` | `MixKey(DH(s, rs))` —— 静态 × 静态，内核用 `precomputed_static_static` 长期缓存 |
| `{t}` | `EncryptAndHash(tai64n_now())` |

消息布局（内核 `messages.h` 的 `sizeof`，共 **148 字节**）：

```
type(4, LE32: 低字节类型/高 3 字节保留零) | sender_index(4)
unencrypted_ephemeral(32) | encrypted_static(48 = 32+16) | encrypted_timestamp(28 = 12+16)
mac1(16) | mac2(16)
```

### 3. 消息 2（响应方 → 发起方）：e, ee, se, psk

`ee` 用双方临时公钥、`se` 用**响应方临时私钥 × 发起方静态公钥**，随后 `MixKeyAndHash(psk)`
同时把 PSK 混进 ck、h 与 k —— 这是 `psk2` 的定义位置（第二条消息末尾）。最后加密**空负载**：
它的 16 字节 tag 就是响应方的密钥确认（`sizeof` = **92 字节**）。

### 4. 传输阶段与 Split

```
c1, c2 = Split() = HKDF(ck, 空输入, 2)      # c1: 发起→响应, c2: 响应→发起
数据消息 = type(4) | key_idx(4) | counter(8) | AEAD(AD 为空)   # 明文补到 16 字节整数倍
```

AEAD 的 nonce 固定为 **4 字节零 ‖ 8 字节小端计数器**，因此同一密钥下的 nonce 天然不重复；
计数器还驱动 8192 位滑动窗口防重放（冗余 64 位，避免移位越界）。

### 5. 防 DoS：MAC1 与 cookie

```
mac1_key = BLAKE2s("mac1----" ‖ 响应方静态公钥)            # 32 字节, 未键控
MAC1     = keyed-BLAKE2s(mac1_key, 消息中 MAC1 之前的全部字节, 16 字节)
cookie   = keyed-BLAKE2s(secret, 源 IP ‖ 源端口, 16 字节)   # secret 每 120 秒轮换
MAC2     = keyed-BLAKE2s(cookie, 消息中 MAC2 之前的全部字节, 16 字节)
```

cookie 绑的是**源地址**而不是消息内容，所以无法被搬到别处重放。cookie 本身经
**XChaCha20-Poly1305**（24 字节随机 nonce）加密后回给发起方，加密密钥是
`BLAKE2s("cookie--" ‖ 响应方静态公钥)`、AD 取该消息的 MAC1 —— 只有能算出该 MAC1 的对端才解得开。

## 对比 / 选型

| 维度 | WireGuard（Noise_IKpsk2） | IPsec/IKEv2 | OpenVPN(TLS) |
| --- | --- | --- | --- |
| 算法协商 | **无协商**，写死 | 大量 SA 提议 | 随 TLS 套件，配置空间大 |
| 握手往返 | 1-RTT | 2 轮 4 消息 | 依赖 TLS 完整握手 |
| 前向保密 | 每 2 分钟重密钥（`REKEY_AFTER_TIME`） | 依配置 | 依配置 |
| DoS 防护 | 无状态 MAC1/cookie + 令牌桶 | IKEv2 cookie | 需依赖 TCP |

## 环境准备

- 操作系统：Linux / macOS / Windows（本 demo 纯算法模拟，不建真实隧道）
- Python 3.10+（仅标准库）；C 需 OpenSSL 1.1.1+（`EVP_PKEY_X25519`、`EVP_chacha20_poly1305`）；
  Go 1.20+（`crypto/ecdh` 提供 X25519，BLAKE2s 与 ChaCha20 在本目录自行实现）

## 运行方式

### Python（真跑自检：官方向量 + 握手 + cookie + 重放窗口，共 65 项断言）

```bash
cd python && python wg_test.py     # 输出 wg_test: 65 checks passed
```

### C

```bash
cd c && gcc -O2 -Wall -Wextra -pedantic wg_handshake.c -lcrypto -o wg_demo && ./wg_demo
```

实现分在 `wg_crypto_impl.h`（BLAKE2s / HMAC-BLAKE2s / KDF）与 `wg_noise_impl.h`
（X25519、AEAD、握手哈希链）两个**实现头**里，被主文件文本级 `#include`，
所以构建命令**只列 `.c`**。

### Go

```bash
cd go && go run .
```

## 关键代码片段

```python
# python/wg_noise.py —— 握手哈希链（每个 token 一步，与 Noise 规范逐条对应）
class Symmetric:
    def __init__(self, peer_static_pub):          # 对应 Initialize() + MixHash(rs)
        self.ck, self.h = handshake_init()        # ck0/h0 见 README §1
        self.k = None
        self.mix_hash(peer_static_pub)

    def mix_ephemeral(self, pub):                 # PSK 握手特有：e 还要 MixKey(pub)
        self.mix_hash(pub)
        self.ck = kdf(self.ck, pub, HASH_LEN)[0]

    def mix_key_and_hash(self, ikm):              # psk token 的三输出用法
        self.ck, temp_h, self.k = kdf(self.ck, ikm, HASH_LEN, HASH_LEN, SYM_LEN)
        self.mix_hash(temp_h)
```

```c
/* c/wg_crypto_impl.h —— KDF 是「HMAC-BLAKE2s 上的 HKDF」，与内核 kdf() 同构 */
static void kdf(uint8_t *out1, uint8_t *out2, uint8_t *out3,
                const uint8_t *data, size_t datalen, const uint8_t ck[HASH_LEN]) {
    uint8_t secret[HASH_LEN], buf[HASH_LEN + 1];
    hmac_blake2s(secret, ck, HASH_LEN, data, datalen);   /* Extract */
    for (n = 1; n <= 3; n++) {                           /* Expand: 0x01/0x02/0x03 */
        ...
        buf[HASH_LEN] = (uint8_t)n;
        hmac_blake2s(dst, secret, HASH_LEN, buf, i);
    }
}
```

## 性能与边界

| 项 | 值 | 说明 |
| --- | --- | --- |
| 握手消息 | 148 / 92 字节 | 与内核结构体 `sizeof` 一致，见自检 |
| 数据消息开销 | 32 字节 | 16 字节头 + 16 字节 tag，再按 16 字节对齐填充 |
| 重密钥 | 120 s 或 2^60 条消息 | `REKEY_AFTER_TIME` / `REKEY_AFTER_MESSAGES` |
| 硬失效 | 180 s | `REJECT_AFTER_TIME`；`MAX_TIMER_HANDSHAKES = 90/5 = 18` |
| 重放窗口 | 8192 位 | 其中 64 位为冗余，有效窗口 8128 |
| 初始化速率 | 50 次/秒 | `INITIATIONS_PER_SECOND`，也决定 TAI64N 纳秒对齐粒度 |
| cookie secret | 120 秒轮换 | `COOKIE_SECRET_MAX_AGE`，接受 `-5` 秒的时钟偏差 |

## 注意事项与常见坑

- **两个"静态公钥"别搞混**：`se` 用**发起方**静态公钥（响应方解出 `encrypted_static` 才拿到），
  而 MAC1 的密钥用**收件方**的静态公钥。本 demo 初版就把 `se` 写成了响应方自己的公钥，
  表现是"握手能推进但响应消息解不开"。
- **PSK 握手里每个 `e` token 都要额外 `MixKey(e.pub)`**（Noise §9.2）。漏掉这一步时
  单侧自测仍能全绿，只有双方对拆时才会暴露。
- **HMAC 的输入长度必须显式设限**：C 版曾用"超长则截断"的写法，这类静默截断会让 MAC
  "永远算得出但永远不对"，改为超限即报错。
- **BLAKE2s 的 keyed 模式**不是"先哈希密钥再哈希消息"，而是把密钥当一个满块喂进去
  （空密钥也必须补足 64 字节），这个分支写错时只有 MAC1/MAC2 会不对。
- **XChaCha20 不是"ChaCha20 换个 nonce 长度"**：前 16 字节必须经 HChaCha20 派生**子密钥**，
  内层 nonce 是"4 个零字节 ‖ 后 8 字节"。cookie 之所以选它，是因为 96 位 nonce 下随机取值
  的碰撞概率不足以支撑长期复用同一密钥。
- **TAI64N 纳秒被对齐到 2^24**：同一秒内两次取样可能得到相同时间戳，因此重放判据是
  "**不更新即拒**"（`not newer`）而不是"必须更大"。
- 本机环境无 C/Go 工具链，两语言实现按**人工审查 + 机械核查**（`c_sanity.py --tu`、
  `go_sanity.py`、`go_crossref.py`、`bracket_check.py`）验证，未实际编译。

## 参考资料（实际阅读过）

- [WireGuard 内核实现：drivers/net/wireguard/messages.h](https://git.zx2c4.com/wireguard-linux/plain/drivers/net/wireguard/messages.h) —
  消息结构体与全部常量（`REKEY_*`、`COOKIE_*`、计数器位数、填充倍数）
- [noise.c](https://git.zx2c4.com/wireguard-linux/plain/drivers/net/wireguard/noise.c) — `handshake_name`/`identifier_name`、
  `hmac()`/`kdf()`/`mix_hash()`/`mix_psk()`/`mix_dh()`、握手消息顺序、`tai64n_now()` 的纳秒对齐
- [cookie.c](https://git.zx2c4.com/wireguard-linux/plain/drivers/net/wireguard/cookie.c) / [receive.c](https://git.zx2c4.com/wireguard-linux/plain/drivers/net/wireguard/receive.c) —
  `mac1----`/`cookie--` 标签与 MAC1/MAC2 口径、XChaCha20Poly1305 封 cookie、8192 位重放窗口
- [Noise Protocol Framework 规范](https://noiseprotocol.org/noise.html) —
  IK 与 IKpsk2 模式定义、`MixHash`/`MixKey`/`MixKeyAndHash`/`Split()`、PSK 对 `e` token 的额外要求
- [RFC 7748](https://www.rfc-editor.org/rfc/rfc7748.html) / [RFC 8439](https://www.rfc-editor.org/rfc/rfc8439.html) —
  X25519 与 ChaCha20-Poly1305 官方向量（Python 自检逐条对照）
- [draft-irtf-cfrg-xchacha §2](https://datatracker.ietf.org/doc/html/draft-irtf-cfrg-xchacha) —
  HChaCha20 与 XChaCha20-Poly1305 的结构（本 demo 用其 §2.2.1 向量校验 HChaCha20）

> 说明：`wireguard.com/protocol/` 与 `wireguard.com/papers/*.pdf` 在本机网络下**不可达**
> （连接超时/被重置），因此协议细节全部改以上方官方实现源码 + Noise 规范为准。

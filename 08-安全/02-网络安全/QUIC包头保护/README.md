# QUIC v1 包头保护（Header Protection）

QUIC 在 TLS 1.3 的 AEAD 之外**额外加密了包头本身**：链路上的观察者看不到包号，
也看不到「包号字段占几个字节」。本 demo 从零实现 QUIC v1 的包格式、包号编解码、
头保护与短头包收发，全部用 RFC 原文给出的实例做断言。

| 实现 | 文件 | 覆盖 | 备注 |
| --- | --- | --- | --- |
| Python | `python/quic_crypto.py` | ChaCha20 / Poly1305 / AEAD / HKDF-Expand-Label | 纯标准库（无 AES） |
| | `python/quic_packet.py` | varint / 头部布局 / pn_offset / 包号编解码 / nonce | |
| | `python/quic_protect.py` | 头保护（采样・掩码・异或）/ 短头包收发 | |
| | `python/quic_test.py` | **91 项断言** | RFC 9001 A.1/A.2/A.5 + RFC 9000 A.2/A.3 |
| C | `c/quic_kdf_impl.h` | HKDF-SHA256 + Expand-Label（OpenSSL HMAC） | 实现头，同 TU |
| | `c/quic_packet_impl.h` | varint / pn_offset / 包号编解码 / nonce | |
| | `c/quic_protect_impl.h` | **AES-ECB 与 ChaCha20 两套头保护** + AEAD | OpenSSL EVP |
| | `c/quic_demo.c` | 可执行自检（唯一被编译的单元） | `gcc quic_demo.c -lcrypto` |
| Go | `go/quic_kdf.go` | HKDF + Expand-Label | 纯标准库 |
| | `go/quic_chacha.go` | 手写 ChaCha20 / Poly1305 / AEAD | 标准库没有 chacha20poly1305 |
| | `go/quic_packet.go` / `go/quic_protect.go` | 包格式与头保护 | |
| | `go/quic_main.go` | 同一批 RFC 实例断言 | |

## 原理详解

### 1. 为什么包头也要保护

TLS 1.2/1.3 只加密记录层负载，记录头（长度、类型）是明文，攻击者可以据此做流量分析
（例如按长度指纹识别网页）。QUIC 把**包号与首字节的部分位**也纳入加密范围，代价是：
接收端在解密之前必须先解出「包号字段有多长」，而包号长度字段本身又被加密了 ——
这个循环只能靠**与头部内容无关的采样规则**打破。

### 2. 变长整数（RFC 9000 §16）

2 位前缀决定宽度：`00`→1 字节（6 位值）、`01`→2 字节（14 位）、`10`→4 字节（30 位）、
`11`→8 字节（62 位）。**除 Frame Type 外，规范不要求最短编码**：`0x40 0x3f` 是合法的
63，解码器不能因为「有更短的编码」就拒绝它。初学者常在这里加错误的规范性检查。

### 3. 头部布局与 pn_offset

长头：`首字节(1) | Version(4) | DCID Len(1) | DCID | SCID Len(1) | SCID | Token Length(i) | Token | Length(i) | 包号 | 负载`；
短头：`首字节(1) | DCID | 包号 | 负载`（没有长度字段）。

关键点：**Length 与 Token Length 是 varint，它们自身占几个字节也参与偏移计算**，
所以「包号固定在某个偏移」的假设是错的。本 demo 一律按规范顺序逐段解码
（`parse_initial_header`），并与公式 `pn_offset_initial()` 交叉验证。

### 4. 头保护（RFC 9001 §5.4）

```
mask = header_protection(hp_key, sample)     # 5 字节
packet[0] ^= mask[0] & (长头 ? 0x0f : 0x1f)
packet[pn_offset .. pn_offset+pn_length) ^= mask[1 .. 1+pn_length)
```

- **采样**：`sample_offset = pn_offset + 4`，固定取 16 字节 —— 一律**按包号最长为 4 字节预留**，
  这样采样位置与被保护的「包号长度」位无关。包号只有 1 字节时就会跳过 3 字节负载。
- **长度不足必须整包丢弃**（规范用 MUST），否则会把首字节解成随机包号长度。
- **两套掩码算法**：AES 套件 `mask = AES-ECB(hp_key, sample)`；ChaCha20 套件把样本前 4 字节
  当块计数器（小端）、后 12 字节当 nonce，用 ChaCha20 加密 **5 个零字节**。
- **只掩部分位**：长头掩低 4 位（保住类型位与固定位），短头掩低 5 位（保住固定位与密钥相位）。
- **负载补齐**：`包号 + 受保护负载 ≥ 4 + 16`，因此 1 字节包号需 3 字节帧、2 字节包号需 2 字节。

### 5. 包号编解码（RFC 9000 §17.1 + 附录 A.2/A.3）

发送端不必发完整包号，只需「足以表示未确认区间两倍以上」的字节数：

```
num_unacked = largest_acked is None ? full_pn + 1 : full_pn - largest_acked
min_bits    = ceil(log2(num_unacked) + 1)      # 注意是实数 log
num_bytes   = ceil(min_bits / 8)
```

接收端按**半窗口**还原（附录 A.3）：以「最大已处理包号 + 1」为期望值，把截断值拼进去，
若候选低于期望值半窗则加一个窗口、高于则减一个窗口。这样在丢包与乱序下仍能唯一还原。

### 6. 密钥派生与密钥更新

沿用 TLS 1.3 的 `HKDF-Expand-Label`（标签必须带 `tls13 ` 前缀），派生三个值：
`quic key`（AEAD 密钥）、`quic iv`（nonce 基值）、`quic hp`（头保护密钥），实现密钥分离。
Initial 级别例外：由固定盐 `38762cf7…7f0a` + 「客户端首个 Initial 包的 DCID」派生。
密钥更新只换 `quic ku` 标签推导新 secret，**头保护密钥随之重新派生**（来自新 secret 的
`quic hp`），所以更新后旧包既解不开负载也去不掉头保护。

## 与其他方案的对比

| 维度 | TLS 1.2/1.3 记录层 | QUIC v1 | WireGuard |
| --- | --- | --- | --- |
| 包头 | 明文 | 包号 + 首字节部分位被加密 | 无包号，负载整体 AEAD |
| KDF | HKDF-Expand-Label | 同一套（复用 TLS 1.3） | 自有的 `HMAC-BLAKE2s` HKDF |
| 重放防护 | 依赖记录序号 | 包号 + 半窗口还原 | 8192 位滑动窗口 |
| 防流量分析 | 弱 | 包号不可见（长度仍可见） | 长度可见 |

## 环境与运行

```bash
# Python：仅标准库
cd python && python quic_test.py

# C：需要 OpenSSL 开发库
cd c && gcc -O2 -Wall -Wextra -o quic_demo quic_demo.c -lcrypto && ./quic_demo

# Go：仅标准库（1.21+，用到了 min/max 内建函数）
cd go && go run .        # 或 go build ./... && ./go
```

## 关键代码

```python
# python/quic_protect.py —— 去头保护：顺序不能反
mask = header_mask(hp_key, sample_for(pkt, pn_offset))   # 只用偏移就能算
pkt[0] ^= mask[0] & (0x0F if pkt[0] & 0x80 else 0x1F)
pn_length = pn_length_from_first_byte(pkt[0])            # 现在才知道包号多长
for i in range(pn_length):
    pkt[pn_offset + i] ^= mask[1 + i]
```

```c
/* c/quic_packet_impl.h —— 附录 A.3 的解码窗口必须用有符号比较 */
int64_t expected = (int64_t)largest_pn + 1;
if (cand <= expected - hwin && ...) return (uint64_t)(cand + win);
```

```go
// go/quic_protect.go —— ChaCha20 套件的掩码 = 加密 5 个零字节
func headerMask(hpKey, sample []byte) []byte {
    return chacha20XOR(hpKey, binary.LittleEndian.Uint32(sample[:4]), sample[4:16], make([]byte, 5))
}
```

## 性能边界

- 头保护只多一次 5 字节的分组/流密码调用（AES-ECB 一次分块、ChaCha20 一个块），
  实测开销相对于 AEAD 主体（每 16 字节一次 Poly1305 / GHASH 乘）可忽略。
- 真正的成本在**采样位置必须先解析头部**：实现必须按 varint 逐字段走一遍，
  不能用一个固定偏移直接取，这挡住了「一次 memcpy 取样本」的优化。
- 密钥更新不改变包大小与前缀，因此可以在不停顿连接的前提下切换密钥；
  但接收端要同时持有新旧两组密钥以容忍乱序包。

## 注意事项与常见坑

1. **Length 字段解出的值不含前缀位**：RFC 9001 附录 A.2 的线上字节是 `44 9e`，
   但规范文字说长度是 1182（`0x049E`），不是 `0x449E`。断言里写成 `0x449E` 会立刻失败。
2. **包号长度是发送方的自由选择**：附录 A.2 的示例用 4 字节编码包号 2，附录 A.5 用
   3 字节编码 654360564（为了省掉 PADDING 帧）；附录 A.2 的示例算法对同一包号只会给
   1 字节。断言不能把「示例的选择」当成算法要求。
3. **`log(n,2)` 是实数运算**：`n` 恰为 2 的幂时 `min_bits` 不再多一位。写成
   `bit_length(n) + 1` 会在 n=128、32768 这类点上多要一个字节。
4. **C/Go 的无符号下溢**：附录 A.3 的 `expected_pn - pn_hwin` 在 `expected_pn` 很小时
   （重放旧包、首包）会下溢成巨大值，使第一个分支恒真、包号算错。Python 的任意精度整数
   不会暴露这个问题 —— 这个 bug 只在移植时出现，必须有 `decodePacketNumber(0, 0x40, 8) == 0x40`
   这类哨兵断言。
5. **解码一侧的入参是线上字节，不是任意短值**：24 位窗口要传 `0xACE8FE` 而不是 16 位的 `0xE8FE`。
6. **受保护的首字节读不出包号长度**：附录 A.5 的 `0x4c` 低 2 位是 0，直接读会得出「1 字节」的
   错误结论；必须先解头保护。
7. **采样不足要丢包**，不要「尽量算」：掩码会随机化首字节，白耗一次解密还可能被用作放大点。
8. **两个套件的掩码算法不能混用**：AES 用 AES-ECB，ChaCha20 用 ChaCha20 加密零串 ——
   签名相同、结果完全不同。

## 参考资料

- RFC 9000（QUIC: A UDP-Based Multiplexed and Secure Transport）§16 变长整数、§17.1 包号、
  §17.2 长头、附录 A.2 包号编码示例、附录 A.3 包号解码示例 —— https://www.rfc-editor.org/rfc/rfc9000.txt
- RFC 9001（Using TLS to Secure QUIC）§5.2 Initial 密钥、§5.3 nonce、§5.4 头保护
  （5.4.1 应用 / 5.4.2 采样 / 5.4.3 AES-ECB / 5.4.4 ChaCha20）、§6 密钥更新、
  附录 A.1 Initial 密钥、A.2 客户端 Initial、A.5 ChaCha20-Poly1305 短头包 ——
  https://www.rfc-editor.org/rfc/rfc9001.txt
- RFC 8446（TLS 1.3）§7.1 HKDF-Expand-Label —— https://www.rfc-editor.org/rfc/rfc8446.txt
- RFC 8439（ChaCha20 and Poly1305 for IETF Protocols）§2.3 ChaCha20 块函数、
  §2.4 ChaCha20 加密算法、§2.5 Poly1305、§2.8 AEAD 构造 ——
  https://www.rfc-editor.org/rfc/rfc8439.txt
- RFC 5869（HKDF） —— https://www.rfc-editor.org/rfc/rfc5869.txt
- OpenSSL `EVP_chacha20` / `EVP_aes_128_ecb` / `EVP_chacha20_poly1305` 文档 ——
  https://docs.openssl.org/master/man3/EVP_EncryptInit/

# WebPush 端到端加密（aes128gcm）

> Web Push 的推送服务是**中间人**：应用服务器把消息交给 push service，push service 再转给浏览器。RFC 8291 要解决的正是"这条消息不能被中间人看懂、篡改、伪造"。答案是**端到端加密**：push service 只搬密文，密钥从来没见过。
>
> 来源：[RFC 8291](https://www.rfc-editor.org/rfc/rfc8291)（密钥管理与 §5 示例、附录 A 全部中间值）、[RFC 8188](https://www.rfc-editor.org/rfc/rfc8188)（aes128gcm 内容编码）、[RFC 5869](https://www.rfc-editor.org/rfc/rfc5869)（HKDF）。本 demo 的 Python 实现**逐字节复现了 RFC 8291 附录 A 的官方中间值**。

## 一、三方模型决定了必须端到端加密

```
   +-------+           +--------------+       +-------------+
   |  UA   |           | Push Service |       | Application |
   +-------+           +--------------+       +-------------+
       |                      |                      |
       |        Setup         |                      |
       |<====================>|                      |
       |           Provide Subscription              |
       |-------------------------------------------->|
       :                      :                      :
       |                      |     Push Message     |
       |    Push Message      |<---------------------|
       |<---------------------|                      |
```

UA（浏览器）在建立订阅时生成一对 P-256 密钥和一个 16 字节 `auth_secret`，把**公钥和 auth_secret 交给应用服务器**。之后应用服务器每次发送都自己加密，push service 拿到的只是密文。

注意 §7 的一句硬约束：

> **no HTTP header fields are protected by the content encoding scheme.** A user agent MUST consider HTTP header fields to have come from the push service.

也就是说 `TTL`、`Topic`、`Urgency` 这些头是**push service 提供的**，不能被当成应用服务器的可信输入。

## 二、密钥派生：两次 HKDF，五个中间值

RFC 8291 §3 把加密分成四步，§3.4 给了可直接照抄的伪代码：

```
ecdh_secret = ECDH(as_private, ua_public)
PRK_key     = HMAC-SHA-256(auth_secret, ecdh_secret)          # HKDF-Extract
key_info    = "WebPush: info" || 0x00 || ua_public || as_public
IKM         = HMAC-SHA-256(PRK_key, key_info || 0x01)         # HKDF-Expand, L=32
PRK         = HMAC-SHA-256(salt, IKM)                         # 交给 RFC 8188
CEK         = HMAC-SHA-256(PRK, "Content-Encoding: aes128gcm" || 0x00 || 0x01)[:16]
NONCE       = HMAC-SHA-256(PRK, "Content-Encoding: nonce"      || 0x00 || 0x01)[:12]
```

三处容易写错：

1. **两次 HKDF 的 salt 不是同一个东西**：第一次的 salt 是 `auth_secret`、IKM 是 `ecdh_secret`；第二次的 salt 是报文里的 `salt`、IKM 是第一次的输出。
2. **`key_info` 里两个公钥的顺序是 `ua_public || as_public`**，且中间塞一个 `0x00`。顺序写反不会报错，只会静默地解不开（自检里有这条断言）。
3. **两个 info 串都不以 NUL 结尾**，那个 `0x00` 是拼接时另加的分隔符；而 HKDF-Expand 的计数器 `0x01` 是另一回事。

`auth_secret` 的作用是把"只知道 UA 公钥"的攻击者排除在外——光有公钥推不出 CEK。

## 三、报文体：86 字节头 + 一条记录

RFC 8188 §2.1 的头部布局：

```
+---------------+---------------+-----------+------------------+
| salt (16 字节) | rs (4 字节 BE) | idlen (1) | keyid (idlen 字节) |
```

Web Push 里 `idlen` 恒为 65（`keyid` 就是未压缩的 P-256 点），`rs` 默认 4096，所以**头固定 86 字节**。记录体是：

```
明文 || 0x02 || 填充（若干 0x00）
```

然后整体过一次 AEAD_AES_128_GCM，输出 `密文 || tag(16)`。

### 只允许一条记录

RFC 8291 §4 写得非常死：

- 应用服务器 **MUST** 用单条记录加密；
- `rs` **MUST** 大于 `len(明文) + 1(分隔符) + len(填充) + 16(tag)`；
- 填充分隔符**必须**是 `0x02`，"values other than 0x02 MUST cause the message to be discarded"；
- `Content-Encoding` 只能有一个值，就是 `aes128gcm`（不允许再用压缩类编码——压缩会泄漏内容长度信息）。

正因为只有一条记录、序号恒为 0，**随机数是 NONCE 本身**，不需要与序号异或（RFC 8291 §3.4 末尾那句注释）。

### 3993 这个数字怎么来的

> A push service is not required to support more than 4096 octets of payload body... Absent header (86 octets), padding (minimum 1 octet), and expansion for AEAD_AES_128_GCM (16 octets), this equates to, at most, **3993 octets of plaintext**.

```
4096 - 86 - 1 - 16 = 3993
```

## 四、对拍：RFC 8291 附录 A 的官方中间值

本机没有 `cryptography`，所以 `python/p256.py` 与 `python/aesgcm.py` 是手写的，用官方向量钉死：

| 量 | RFC 8291 附录 A | 本实现 |
| --- | --- | --- |
| `ecdh_secret` | `kyrL1jIIOHEzg3sM2ZWRHDRB62YACZhhSlknJ672kSs` | 同 |
| `PRK_key` | `Snr3JMxaHVDXHWJn5wdC52WjpCtd2EIEGBykDcZW32k` | 同 |
| `IKM` | `S4lYMb_L0FxCeq0WhDx813KgSYqU26kOyzWUdsXYyrg` | 同 |
| `CEK` | `oIhVW04MRdy2XN9CiKLxTg` | 同 |
| `NONCE` | `4h_95klXJ5E_qnoN` | 同 |
| 头部（86 B） | `DGv6ra1nlYgDCS1FRnbzlwAAEABB...` | 同 |
| 密文+tag | `8pfeW0KbunFT06SuDKoJH9Ql87S1QUrd...` | 同 |

最后是**整个 144 字节 body 逐字节相同**，而不只是"能解回来"。明文是 `"When I grow up, I want to be a watermelon"`。

## 五、代码结构

| 文件 | 内容 |
| --- | --- |
| `python/p256.py` | P-256 素域运算（ECDH 复用 ECDSA 那份实现） |
| `python/aesgcm.py` | AES-128 与 GCM 的手写实现：S 盒由 GF(2^8) 逆元+仿射现算，GHASH 在 GF(2^128) |
| `python/webpush.py` | HKDF 两步派生、头部编解码、rs/填充预算、加解密 |
| `python/main.py` | 五段演示输出 |
| `python/selfcheck_webpush.py` | 64 条断言 |
| `go/webpush.go` | 同一套算法的 Go 版，改用标准库 `crypto/ecdh` + `crypto/cipher` |

```bash
cd python && python main.py && python selfcheck_webpush.py
```

## 六、三个反直觉的点

1. **认证失败而不是解出乱码**。GCM 是 AEAD，`auth_secret` 或私钥任何一个不对，得到的是 `ValueError: authentication failed`。所以"推送解出来是乱码"基本不可能是密钥算错了，只可能是**你加密的内容本身就不是那串字节**（比如先把 JSON 序列化再加密、解密端却当纯文本）。
2. **`keyid` 不是合法 UTF-8**。RFC 8291 §4 特意点出：65 字节的未压缩点是裸二进制，"the `keyid` parameter will not be valid UTF-8 as recommended in RFC 8188"。按字符串处理它的代码会在某条消息上偶发炸掉。
3. **填充不是为了对齐，是为了遮长度**。§7：消息长度"could be revealed unless the padding provided by the content encoding scheme is used to obscure length"。所以填充是隐私手段，不是补齐手段。

## 参考资料（实际读过的来源）

- [RFC 8291 — Message Encryption for Web Push](https://www.rfc-editor.org/rfc/rfc8291) — §2 总览、§3.1~§3.4 派生、§4 单记录限制、§5 示例、§7 安全考虑、附录 A 全部中间值
- [RFC 8188 — Encrypted Content-Encoding for HTTP](https://www.rfc-editor.org/rfc/rfc8188) — §2.1 头部布局、§2.2 记录与填充分隔符
- [RFC 5869 — HKDF](https://www.rfc-editor.org/rfc/rfc5869) — Extract/Expand 两步定义
- [RFC 8030 — Generic Event Delivery Using HTTP Push](https://www.rfc-editor.org/rfc/rfc8030) — 三方模型与 4096 字节 body 的出处

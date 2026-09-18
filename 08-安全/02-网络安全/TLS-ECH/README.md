# TLS 1.3 ECH（Encrypted Client Hello）—— 内层 ClientHello 的封装与还原

TLS 1.3 把 SNI 之外的握手内容都加了密，唯独 **SNI 仍是明文** —— 观察者（ISP、中间盒、
同 Wi-Fi 的邻居）光看 ClientHello 就知道你在访问哪个域名。
**ECH（Encrypted Client Hello，RFC 9849，2026-03 成为 Standards Track）** 把 ClientHello
拆成两份：**ClientHelloOuter** 交给网络（SNI 是服务端公开的 `public_name`），
**ClientHelloInner** 装真实 SNI 与其余参数、用 **HPKE** 加密后塞进 outer 的
`encrypted_client_hello(0xfe0d)` 扩展里。

配置（公钥、`public_name`、`config_id`、`maximum_name_length`）由服务端通过 **HTTPS DNS
记录**里的 `ech` 参数发布，所以 ECH 必须和 DoH/DoT 一起用，否则 DNS 查询本身就漏了域名。

本 demo 覆盖完整三段：**ECHConfig 编解码 → 加密与填充 → 服务端还原**，
外加 `ech_outer_extensions` 压缩与 §5.1 的四条 MUST abort、§7.2 的接受确认值。

## 原理详解

### 1. HPKE：ECH 的加密层（RFC 9180）

固定套件 `DHKEM(X25519, HKDF-SHA256) + HKDF-SHA256 + ChaCha20-Poly1305`
（`kem_id=0x0020` / `kdf_id=0x0001` / `aead_id=0x0003`），正好对上 RFC 9180 附录 A.2
的官方测试向量 —— 这是本 demo 唯一有权威期望值的部分。三个最容易抄错、且**错了也不报错**
的地方：

| 位置 | 正确做法 | 抄错的后果 |
| --- | --- | --- |
| suite_id | KEM 用 `"KEM"\|kem_id`（**5** 字节），HPKE 用 `"HPKE"\|kem_id\|kdf_id\|aead_id`（**10** 字节） | 密钥全错，长度不同也不会越界 |
| LabeledExpand | `I2OSP(L,2)` 在**最前面**，其后才是 `"HPKE-v1"\|suite_id\|label\|info` | 同上 |
| 密钥计划 | `psk_id_hash`/`info_hash` 的盐是**空串**，`secret` 的盐是 **shared_secret** | 同上 |

`Encap` 返回的 `enc` 是**发送方**的临时公钥（`pkEm`），不是接收方的 `pkRm` ——
自检里专门有一条向量断言固定这个方向。

### 2. AAD：为什么可以把 payload 换成零再算一遍

`ClientHelloOuterAAD` 就是**去掉 4 字节握手头**的 ClientHelloOuter 序列化结果，
但把 ECH 扩展的 payload 换成**等长的全零**（§5.2）。客户端需要它是因为顺序问题：
AAD 必须在 Seal 之前算出来，可 Seal 之后 payload 才有真值。两段等长意味着
**任何长度前缀都不用重算**，把占位整段替换即可；服务端反向做同一件事，再 Open。

### 3. 填充：把长度对齐到 32 字节（§6.1.3）

```
内层有 SNI : padding = max(0, M - D)        M = maximum_name_length, D = SNI 长度
内层无 SNI : padding = M + 9                （= 一个 M 字节 host_name 的扩展长度）
最后再补   : N = 31 - ((L - 1) % 32)         L = 上一步之后的总长
```

填充的目的**不是隐藏长度**，而是消除「长度 → SNI 长度」这个侧信道：同一条链路上访问
两个不同长度的域名，观察到的 ClientHelloInner 长度必须一样。服务端还原时必须校验
尾部**全零**，否则它就是一个可被滥用的隐蔽信道。

### 4. ech_outer_extensions：压缩，以及四条 MUST abort（§5.1）

内层里与 outer **逐字节相同**的扩展不重复发送，改成一个类型列表
`ech_outer_extensions(0xfd00)`，服务端按列表从 outer 搬回来。少了下面任何一条，
服务端就成了**放大攻击的反射器**：

1. 引用了 ClientHelloOuter 里**不存在**的扩展 → abort；
2. **重复引用**同一扩展 → abort；
3. 引用了 `encrypted_client_hello` **自己** → abort；
4. 引用的扩展在 outer 里的**相对顺序**与列表不一致 → abort。

第 4 条最容易漏：不校验顺序，攻击者就能让服务端把外层扩展**重排**后回显，
用一次很小的请求引导服务端发送一条大得多的响应。

### 5. 接受确认：服务端怎么「悄悄地」告诉你它解开了（§7.2）

`ServerHello` 走明文，服务端不能直接回答「我用了 inner」，否则等于把 SNI 泄了。
做法是把一个 8 字节标签塞进 `ServerHello.random` 的**最后 8 字节**：

```
prk = HKDF-Extract(0, ClientHelloInner.random)
accept_confirmation = HKDF-Expand-Label(prk, "ech accept confirmation", transcript_ech_conf, 8)
```

这里的 `HKDF-Expand-Label` 是 **RFC 8446 §7.1** 的版本，和 HPKE 的 `LabeledExpand`
**编码完全不同**（HkdfLabel 结构 + `"tls13 "` 前缀，没有 `"HPKE-v1"`/suite_id）。
两者长得像，混用不会报错，只会算出错的值。HRR 场景换标签为
`"hrr ech accept confirmation"`，两条永远不会撞上。

## 对比

| 方案 | 隐藏 SNI | 隐藏对象 | 依赖 | 现状 |
| --- | --- | --- | --- | --- |
| ESNI（旧草案） | ✓ | 仅 SNI | 自造 AEAD 封装 | 已废弃，被 ECH 取代 |
| **ECH（本 demo）** | ✓ | **整个 ClientHelloInner** | HPKE + DNS 发布配置 | RFC 9849 |
| VPN / 隧道 | ✓ | 全部流量 | 额外基础设施 | 与 ECH 正交 |

ECH 保护的是**握手起点**：证书与后续应用数据本就被 TLS 1.3 加密，但「第一次握手的去向」
是唯一漏掉的地方。它**不隐藏** IP 地址与流量特征，也不阻止主动探测。

## 环境与运行

无第三方依赖：Python 只用标准库；C 用 OpenSSL 的 X25519 与 ChaCha20-Poly1305；
Go 只用标准库（`crypto/ecdh` 提供 X25519，ChaCha20-Poly1305 按 RFC 8439 自实现，
因为它在 `golang.org/x/crypto` 里）。

```bash
# Python —— 81 项断言，实跑通过
cd python && python ech_test.py

# C —— 95 项断言（本机无 C 工具链，只做了机械核查，见下）
cd c && cc -O2 -Wall -Wextra -o ech_demo ech_demo.c -lcrypto && ./ech_demo

# Go —— 88 项断言
cd go && go run ./go
```

自检按同一套分组打印每组条数：A 官方向量 · B 编解码 · C 填充 · D 端到端 ·
E 篡改 · F 压缩与四条 abort · G 接受确认。

| 实现 | 断言 | 验证方式 |
| --- | --- | --- |
| Python | 81 | 实际运行 |
| C | 95 | `c_sanity.py --tu` + `bracket_check.py`（本机无 C 工具链） |
| Go | 88 | `go_sanity.py` + `go_crossref.py`（本机无 Go 工具链） |

C 与 Go 的**算法端口**另外做了验证：把 `poly1305MAC` / `chachaBlock` / `labeledExpand` /
密钥计划逐行移植回 Python，对 A.2 的 6 组加密向量与 3 组 Export 逐一比对 ——
这一步抓出了 Go 版一个真 bug，见「注意事项」第 9 条。

## 关键代码

```python
# ech.py —— 客户端（§6.1）：先算 AAD 再 Seal，最后整段替换 payload
enc, ctx = hpke.setup_base_s(cfg.key_config.public_key, b"")
zero = build_client_hello_outer(outer_tpl, cfg, SUITE, enc,
                                b"\x00" * hpke.sealed_size(len(inner_enc)))
sealed = ctx.seal(zero.encode(), inner_enc)          # AAD 已定，长度不变
return build_client_hello_outer(outer_tpl, cfg, SUITE, enc, sealed)
```

```c
/* ech_flow_impl.h —— 四条 MUST abort 各给一个负返回码，自检按原因分辨 */
if (ch_ext_index(outer, order[i]) < 0) return -4;   /* 引用缺失 */
if (order[j] == order[i])               return -5;  /* 重复引用 */
if (order[i] == ECH_EXT_TYPE)           return -6;  /* 引用 ECH 自己 */
if (lhs >= rhs)                         return -7;  /* 相对顺序不一致 */
```

```go
// hpke_aead.go —— Poly1305 每块的隐含最高位：大端表示下它在**最前面的字节**
blk := make([]byte, n+1)
blk[0] = 1
copy(blk[1:], reverseBytes(msg[i:i+n]))
acc.Add(acc, new(big.Int).SetBytes(blk))
```

## 跨语言一致性是怎么保证的

三份实现共用同一组 A.2 密钥与同一组夹具（`public_name=example.com`、`SNI=secret.example.org`、
`config_id=0x7F`、`maximum_name_length=64`），错误口径按下表对齐 —— 三种写法同一件事：

| 场景 | Python | C | Go |
| --- | --- | --- | --- |
| AEAD 认证失败 | `hpke.HpkeError` | `-1` | `errHpkeAuth` |
| config_id/套件不匹配 | `EchError` | `-2` | `errEchConfigID` |
| 填充含非零字节 | `EchError` | `-3` | `errEchPadding` |
| abort 1/2/3/4 | `EchError` | `-4/-5/-6/-7` | `errOuterExt*` |

有一处**有意为之**的差异：Python 的 `ClientHello.decode` 要求吃光输入，而 C/Go 返回
`consumed`（因为后面就跟着零填充，解码器必须只吃到 ClientHello 的边界），填充校验
由调用方做 —— 语义上三种实现仍然一致。

## 性能边界

- **服务端要试解密**：`config_id` 对不上就换下一个 ECHConfig 再试，成本是一次 HPKE
  Open（X25519 + HKDF + 一次 AEAD）。真实部署里配置列表通常只有一两条。
- **ClientHello 变大**：填充最多多出 `maximum_name_length + 9 + 31` 字节，再加 ECH
  扩展头（约 42 字节 + 32 字节 `enc`）。取 M=64、SNI 18 字节时，本 demo 的内层明文
  192 字节、外层 ECH 扩展 payload 208 字节。
- **压缩的收益被 32 对齐吃掉一部分**：断言因此写 `len(shrunk) + 32 <= len(plain)`。

## 注意事项与常见坑

1. **`enc` 是发送方的临时公钥**。A.2 的向量里 `enc == pkEm`，写成 `pkRm` 会一路错到底。
2. **两条 `suite_id` 长度不同**（5 / 10 字节），不能复用同一条。
3. **`secret` 的盐是 `shared_secret`**，和 `psk_id_hash`/`info_hash` 的空盐不同。
4. **AAD 不含 4 字节握手头**：是 `ClientHello` 结构本身的序列化，不是完整记录。
5. **填充必须校验全零**：服务端漏了这一步，EncodedClientHelloInner 就成了隐蔽信道。
6. **`ech_outer_extensions` 的四条 abort 缺一不可**，尤其是第 4 条（相对顺序）。
7. **`server_name` 的 extension_data 是 `ServerNameList`**，要解两层才到 `name_type`；
   只解一层会在真实配置上解析错位（本 demo 这条是实跑时抓出来的）。
8. **`accept_confirmation` 用 RFC 8446 的 HKDF-Expand-Label**，不是 HPKE 的
   `LabeledExpand`；`ServerHello.random` 的**最后** 8 字节才是它。
9. **Poly1305 换成 `big.Int` 时，每块隐含的 `2^(8n)` 位要放在大端表示的「最前面」**：
   放末尾只会给这一块加 1。而且 seal 与 open 同时错、往返测试照样全过 —— 本 demo 的
   Go 版就是这么错的，最后靠 A.2 官方向量才暴露（源码里留了注释）。
10. **ECH 的配置靠 HTTPS DNS 记录发布**：DNS 查询不加密，ECH 等于白做。

## 参考资料

- [RFC 9849 — TLS Encrypted Client Hello](https://www.rfc-editor.org/rfc/rfc9849.txt)（本 demo 的主规范，§4-§7 逐节对照实现）
- [RFC 9180 — Hybrid Public Key Encryption](https://www.rfc-editor.org/rfc/rfc9180.txt)（§4.1 DHKEM、§5.1 密钥计划、§6.1 Seal/Open、附录 A.2 官方向量）
- [RFC 8446 — TLS 1.3](https://www.rfc-editor.org/rfc/rfc8446.txt)（§4.2 扩展规则、§7.1 HKDF-Expand-Label）
- [RFC 5869 — HKDF](https://www.rfc-editor.org/rfc/rfc5869.txt)（Extract/Expand 与空盐语义）
- [RFC 8439 — ChaCha20 and Poly1305](https://www.rfc-editor.org/rfc/rfc8439.txt)（§2.3.2 块函数、§2.5 Poly1305、§2.8 AEAD 构造）
- [RFC 7748 — Elliptic Curves for Security](https://www.rfc-editor.org/rfc/rfc7748.txt)（§5 X25519 的 clamping 与基点）


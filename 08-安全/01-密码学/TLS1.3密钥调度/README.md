# TLS 1.3 密钥调度（RFC 8446 §7.1–§7.3）

## 1. 简介

TLS 1.3 把“握手协商出来的熵”与“保护记录的对称密钥”彻底分开：中间隔着一条
**五层 Secret 调度链**。本 demo 从 SHA-256 起自底向上把它完整实现一遍，并用 30 条断言
钉住每一层的输入输出关系。

要回答的核心问题：

1. 为什么改一个字节的握手消息，流量密钥就全变了？（transcript 绑定发生在哪一层）
2. 为什么 PSK 与 (EC)DHE 是“注入”而不是“拼接”？
3. 0-RTT 密钥为什么能在 ClientHello 发出时就算出来？
4. KeyUpdate 为什么能单向推进、不可回退？

## 2. 原理详解

### 2.1 两个基元

```
HKDF-Expand-Label(Secret, Label, Context, Length) = HKDF-Expand(Secret, HkdfLabel, Length)

struct {
    uint16 length = Length;
    opaque label<7..255>   = "tls13 " + Label;
    opaque context<0..255> = Context;
} HkdfLabel;

Derive-Secret(Secret, Label, Messages) =
    HKDF-Expand-Label(Secret, Label, Transcript-Hash(Messages), Hash.length)
```

三点容易写错的地方：

- **label 恒带 `"tls13 "` 前缀**（6 字节），所以 `"derived"` 实际编码长度是 13；
- **context 为空时也要写 1 字节长度 0**，不能省略整段；
- `length` 是 **uint16 大端**，不是 1 字节。

RFC 8446 还专门说明：规范里所有标签都选得足够短（≤12 字符），
使常见哈希下不会触发额外一轮压缩调用。

### 2.2 五层调度

```
       0
       |
       v
PSK -> HKDF-Extract = Early Secret
       |            +--> "ext binder"/"res binder" , ""          -> binder_key
       |            +--> "c e traffic"  , CH                     -> client_early_traffic_secret
       |            +--> "e exp master" , CH                     -> early_exporter_master_secret
       v
   Derive-Secret(., "derived", "")
       |
(EC)DHE -> HKDF-Extract = Handshake Secret
       |            +--> "c hs traffic" , CH..SH
       |            +--> "s hs traffic" , CH..SH
       v
   Derive-Secret(., "derived", "")
       |
   0 ->  HKDF-Extract = Master Secret
                    +--> "c ap traffic" / "s ap traffic" , CH..server Finished
                    +--> "exp master"                    , CH..server Finished
                    +--> "res master"                    , CH..server Finished
```

读法：`HKDF-Extract` 的 **Salt 走上方、IKM 走左方**。
所以调度是一个“状态机”：当前 Secret 当盐，新熵当输入密钥材料，产出新的状态。

**三条结构性事实**（RFC 8446 §7.1 原文精神，本 demo 逐条断言）：

| 事实 | 含义 |
| --- | --- |
| 不跳过轮次 | 没有 PSK 时 Early Secret **仍然**是 `HKDF-Extract(0, 0)`，不是直接跳到 Handshake Secret |
| Master Secret 的 IKM 是 0 | 主密钥不掺新熵，只是把握手上下文“固化”下来 |
| Derived 不绑定 transcript | `Derive-Secret(., "derived", "")` 的 Context 是空串 ⇒ Handshake Secret 本身不随握手消息变化 |

### 2.3 为什么必须有 traffic secret 这一层

上表左列（Early/Handshake/Master Secret）是**裸熵**，右列（`*_traffic_secret`）才带上下文。
裸熵不能直接当密钥用，因为它对“这次握手”没有任何承诺。
Transcript-Hash 在 `Derive-Secret` 这一步被当作 `Context` 灌进 HKDF，
于是任何改写握手消息的中介都会得到一个不同的流量密钥，后续记录必然认证失败。

这就是 TLS 1.3 密钥调度对 **密钥一致性（key confirmation）** 的贡献：
它不需要额外的显式确认消息，transcript 绑定本身就把双方锁在同一把密钥上。

### 2.4 流量密钥与 nonce（§7.3 / §5.3）

```
[sender]_write_key = HKDF-Expand-Label(Secret, "key", "", key_length)
[sender]_write_iv  = HKDF-Expand-Label(Secret, "iv",  "", iv_length)
```

`Secret` 的取值随记录类型切换：0-RTT 用 `client_early_traffic_secret`、
握手期用 `[sender]_handshake_traffic_secret`、应用数据用 `[sender]_application_traffic_secret_N`。
**只要 Secret 变了，key/iv 全部重算**。

nonce 不是计数器，而是：

```
per-record nonce = (序列号左补零到 iv_length) XOR 静态 write_iv
```

`seq = 0` 时 nonce 恰好等于 IV；`seq = 1` 时只有最后一个字节翻转。
所以 **IV 是一次性的，nonce 才是每条记录唯一的** —— 复用 IV 而只改 seq 是设计之内，
反过来（改 IV 不动 seq）才是灾难。

### 2.5 KeyUpdate（§7.2）

```
application_traffic_secret_N+1 =
    HKDF-Expand-Label(application_traffic_secret_N, "traffic upd", "", Hash.length)
```

单向链：从 N+1 推不回 N（HKDF-Expand 单向），但任何人只要持有 N 就能验证 N+1。
实现应在算出 N+1 与其密钥后**删除** N —— 这是 RFC 的 SHOULD 要求，也是前向保密的实现细节。

### 2.6 0-RTT 的代价

`client_early_traffic_secret` 的 transcript 只到 ClientHello，
因此客户端在发出 0-RTT 数据时就能算出密钥（本 demo 断言：改 ServerHello 不影响它）。
代价是这段数据**没有**服务端参与的新鲜性，可被重放，且不提供前向保密。
这正是 `early_exporter_master_secret` 与应用期 `exporter_master_secret` 分开的原因。

## 3. 与 TLS 1.2 的对比

| 维度 | TLS 1.2 | TLS 1.3 |
| --- | --- | --- |
| 主密钥 | `PRF(pre_master_secret, "master secret", randoms)` 一次性产出 | 五层 HKDF 调度，逐层注入熵与上下文 |
| transcript 绑定 | 只在 Finished 消息里校验 | 每层 traffic secret 都绑定 Transcript-Hash |
| 密钥切换 | 靠 ChangeCipherSpec 显式信号 | 靠记录类型隐含 + 密钥调度位置 |
| 重协商 | 会话内可再次握手换密钥 | 只有 KeyUpdate 单向推进，不能回滚 |
| 0-RTT | 无（会话恢复也要 1-RTT） | 由 `client_early_traffic_secret` 支撑 |

## 4. 文件与运行

```
python tls13_key_schedule.py     # 30 条断言（含 RFC 5869 / RFC 4231 / FIPS 180-4 官方向量）
go run tls13_key_schedule.go     # 17 条断言
cc -O2 -o ks tls13_key_schedule.c && ./ks   # 15 条断言
```

三份实现互不引用、独立实现同一条调度链，便于交叉对照。

## 5. 关键代码

`HKDF-Expand-Label` 的编码是最容易写错的地方（Python 版）：

```python
def hkdf_label(length, label, context):
    lab = b"tls13 " + label.encode()
    return (length.to_bytes(2, "big") + bytes([len(lab)]) + lab
            + bytes([len(context)]) + context)
```

而“注入”语义就是 `HKDF-Extract` 的参数位置（Go 版）：

```go
s.earlySecret = hkdfExtract(zero, psk)                       // salt=0,  IKM=PSK
s.handshakeSecret = hkdfExtract(derived, dhe)                // salt=derived, IKM=DHE
s.masterSecret = hkdfExtract(deriveSecret(hs, "derived", nil), zero)
```

## 6. 性能与边界

- 调度全程只做 HMAC-SHA256 调用：一次完整 1-RTT 握手约 15~20 次 HMAC，可忽略不计；
  真正的开销在 (EC)DHE 与证书验证。
- `HKDF-Expand` 每 32 字节输出一次 HMAC ⇒ 输出长度 >32 字节时要多轮；
  `key_length=32`（AES-256）仍在单轮内。
- 标签长度上限 255、context 上限 255 来自 `opaque<7..255>` / `opaque<0..255>` 的长度前缀是单字节；
  “7”的下界恰好是 `"tls13 "` 的长度，也就是说标签本身不能为空。
- Transcript-Hash 要包含**握手消息的类型与长度字段**，但不包含记录层头部 —— 实现时最容易多算或少算这两段。

## 7. 注意事项与常见坑

1. **写成 `"derived"` 而漏掉 `"tls13 "` 前缀**：能跑通，但与对端不兼容，且不会报任何错。
2. **空 context 不写长度字节**：`HkdfLabel` 解出来会错位，同样静默不兼容。
3. **复用 IV 而不推进 seq**：AEAD 的 nonce 重复直接导致 GCM 认证密钥恢复攻击。
4. **忘记“不跳过轮次”**：无 PSK 时也要算 Early Secret，否则握手 secret 与规范不一致。
5. **KeyUpdate 后没删除旧 secret**：前向保密形同虚设（旧密钥仍在内存里）。
6. **把 Master Secret 当导出密钥用**：应用数据导出必须用 `exporter_master_secret`（RFC 5705 语义），
   0-RTT 期则只能用 `early_exporter_master_secret`。

## 8. 参考资料

- RFC 8446（TLS 1.3）§5.3 Per-Record Nonce、§7.1 Key Schedule、§7.2 Updating Traffic Secrets、§7.3 Traffic Key Calculation — https://www.rfc-editor.org/rfc/rfc8446.txt
- RFC 5869（HKDF）Appendix A.1 SHA-256 测试向量 — https://www.rfc-editor.org/rfc/rfc5869.txt
- RFC 2104（HMAC）/ RFC 4231（HMAC 测试向量）— https://www.rfc-editor.org/rfc/rfc4231.txt
- FIPS 180-4（SHA-256）§6.2 — https://nvlpubs.nist.gov/nistpubs/FIPS/NIST.FIPS.180-4.pdf

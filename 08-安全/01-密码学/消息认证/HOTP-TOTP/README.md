# HOTP/TOTP: 基于 HMAC 的一次性口令(RFC 4226 / RFC 6238)

## 简介

TOTP 是 Google Authenticator、银行动态口令牌、2FA 短信码之外的另一主流形态: 客户端与服务器共享同一密钥, 依据**计数器(HOTP)或时间步(TOTP)**生成 6-8 位一次性数字码。它把"证明你拥有密钥"变成"证明你能实时计算同一串数字", 是最广泛部署的 RFC 之一。

本 demo 从零实现动态截断与时间步派生(Python 侧 HMAC 用 stdlib, C 版连 SHA-1 都自实现), 完整命中 RFC 4226 附录 D 与 RFC 6238 附录 B 官方向量。

## 原理详解

### 1. HOTP(RFC 4226): 计数器模式

```
HOTP(K, C) = Truncate(HMAC-SHA-1(K, C)) mod 10^Digit
```

- C 是 **8 字节大端**计数器, 每次成功验证 +1(客户端与服务端必须保持同步);
- **动态截断(Dynamic Truncation)** 是全部巧妙所在——HMAC 输出 20 字节, 但只取 4 字节:
  1. `offset = HS[19] & 0xF`(末字节低 4 位选位置, 均匀 0-15);
  2. 取 `HS[offset..offset+3]` 4 字节, **首字节 & 0x7F**(砍最高位成 31 bit, 避免有符号取模歧义);
  3. `mod 10^6` 得 6 位数字。
- 20 字节里只暴露 31 bit, 且位置由数据本身决定——比固定取前 4 字节的偏置更小。

### 2. TOTP(RFC 6238): 把计数器换成时间

```
T = floor((UnixTime - T0) / X)    # 默认 X=30s, T0=0
TOTP(K, T) = HOTP(K, T)
```

- 30 秒一步, 每步码不同; 客户端与服务器**时钟各自独立**, 允许 ±1~2 步漂移窗口(resync);
- RFC 6238 允许换 HMAC-SHA-256/512(密钥同步加长到 32/64 字节), 向量已覆盖;
- T 是 64 位整数, 必须撑过 2038 年(32 位 time_t 的经典坑)。

### 3. 验证方工程要点(RFC 6238 §5.2/§7.3)

- 同一时间窗内**同一码不得重放**(验证成功即推进 last_seen);
- 连续失败应限速/锁定(10^6 空间在线穷举需 ~百万次尝试, 限速即免疫);
- 窗口调宽方便用户, 也放大重放面——银行常用 window=1, 高安全场景 0。

## 对比

| 维度 | TOTP(HOTP) | 挑战-响应(OCRA) | WebAuthn/FIDO2 |
| --- | --- | --- | --- |
| 共享密钥 | 双方对称共享 | 对称共享 | 私钥不出设备 |
| 钓鱼抵抗 | 弱(码可被转发) | 中(绑挑战) | 强(源绑定) |
| 依赖 | 时钟同步 | 交互往返 | 硬件令牌 |
| 部署成本 | 极低 | 低 | 高 |

## 环境

- Python 3.8+(hashlib/hmac stdlib); C99; Go 1.20+

## 运行方式

```bash
python hotp_totp.py   # 附录 D + 附录 B 全向量 + 截断细节 + 窗口 + 雪崩
go run hotp_totp.go
cc hotp_totp.c -o hotp_totp && ./hotp_totp   # C 版自含 SHA-1 实现
```

## 关键代码

- `hotp()`: 三步——大端计数器 → HMAC → 动态截断(`hs[-1] & 0xF` / `& 0x7F`);
- `verify_wide()`: ±1 步重同步窗口, 模拟真实服务端验证逻辑;
- C 版自含 SHA-1(FIPS 180-1 四轮逻辑)与 HMAC(RFC 2104, B=64)。

## 性能边界

- 单次生成 = 2 次哈希(约微秒级); TOTP 瓶颈永远在时钟与 UX, 不在计算;
- 6 位码空间 10^6: 在线攻击需限速; 8 位码(Digit=8)为 10^8, RFC 向量即用 8 位。

## 注意事项与常见坑

1. **计数器/时间步字节序**: 必须大端 8 字节; 小端或变长编码在官方向量上直接现形。
2. **& 0x7F 只砍首字节高位**: 4 字节取值是 31 bit 而非 32 bit, 写成 `& 0x7FFFFFFF` 等价, 但砍别的字节不等价。
3. **SHA256/512 的密钥长度不同**: RFC 6238 向量组密钥分别是 20/32/64 字节(同一字符串循环补齐), 用 20 字节密钥配 SHA512 会全部错位。
4. **不要把 verify 窗口当成重放许可**: 窗口只解决时钟漂移, 同一码二次提交必须拒绝。
5. TOTP 密钥常以 Base32 分发(Google Authenticator 的 `otpauth://` URI); 短信转发/截图泄露 = 密钥泄露, TOTP 本身不抗钓鱼。

## 参考资料(实际读过)

1. RFC 4226(HOTP 规范 + §5.4 截断示例 + 附录 D 向量): https://www.rfc-editor.org/rfc/rfc4226.html
2. RFC 6238(TOTP 规范 + 附录 B 三算法向量): https://www.rfc-editor.org/rfc/rfc6238.html
3. RFC 2104(HMAC, C 版基件): https://www.rfc-editor.org/rfc/rfc2104.html
4. RFC 6287(OCRA, 挑战-响应扩展, 对比参考): https://www.rfc-editor.org/rfc/rfc6287.html

# JOSE 令牌：JWS 与 JWE（RFC 7515 / 7516 / 7518 / 8725）

JOSE 是「一套把 JSON 头 + 载荷做签名/加密的编码规范」，JWT 只是它最常见的一种载荷。
本 demo 从零实现 AES-128 与 RFC 3394 密钥包装，完整复现 **RFC 7515 §3.3 的 JWS**
与 **RFC 7516 附录 A.3 的 JWE**（整串逐字节相同）。

## 1. 两种序列化

```text
紧凑（Compact）—— 只有一种形态，只能有一个签名 / 一个收件人
  JWS: BASE64URL(Protected) '.' BASE64URL(Payload) '.' BASE64URL(Signature)      3 段
  JWE: Protected '.' EncryptedKey '.' IV '.' Ciphertext '.' Tag                  5 段

JSON（General）—— 载荷一份，signatures / recipients 是数组，可多签 / 多收件人
  { "payload": "...", "signatures": [ {"protected":"...", "header":{...}, "signature":"..."} ] }
```

头部在 JSON 序列化里被拆成三层：`protected`（受完整性保护）、共享 `unprotected`、
以及每个签名/收件人自己的 `header`。**`protected` 与 `unprotected` 的成员名不得重叠**
（RFC 7515 §7.2.1），重复的成员名会让「哪一份才是生效的」变成未定义行为。

## 2. 三个最容易写错的地方

### ① AAD 是 `ASCII(BASE64URL(Protected))`，不是整串、也不是整个头

写错成「五个部分拼起来」或「整个 header 字符串」，tag 就会对不上。
demo 里用等长的假 AAD 重算，断言 tag **必然不同**（`selfcheck` 第 C 节）。

### ② `alg` 与 `enc` 是两件事

`alg` 管**密钥怎么给到对方**（`dir` / `A128KW` / `RSA-OAEP` …），
`enc` 管**内容怎么加密**（`A128CBC-HS256` / `A128GCM` …）。
`alg=dir` 时 JWE Encrypted Key 是**空串**（`a..b..c`），分隔符不能省。

### ③ A128CBC-HS256 的密钥是被切成两半用的（RFC 7518 §5.2.2.1）

```text
CEK 32 字节 = MAC_KEY(前 16) ‖ ENC_KEY(后 16)
AL  = 64 位大端的 AAD **位**长
tag = HMAC-SHA256(MAC_KEY, AAD ‖ IV ‖ CT ‖ AL)[:16]      ← 先加密后认证（Encrypt-then-MAC）
```

注意 `AL` 是**位**长不是字节长；HMAC 的输入顺序是 AAD → IV → 密文 → AL。

### 附：AES 密钥包装（RFC 3394）

`A = A6A6A6A6A6A6A6A6`，`n = len/8`，跑 `6n` 轮：
`A ‖ R_i = AES-Enc(K, A ‖ R_i)`，然后 `A ^= n*j + i`。
**解包最后必须回收到默认 IV**——这既是完整性校验，也是"密钥对不对"的唯一判据。

## 3. 安全防线（RFC 8725 JWT BCP）

| 条款 | 要求 | demo 里的断言 |
| --- | --- | --- |
| §3.1 算法校验 | 库 MUST 让调用方指定算法集合，**每个密钥只用于一个算法** | `allowed_algs` 白名单 + `key_alg` 比对，两者不一致都拒绝 |
| §3.2 合适的算法 | `alg: none` 默认不接受 | 默认拒绝；显式允许 `none` 才通过 |
| §3.11 显式类型 | 用 `typ` 区分不同种类的 JWT | RFC 7515 A.1 的头就是 `{"typ":"JWT","alg":"HS256"}` |
| §4.1.11 crit | `crit` 引用的头必须**同时出现在头里**且被理解 | 三处断言：引用不存在的头 / 不认识的头 / 被理解时放行 |

最经典的事故是「跟着 token 里的 `alg` 走」：攻击者把 `RS256` 改成 `HS256`，
拿**公开的 RSA 公钥**当 HMAC 密钥重新签名，服务端若直接信任头部 `alg` 就全盘失守。
`jws_verify` 因此把算法白名单和密钥 `alg` 都做成**入参**而不是从 token 里读。

## 4. 官方向量复现

| 来源 | 内容 | 结果 |
| --- | --- | --- |
| FIPS 197 附录 C.1 | AES-128(`000102…0f`, `001122…ff`) | `69c4e0d86a7b0430d8cdb78070b4c55a` ✓ |
| RFC 7516 A.3 | AES-KW 包装出的 Encrypted Key | 与 `6KB707dM9YTIgHtLvtgWQ8mKwboJW3of9locizkDTHzBC2IlrT1oOQ` 相同 ✓ |
| RFC 7516 A.3 | **整串紧凑 JWE** | 五段逐字符相同 ✓，解密得 `Live long and prosper.` ✓ |
| RFC 7515 §3.3 | 官方 JWS 与官方 oct 密钥 | 验签通过，签名串 `dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk` 一致 ✓ |

顺带一个 JOSE 特有的细节：RFC 7515 A.1 的头里是 **`{"typ":"JWT",\r\n "alg":"HS256"}`**
——带 CRLF 与一个前导空格，规范特意把八位组序列逐字节列出来。
**JSON 里多一个空格，签名就全变**，所以序列化必须用紧凑格式（`separators=(",", ":")`）。

## 5. 运行

```bash
python python/selfcheck_jose.py   # 64 条断言全绿
python python/main.py
go run go/jose.go go/jwe_crypto.go go/main.go     # Go 用标准库 crypto/*，与 Python 从零实现同题
```

## 6. 参考资料（实读）

- RFC 7515（JWS，131110 B）：§3.3 示例、§4.1.11 crit、§7.2 General JSON 序列化、附录 A.1 HMAC 向量与密钥
  —— https://www.rfc-editor.org/rfc/rfc7515.txt
- RFC 7516（JWE，108322 B）：§5.1 紧凑序列化五段、§5.2 步骤、附录 A.3（A128KW + A128CBC-HS256 全量向量）、附录 B.1–B.7
  —— https://www.rfc-editor.org/rfc/rfc7516.txt
- RFC 7518（JWA，155905 B）：§4 `alg` 取值、§5.2.2 AES_128_CBC_HMAC_SHA_256 的密钥切分与 AL
  —— https://www.rfc-editor.org/rfc/rfc7518.txt
- RFC 7517（JWK，93906 B）：`kty` / `use` / `key_ops` / `alg` / `kid` 的语义
- RFC 8725（JWT BCP，30793 B）：§3.1 算法校验、§3.2 算法选择、§3.11 显式类型
- FIPS 197（AES）：§5.1.1 S 盒定义、附录 C.1 向量

> 口径：S 盒不硬编码，由 GF(2^8) 乘法逆 + 仿射变换**现算**，再用 FIPS 197 与 RFC 7516 向量验证；
> 本 demo 只实现 HS256 / A128KW / dir + A128CBC-HS256，未实现 RS256/ES256/A128GCM（RSA 与 GCM
> 已在同目录其它 demo 覆盖）。

# RPKI 前缀源验证（ROA / VRP / RFC 6811）

BGP 本身没有任何机制证明「谁有权宣告某个前缀」。RPKI 用一套基于资源证书的签名体系给出
「前缀 → 授权源 AS」的映射，路由器据此把每条路由判为 **NotFound / Valid / Invalid**。
本 demo 从零实现 ROA 的 DER 结构、VRP 展开与三态验证，并用 RFC 原文的例子做断言。

| 实现 | 文件 | 覆盖 | 备注 |
| --- | --- | --- | --- |
| Python | `python/rpki_der.py` | DER 编解码（OID/INTEGER/BIT STRING/OCTET STRING/长度形式） | 严格模式：拒绝 BER 写法 |
| | `python/roa.py` | ROA 数据模型、DER 编解码、VRP 展开、RFC 6811 三态验证 | 含 IPv4/IPv6 前缀运算 |
| | `python/bgp_origin.py` | AS_PATH → Route Origin ASN（含 NONE 与联邦段） | |
| | `python/rpki_test.py` | **84 项断言** | RFC 6482 §3.3 例子 + 手推 DER 字节 |
| C | `c/rpki_impl.h` | 前缀位比较、maxLength 约束、三态验证、BIT STRING 编码、源 AS 推导 | |
| | `c/rpki_demo.c` | 可执行自检（唯一被编译的单元） | `gcc rpki_demo.c` |
| Go | `go/prefix.go` | 基于标准库 `net/netip` 的前缀运算与验证 | 用 `Masked()` 得到左对齐字节 |
| | `go/rpki_main.go` | 同一批 RFC 例子断言 | |

## 原理详解

### 1. 要防的问题

- **前缀劫持**：别家 AS 宣告不属于自己的前缀，流量被吸走。
- **子前缀劫持**：宣告一个更具体的前缀（如 `203.0.113.0/25`）抢走部分流量 ——
  它比原始 `/24` 更具体，BGP 最长匹配会让它胜出。**这正是 maxLength 存在的理由**。
- **伪造源**：把 AS_PATH 写成自己不在其中的路径。

RPKI 只能回答「源头是否被授权」，不验证整条路径 —— 路径验证是 BGPsec 的范围（RFC 8205）。

### 2. RPKI 的层次

| 层 | 规范 | 作用 |
| --- | --- | --- |
| 资源证书 | RFC 3779 | X.509 v3 扩展，把 IP 地址块/AS 号绑定到证书主体 |
| 签署对象容器 | RFC 6488 | RPKI 对象的通用 CMS 封装模板 |
| ROA | RFC 6482 | 内容为「AS + 前缀列表 + 最大长度」的授权声明 |
| 转发给路由器 | RFC 6810 | RTR 协议（把 VRP 推给路由器） |
| 验证算法 | RFC 6811 | 路由器侧的三态判定 |

### 3. ROA 的结构（RFC 6482 §3）

```asn1
RouteOriginAttestation ::= SEQUENCE {        -- DEFINITIONS EXPLICIT TAGS
   version [0] INTEGER DEFAULT 0,
   asID  ASID,                               -- INTEGER
   ipAddrBlocks SEQUENCE (SIZE(1..MAX)) OF ROAIPAddressFamily }
ROAIPAddressFamily ::= SEQUENCE {
   addressFamily OCTET STRING (SIZE (2..3)), -- 0001 = IPv4，0002 = IPv6
   addresses SEQUENCE (SIZE (1..MAX)) OF ROAIPAddress }
ROAIPAddress ::= SEQUENCE {
   address IPAddress,                        -- BIT STRING
   maxLength INTEGER OPTIONAL }
```

**maxLength 的语义**（RFC 6482 §3.3 原文例子）：`203.0.113/24` + maxLength 26
授权该 AS 宣告 `203.0.113/24`、`203.0.113.128/25`、`203.0.113.0/25`
以及 `/24` 内的**任意** `/26`，但**不授权** `203.0.113.0/27`。
省略 maxLength 时只授权**精确**前缀那一条。

一条 ROA 里允许出现互相包含的前缀（如同时有 `203.0.113/24 maxLength 26` 与
`203.0.113.0/28 maxLength 28`），判定结果仍然是确定的。

### 4. VRP 与三态验证（RFC 6811 §2）

依赖方验签后把每个 `ROAIPAddress` 展开成一条 **VRP**：`(前缀, 最大长度, 源 ASN)`。
路由器再用 VRP 库判定路由：

- **Covered**：VRP 前缀不更长，且其指定的所有位与路由前缀相同（路由要么相同、要么更具体）。
- **Matched**：Covered **且** 路由前缀长度 ≤ maxLength **且** 源 AS 相等。
- **NotFound**：没有任何 VRP 覆盖该路由前缀。
- **Valid**：至少一条 VRP Matched。
- **Invalid**：至少一条覆盖，但没有任何一条 Matched。

规范给出的伪代码就是「遍历所有覆盖项，命中即返回 Valid；遍历完仍无命中但有覆盖则 Invalid」。
两个边界值得记住：VRP 的 ASN 可以是 0（保留值，永不可能 Matched），
源 AS 可能是特殊值 **NONE**（末段是 AS_SET 之类时），同样永不可能 Matched。

### 5. Route Origin ASN 不等于「最后一个 AS 号」

RFC 6811 §2 按**最后一个 AS_PATH 段的类型**决定：

| 最后一段类型 | 源 AS |
| --- | --- |
| `AS_SEQUENCE` | 该段最右边的 AS |
| `AS_CONFED_SEQUENCE` / `AS_CONFED_SET` | 本机 AS（联邦内部 AS 不出现在对外路径里） |
| AS_PATH 为空 | 本机 AS |
| 其它（如 `AS_SET`） | **NONE** |

### 6. DER 的几条硬规则（本 demo 的 DER 实现只做这个子集）

- 长度必须**最短形式**：< 128 用 1 字节；更长用 `0x81/0x82…` + 大端长度，且首字节不能是 0。
- **禁止不定长**（`0x80` … `0x00 0x00`）——那是 BER，DER 明确不允许。
- 整数用**最短补码**：只有最高位为 1 时才补 `0x00`（如 64496 → `02 03 00 FB F0`）。
- `BIT STRING` 内容首字节是未使用位数，且**未使用的位必须为 0**。
- `DEFAULT` 值必须省略：version = 0 时不得出现 `[0]` 标签。

## 与其它方案的对比

| 方案 | 验证对象 | 数据来源 | 代价 |
| --- | --- | --- | --- |
| RPKI ROA（RFC 6811） | 只验证**源 AS** | 全局 RPKI 仓库 + RTR | 前缀劫持（更具体前缀）仍可能 |
| BGPsec（RFC 8205） | 验证**整条 AS 路径** | 每跳签名 | 每跳签名开销、需要全网部署 |
| IRR + 前缀过滤 | 只验证与登记意向一致 | 人工登记、可信度低 | 数据不可信 |
| ASPA（后起） | 验证路径中的**相邻关系** | RPKI 仓库 | 部署中 |

## 环境与运行

```bash
cd python && python rpki_test.py          # 纯标准库
cd c      && gcc -O2 -Wall -Wextra -o rpki_demo rpki_demo.c && ./rpki_demo
cd go     && go run .                     # 纯标准库（net/netip）
```

## 关键代码

```python
# python/roa.py —— 三态验证（照搬 RFC 6811 §2.1）
for vrp in vrps:
    if not vrp.prefix.contains(route_prefix):
        continue
    covered = True
    if route_prefix.length <= vrp.max_length and origin_asn is not None and \
            vrp.asn != 0 and origin_asn == vrp.asn:
        return VALID
return INVALID if covered else NOT_FOUND
```

```c
/* c/rpki_impl.h —— BIT STRING 必须左对齐：低位清零后整字节取用 */
if (p->length % 8 != 0) {
    buf[p->length / 8] &= (uint8_t)(0xFF << (8 - p->length % 8));
}
out[0] = (uint8_t)(8 * nbytes - p->length);      /* 未使用位数 */
```

```go
// go/prefix.go —— netip 的 Masked() 正好给出 DER 需要的左对齐字节
nbytes := (p.Bits() + 7) / 8
out = append(out, byte(8*nbytes-p.Bits()))
return append(out, raw[:nbytes]...)
```

## 性能边界

- 路由器要在一个 UPDATE 里对**每个前缀**做一次判定，且 VRP 库是全局的（数十万条），
  所以真实实现用**前缀树 / 区间树**做查找，而不是本 demo 的线性扫描。
- maxLength 让「一个 VRP 覆盖一组路由前缀长度」成为可能，因此查找不是简单的精确匹配，
  而是「沿前缀路径枚举所有祖先 + 检查长度区间」。
- 缓存刷新、RTR 增量同步与验证状态重算（RFC 6811 §4 要求映射变化后重跑决策）是运营上的
  真实成本所在。

## 注意事项与常见坑

1. **DER 不许「差不多」**：非最短长度、非最短整数、不定长都必须报错。用宽容的 BER 解析器读
   RPKI 对象会掩盖上游的编码错误。
2. **`version [0]` 是显式标签**（RFC 6482 附录 A 声明 `DEFINITIONS EXPLICIT TAGS`），
   编码是 `A0 03 02 01 01`；把它当成隐式标签写成 `A0 01 01` 会解不出来。
3. **BIT STRING 要左对齐**：写成「右移 (位数−长度) 再取字节」只在长度是 8 的倍数时巧合正确。
   `203.0.113.0/26` 的正确内容是 `CB 00 71 00`（未使用 6 位），右移写法会得到 `03 2C 01 C4`。
4. **主机位必须先清零**：`203.0.113.1/24` 与 `203.0.113.0/24` 必须编出同一串字节。
5. **maxLength 缺省 ≠ maxLength = 前缀长度之外的一切**：缺省只授权精确前缀那一条。
6. **maxLength 越界要判非法**：必须 ≥ 前缀长度且 ≤ AFI 位宽（IPv4 32 / IPv6 128）。
7. **判定「覆盖」时要区分地址族**：不同族的两个前缀谈不上覆盖，直接落到 NotFound。
8. **源 AS 的四种来源别只写「最后一个 AS」**：末段是 AS_SET 或联邦段时结果完全不同。
9. **Invalid 不等于丢弃**：RFC 6811 §5 的操作实践是策略问题（很多网络用 soft-fail，
   只降低优先级而不丢弃），规范还明确要求验证状态本身不得导致路由被移出 Adj-RIB-In。
10. **「Valid」只说明源被授权**，不代表路径没被篡改 —— 本 demo 与 RFC 6811 都不覆盖路径验证。

## 参考资料

- RFC 6482（A Profile for Route Origin Authorizations (ROAs)）§3 结构、§3.1 version、
  §3.2 asID、§3.3 ipAddrBlocks 与 maxLength、§4 内容类型 OID、附录 A ASN.1 模块 ——
  https://www.rfc-editor.org/rfc/rfc6482.txt
- RFC 6811（BGP Prefix Origin Validation）§2 VRP/Covered/Matched 与三态定义、
  §2.1 伪代码、§3 策略控制、§4 与本地缓存交互 —— https://www.rfc-editor.org/rfc/rfc6811.txt
- RFC 6488（Signed Object Template for the RPKI）摘要（CMS 封装） ——
  https://www.rfc-editor.org/rfc/rfc6488.txt
- RFC 3779（X.509 Extensions for IP Addresses and AS Identifiers）摘要（IPAddress 扩展） ——
  https://www.rfc-editor.org/rfc/rfc3779.txt
- RFC 8205（BGPsec Protocol Specification）摘要（路径验证与 ROA 的分工） ——
  https://www.rfc-editor.org/rfc/rfc8205.txt

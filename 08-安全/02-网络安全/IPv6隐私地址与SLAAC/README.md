# IPv6 隐私地址（临时地址）与 SLAAC

## 简介

SLAAC 的无状态便利有个副作用：把 MAC 地址嵌进接口标识符（EUI-64），意味着**只要换网络，设备的后半段地址始终不变**。于是网站、日志、广告网络可以把同一个人在家、在公司、在咖啡馆的活动串起来 —— 地址本身成了一个跨网络的持久标识。

RFC 4941（2007）为此引入**临时地址**：周期性生成随机化的接口标识符，用于对外发起连接，而基于 MAC 的公开地址只用来接收。RFC 8981（2021）取代 RFC 4941，改用带密钥的伪随机函数，并给出了默认参数。

本 demo 实现两代算法，并把生命周期的每一条约束做成断言。

## 原理详解

### 1. RFC 4941 §3.2.1：MD5 + history value

```
1. 取上一轮的 history value（首次是随机数），后面接上按 [ADDRARCH] 生成的接口标识符
2. 对拼出来的量求 MD5
3. 取 MD5 的**左 64 位**，把 bit 6（左起第 0 位编号）清零 —— U/L 位表示"仅本地意义"
4. 若与保留 IID 或本机已用 IID 冲突，改用 MD5 的**右 64 位**作为 history 回到第 1 步
5. 保存生成的标识符
6. 把 MD5 的**右 64 位**存进稳定存储，作为下一轮的 history
```

**history 机制的目的**被 RFC 写得很直白：理论上逐次随机并不比"用 history 递推"更安全，但实践中真随机数很难做。若两台设备撞出同一个 IID、都通过 DAD 发现冲突、然后又用同一个（有缺陷的）随机数生成器算出**同一个**新 IID，就会陷入死循环。把（通常全球唯一的）公开 IID 掺进下一轮的输入，就能保证它们第二次算出不同的值。

**U/L 位方向**：公开地址（EUI-64）把 U/L 位翻成 1（全局），临时地址清成 0（本地）。实测 `00:11:22:33:44:55` → 公开 IID `021122fffe334455`（U/L=1），临时 IID 的 U/L 恒为 0。

### 2. RFC 8981 §3.3.2：PRF 方案

```
RID = F(Prefix, Net_Iface, Network_ID, Time, DAD_Counter, secret_key)
IID = 取 RID 中从**最低有效位**开始的所需位数
```

`F()` 必须对外部不可计算、难以求逆；文档点名 BLAKE3（256 位密钥）与 HMAC-SHA-256，并明确说 **HMAC-MD5 不可接受**。

六个输入各有分工：`Net_Iface`（MAC）让"随机化 MAC"能顺带换地址；`Network_ID`（如 Wi-Fi SSID）让换网络换地址；`Time` 让同一网络内地址随时间变化；`DAD_Counter` 解决冲突；`secret_key` 至少 128 位，**必须与生成稳定地址（RFC 7217）用的密钥分开**。

### 3. 两代方案的三个方向性差异

| 维度 | RFC 4941 | RFC 8981 |
| --- | --- | --- |
| 取位方向 | MD5 的**左** 64 位 | RID 从**最低有效位**开始取 |
| U/L 位 | 清零（表示本地） | 不动（RFC 7136：IID 里没有特殊位） |
| 冲突处理 | 换成 MD5 右 64 位作 history 重来 | `DAD_Counter` 加 1 重来 |

实测对照：64 个时间片下 RFC 8981 会产出 U/L=1 的 IID（约一半），而 RFC 4941 方案全部为 0。

### 4. 生命周期（§3.3 step 4）

```
Valid Lifetime     = min(公开地址的 Valid Lifetime,     TEMP_VALID_LIFETIME)
Preferred Lifetime = min(公开地址的 Preferred Lifetime, TEMP_PREFERRED_LIFETIME - DESYNC_FACTOR)
```

**只有 preferred 减 `DESYNC_FACTOR`，valid 不减** —— 这是最容易写错的一点（§3.3 step 1 的总括句也是这么说的：no temporary addresses should ever remain "valid" or "preferred" for longer than `TEMP_VALID_LIFETIME` or `TEMP_PREFERRED_LIFETIME − DESYNC_FACTOR`）。

另外三条：

- **step 5**：只有算出来的 Preferred Lifetime **严格大于** `REGEN_ADVANCE` 才创建；绝不能创建 Preferred 为 0 的临时地址。
- **step 2**：更新已有临时地址时，到期时刻取 `min(RA 给的到期时刻, CREATION_TIME + TEMP_PREFERRED_LIFETIME - DESYNC_FACTOR)`。
- **step 7**：DAD 失败就重新生成 IID 再来，最多 `TEMP_IDGEN_RETRIES` 次；全部失败要记系统错误，且**不再为该接口生成临时地址**。

### 5. 默认参数（RFC 8981 §3.8）

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `TEMP_VALID_LIFETIME` | 2 天 | 必须大于 `TEMP_PREFERRED_LIFETIME` |
| `TEMP_PREFERRED_LIFETIME` | 1 天 | |
| `TEMP_IDGEN_RETRIES` | 3 | |
| `REGEN_ADVANCE` | `2 + 3×1×1000/1000 = 5` 秒 | 由 DAD 最坏耗时推导，避免"新地址还没生成旧的就废了" |
| `MAX_DESYNC_FACTOR` | `0.4 × 86400 = 34560` 秒 | 让不同客户端的地址生命周期 statistically 不同，且默认下最多 3 个临时地址并存 |

`DESYNC_FACTOR` 必须同时满足 `0 ≤ d ≤ MAX_DESYNC_FACTOR` 且 `d < TEMP_PREFERRED_LIFETIME - REGEN_ADVANCE`。

### 6. 重新生成频率（§3.5）

至少每 `TEMP_PREFERRED_LIFETIME - REGEN_ADVANCE - DESYNC_FACTOR` 生成一次新 IID。默认下是 86395 秒（DESYNC=0）到 51835 秒（DESYNC 取上限）。

**§3.4 有个容易漏的特例**：如果临时地址是因为收到 `Preferred Lifetime = 0` 的 Prefix Information Option 而变为 deprecated，**不得**生成新的临时地址。

## 对比表

| 特性 | 公开地址（EUI-64） | 临时地址 |
| --- | --- | --- |
| IID 来源 | MAC 地址 | 随机化（MD5 链 / 带密钥 PRF） |
| U/L 位 | 1（全局） | RFC 4941：0；RFC 8981：不定 |
| 用途 | 接收连接 | 发起连接 |
| 生命周期 | 跟随前缀 | 被 `TEMP_*_LIFETIME` 截断 |

## 环境

- Python 3.13（标准库 `hashlib` / `hmac`）
- Go 1.22（标准库 `crypto/md5` / `crypto/hmac` / `crypto/sha256`）
- 纯算法模型：不发 RA、不做真实 DAD

## 运行方式

```bash
cd python && python selfcheck_slaacprivacy.py   # 65 条断言，全部实跑
cd python && python main.py                     # 打印六组对照表
cd go     && go run .
```

## 关键代码

```python
def md5_iid(history_value, public_iid):
    """MD5(history || public_iid)：左 64 位清 U/L 后作 IID，右 64 位作下一轮 history。"""
    digest = hashlib.md5(history_value.to_bytes(8, "big")
                         + public_iid.to_bytes(8, "big")).digest()
    left = int.from_bytes(digest[:8], "big")
    right = int.from_bytes(digest[8:], "big")
    return (left & ~(UL_BIT_MASK << 56)) & ((1 << 64) - 1), right
```

```python
def temp_address_lifetimes(prefix_valid, prefix_preferred, desync_factor=0):
    valid = min(prefix_valid, TEMP_VALID_LIFETIME)
    preferred = min(prefix_preferred, TEMP_PREFERRED_LIFETIME - desync_factor)
    return valid, preferred          # 只有 preferred 减 DESYNC_FACTOR
```

## 性能与边界

- **MD5 只是历史选择**：RFC 4941 自己说"MD5 是为了方便选的，实现可以用别的算法"；RFC 8981 则明确禁止 HMAC-MD5。新实现一律走 §3.3.2 的 PRF。
- **地址数量有代价**：§3.5 末段提醒，节点上挂太多地址会影响地址查找、组播组加入等；频繁换（比如几分钟一次）会有性能问题，而"每天或每周一次"就足以缓解绝大多数隐私担忧。
- **`REGEN_ADVANCE` 依赖两个别的协议的默认值**：`DupAddrDetectTransmits`（RFC 4862，默认 1）与 `RetransTimer`（RFC 4861，默认 1000 ms）。改了这两个参数，`REGEN_ADVANCE` 会跟着变，本 demo 用函数而不是常量就是为了这件事。
- **最多 3 个临时地址并存**：这是 `MAX_DESYNC_FACTOR = 0.4 × TEMP_PREFERRED_LIFETIME` 配默认值推出来的统计结论，不是硬限制。

## 注意事项与常见坑

1. **`valid` 不减 `DESYNC_FACTOR`**：只有 `preferred` 减。写成两个都减会让临时地址提前失效，且违反 §3.3 step 1 的总括句。
2. **"是否创建"用的是严格大于 `REGEN_ADVANCE`**：恰好等于（默认 5 秒）时**不创建**。平局输入是唯一能区分 `>` 与 `>=` 的地方，测试一定要覆盖。
3. **两个 RFC 的取位方向相反**：RFC 4941 取 MD5 的**左** 64 位，RFC 8981 取 RID 从**最低有效位**开始的位。用同一个 RID 试算，两者会给出完全不同的 IID。
4. **U/L 位的处理也相反**：RFC 4941 明确清零；RFC 8981 依据 RFC 7136 说"IID 里没有特殊位"，不再动它。若沿用旧逻辑清位，会损失一位熵且偏离规范。
5. **`secret_key` 不得复用**：§3.3.2 明确要求生成稳定地址与生成临时地址**不能**用同一个密钥。
6. **`Preferred Lifetime = 0` 的 RA 是特例**：它让现有地址 deprecated，但按 §3.4 **不得**触发新地址的生成。把它当成"正常过期"会导致地址反复重建。
7. **重试计数器的语义不同**：RFC 4941 重试时换的是 **history**（MD5 右 64 位），RFC 8981 换的是 **`DAD_Counter`**（+1）。成功后保存的 history 是**本次** MD5 的右 64 位，不是上一次的。
8. **Go 侧 `uint64` 装得下 64 位 IID**：但前缀是 128 位，本 demo 把它拆成两个 `uint64` 传入；真正实现时应直接用 `[16]byte`。

## 参考资料

- RFC 4941《Privacy Extensions for Stateless Address Autoconfiguration in IPv6》§3.2.1（MD5 + history 的生成算法与为什么需要 history）、§3.3（收到 RA 后的六个步骤与生命周期）、§3.4（过期与重新生成、Preferred=0 的特例）、§3.5（重新生成频率与客户端代价）—— https://www.rfc-editor.org/rfc/rfc4941.txt
- RFC 8981《Temporary Address Extensions for Stateless Address Autoconfiguration in IPv6》§3.3.2（PRF 生成算法、六个输入的含义、F() 的要求、secret_key 约束）、§3.4（IID 变化带来的运维影响）、§3.8（全部协议参数与默认值及其 rationale）—— https://www.rfc-editor.org/rfc/rfc8981.txt

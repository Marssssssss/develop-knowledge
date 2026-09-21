# TCP 盲注入攻击与 RFC 5961 缓解

## 简介

TCP 连接一旦建立，任何能猜到四元组（两端 IP + 端口）的**旁路攻击者**都可以伪造报文。RFC 793 的原文判据是「RST 的序列号**落在接收窗口内**就算有效」，而窗口通常是 32768 或 65535，于是需要的猜测次数从 2^31 骤降到 2^31/窗口 ≈ 6.5 万 —— [SITW]（Watson 2004）证明这在今天的接入带宽下几分钟就能打完，BGP 这类长连接首当其冲。

RFC 5961（*Improving TCP's Robustness to Blind In-Window Attacks*，2010）把判据收紧为「必须**精确等于** RCV.NXT」，并引入 challenge ACK，使难度回到 2^31。本 demo 逐行转写 Linux 内核 `net/ipv4/tcp_input.c` 的 `tcp_validate_incoming()` / `tcp_sequence()` / `tcp_reset_check()` / `tcp_send_challenge_ack()`，并与 RFC 5961 的规范文本逐条对拍。

## 原理详解

### 1. RST：三分支而不是两分支

RFC 793 只有「窗外静默丢弃 / 窗内接受」两条。RFC 5961 §3.2 拆成三条：

| RST 序列号位置 | RFC 793 | RFC 5961 + 内核 | 动作 |
| --- | --- | --- | --- |
| 窗外（左侧） | 丢弃 | 丢弃 | `discard` |
| **恰好 == RCV.NXT** | 接受 | 接受 | `reset` |
| 窗内但 ≠ RCV.NXT | **接受** | **发 challenge ACK** | `challenge_ack` |
| 窗外（右侧） | 丢弃 | 丢弃 | `discard` |

challenge ACK 的内容是 `<SEQ=SND.NXT><ACK=RCV.NXT><CTL=ACK>`。对端若真发过 RST，它没有 TCB 了，收到 ACK 会再回一个序列号恰好匹配的 RST，连接照样被拆 —— 所以**合法 RST 不会被误伤**，只是多一跳。

内核 `tcp_validate_incoming()` 的 step 2 在此之上又放宽了两条（源码注释明写 "extend to match against (RCV.NXT - 1) after a FIN and SACK too if available"）：

- `tcp_reset_check()`：`seq == rcv_nxt - 1` 且状态 ∈ {CLOSE_WAIT, LAST_ACK, CLOSING} → 直接 reset。这三个状态是对端已经发过 FIN 的场景，此时站在 `rcv_nxt-1` 上的 RST 就是 FIN 的伴生包。
- SACK：`seq == max(所有 SACK 块的 end_seq)` → 直接 reset。最右那个空洞的右边缘等价于"下一个期望字节"。

### 2. SYN：与 RST 完全相反

RFC 5961 §4.2 的措辞是 **"irrespective of the sequence number"** —— 处于同步态时收到 SYN，**不管序列号落在哪里**都发 challenge ACK，连窗外也不例外。内核源码里 `syn_challenge` 标签被 step 1（序列号检查失败）和 step 4 共同跳转，就是这条规则的落地。

对比一下就很清楚：RST 在窗外是**静默丢弃**（否则 attacker 可以用窗外 RST 放大出无限 challenge ACK），SYN 在窗外却是**照发 challenge ACK**。这是两者最大的行为差异。

唯一的例外是 SYN_RECV 下的重传纯 ACK：`seq+1 == rcv_nxt && ack_seq == snd_nxt && end_seq == seq+1` 时走正常路径放行（`goto pass`），否则握手完成后的重传 ACK 会被自己的 challenge 机制卡住。

### 3. 数据注入：收紧 ACK 判据

注入数据除了要猜中序列号，ACK 字段也得合法。RFC 793 / RFC 5961 §5.1 描述的旧判据是

```
(SND.UNA - (2^31 - 1)) <= SEG.ACK <= SND.NXT
```

这个下界往回让了整整 21 亿，可接受取值有 **2^31 个**。RFC 5961 §5.2 把它换成

```
(SND.UNA - MAX.SND.WND) <= SEG.ACK <= SND.NXT
```

可接受取值瞬间降到 `MAX.SND.WND + 1`（默认 65536 → 65537 个），**缩小约 32768 倍**（实测比值 2147483648 / 65537 = 32767.5）。注意 RFC 5961 把这条定为 **MAY** 而不是 SHOULD：因为数据注入本身就要多猜一次，难度已是 RST/SYN 的两倍（平均 `2^32/RCV.WND` 次 vs `2^31/RCV.WND` 次）。

### 4. challenge ACK 的两级限速

challenge ACK 本身会被伪造的 RST/SYN 放大成 ACK 风暴，所以 RFC 5961 §7 要求做 ACK Throttling（建议值示例：5 秒窗口内不超过 10 个）。Linux 的实现是**两级**，且第二级有个反直觉的细节：

```c
static bool tcp_challenge_ack_allowed(struct net *net)
{
    ack_limit = READ_ONCE(net->ipv4.sysctl_tcp_challenge_ack_limit);
    if (ack_limit == INT_MAX) return true;
    now = jiffies / HZ;
    if (now != READ_ONCE(net->ipv4.tcp_challenge_timestamp)) {
        u32 half = (ack_limit + 1) >> 1;
        WRITE_ONCE(net->ipv4.tcp_challenge_timestamp, now);
        WRITE_ONCE(net->ipv4.tcp_challenge_count,
                   get_random_u32_inclusive(half, ack_limit + half - 1));
    }
    ...
}
```

**每秒的计数不是复位成 `ack_limit`，而是复位成 `[half, ack_limit + half - 1]` 里的一个随机值**（`half = (limit+1)>>1`）。以默认口径 limit=1000 为例：half=500，初值落在 [500, 1499]，于是单秒实际可发出的 challenge ACK 数量在 500~1499 之间 —— **上界 1499 反而大于 limit**。这样设计是为了让攻击者无法通过"每秒恰好打满"来测量这个阈值。另外 `ack_limit == INT_MAX` 是"关闭限速"的哨兵值，直接恒返回 true。

第一级是 per-socket 的：`__tcp_oow_rate_limited()` 用 `sysctl_tcp_invalid_ratelimit`（jiffies）做窗口，判据是 `0 <= elapsed < ratelimit`——**严格小于**，恰好等于时放行；`elapsed` 是有符号 32 位，为负（对端时间"超前"或回绕）时同样放行。

### 5. 难度实测

按 RFC 5961 §1.3 的口径（攻击者以窗口为步长横扫整个序列空间）：

| 场景 | 平均需要 | 实测 |
| --- | --- | --- |
| 窗口 32768，RFC 793 判据 | 2^31/32768 | 65536 |
| 窗口 65535，RFC 793 判据 | 2^31/65535 | 32768.5（文档取整 32768） |
| 任意窗口，RFC 5961 判据 | 2^31 | 2147483648 |
| 数据注入（窗口 32768） | 2^32/32768 | 131072 |

用 N=2^12、WND=64 的小规模穷举核对：窗口判据的平均命中下标 32.5、精确判据 2048.5，与闭式 `N/(2W)`、`(N+1)/2` 完全吻合。

## 对比表

| 维度 | RFC 793 | RFC 5961 | Linux 实际 |
| --- | --- | --- | --- |
| RST 有效性 | 窗口内即可 | 必须 == RCV.NXT | 同左，另放宽 `rcv_nxt-1`（三个关闭态）与 SACK 最右边缘 |
| SYN（同步态） | 窗口内则回 RST | 一律 challenge ACK | 同左，唯一例外是 SYN_RECV 的重传纯 ACK |
| 窗外 RST | 静默丢弃 | 静默丢弃 | 同左（不发 challenge，避免放大） |
| ACK 可接受区间 | 下界回让 2^31-1 | 下界只回让 MAX.SND.WND | 采用 RFC 5961 判据 |
| challenge 限速 | 无 | 建议（示例 10/5s） | 两级：per-socket + per-netns（随机初值） |

## 环境

- Python 3.13（标准库即可，无需第三方包）
- Go 1.22（仅编译运行，无第三方依赖）
- 无内核依赖：本 demo 是把内核判定逻辑**转写成用户态模型**，不发包、不改 sysctl

## 运行方式

```bash
cd python && python selfcheck_tcp5961.py   # 68 条断言，全部实跑
cd python && python main.py                # 打印六组证据表
cd go     && go run .                      # Go 版同模型（需 Go 工具链）
```

## 关键代码

`python/tcp5961.py` 中 `validate_incoming()` 是内核 `tcp_validate_incoming()` 的直译：

```python
reason = tcp_sequence(sk, seg)
if reason is not None:                    # step 1：序列号不可接受
    if not rst:
        if syn:
            return challenge("syn_challenge")   # SYN 窗外也 challenge
        return (DISCARD, "dupack/" + reason)
    if reset_check(sk, seg):              # 只有 RST 还有机会被 reset_check 捞回
        return (RESET, "reset_check_out_of_window")
    return (DISCARD, "silent/" + reason)  # 窗外 RST：静默

if rst:                                   # step 2
    if seq == sk.rcv_nxt or reset_check(sk, seg):
        return (RESET, "seq_matches_rcv_nxt")
    if sk.sacks:
        max_sack = max(e for _, e in sk.sacks)
        if seq == (max_sack & MASK32):
            return (RESET, "seq_matches_max_sack_edge")
    return challenge("rst_challenge")

if syn:                                   # step 4
    ...
    return challenge("syn_challenge")
```

## 性能与边界

- **序列号比较必须用模 32 位**：`seq_before(a,b)` 定义为 `(s32)(a-b) < 0`，只能分辨 2^31 以内的相对关系，跨半程比较会反转。demo 中所有比较都过 `seq_before/seq_after`，不做裸整数比较。
- **challenge ACK 是"非放大"的**：每个伪造段最多换来一个 ACK，且 ACK 比收到的段更短；但 RFC 5961 §9.3 提醒有些中间盒会**丢弃** challenge ACK，导致对端永远收不到挑战、连接无法被正常拆掉。
- **RFC 5961 §4.2 留了一个未处理的角例**：对端重启后恰好选到相同 IP+端口，且 ISN 正好是 `(RCV.NXT - 1)`，此时 challenge ACK 会落进对方的窗口而被当重复 ACK 忽略，连接要等到 SYN 重传超时才断。这是 RFC 793 的遗留问题，不是新引入的。
- **MD5 选项（RFC 2385）比 RFC 5961 更彻底**：BGP 这类场景应该直接用 TCP-MD5，让盲注从"很难"变成"不可能"。

## 注意事项与常见坑

1. **RFC 5961 §3.2 的判据文本自身不自洽**：第 1 条把"窗外"写成 `SEG.SEQ <= RCV.NXT || SEG.SEQ > RCV.NXT+RCV.WND`，第 2 条又把"可接受"写成 `RCV.NXT <= SEG.SEQ < RCV.NXT+RCV.WND` —— 两者在 `SEG.SEQ == RCV.NXT` 处**同时为真**。实现一律以第 3 条（精确匹配就 reset）为准，内核 `seq == tp->rcv_nxt → goto reset` 也是这么做的。
2. **右边界的口径是 `>` 而不是 `>=`**：内核 `tcp_sequence()` 判的是 `after(end_seq, seq_limit)`，即 `end_seq > rcv_nxt + max_receive_window` 才算窗外。所以 `seq == rcv_nxt + rcv_wnd`（无载荷时 `end_seq == seq`）会被判为**窗口内**并触发 challenge ACK，而按 RFC 793 的字面口径它已经在窗外了。
3. **内核用的是 `tcp_max_receive_window()` 不是 `rcv_wnd`**：前者会并入窗口缩放与 `net.ipv4.tcp_adv_win_scale` 的调整。demo 为可复现起见直接用 `rcv_wnd`，真实内核的右边界会更宽。
4. **`sysctl_tcp_challenge_ack_limit` 的默认值是发行版相关的**，本 demo 不硬编码，一律以参数注入（README 中所有数字都在 `limit=1000` 下给出）。同理 `sysctl_tcp_invalid_ratelimit` 以 jiffies 计，demo 用 500。
5. **别把"平均尝试次数"当成"保证次数"**：2^31/窗口是几何分布的期望，中位数是 `ln2 × 期望`，约 0.693 倍；安全评估应该看后者。
6. **`ack_window_size()` 的两个口径不能混用**：RFC 793 的可接受取值数是 2^31（含端点），RFC 5961 是 `MAX.SND.WND + 1`；差一个"是否 +1"会让比值从 32767.5 变成 32768，不影响结论但会让断言对不上。

## 参考资料

全部为实际读取并抽取原文的链接：

- RFC 5961《Improving TCP's Robustness to Blind In-Window Attacks》§1.2/§1.3（攻击方法与概率）、§3.2（RST 缓解三条）、§4.2（SYN 缓解）、§5.1/§5.2（ACK 判据）、§7（ACK Throttling）、§9.3（中间盒丢弃 challenge ACK）—— https://www.rfc-editor.org/rfc/rfc5961.txt
- Linux 内核 `net/ipv4/tcp_input.c`：`tcp_validate_incoming()`、`tcp_sequence()`、`tcp_reset_check()`、`tcp_send_challenge_ack()`、`tcp_challenge_ack_allowed()`、`__tcp_oow_rate_limited()` —— https://cdn.jsdelivr.net/gh/torvalds/linux@master/net/ipv4/tcp_input.c
- Linux 内核 `net/ipv4/sysctl_net_ipv4.c`：`tcp_challenge_ack_limit` 的 proc 注册 —— https://cdn.jsdelivr.net/gh/torvalds/linux@master/net/ipv4/sysctl_net_ipv4.c

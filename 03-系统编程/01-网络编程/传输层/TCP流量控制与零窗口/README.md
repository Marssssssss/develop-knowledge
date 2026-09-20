# TCP 流量控制、零窗口探测与窗口扩大

流量控制常被一句「接收方用窗口告诉发送方还能发多少」带过。真正有信息量的是三件事：**窗口更新为什么会被主动压住**（SWS）、**窗口归零以后靠什么解开死锁**（ZWP）、**16 位窗口字段在高速网络下是怎么被位移救活的**（RFC 7323）。

## 一、原理详解

### 1.1 可用窗口 U：三个变量而不是一个

RFC 9293 §3.8.6.2.1 定义：

```
U = SND.UNA + SND.WND - SND.NXT
```

即「通告窗口减去已发出但未确认的量」。**`SND.WND` 是对方给的额度，`U` 才是此刻真能发的量** —— 混淆这两者是流量控制类 bug 的头号来源。

### 1.2 发送端 SWS：四条判据，任一成立即发

糊涂窗口综合症（Silly Window Syndrome）是「窗口右边界一小步一小步地挪，导致双方稳定地交换小报文」。RFC 9293 给了四条判据：

| # | 条件 | 含义 |
| --- | --- | --- |
| (1) | `min(D,U) >= Eff.snd.MSS` | 够一个满报文就发 |
| (2) | `[SND.NXT = SND.UNA and] PUSHed and D <= U` | 被推且能一次发完 |
| (3) | `[SND.NXT = SND.UNA and] min(D,U) >= Fs * Max(SND.WND)` | 够半个历史最大窗口 |
| (4) | override 超时 | 兜底，`0.1–1.0` 秒 |

两个容易被忽略的细节：

- **判据 (2)(3) 都要求 `SND.NXT == SND.UNA`**，也就是「没有在飞数据」。有数据在飞时只剩判据 (1)。
- **`Fs` 推荐 1/2，而 `Max(SND.WND)` 是历史最大值、只增不减**。规范明说这只是对 `RCV.BUFF` 的**估计**（*"this can only be an estimate; the receiver may at any time reduce the size of RCV.BUFF"*），所以才需要判据 (4) 的超时兜底来避免死锁。

demo 里专门做了一组隔离实验：当 `Fs*Max(SND.WND) > MSS` 时判据 (1) 会**完全盖住**判据 (3)，必须把窗口压到 `Fs*Max < MSS` 才能单独观察 (3)。这解释了为什么很多实现里 (3) 实际永远不触发。

### 1.3 接收端 SWS：右边界不动，攒够才更新

RFC 9293 §3.8.6.2.2 把 `RCV.BUFF` 切成三段：

```
|<----------------- RCV.BUFF ----------------->|
     1               2              3
 ----|---------|------------------|------|----
        RCV.USER      RCV.WND      Reduction
```

- 1 = 已确认但应用未取走（`RCV.USER`）
- 2 = 已通告给发送方的空间（`RCV.WND`）
- 3 = 可用但**尚未通告**的空间

规则原文：*"Keeping the right window edge fixed as data arrives and is acknowledged requires that the receiver offer less than its full buffer space"* —— 收到 n 字节时 `RCV.NXT` 前进 n，为了让 `RCV.NXT+RCV.WND` 不动，`RCV.WND` 必须**同步减 n**。

只有当下式成立才把 `RCV.WND` 一次性放开：

```
RCV.BUFF - RCV.USER - RCV.WND >= min(Fr * RCV.BUFF, Eff.snd.MSS)     Fr = 1/2
→ RCV.WND = RCV.BUFF - RCV.USER
```

demo 的实测：缓冲 65536、MSS 1460，应用分两次读走 1000 和 600 字节 —— **第一次不更新**（reduction=1000 < 1460），**第二次一次性把右边界前移 1600**（不是本次的 600）。这正是「抑制小幅更新」的字面含义：更新是延迟且批量的。

### 1.4 零窗口与死锁的解除

应用停止读取 → `RCV.USER == RCV.BUFF` → `RCV.WND = 0`。此时双方进入一个危险状态：

- 发送方没有额度，不再发数据，也就**收不到新的 ACK**；
- 接收方的应用迟早会读走数据、窗口会重开，但如果它不发 ACK，发送方**永远不知道**。

RFC 9293 §3.8.6.1 的解法是零窗口探测（ZWP）：

> *"The transmitting host SHOULD send the first zero-window probe when a zero window has existed for the retransmission timeout period, and SHOULD increase exponentially the interval between successive probes."*

即首个探测在**零窗口持续 RTO 之后**，间隔指数增长。demo 取 RTO=1.0s，实测探测时刻为 `1, 3, 7, 15`（间隔 `1, 2, 4, 8` 个 RTO）。

另外两条 MUST/MAY 是配套的：

| 编号 | 原文要点 | 后果 |
| --- | --- | --- |
| MUST-36 | 零窗口探测必须支持 | 不做就永久死锁 |
| MAY-8 | 接收方**可以无限期**把窗口关着 | 「打印机没纸了」场景是合法的 |
| MUST-37 | 只要对方还在回 ACK，发送方**必须保持连接不拆** | 零窗口不是空闲连接，不能按 idle timeout 处理 |
| — | 零窗口下收到报文**仍要回 ACK**，窗口字段填 0 | 不回 ACK 就会被判定为死连接 |

**工程含义**：看到抓包里 `win=0` 不要急着判故障；要看发送方有没有按时发探测、接收方有没有回 `win=0` 的 ACK。反过来，如果接收方连 ACK 都不回，那才是真断了。

### 1.5 窗口扩大：16 位字段的位移救赎

不缩放时窗口上限是 65535 字节。RFC 7323 的办法是在 **SYN 段**协商一个位移量：

```
SND.WND = SEG.WND << Snd.Wind.Shift      （收方向）
SEG.WND = RCV.WND >> Rcv.Wind.Shift      （发方向）
```

| 约束 | 值 |
| --- | --- |
| `shift.cnt` 上限 | **14** |
| 最大可表达窗口 | 2^(14+16) = **1 GiB** |
| 出现位置 | **只在 SYN / SYN,ACK**；非 SYN 段出现 MUST be ignored |
| SYN 段窗口字段 | **MUST NOT be scaled** |

两个位移带来的副作用，demo 都有断言：

1. **量化损失**：`RCV.WND` 不是 2^shift 整数倍时，右移再左移会**向下取整**。窗口 1000、`shift=4` → 线上字段 62 → 还原得 992，**白白少用 8 字节**。所以接收缓冲设成 2 的幂并非迷信。
2. **窗口回缩**：右移会让通告窗口**变小**。真实窗口 1000→991 时，线上字段从 62 掉到 61 —— 规范 §2.4 明确 *"Implementations MUST ensure that they handle a shrinking window"*。但回缩**不是必然**的：992 仍落在 62 这一档。这一条的判别价值在于「别以为窗口只会变大」。

## 二、与拥塞控制的对比

| | 流量控制（本 demo） | 拥塞控制（见 `TCP拥塞控制/`） |
| --- | --- | --- |
| 保护对象 | 接收方缓冲 | 网络路径 |
| 状态量 | `RCV.WND` / `SND.WND` | `cwnd` / `ssthresh` |
| 信息来源 | 对端显式通告 | 自身推断（丢包 / RTT / ECN） |
| 上限 | `RCV.BUFF`（缩放后 ≤ 1 GiB） | 无硬上限 |
| 实际发送量 | `min(cwnd, SND.WND)` | 同上 |

**实际发送窗口取两者的较小值** —— 只调大 `SO_RCVBUF` 而路径 BDP 不够，或只改拥塞算法而接收缓冲仍是 64 KB，都不会有收益。

## 三、环境要求

- Python 3.8+（仅标准库）
- Go 1.18+（本机无 Go 工具链，代码为人工审查 + 结构校验）

## 四、运行方式

```bash
cd TCP流量控制与零窗口
python selfcheck_tcpwin.py    # 47 项断言
go run tcpwin.go              # Go 版 25 项断言
```

## 五、关键代码

```python
def may_send(self, D, pushed=False, override=False):
    U = self.usable()
    if U <= 0:
        return False
    if min(D, U) >= self.mss:                                    # (1)
        return True
    if pushed and self.nxt == self.una and D <= U:               # (2)
        return True
    if self.nxt == self.una and min(D, U) >= FS * self.max_wnd:  # (3)
        return True
    return bool(override)                                        # (4)
```

接收端抑制更新：

```python
def receive(self, n):
    self.user += n
    self.nxt += n
    self.wnd = max(0, self.wnd - n)   # 保持 RCV.NXT+RCV.WND 不动
    return self.maybe_update_window()

def maybe_update_window(self):
    if self.reduction >= min(FR * self.buff, self.mss):
        self.wnd = self.buff - self.user
        self.updates += 1
        return True
    return False
```

## 六、性能边界

- **BDP 决定要不要缩放**：窗口必须 ≥ `带宽 × RTT`。1 Gbps × 100 ms 需要约 12.5 MB 窗口，不缩放（上限 64 KB）时吞吐被压到约 5 Mbps。
- **缩放只在 SYN 协商一次**，连接建立后不可改；所以 `SO_RCVBUF` 必须在 `connect()`/`listen()` **之前**设置。
- **量化损失随 shift 增大**：`shift=14` 时一档就是 16 KB，小缓冲配大 shift 会浪费显著。
- **Linux 侧**：`net.ipv4.tcp_window_scaling` 默认开启；`tcp_rmem` 是 `min default max` 三元组，`tcp(7)` 明说想用大窗口必须开 `tcp_window_scaling`。`SO_RCVBUF` 经 `setsockopt` 设置后内核会**翻倍**记账（bookkeeping overhead）。
- 本 demo 只建模状态机，**不涉及真实内核时序**；RTO 取 1.0 s 是为了让时刻表可读，真实 RTO 由 RTT 测算得出。

## 七、注意事项与常见坑

1. **`SND.WND` ≠ 能发的量**，能发的是 `U`。
2. **零窗口不是连接故障**：MUST-37 要求发送方保持连接。按 idle timeout 拆零窗口连接是错的。
3. **`Max(SND.WND)` 只是估计值**，判据 (3) 依赖它，所以必须保留 (4) 的超时兜底，否则接收端缩小缓冲时会死锁。
4. **窗口可以回缩**（RFC 7323 §2.4），任何假设「窗口单调不减」的代码都会在特定 shift 组合下出错。
5. **设 `SO_RCVBUF` 要赶在连接建立前**；而且 Linux 会翻倍记账，`getsockopt` 读回来的值是你设的两倍左右，不是 bug。
6. **接收端 SWS 会让窗口更新延迟**，抓包时看到「应用明明读了数据窗口却没变」是正常行为，不是内核迟钝。
7. **判据 (1) 会盖住 (3)**：`Fs*Max(SND.WND) > MSS` 时 (3) 永不触发，写测试时若想覆盖 (3) 必须构造小窗口场景。

## 八、参考资料

- RFC 9293《Transmission Control Protocol (TCP)》§3.8.6 / §3.8.6.1 / §3.8.6.2 — https://www.rfc-editor.org/rfc/rfc9293.txt
- RFC 7323《TCP Extensions for High Performance》§2.2 / §2.3 / §2.4 — https://www.rfc-editor.org/rfc/rfc7323.txt
- `tcp(7)` — https://man7.org/linux/man-pages/man7/tcp.7.html （`tcp_rmem` 三元组、`tcp_adv_win_scale`、`tcp_window_scaling`）
- `socket(7)` — https://man7.org/linux/man-pages/man7/socket.7.html （`SO_RCVBUF` 翻倍记账）

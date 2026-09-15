# TCP 拥塞控制：Reno vs CUBIC

## 简介

**拥塞控制**是 TCP 在"不知道链路容量"的前提下，通过丢包/延迟等间接信号推断可用带宽，
并据此调整发送速率的闭环机制。它决定了互联网上每条 TCP 流能拿到多少带宽，也决定了
整个网络在过载时是"优雅收敛"还是"拥塞崩溃"。

关键概念：

- **cwnd（拥塞窗口）**：发送方侧的限制，实际可发送量 = `min(cwnd, rwnd)`（rwnd 是接收方窗口）。
- **ssthresh（慢启动阈值）**：cwnd 低于它走慢启动（指数增长），高于它走拥塞避免（加性增）。
- **AIMD**：加性增/乘性减（Additive Increase Multiplicative Decrease）——探路时每次 +1 段，
  撞墙时直接砍半。所有"Reno 系"算法的共同骨架。
- **BDP（带宽时延积）**：`带宽 × RTT`，即"管道里能装多少数据"。高 BDP 的长肥管道是 Reno 的死穴。
- **拥塞事件**：3 个重复 ACK（快重传，说明只有个别段丢失）或 RTO 超时（说明严重拥塞）。

历史背景：1986 年互联网经历第一次**拥塞崩溃**（吞吐从 32 kbps 掉到 40 bps），Jacobson 在
1988 年提出慢启动/拥塞避免，RFC 5681 是其标准化文本。此后 20 年，随着带宽从 kbps 涨到
Gbps、RTT 却基本不变，Reno 的"每 RTT 加 1 段"变成致命瓶颈——于是 Rhee 与 Xu 在 2005/2008
年提出 CUBIC，用**时间的三次函数**替代线性增长，现已成为 Linux / Windows / Apple 的默认
拥塞控制算法（RFC 9438 已将其实质标准化）。

## 原理详解

### 1. 慢启动：找到"管道容量"的下界

每收到一个确认新数据的 ACK，`cwnd += min(N, SMSS)`（RFC 5681 公式 2）。因为一个 RTT 内
会收到约 cwnd 个 ACK，**窗口每 RTT 翻倍**（指数增长），直到超过 ssthresh 或发生拥塞。

### 2. 拥塞避免：AIMD 的加性增

`cwnd += SMSS*SMSS/cwnd`（RFC 5681 公式 3）——每个 ACK 加一点，**净效果是每 RTT 恰好 +1 段**。
RFC 明确写 "MUST NOT be increased by more than SMSS bytes"，即一个 RTT 最多涨 1 段。

### 3. 两种丢包信号，两种反应

| | 3 个重复 ACK（快重传/快恢复） | RTO 超时 |
| --- | --- | --- |
| 判断依据 | 有段离开网络，ACK 时钟还在打点 | 整窗可能全丢 |
| ssthresh | `max(FlightSize/2, 2*SMSS)`（公式 4） | 同上（仅首次该段重传，后续保持不变） |
| cwnd | `ssthresh + 3*SMSS`（膨胀），每多一个 dup ACK 再 +SMSS；收到确认新数据的 ACK 后"放气"到 `ssthresh` | 设成 **LW = 1 个满尺寸段** |
| 后续 | 直接回到拥塞避免（净效果 = 砍半后继续 AIMD） | 从 1 段重新慢启动爬到新 ssthresh |

净结论：**快重传 ≈ 窗口乘性减半**，**RTO 把窗口打回 1 段**——后者代价高得多，所以本 demo
把两条路径都实现出来对照。

### 4. CUBIC：为什么不用线性增长

Reno 丢包后掉到 `W_max/2`，而"每 RTT 只 +1 段"意味着要 **W_max/2 个 RTT** 才能爬回原窗口。
在 100 ms RTT、10 Gbps 的链路上，W_max 可能上万段，这段恢复期长达几十分钟——链路利用率
只有 75% 左右。CUBIC 换成三次函数：

```
W_cubic(t) = C * (t - K)^3 + W_max        (RFC 9438 §4.2 Figure 1)
K = cbrt((W_max - cwnd_epoch) / C)        (RFC 9438 §4.2 Figure 2)
```

- `C = 0.4`（SHOULD，RFC 9438 §5），`beta_cubic = 0.7`（SHOULD，§3.4）——注意不是 Reno 的 0.5。
- `K` 是"从当前窗口涨回 W_max"的时间，**与 BDP 只有三次根关系**（BDP 翻 8 倍，恢复时间才翻倍）。
- 曲线的形状就是它的名字来源：

```
     cwnd
      ^
W_max |          ,---------..____ platform（增速触底，贴着饱和点走）
      |      ,-''                    `--..___
      |   ,-'   凹段(concave)          凸段(convex) --..
      | ,'  增速递减                  增速递增（探测新带宽）
      +-------------------------------------------------> t
         t_epoch        K
```

  - **凹段**（`t < K`）：增速越来越慢，窗口在 W_max 附近形成一个"平台"——链路利用率最高
    的区间恰好停在这里，这就是 CUBIC 稳定性好的原因。
  - **凸段**（`t > K`）：开始加速向外探测，寻找可能新出现的带宽。

### 5. 三个区域与两条护栏（RFC 9438 §4.3 / §4.7）

- **Reno-friendly 区域**：同时维护 `W_est`（一个模拟 Reno AIMD(1,0.5) 的窗口估计），
  每轮 `W_est += α`，取 `cwnd = max(W_cubic, W_est)`。`α = 3(1-β)/(1+β) ≈ 0.5294`
  （β=0.7 时），使得**平均窗口与 Reno 相同**——保证与 Reno 流竞争时不吃亏。
  一旦 `W_est` 追上 `cwnd_prior`，`α` 降为 1。
- **Fast convergence**（§4.7）：拥塞发生时若 `cwnd < W_max`，说明饱和点在下移、
  有别的流让出了带宽，于是 `W_max = cwnd*(1+β)/2` **额外收缩一次**，主动多让带宽并
  让本流更早进入平台。**只在多流环境启用**，单流时应关闭（本 demo 两个开关都实现）。
- **RTO（§4.8）**：cwnd 按 Reno 规则降到 1 段，但 ssthresh 用 `β_cubic`（不是 1/2）；
  且 `K = 0`、`W_max = 本阶段起始 cwnd`，`W_est` 同步重置。

### 6. 关键技术细节

- `target`（本轮目标）取的是 `W_cubic(t + RTT)`，即"下一个 RTT 之后"的值。
- 上界 `min(target, 1.5*cwnd)`：保证增速不超过慢启动。
- 下界 `cwnd = max(cwnd, target)`：保证增速非递减。
- 三次函数的 `t` **不包含应用受限（application-limited）期间**——没数据可发时窗口不该长。

## 对比 / 选型

| 维度 | Reno (RFC 5681) | CUBIC (RFC 9438) |
| --- | --- | --- |
| 拥塞避免增速 | +1 段/RTT（线性） | 三次函数，与时间而非 RTT 挂钩 |
| 乘法递减因子 | 0.5 | **0.7** |
| 丢包后回到 W_max | ≈ W_max/2 个 RTT | ≈ K = cbrt(0.3·W_max/C) 个 RTT |
| 高 BDP 利用率 | 低（窗口恢复太慢） | 高（平台贴着饱和点） |
| RTT 公平性 | 差（吞吐 ∝ 1/RTT） | 好（增速与 RTT 解耦，吞吐 ∝ 1/RTT 而非 1/RTT²） |
| 与 Reno 竞争 | 基准 | 由 Reno-friendly 区域保证不劣化 |
| 适用 | 小 BDP、低带宽 | 长肥管道（默认选它） |

## 环境准备

- 操作系统：任意（本 demo 是纯用户态窗口动力学模拟，不开 socket，Windows 也可跑）
- Python：3.8+（仅标准库）
- C：gcc/clang，需链接数学库 `-lm`（用到 `cbrt`）
- Go：1.21+（`math.Cbrt`）

## 运行方式

### Python（自带 12 项断言自检，推荐先跑这个）

```bash
python3 main.py
```

### C

```bash
gcc -O2 -Wall -Wextra -pedantic main.c -o cubic_demo -lm
./cubic_demo
```

### Go

```bash
go run main.go
```

## 关键代码片段

```python
def cubic_k(w_max, cwnd_epoch):        # RFC 9438 §4.2 Figure 2
    return ((w_max - cwnd_epoch) / C_CUBIC) ** (1.0 / 3.0)

def w_cubic(t, k, w_max):              # RFC 9438 §4.2 Figure 1
    return C_CUBIC * (t - k) ** 3 + w_max

def on_loss(self, timeout):            # 拥塞事件：先定 W_max，再乘性减
    if self.fast_convergence and self.w_max > 0 and self.cwnd < self.w_max:
        self.w_max = self.cwnd * (1 + BETA_CUBIC) / 2     # §4.7 fast convergence
    else:
        self.w_max = self.cwnd
    self.cwnd *= BETA_CUBIC                                # §4.6 注意是 0.7 不是 0.5
    self.cwnd_epoch = self.cwnd
    self.k = cubic_k(self.w_max, self.cwnd_epoch)
    self.w_est = self.cwnd                                 # §4.3 Reno-friendly 重置

def on_round(self, loss):              # 拥塞避免：cubic 与 W_est 取大者
    elapsed = self.t - self.t_epoch
    target = min(w_cubic(elapsed, self.k, self.w_max), 1.5 * self.cwnd)
    self.cwnd = max(self.cwnd, target)
    self.w_est += self.alpha
    if self.w_est >= self.cwnd_prior:
        self.alpha = 1.0
    self.cwnd = max(self.cwnd, self.w_est)
```

## 性能与边界

- **本 demo 实测输出**（Python 版，饱和窗口 400 段 ≈ 4.7 Mbit/RTT，400 轮）：

  | 指标 | Reno | CUBIC |
  | --- | --- | --- |
  | 平均窗口 | 298.9 段 → 3.49 Mbit/s | 351.7 段 → 4.11 Mbit/s（**+17.7%**） |
  | 丢包后爬回 W_max（640 段） | 320 个 RTT | **8 个 RTT**（≈ 40× 快） |
  | 区域分布（sat=400） | — | concave 209 / convex 137 / fast_retransmit 48 |

- 复杂度：每轮 O(1)，模拟 N 轮是 O(N)（三次函数退化为常数次算术）。
- 真实网络里 CUBIC 的收益比这里更明显：本模拟用的是"窗口超过链路容量立即丢 1 段"的
  理想化模型，没有排队延迟、随机丢包和 ACK 压缩。

## 注意事项与常见坑

1. **β 不是 1/2**：CUBIC 用 0.7。照抄 Reno 的减半会让曲线完全跑偏，且断言 2 会直接抓住。
2. **CUBIC 不改慢启动，也不改快重传/快恢复**：RFC 9438 原文是 "It does not make any changes
   to the TCP Fast Retransmit and Fast Recovery algorithms"。只在**拥塞避免**阶段换增长函数。
3. **初始状态必须进慢启动**：新连接 `ssthresh = ∞`，第一版实现忘了这一点，导致没有丢包时
   `W_cubic` 从 `W_max = 0` 直接按 `0.4*t³` 爆炸增长（t=60 时窗口约 9 万段）——断言 7
   "回到 W_max 应花 K 个 RTT" 当场失败。**这属于建模错误而不是断言错误**。
4. **凹 ≠ 越走越慢的起点**：凹段**起始最陡、末端最平**。我最初写的断言"凹段平均增速 >
   凸段平均增速"是错的（实测凹段首轮增量 46 段、凸段末轮 60 段），正确的性质是
   **增速在 t≈K 处触底**（平台）——改断言为"平台两侧的增速都高于平台处"才成立。
5. **`t` 的起点**：`target` 用的是 `W_cubic(t + RTT)`，如果把"当前轮已经过完的时间"也算进去，
   会整体偏移一个 RTT，导致回平台的时间对不上 K。
6. **Fast convergence 不要乱开**：单流环境开了会让自己的窗口被无谓压小，RFC 明确
   "SHOULD be disabled"（没有其他流量时）。
7. **Linux 上验证真实算法**：`sysctl net.ipv4.tcp_congestion_control` 看当前默认；
   `ss -tin` 能看到在途连接的 `cwnd`/`ssthresh`/`rtt`；`cat
   /proc/sys/net/ipv4/tcp_allowed_congestion_control` 看非特权进程可选的算法集合。

## 参考资料（实际阅读过的权威来源）

- [RFC 9438: CUBIC for Fast and Long-Distance Networks](https://www.rfc-editor.org/rfc/rfc9438.html)
  — CUBIC 的正式规范：三区域、`C = 0.4`（§5）、`beta_cubic = 0.7`（§3.4）、Reno-friendly
  与 fast convergence（§4.3/§4.7）、RTO 处理（§4.8）。
- [RFC 5681: TCP Congestion Control](https://www.rfc-editor.org/rfc/rfc5681.html)
  — Reno 的权威定义：公式(2)(3)(4)、快重传 6 条规则、RTO 的 `cwnd = LW = 1*SMSS`。
- [tcp(7) — Linux manual page](https://man7.org/linux/man-pages/man7/tcp.7.html)
  — `tcp_congestion_control` / `tcp_available_congestion_control` / `TCP_CONGESTION`
  socket 选项，以及内核如何按 socket 选择算法。
- [CUBIC: a new TCP-friendly high-speed TCP variant (Rhee & Xu)](https://doi.org/10.1145/1400097.1400105)
  — 原始论文（RFC 9438 附录引用的 [HRX08]），凹/凸双轮廓的设计动机。

# 网络与传输性能

> 时延花在哪一跳：拥塞控制、RTT/BDP、握手开销、QUIC。
> 2026-09-21 由自动巡检 S2 建目录（11-性能分析 类目拓展）。

## 核心研究主题

- **拥塞控制**：慢启动 / 拥塞避免、丢包检测与恢复、BBR vs 基于丢包的算法
- **RTT 与 BDP**：带宽时延积决定在途数据量，窗口不够就跑不满带宽
- **握手开销**：TCP + TLS 的往返次数、会话复用、0-RTT
- **QUIC**：在用户态实现丢包检测与拥塞控制，连接迁移与队头阻塞
- **测量口径**：首字节时间 vs 总下载时间、p50 vs p99

## 已确认的权威口径（RFC 9002 实读）

- RFC 9002 是 **QUIC 的丢包检测与拥塞控制**规范文档，目录里明确分了三块：
  RTT 相关（生成 RTT 样本、`min_rtt` 估计、`smoothed_rtt` 与 `rttvar` 估计）、
  丢包检测、以及拥塞控制。
- 其中 **最小拥塞窗口是 2 个包**（§4.8 The Minimum Congestion Window Is Two Packets）——
  即拥塞窗口的下限不是 1，这条与 TCP 的常见实现约定不同，写模型时别照抄 TCP。
- RTT 是这套机制的核心输入：`min_rtt` 取观测到的最小值，`smoothed_rtt` 做平滑、
  `rttvar` 描述抖动，二者共同决定探测与超时判据。

## 待研究

- [ ] 慢启动指数增长与 BDP 的关系：为什么短连接永远跑不满带宽
- [ ] 丢包检测：packet threshold vs time threshold 的取舍
- [ ] QUIC 的连接迁移与多路径对 RTT 估计的扰动
- [ ] TLS 握手往返数与 0-RTT 的重放风险
- [ ] 队头阻塞：TCP 单流 vs QUIC 多流
- [ ] p99 时延的归因方法（服务端时间 vs 网络时间拆分）

## 参考资料（已读）

- [RFC 9002 — QUIC Loss Detection and Congestion Control](https://www.rfc-editor.org/rfc/rfc9002.txt)

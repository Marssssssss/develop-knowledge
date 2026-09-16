# 应用层可靠性:ack 位图 / 心跳超时 / 断线重连

## 简介

- 为什么游戏不直接用 TCP 的可靠有序流:动作游戏要 10~30 pps 的**稳态包流**,其中大部分(输入、位置、朝向)只有**最新值有意义**;TCP 丢一个包就停下来等重发,**队头阻塞**把后续更新的包一起卡住(Gaffer:重发的旧包到达时早已没用)。所以 FPS 几乎全用 UDP,自己造一层「**通知式**可靠性」。
- 本 demo 实现 Glenn Fiedler《Networking for Game Programmers》的经典方案:序号 + 冗余 ack 位图 + 丢包推断 + RTT EMA + 会话心跳超时 + 断线重连断点续传。
- 关键概念:
  - **永不重发同一序号**:每个序号只发一次,丢失的数据由应用层组新包(新序号)携带 —— 这是与 TCP 的本质分歧。
  - **恒定 33 个 ack/包**:ack(最高已收序号)+ 32 位位图,每个 ack 被冗余发送约 32 次,ack 本身丢包也不影响。
  - **丢包是推断的**:1 秒内没收到 ack 就「合理确定」丢了(30pps × 32 倍冗余下,100% 丢包持续一秒才会漏 ack)。
  - **重连续传**:会话保留宽限期 + 未确认消息重发 + **消息号去重**(重发可能造成重复到达)。

## 原理详解

### 包头与 ack 位图(Gaffer 原文布局)

```
[uint32 protocol id][uint16 sequence][uint16 ack][uint32 ack bitfield]
                                                      └ bit n(n=1..32)=1 ⇔ 已收到 seq ack-n
ack = 本端收到的最高序号(远端序号);位图覆盖其前 32 个 —— 每个包恒定携带 33 个 ack
```

- 发送方收到位图后:`seq = ack - n` 逐位标记送达;ack 自身(n=0)也在包里。
- 双向速率不对称(客户端 30pps、服务端 10pps)也没问题:位图一批带回最多 33 个 ack。
- **序号回绕比较**:差值 `(s1-s2) mod 2^16` 小于半量程(0x8000)→ 直接比大小;否则反向 —— 0,1,2 比 65535 新。任何序号处理都必须用这个比较。

### RTT 与丢包推断

- 每个发出的包记 `(seq, 发送时刻)`;收到该 seq 的 ack 时样本 RTT = 本地时刻差;
- 平滑用 **EMA,α=10%**(原文:"10% seems to work well in practice");
- 发送队列里超过 1 秒没 ack 的包 → 判丢。推论:**重发的数据必须带应用层消息号**,同一消息到达两次要能丢弃。

### 心跳与超时(Gaffer《Client Server Connection》)

- 稳态双向包流本身即是保活,**无需额外 keep-alive 包**;
- 「**5 秒没收到包就超时**」是原文给出的建议值;服务器侧超时 → 槽位回收可复用;客户端侧超时 → 转错误态。
- 没有稳态流量的连接(大厅/挂机)才需要主动心跳包刷新 `last_seen`。

### 断线重连断点续传

1. 服务器为会话保留宽限期(> 客户端重连周期);
2. 客户端带 token 重连,上报最后处理到的消息号;
3. 未确认消息以**新序号**重发,服务端按**消息号**去重;
4. 效果 = 应用层恰好一次(exactly-once)投递。

## 对比 / 选型

| 维度 | TCP 可靠有序流 | 本方案(UDP + ack 通知) |
| --- | --- | --- |
| 丢包影响 | 队头阻塞,后续新数据全卡住 | 旧数据照丢,最新数据直达 |
| 重发单位 | 同一 seq 重发 | 永不重发旧 seq,新 seq 携带重发数据 |
| ack 形式 | 期待下一个字节序号(滑动窗口) | 最高已收 + 32 位位图(冗余 33 个/包) |
| 适用 | 回合制/聊天/登录 | 实时状态流(FPS/MOBA 快照) |

## 环境准备

- OS 任意;Python 3.8+ / Go 1.18+,均纯标准库,无网络 IO(demo 用确定性模拟信道)。

## 运行方式

```bash
python3 python/main.py   # 23 项断言:位图编解码/回绕/冗余 ack/丢包推断/会话/重连
go run go/main.go
```

## 关键代码片段(Python)

```python
def make_ack(self):
    """恒定 33 个 ack:ack + 32 位位图(冗余对抗 ack 本身丢包)。"""
    if self.remote_seq is None:
        return None, 0
    bits = 0
    have = set(self.recv_window)                  # 最近 33 个已收序号
    for n in range(1, ACK_WINDOW + 1):
        if (self.remote_seq - n) & MASK in have:  # 回绕安全的减法
            bits |= 1 << (n - 1)
    return self.remote_seq, bits

def process_ack(self, ack, bits, now_ms):
    for n in range(0, ACK_WINDOW + 1):            # n=0 即 ack 本身
        seq = (ack - n) & MASK
        if n == 0 or (bits >> (n - 1)) & 1:       # 新送达 -> RTT 样本
            ...self.rtt_ms = self.rtt_ms * 0.9 + sample * 0.1
```

## 性能与边界

- ack 开销恒定 8 字节(seq+ack+位图)/包,不随丢包率变化;
- 33-ack 窗口覆盖约 1 秒(30pps)—— **发包速率低于 ~0.5pps 时窗口盖不满重发判定**,要么加心跳要么加大位图;
- RTT EMA 在 10% 平滑下需 ~20 个样本才稳定,重连后应重置而不是沿用旧值。

## 注意事项与常见坑

- **序号回绕必须用差值比较**,直接 `<`/`>` 在 65535→0 处全错;demo 断言 1 专门覆盖。
- **位图位序**:bit n 对应 `ack - n`,n 从 1 起;bit0 并不存在(n=0 的 ack 本体走 ack 字段)—— 写编码时最容易错一位。
- **ack 窗口只有 32 深**:若接收方长期不回包(半双工),33 个 ack 之外的历史永远无法确认 —— 所以该方案假设双向稳态流量。
- **重发数据必须带消息号**(Gaffer 原文明示:收两次要能在应用层丢弃),否则重连后必然重复投递。
- **断言的教训**(本 demo 开发中实测):模拟双向交换时若先发完 100 个包再统一回 ack,位图只盖得住最后 33 个 —— 交错逐包应答才是真实时序。

## 参考资料(实际阅读过的权威来源)

- [Reliability and Congestion Avoidance over UDP — Glenn Fiedler, Gaffer On Games](https://gafferongames.com/post/reliability_ordering_and_congestion_avoidance_over_udp/) — 本 demo 核心方案:33 ack/包、位图语义、回绕比较、1s 丢包推断、RTT EMA 10%、二元拥塞规避(30→10pps)。
- [Client Server Connection — Glenn Fiedler, Gaffer On Games](http://chunyantex.cn.gafferongames.com/post/client_server_connection/) — 槽位模型、稳态流量免 keep-alive、5 秒超时建议值、CRC32+协议号拒绝杂包。
- [RUDP(基于 Gaffer 文章的实现)](https://github.com/renatommartins/RUDP) — 16 位 seq + 32 位 ack 位图 + 32 包无收即断链的工程化头部布局参照。

# TCP_QUICKACK 与 TCP_USER_TIMEOUT

同属「让 TCP 的默认温和策略变激进/变可控」的两个开关,但作用面完全不同:
`TCP_QUICKACK` 改的是**确认节奏**(多快回 ACK),`TCP_USER_TIMEOUT` 改的是
**放弃时刻**(多久没进展就掐断)。两者都在 socket 层,都不影响对端。

## 一、TCP_QUICKACK:一个「一次性」开关

`tcp(7)` 原文(实际抓取并阅读):

```
TCP_QUICKACK (since Linux 2.4.4)
  Enable quickack mode if set or disable quickack mode if cleared.
  In quickack mode, acks are sent immediately, rather than delayed if
  needed in accordance to normal TCP operation. This flag is not
  permanent, it only enables a switch to or from quickack mode.
```

三个要点:

1. **不是永久开关**。它只做「切入/切出一次」,之后内核会根据
   `tcp_event_data_recv()` 的自适应逻辑重新进出 quickack 模式。想一直保持
   快速确认,必须在每次 `recv` 后再设一次——这是它被吐槽最多的地方。
2. **`tcp(7)` 明确写「should not be used in code intended to be portable」**。
3. 它**不等于** `TCP_NODELAY`:后者管发端是否攒包,前者管收端是否攒 ACK。

## 二、内核里的三处实现

| 函数 | 位置 | 作用 |
| --- | --- | --- |
| `tcp_incr_quickack()` | `net/ipv4/tcp_input.c` | 计算并抬高 `icsk_ack.quick` 配额 |
| `tcp_enter_quickack_mode()` | 同上 | 抬配额 + 退出 pingpong + `ato = TCP_ATO_MIN` |
| `tcp_in_quickack_mode()` | 同上 | 判定当前是否处于 quickack |

配额公式(`HZ=1000`,常量取自 `include/net/tcp.h`):

```c
quickacks = tp->rcv_wnd / (2 * icsk->icsk_ack.rcv_mss);
if (quickacks == 0) quickacks = 2;
quickacks = min(quickacks, max_quickacks);   /* TCP_MAX_QUICKACKS = 16U */
if (quickacks > icsk->icsk_ack.quick) icsk->icsk_ack.quick = quickacks;
```

即:**窗口越大、MSS 越小,一次能拿到的快速确认配额越多,上限 16**;
商为 0 时兜底成 2(不是 0)。典型 64 KiB 窗口 + 1460 MSS 直接顶到 16。

判定式里 `dst_quick_ack` 是短路项:

```c
return icsk->icsk_ack.dst_quick_ack ||
       (icsk->icsk_ack.quick && !inet_csk_in_pingpong_mode(sk));
```

**pingpong 模式会一票否决 quickack**——交互式请求-响应被内核识别成 pingpong 后,
即使 `quick` 还有配额也不会快速确认。

## 三、ato 自适应:延时确认到底延多久

`TCP_DELACK_MAX = HZ/5 = 200 ms`、`TCP_DELACK_MIN = TCP_ATO_MIN = HZ/25 = 40 ms`。
这就是「Linux 延时 ACK 最短 40 ms、最长 200 ms」的出处。

`tcp_event_data_recv()` 里按「本段与上一段的间隔 m」分四支:

| 条件 | 动作 |
| --- | --- |
| `ato == 0`(首段) | `incr_quickack(TCP_MAX_QUICKACKS)` 且 `ato = TCP_ATO_MIN` |
| `m <= TCP_ATO_MIN/2` | `ato = (ato >> 1) + TCP_ATO_MIN/2` |
| `m < ato` | `ato = min((ato >> 1) + m, rto, TCP_DELACK_MAX)` |
| `m > rto` | 重新 `incr_quickack(TCP_MAX_QUICKACKS)`(判发送方窗口停摆) |

**`m` 有空档**:当 `TCP_ATO_MIN/2 < m` 且 `m >= ato` 且 `m <= rto` 时三个分支都不进,
`ato` 原地不动。实测序列(见 `python/main.py` 第 ② 表,到达时刻
0/1/3/8/20/41/71/101/201/500 ms):

```text
40, 40, 40, 40, 40, 41, 50, 55, 55, 55
         ↑t=201 落在空档        ↑t=500 走 m>rto 分支
```

递推式 `a ← floor(a/2) + m` 的整数不动点是 **`2m − 1` 而不是 `2m`**:取 m=99 时
`ato` 收敛到 **197**,永远靠自身增长**到不了** `TCP_DELACK_MAX = 200`。

### delack 定时器实际装填值

`tcp_send_delayed_ack()` 先算 `max_ato` 再取两次 `min`:

```c
max_ato = HZ/2;                                  /* 500 ms */
if (pingpong || (pending & ICSK_ACK_PUSHED)) max_ato = TCP_DELACK_MAX;
if (tp->srtt_us) {
    rtt = max_t(int, usecs_to_jiffies(tp->srtt_us >> 3), TCP_DELACK_MIN);
    if (rtt < max_ato) max_ato = rtt;
}
ato = min(ato, max_ato);
ato = min_t(u32, ato, tcp_delack_max(sk));        /* 默认就是 TCP_DELACK_MAX */
```

最后那道 `tcp_delack_max()` 会把 pingpong / srtt 造成的差异全部压到 200 ms。
**不带最后这道钳制**才看得见差异(`python/main.py` 第 ③ 表):

| ato | srtt | pingpong | 未封顶 | 最终 |
| --- | --- | --- | --- | --- |
| 300 | 2 s | no | **250** | 200 |
| 300 | 2 s | yes | **200** | 200 |

`srtt_us >> 3` 是 srtt 的 1/8;`HZ=1000` 下 `usecs_to_jiffies` 即「微秒向上取整到毫秒」。

`alloc_skb` 失败时 `tcp_delack_timer_handler()` 走指数退避
`delay = TCP_DELACK_MAX << retry`,只在 `delay < tcp_rto_max()` 时递增,
故 `retry` 停在 10(`200 << 10 = 204800 ms` 已越过 120 s)。

## 四、TCP_USER_TIMEOUT:三条路径同一个上限

`tcp(7)` 的措辞是「已发送但未被确认的最大毫秒数,或(因零窗口)未发出的
最大毫秒数」,到点后**强制关闭连接并给应用返回 `ETIMEDOUT`**。

三条读取 `icsk->icsk_user_timeout` 的路径:

1. **重传定时器** — `tcp_clamp_rto_to_user_timeout()`

```c
elapsed = tcp_time_stamp_ts(tp) - tp->retrans_stamp;
remaining = user_timeout - elapsed;
if (remaining <= 0) return 1;                     /* 立刻到点 */
return min_t(u32, icsk->icsk_rto, msecs_to_jiffies(remaining));
```

注意 **到点返回 1(1 jiffy)而不是 0**——0 在 `sk_reset_timer()` 里没有意义。

2. **重传超时判定** — `retransmits_timed_out()`

```c
if (!inet_csk(sk)->icsk_retransmits) return false;   /* 一次都没重传 -> 永不超时 */
...
timeout = tcp_model_timeout(sk, boundary, rto_base);
return (s32)(tcp_time_stamp_ts(tp) - start_ts - timeout) >= 0;
```

传入非零 `timeout`(即 `icsk_user_timeout`)时**完全绕开** `tcp_model_timeout()`。
`tcp_model_timeout()` 的线性退避阈值是 `ilog2(TCP_RTO_MAX/TCP_RTO_MIN) = ilog2(600) = 9`:

| boundary | 超时 |
| --- | --- |
| 9 | 204.6 s |
| 10 | 324.6 s |
| 15(`tcp_retries2` 默认) | **924.6 s ≈ 15.4 分钟** |

3. **零窗口探测** — `tcp_clamp_probe0_to_user_timeout()` 与 `tcp_probe_timer()`
   (剩余时间有 `TCP_TIMEOUT_MIN = 2` jiffies 下限;到点直接 `tcp_write_err()`)。

RFC 1122 4.2.2.17 要求「只要对端还在回探测就一直等下去」,默认实现确实如此
(`icsk_probes_out` 被 ACK 复位);**设了 `TCP_USER_TIMEOUT` 才变成有界**。

## 五、容易记错的三点

1. **`TCP_USER_TIMEOUT = 0` 表示「用系统默认」,不是「立刻超时」。**
2. 它**不改重传节奏**,只钳制定时器装填值;`tcp(7)` 原话:「The option has no
   effect on when TCP retransmits a packet, nor when a keepalive probe is sent.」
   但与 `SO_KEEPALIVE` 并用时,**它覆盖 keepalive** 来决定何时关闭。
3. 与 `SO_KEEPALIVE` 的区别:keepalive 管**空闲**连接是否还活着,
   user timeout 管**有数据未确认**时多久放弃。

## 六、代码

| 文件 | 说明 |
| --- | --- |
| `python/dack_model.py` | 常量、延时 ACK 引擎、`user_timeout` 三函数 |
| `python/selfcheck_dack.py` | 85 条断言(实跑全绿) |
| `python/main.py` | 八张对照表 |
| `go/dack.go` + `go/main.go` + `go/selfcheck_dack.go` | Go 转写与同构断言 |

## 参考资料(2026-09-24 10:00 槽实际抓取并阅读)

- `tcp(7)` — https://man7.org/linux/man-pages/man7/tcp.7.html
- `recv(2)` — https://man7.org/linux/man-pages/man2/recv.2.html
- Linux `net/ipv4/tcp_input.c`:`tcp_incr_quickack` / `tcp_enter_quickack_mode` /
  `tcp_in_quickack_mode` / `tcp_event_data_recv`
- Linux `net/ipv4/tcp_output.c`:`tcp_send_delayed_ack` / `tcp_delack_timer_handler`
- Linux `net/ipv4/tcp_timer.c`:`tcp_model_timeout` / `retransmits_timed_out` /
  `tcp_write_timeout` / `tcp_probe_timer` / `tcp_clamp_rto_to_user_timeout` /
  `tcp_clamp_probe0_to_user_timeout`
- Linux `include/net/tcp.h`:`TCP_ATO_MIN` / `TCP_DELACK_MAX` / `TCP_RTO_MIN` /
  `TCP_RTO_MAX` / `TCP_MAX_QUICKACKS` / `TCP_TIMEOUT_MIN`
- Linux `include/net/inet_connection_sock.h`:`icsk_ack` / `icsk_user_timeout` /
  `icsk_probes_tstamp`
- Linux `include/linux/jiffies.h`:`_msecs_to_jiffies` / `usecs_to_jiffies`

## 口径说明

- 全部时间换算假定 **HZ = 1000**。该假定下 `msecs_to_jiffies` 与
  `jiffies_to_msecs` 恒等、`usecs_to_jiffies(u) = ceil(u/1000)`;
  `tcp.h` 对 `HZ < 25` 另有 `4U` 的无量纲分支,本 demo 未实现。
- `tcp_send_delayed_ack()` 里 `pingpong` 与 `srtt` 的差异被最后那道
  `tcp_delack_max()` 抹平,表中「未封顶」一列是本 demo 为暴露差异而跳过了该钳制。

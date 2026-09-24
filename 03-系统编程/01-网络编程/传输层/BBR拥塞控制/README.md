# BBR 拥塞控制

`net/ipv4/tcp_bbr.c` 的文件头把整个算法写成四行:

```
   On each ACK, update our model of the network path:
      bottleneck_bandwidth = windowed_max(delivered / elapsed, 10 round trips)
      min_rtt = windowed_min(rtt, 10 seconds)
   pacing_rate = pacing_gain * bottleneck_bandwidth
   cwnd = max(cwnd_gain * bottleneck_bandwidth * min_rtt, 4)
```

「The core algorithm does not react directly to packet losses or delays」
——这是它与 Reno / CUBIC 最本质的区别:退让不靠丢包信号,而靠**估 BDP 后按增益表去探**。

## 一、状态机

源码注释里的那张图(ASCII 原样保留在文件头):

```
             |
             V
    +---> STARTUP  ----+
    |        |         |
    |        V         |
    |      DRAIN   ----+
    |        |         |
    |        V         |
    +---> PROBE_BW ----+
    |      ^    |      |
    |      |    |      |
    |      +----+      |
    |                  |
    +---- PROBE_RTT <--+
```

## 二、增益是定点整数,不是浮点

```c
#define BBR_SCALE 8
#define BBR_UNIT (1 << BBR_SCALE)          /* 256 */
#define BW_SCALE 24
#define BW_UNIT (1 << BW_SCALE)            /* 16777216 —— bw 的单位是 pkts/μs << 24 */
```

| 名称 | 源码表达式 | 定点值 | 浮点 |
| --- | --- | --- | --- |
| `bbr_high_gain` | `BBR_UNIT * 2885 / 1000 + 1` | **739** | 2.8867 |
| `bbr_drain_gain` | `BBR_UNIT * 1000 / 2885` | **88** | 0.3438 |
| `bbr_cwnd_gain` | `BBR_UNIT * 2` | 512 | 2.0 |

源码注释说 `high_gain` 取 `2/ln(2)` 是「能让 pacing rate 每 RTT 翻倍、且每 RTT
发包数与不 pacing 的 Reno/CUBIC 慢启动相同」的最小增益。

**`drain_gain` 不是 `high_gain` 的精确倒数**:两者的整数除法各自截断,
`(739 × 88) >> 8 = 254` 而不是 256(见 `python/selfcheck_bbr.py` 断言 8-9)。

`PROBE_BW` 的 8 相增益循环:

```c
static const int bbr_pacing_gain[] = {
	BBR_UNIT * 5 / 4,	/* 320  探更多带宽 */
	BBR_UNIT * 3 / 4,	/* 192  排空 / 让出带宽 */
	BBR_UNIT, BBR_UNIT, BBR_UNIT,	/* 256 ×6,以 1.0x 巡航 */
	BBR_UNIT, BBR_UNIT, BBR_UNIT
};
```

起始相位是**随机**的,而且有个反直觉的结论:

```c
bbr->cycle_idx = CYCLE_LEN - 1 - get_random_u32_below(bbr_cycle_rand);
bbr_advance_cycle_phase(sk);	/* flip to next phase of gain cycle */
```

`bbr_cycle_rand = 7`,所以 `cycle_idx ∈ [1,7]`,紧接着又 +1 取模 8 →
**实际起始相位取值集合是 `{0,2,3,4,5,6,7}`,永远不可能是 1**。

## 三、`bbr_bdp()`:一次带向上取整的定点乘法

```c
w = (u64)bw * bbr->min_rtt_us;
bdp = (((w * gain) >> BBR_SCALE) + BW_UNIT - 1) / BW_UNIT;
```

最后这次除法是**向上取整**,源码注释明确说这是「to avoid a negative
feedback loop」。本 demo 的算例(`bw = 0.12 pkts/μs`,`min_rtt = 1 ms`):

| gain | 定点 | bdp |
| --- | --- | --- |
| 1.0 | 256 | 120 |
| 2.0 | 512 | 240 |
| 2.887 | 739 | **347**(240×2.887 = 346.4,ceil) |

没有有效 RTT 样本时(`min_rtt_us == ~0U`)直接返回 `TCP_INIT_CWND = 10`。

## 四、`bbr_quantization_budget()`:三步,顺序不能换

```c
cwnd += 3 * bbr_tso_segs_goal(sk);      /* 喂满两端的整包 */
cwnd = (cwnd + 1) & ~1U;                /* 抬到偶数,减少延时 ACK */
if (bbr->mode == BBR_PROBE_BW && bbr->cycle_idx == 0)
        cwnd += 2;                      /* 小 BDP 也要能抬过 BDP */
```

第二行取偶是很多人会忽略的一步。上例中 `bdp=120` → `126` → 取偶仍是 126 →
`cycle_idx == 0` 再 +2 → **128**。

## 五、`pacing_rate`:顺序敏感,且留 1% 余量

```c
rate *= mss;
rate *= gain;
rate >>= BBR_SCALE;
rate *= USEC_PER_SEC / 100 * (100 - bbr_pacing_margin_percent);   /* 990000 */
return rate >> BW_SCALE;
```

源码注释:这个顺序是「carefully chosen to avoid overflow of u64」,可撑到
2.9 Tbit/s。`bbr_pacing_margin_percent = 1` 的含义写在另一处注释里:

```
Pace at ~1% below estimated bw, on average, to reduce queue at bottleneck.
```

同一算例下:MSS=1200 时 1.0x 给 **142.56 MB/s**(= 0.12×10⁶×1200×0.99),
0.75x 给 106.92,1.25x 给 178.20(与解析值的差来自 2²⁴ 定点量化)。

## 六、什么时候认为「管道满了」

```c
bw_thresh = (u64)bbr->full_bw * bbr_full_bw_thresh >> BBR_SCALE;   /* ×1.25 */
if (bbr_max_bw(sk) >= bw_thresh) { bbr->full_bw = ...; bbr->full_bw_cnt = 0; return; }
++bbr->full_bw_cnt;
bbr->full_bw_reached = bbr->full_bw_cnt >= bbr_full_bw_cnt;        /* 3 */
```

`bbr_full_bw_thresh = 1.25`、`bbr_full_bw_cnt = 3`,且**只统计**
`round_start && !is_app_limited` 的轮次。源码给了「为什么是 3 轮」:
1 轮给 rwin 自调优涨窗口、1 轮填满更大的 rwin、1 轮才拿到更高的投递率样本。

进入 `DRAIN` 后有个 **fall through**:同一轮里若
`packets_in_net_at_edt <= bdp(1.0)`,会**立刻**跳到 `PROBE_BW`
(源码注释:/* fall through to check if in-flight is already small: */)。

## 七、`min_rtt` 窗口与 `PROBE_RTT`

- `bbr_min_rtt_win_sec = 10`:min_rtt 的滑窗是 **10 秒**。
- 窗口过期后,**即使新样本更大也会被采纳**(条件是 `!rs->is_ack_delayed`)。
  `is_ack_delayed` 只关掉「用大样本重置」这一条,**不影响** PROBE_RTT 的进入。
- 进入 `PROBE_RTT` 的条件:`filter_expired && !idle_restart && mode != PROBE_RTT`。
- `PROBE_RTT` 期间:在飞降到 `bbr_cwnd_min_target = 4` 包以下时起表,
  维持 **max(200 ms, 1 个 round)** 再退出;退出时
  `full_bw_reached ? PROBE_BW : STARTUP`。
- 带宽滤波窗口 `bbr_bw_rtts = CYCLE_LEN + 2 = 10` 个 round。

## 八、代码

| 文件 | 说明 |
| --- | --- |
| `python/bbr_model.py` | 常量、`bbr_bdp` / 量化 / pacing rate / 状态机 |
| `python/selfcheck_bbr.py` | 101 条断言(实跑全绿) |
| `python/main.py` | 九张对照表 |
| `go/bbr.go` + `go/main.go` | Go 转写与同构断言 |

## 参考资料(2026-09-24 10:00 槽实际抓取并阅读)

- Linux `net/ipv4/tcp_bbr.c`(全文 42814 B):文件头状态图、`BBR_SCALE` /
  `BW_SCALE`、`bbr_high_gain` / `bbr_drain_gain` / `bbr_cwnd_gain` /
  `bbr_pacing_gain[]` / `bbr_cycle_rand`、`bbr_bdp` /
  `bbr_quantization_budget` / `bbr_inflight`、`bbr_rate_bytes_per_sec` /
  `bbr_set_pacing_rate`、`bbr_update_gains`、`bbr_advance_cycle_phase` /
  `bbr_reset_probe_bw_mode`、`bbr_check_full_bw_reached` / `bbr_check_drain`、
  `bbr_update_min_rtt` / `bbr_check_probe_rtt_done`、`bbr_lt_bw_sampling`
- (同槽已读)`net/ipv4/tcp_input.c`、`net/ipv4/tcp_timer.c`、
  `include/net/tcp.h` —— 用于与 Reno/CUBIC 的 RRULE 侧对照

## 口径说明

- 本 demo 只建模**单车流**的定点算术与状态迁移,**没有**建模多流收敛、
  `bbr_packets_in_net_at_edt` 的 EDT 估算、以及 `bbr_tso_segs_goal` 的完整
  逻辑(它以参数 `tso_segs` 形式留在外面,默认 2)。
- `bbr_lt_bw_*`(长时带宽 / 流量监管检测)系列常量已写入模型但本 demo
  未对其做行为断言——源码注释里「lost/delivered ratio > 20%」与常量
  `bbr_lt_loss_thresh = 50` 的对应关系在原文中未给出换算式,故不作数值断言。

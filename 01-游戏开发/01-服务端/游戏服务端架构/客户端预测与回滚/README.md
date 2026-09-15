# 客户端预测与回滚（Server-Authoritative + Prediction / Reconciliation / Lag Compensation）

## 简介

服务器权威（server-authoritative）模型下，**服务器是唯一真相**：客户端只发送输入，位置/命中都由服务器判定。代价是玩家自己的操作要等一个 RTT 才生效（150 ms 延迟下「按了前进键，0.15 秒后才动」），手感完全不可接受。

解法是三层组合：

| 机制 | 作用 | 代价 |
| --- | --- | --- |
| **客户端预测（client-side prediction）** | 本地立刻按同一套规则算出自己的结果，操作「零延迟」 | 会产生**预测误差** |
| **服务端和解（reconciliation）** | 用权威快照纠正本地预测，并**重放**未确认输入 | 修正可能造成视角跳变 |
| **延迟补偿（lag compensation）** | 服务器把其他玩家回退到「开火那一刻」判命中 | 出现「躲到掩体后仍被打中」 |

关键概念：

- **tickrate**：服务器模拟步长，Source 默认 **15 ms（≈66.67 tick/s）**；
- **snapshot（快照）**：世界状态增量下发，默认约 **20 次/秒**（`cl_updaterate 20`）；
- **user command**：客户端输入快照，默认约 **30 个包/秒**（`cl_cmdrate`）；
- **cl_interp**：实体插值周期，默认 **0.1 s**；
- **cl_smoothtime / cl_smooth**：预测误差的平滑时间与开关。

历史背景：这套模型由 Quake/Source 一系确立，是竞技 FPS 的事实标准。核心前提是「**服务器不能信任客户端**」——即使客户端装了反作弊，数据包仍可在中途被第三方机器改写（"cheat proxy" 注入假命中消息），因此命中判定必须留在服务器。

## 原理详解

### 1. 三个时间尺度

```
tick        15 ms   服务器模拟步长(66.67/s)
快照        45 ms   服务器 -> 客户端的世界更新(默认约 20/s)
用户命令    30 ms   客户端 -> 客户端的输入(默认约 30/s)
单向延迟    45 ms   本 demo 取值(3 tick)
RTT         90 ms   双程
```

注意 **tickrate ≠ 快照率 ≠ 命令率**：一个快照里可能包含多个 tick 的变化（用增量压缩只发变化量），一个命令包里也可能装多个命令。

### 2. 客户端预测与和解（最关键的一步）

```
客户端:  按键 -> [立刻] 本地按服务器同款规则算出新位置 -> 渲染「零延迟」
                 ↓ 同一时刻把命令塞进 PendingInputQueue 并发给服务器
服务器:  收到命令 -> 用权威世界算出结果 -> 快照 + last_processed_cmd_id 下发
客户端:  收到快照 -> 比对服务器位置与预测位置
                 ├─ 一致 -> 丢弃已确认输入
                 └─ 不一致(预测误差) -> 位置回设到权威值, 再把「未确认输入」按序重放
```

本 demo 实测（40 tick 模拟，阻挡者在 tick 26 出现，其中 x=100）：

| 和解方式 | 最大预测误差 | 和解后偏差 | 被吞掉的输入 | 无阻挡时末态位置 |
| --- | --- | --- | --- | --- |
| **重放式**（正确） | 20.00 u | **0.00** | **0** | 150.00（领先服务器 7.50 = 在途输入） |
| **吸附式**（错误） | 15.00 u | 0.00 | **1** | 127.50（**落后 15.00**） |

两者最终都能对齐权威位置，差别在**过程**：吸附式每次收到快照都把位置硬拽回服务器值并丢弃未确认输入，于是玩家刚按下的输入被凭空吞掉——症状是「**操作丢失/位置回退**」，在高延迟下最明显。所以必须保留 `PendingInputQueue`，只回设到权威位置、然后**重放**。

### 3. 平滑（cl_smoothtime / cl_smooth）

预测误差如果一帧修正完，视觉上就是一次突兀的跳变（rubber-banding）。把修正摊开到一段时间里即可：

| 设置 | 单帧最大修正 | 摊完帧数 | 100 ms 后残余 |
| --- | --- | --- | --- |
| `cl_smooth 0`（不平滑） | 20.00 u（误差的 **100%**） | 1 | 0 |
| 开平滑（τ = 20 ms） | 10.55 u（误差的 **53%**） | 8 | **0.0496 u（0.25%）** |

实现就是指数逼近：`render += (target - render) * (1 - exp(-dt / τ))`。注意这里有个**真实权衡**：τ 越小收敛越快但单帧跳得越狠，τ 越大越平滑但修正拖得越久（超过 `cl_smoothtime` 会显得「脚底打滑」）。Source 的做法是保留 `cl_smoothtime` 时间窗内摊完。

### 4. 实体插值（cl_interp）

远端玩家不能用预测（拿不到别人的按键），只能**插值**：渲染时间落后最新快照一个插值周期，在两个已知快照之间做线性插值。

- 默认 `cl_interp = 0.1 s` → 看到的是 **100 ms 前**的远端位置；
- 本 demo 实测：远端 300 u/s 时，这个滞后对应 **30.0 u** 的位置差；
- 100 ms 的插值周期在 45 ms 的快照间隔下，缓冲里约 **2.2 个快照**——丢一个快照仍有两个有效快照可插值；
- 插值周期公式：`max(cl_interp, cl_interp_ratio / cl_updaterate)`。把 `cl_interp` 设 0 并提高 `cl_updaterate` 可以安全缩短它。

这是**恒定**的滞后，不随时间累积，但对「谁先看到谁」有战术影响。

### 5. 延迟补偿（lag compensation）

玩家在客户端时间 10.5 开火，包在路上飞时服务器继续模拟，目标已经移开 → 按服务器当前位置判定必然脱靶。服务器的做法是**把别人拉回过去**：

```
Command Execution Time = Current Server Time - Packet RTT - Client View Interpolation
```

1. 服务器维护**最近 1 秒**的所有玩家位置历史；
2. 收到用户命令时按上式估算命令的创建时刻；
3. 把所有**其他玩家**（只有玩家，不含其它实体）回退到那个时刻的位置；
4. 执行命中判定（射线/碰撞）；
5. 判定结束后把玩家恢复原位。

本 demo 实测（RTT 90 ms + 插值 100 ms = 回退 190 ms，目标 300 u/s，命中半径 20 u）：

| 判定方式 | 目标位置偏移 | 结果 |
| --- | --- | --- |
| 不回退（按服务器当前位置） | **57.0 u** | **脱靶** |
| 回退到命令执行时刻 | **0.0 u** | **命中** |

回退距离 = 300 u/s × 0.190 s = **57.0 u**。也就是说：不回退，高速移动的目标在 90 ms RTT 下几乎不可能被命中；回退后判定才符合「玩家当时看到的画面」。

## 对比 / 选型

| 方案 | 手感 | 一致性 | 复杂度 | 典型场景 |
| --- | --- | --- | --- | --- |
| 纯插值（无预测） | 差（自己的操作也要等 RTT） | 最好 | 低 | 慢节奏 / 回合制 |
| **预测 + 和解**（本 demo） | 好（自己零延迟） | 好（服务器权威） | 中 | 竞技 FPS / 动作游戏 |
| 预测 + 和解 + **延迟补偿** | 好 | 中（制造「掩体后被打中」） | 中高 | CS/TF2/Valorant 等射击 |
| **回滚网码（rollback）** | 极好 | 好 | 高 | 格斗（GGPO 系）；把远端输入固定在某个延迟下到达，必要时回滚重演若干帧 |
| 纯帧同步 | 取决于实现 | 依赖确定性 | 中（但是调试地狱） | RTS / MOBA |

延迟补偿的取舍值得单独说：它**无法根本消除**「我已躲到掩体后仍被击中」——因为服务器把 hitbox 拉回了过去那个我还暴露着的时刻。只要包速有限，这个不一致就无解；`Increasing tickrate` 能提高精度但要付 CPU/带宽代价（Source 文档实测 tickrate 66 → 100 的 CPU 负载约为 **1.5 倍**，官方不建议超过 66）。

## 环境准备

- 操作系统：任意（纯内存模拟）
- Python ≥ 3.10
- C：任意 C99 以上编译器（需要 `-lm`）
- Go ≥ 1.18
- 依赖：**无第三方依赖**

## 运行方式

### Python

```bash
cd python && python3 prediction.py
```

### C

```bash
cd c && gcc -O2 -Wall -Wextra -pedantic prediction.c -o prediction -lm && ./prediction
```

### Go

```bash
cd go && go run prediction.go
```

## 关键代码片段

```python
def reconcile(self, server_x, acked_seq, blocker):
    """收到快照后的和解: 回设到权威位置 -> 重放未确认输入。"""
    if self.mode == "snap":                 # 错误做法
        self.lost_inputs = len(self.history)
        self.history.clear()                # 未确认输入被丢弃 -> 操作丢失
        self.x = server_x
    else:                                   # 正确做法
        pending = [(s, d) for (s, d) in self.history if s > acked_seq]
        self.history = pending
        self.x = server_x                   # 1) 回设到权威位置
        for _, d in pending:                # 2) 按序重放尚未确认的输入
            self.x = advance(self.x, d, self.known_blocker)
```

```python
# 平滑: 指数逼近, 把一次修正摊到 cl_smoothtime 内
alpha = 1.0 - math.exp(-TICK_MS / SMOOTH_TAU)
step = remaining * alpha
```

```python
# 延迟补偿: 回退到「客户端当时看到的世界」
rewind_ms = rtt_ms + interp_ms          # Command Execution Time = Now - RTT - Interp
y_server_now = target_speed * rewind_ms / 1000.0   # 服务器当前位置(会脱靶)
y_rewound = 0.0                                    # 回退后的位置(命中)
```

## 性能与边界

- **tickrate 与精度**：命中判定不是像素级精确，存在基于 tickrate 与目标速度的固有误差；几毫秒的时间测量误差对快速移动物体可造成数英寸偏差。
- **tickrate 的成本**：Source 文档实测 tickrate 66 → 100 约 **1.5 倍 CPU**；官方建议不要超过 66，把 CPU 留给「很多人同时开枪」的尖峰。
- **带宽**：`rate` 是客户端最重要的网络参数（modem 4500 / ISDN 6000 / DSL 及以上 10000 字节每秒）；服务器侧用 `sv_minrate/sv_maxrate`、`sv_minupdaterate/sv_maxupdaterate` 钳制。
- **插值缓冲深度**：45 ms 快照间隔 + 100 ms 插值 → 缓冲约 2.2 个快照。连续丢包耗尽缓冲后渲染器退化为**外推**（`cl_extrapolate`），且只允许在约 **0.25 s** 的丢包窗口内做。
- **预测的适用范围**：只能预测**自己**以及**仅受自己影响**的实体。预测别人等于「无数据地预测未来」——别人的按键你永远拿不到。
- **回退历史长度**：1 秒。超过 1 秒的延迟不再补偿。

## 注意事项与常见坑

1. **和解时丢弃未确认输入** —— 现象：高延迟下「按键没反应」「位置莫名回退」；原因：把位置硬拽到快照值并清空 `PendingInputQueue`；规避：回设后必须**按序重放**未确认输入（本 demo 实测差 15.00 u）。
2. **预测用在别人身上** —— 现象：远端玩家抖动/抽搐；原因：客户端拿不到远端输入，无法预测；规避：远端只用插值，绝不做预测。
3. **关闭插值或延迟补偿** —— Source 官方明确警告：**不要**关闭视角插值和延迟补偿，关了不会提升精度，只会让判定与视觉脱节（延迟补偿公式里就含插值项）。
4. **依赖插值却把插值关了** —— 现象：明明开了延迟补偿却「感觉打不中」；原因：`Command Execution Time` 公式包含 `Client View Interpolation`，插值关闭后该式失真。
5. **不平滑修正** —— 现象：视角剧烈跳动（rubber-banding）；规避：启用 `cl_smooth` / `cl_smoothtime`，把修正摊开；但也要注意 τ 过大导致「脚底打滑」。
6. **把延迟补偿当成万灵药** —— 现象：玩家投诉「躲到墙后还被击杀」；本质：该现象是包速有限导致的**结构性**不一致，无法根本消除，只能通过 tickrate、匹配区域（保证低 ping）来缓解。
7. **tickrate 盲调高** —— 现象：尖峰时服务器卡顿、反而更差；规避：按官方建议保持 66，先测量再调整。
8. **快照频率与命令频率混为一谈** —— 现象：以为提高 tickrate 就等于提高更新率；实际 `cl_updaterate`（快照率）与 `cl_cmdrate`（命令率）各自独立，且受客户端 `rate` 与服务器 `sv_max*` 限制。

## 参考资料（实际阅读过的来源）

- [Source Multiplayer Networking](https://developer.valvesoftware.com/wiki/Source_Multiplayer_Networking) — Valve Developer Community（官方引擎文档）：tickrate 15 ms / 66 tick、快照与 `cl_updaterate`、`cl_cmdrate`、客户端预测与 `cl_showerror`/`cl_smoothtime`/`cl_smooth`、实体插值 `cl_interp` 与 `cl_extrapolate`、延迟补偿公式与 1 秒位置历史、`sv_showimpacts`/`net_fakelag`、tickrate 66→100 的 1.5 倍 CPU、以及「不要关闭插值与延迟补偿」的官方警告
- [Network Latency](https://wiki.cas.mcmaster.ca/index.php/Network_Latency) — McMaster 课程 wiki：客户端预测与 rubber-banding 的成因、服务器「按玩家 RTT 回溯判定」的机制与数学不精确性
- [高并发竞技射击网络底层同步工程：客户端预测、服务端快照回滚与 Tickrate 解算实战](https://segmentfault.com/a/1190000048288323) — 三层状态同步闭环结构图、`PendingInputQueue`、预测误差阈值 ε 与插值/微调的处理顺序
- [Hacker News: 1500 Archers on a 28.8（讨论串）](https://news.ycombinator.com/item?id=17568149) — 与帧同步路线的对比、回滚网码（rollback / GGPO「8 frames in 16ms」方向）与预测-和解路线的取舍

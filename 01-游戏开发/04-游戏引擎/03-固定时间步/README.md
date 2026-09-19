# 固定时间步与 accumulator 插值

## 简介

- 游戏主循环里"这一帧推进多久"的选择，直接决定物理是否稳定、是否可复现。Glenn Fiedler 的经典文章《Fix Your Timestep!》把四种方案排成一列递进关系，最终方案是**累加器（accumulator）+ 固定 dt + alpha 插值**——如今 Unity 的 `FixedUpdate`、Godot 的 `_physics_process`、Box2D 的推荐用法都是它的变体。
- 本文的核心洞察：**渲染器产生时间，仿真器以固定步长消费时间**。两者解耦后，物理只见过一种 dt，于是"同输入必同输出"（deterministic lockstep 的前提）；渲染帧率爱多少就多少。

## 原理详解

### 四种方案与原文结论

| 方案 | 做法 | 问题 |
| --- | --- | --- |
| 固定 dt | 每帧 `dt = 1/60` | 只有帧率恰好匹配刷新率才对，否则物理时快时慢 |
| 可变 dt | 把上一帧实测耗时当 dt | **仿真结果依赖帧率**：弹簧可能爆炸、高速物体穿墙 |
| 半固定 dt | `min(frameTime, dt)` 切分 | 有上界，但会出现"不是 dt 的零头步"，且可能陷入**死亡螺旋** |
| accumulator（终解） | 只以 dt 步进，余数留到下一帧 | 需要额外做插值，否则有轻微抖动 |

### 终解的循环（原文代码骨架）

```text
accumulator += frameTime
while accumulator >= dt:
    previousState = currentState
    integrate(currentState, t, dt)
    t += dt
    accumulator -= dt
alpha = accumulator / dt
render(currentState * alpha + previousState * (1 - alpha))
```

三条容易被忽略的细节（都在原文里）：

1. **帧时间要钳位**：`if (frameTime > 0.25) frameTime = 0.25;`——断点调试、切后台回来时的一次超长帧不能被当成几秒钟的物理时间。
2. **余数是"还差多久才能再走一步"的度量**，所以 `alpha = accumulator / dt ∈ [0,1)`，用它做 `current/ previous` 的线性插值；刚体朝向要用 slerp。
3. **死亡螺旋**：仿真 X 秒的模拟量要花 Y > X 秒真实时间时，欠账会滚雪球。原文的对策是留足余量（"远小于 X 秒"），或者**限制每帧最大步数**——宁可让游戏变慢，也不要螺旋到死。

### 为什么可变 dt 会"爆炸"

积分器的稳定域是有限的：显式欧拉解弹簧 `a = -K·x` 时，dt 超过约 `2/ω` 后能量单调放大。本 demo 用 `K = 100`（ω = 10）实测：同样 1 秒，60fps 与 30fps 两种帧率下可变步长得到的状态明显不同（`-0.7916` vs `-0.7260`），而固定步长两者**逐位相同**（`-0.8093848211332116`）。

## 对比 / 选型

| 维度 | 固定 dt + accumulator | 可变 dt |
| --- | --- | --- |
| 可复现（回放/联机 lockstep） | ✅ 同输入同输出 | ❌ 与帧率耦合 |
| 数值稳定性 | 只暴露单一 dt，可控 | 大 dt 可能发散 |
| 掉帧表现 | 靠多步追赶，可能螺旋 | 单步变大，直接走形 |
| 视觉平滑 | 需 alpha 插值（否则抖动/停顿） | 天然对齐渲染时刻 |
| 实现复杂度 | 中等 | 最低 |

## 环境准备

- Python 3.8+（标准库，零依赖）
- Go 1.21+（零依赖）

## 运行方式

```bash
python3 python/timestep.py     # 22 条断言
cd go && go run timestep.go
```

## 关键代码片段

本 demo 的时间全部用**整数毫秒**记账（避免浮点误差把步数算错一个）：

```python
acc += min(frame_time, MAX_FRAME_MS)
n = 0
while acc >= DT_MS and (max_steps is None or n < max_steps):
    prev, cur = cur, replace(cur)      # 只在真正步进时更新 previous
    integrate(cur, DT)
    acc -= DT_MS
    n += 1
alpha = acc / DT_MS                    # ∈ [0, 1)
rendered = cur.x * alpha + prev.x * (1 - alpha)
```

死亡螺旋用"上一帧仿真耗时计入本帧帧时间"的反馈建模（`pending_ms = n * cost_per_step_ms`），于是：单步 12ms > dt 10ms 时每帧步数 `1,3,5,8,11,15,19,25…` 发散；单步 8ms < dt 时收敛到稳态 `8 步/帧`（此时仿真时间恰好追平真实时间）。

## 性能与边界

- 步数只由**总时长**决定：1 秒 → 100 步，2 秒 → 200 步，与 60fps / 30fps 无关（断言 5、21）。
- accumulator 余数恒 `< dt`，因此渲染**最多落后一个步长**（`dt - acc`），不会 extrapolate（外插）——外插会在速度突变时冲过头再弹回。
- 高刷场景（200fps，帧时间 5ms < dt）下不插值会有 59/120 帧画面完全不动；插值后降到 1 帧。
- 每帧步数上限是把"变慢"换成"不卡死"的开关：限到 3 步后步数不再发散，但 accumulator 仍在涨（仿真落后于真实时间）——这正是限流的代价。

## 注意事项与常见坑

- **`previousState` 只在真正步进时更新**：写成每帧无条件 `prev = cur`，alpha 插值就退化成恒等（`prev == cur`），抖动照旧。
- **别忘了帧时间钳位**：一次 2 秒的卡顿不钳位会立刻跑 200 步，直接把玩家甩飞（本 demo 断言 9、10 对照）。
- **别在物理里用可变 dt 又指望复现**：定点回放、联机 lockstep、录像比对都要求固定 dt。
- **alpha 的方向**：原文写的是 `current * alpha + previous * (1 - alpha)`（`alpha = acc/dt`）。注意它渲染的是 **previous 与 current 之间**的一点，落后当前步最多一个 dt；原文叙述里"余数 dt/2 表示处在当前步与下一步中间"是另一个口径，实现请以代码为准。
- **刚体朝向要 slerp**，四元数直接 lerp 会导致角速度不均。
- **半固定步长不是"差一点"**：它会引入非 dt 的零头步（本例 63 个），破坏严格可复现性，这也是原文要"free the physics"的原因。

## 参考资料（实际阅读过的权威来源）

- [Glenn Fiedler — Fix Your Timestep!](https://gafferongames.com/post/fix_your_timestep/) — 四种方案演进、accumulator 终解代码、0.25 秒钳位、死亡螺旋的定义与对策、alpha 插值公式与 slerp 提示。
- [Godot — Idle and Physics Processing](https://docs.godotengine.org/en/stable/tutorials/scripting/idle_and_physics_processing.html) — 引擎层的对应物：`_process(delta)` 随帧率、`_physics_process(delta)` 按固定间隔（Physics Fps 默认 60），且两者**不同步**。

# Godot 动画状态机：推进判据与交叉淡入

## 这是什么

动画状态机的两件事：**什么时候切**（`_find_next` 的条件与优先级），**切的时候怎么混**（`_process` 的 cross-fade）。Godot 的实现里有几处很值得钉死的细节：优先级的**比较符号**、`travel` 路径**绕过**条件与优先级、淡入结束时旧状态仍保留一个极小权重、以及单帧环路检测。

## 权威来源（实际读过）

- godotengine/godot@master `scene/animation/animation_node_state_machine.cpp`（75810 B）
  - `AnimationNodeStateMachineTransition::set_xfade_time`（`ERR_FAIL_COND(p_xfade < 0)`）
  - `AnimationNodeStateMachinePlayback::_find_next`、`_check_advance_condition`
  - `AnimationNodeStateMachinePlayback::_transition_to_next_recursive`
  - `AnimationNodeStateMachinePlayback::_process`
- `core/math/math_defs.h`：`CMP_EPSILON = 0.00001`

## 交叉淡入的三条算术

```cpp
if (fading_time && fading_from != StringName()) {
    if (!p_seek) { fading_pos += Math::abs(p_delta); }
    fade_blend = MIN(1.0, fading_pos / fading_time);
}
if (current_curve.is_valid()) { fade_blend = current_curve->sample(fade_blend); }
fade_blend = Math::is_zero_approx(fade_blend) ? CMP_EPSILON : fade_blend;
// 旧状态：fade_blend_inv = 1.0 - fade_blend，同样被 CMP_EPSILON 兜底
```

1. **`fading_pos` 累加的是 `abs(delta)`**：倒放或负 dt 也会推进淡入
2. **`p_seek` 时不累加**：拖动进度条不会推进淡入（实测：连续两帧 `seek=true` 后 `fade_blend` 仍是 0，被兜底成 `CMP_EPSILON`）
3. **淡入结束时旧状态保留 `CMP_EPSILON` 的权重**，不是 0。源码注释解释了原因：*Blend values must be more than CMP_EPSILON to process discrete keys in edge*——权重为 0 会让离散关键帧不再被处理

实测（`xfade = 0.5`、每帧 `delta = 0.1`）：`0.2 → 0.4 → 0.6 → 0.8 → 1.0`，第 5 帧后旧状态权重从 `0.2` 一路降到 `CMP_EPSILON = 1e-5`。

`xfade_time` 由 `set_xfade_time` 校验，**负数会被拒绝**；`xfade_curve` 非空时先采样曲线再兜底（实测平方曲线在 0.5 处给 0.25）。

## 什么时候切：条件、优先级与 travel

`_find_next` 有两条完全不同的路径：

**有 travel 路径时**（`path` 非空）：只认「当前 → `path[0]`」这一条过渡，**既不看 advance condition 也不比 priority**。所以 `travel()` 到远处的状态，会沿路径穿过那些「条件不成立、优先级更差」的过渡。

**否则自动推进**：

```cpp
if (ref_transition->get_priority() <= priority_best) { priority_best = ...; auto_advance_to = i; }
```

- `priority_best` 从 `1e20` 起，所以**数值越小优先级越高**
- `<=` 意味着**优先级相同时下标更大者胜出**（实测：两条 `priority=1` 的过渡，选后面那条）
- `ADVANCE_MODE_DISABLED` 的过渡直接 `continue`，条件成立也不推进
- `_check_advance_condition` 的第一行就是 `if (advance_mode != ADVANCE_MODE_AUTO) return false;`

## 单帧推进会不会走多步

`_transition_to_next_recursive` 是个 `while (true)`：

- 走到一条 **有淡入**的过渡 → 置位 `fading_from/fading_time/fading_pos` 后 **`break`**（淡入必须先被处理）
- 走到**无淡入**的过渡 → 继续找下一条，因此**一帧可以走完整条 travel 路径**（实测：A→B→C 全无淡入时，一帧就从 A 到了 C）
- `transition_path` 记录已走过的节点，出现重复就 `WARN_PRINT_ONCE_ED` 并 `break`——防止 A⇄B 互相指向时死循环

## SWITCH_MODE_SYNC 与 reset

`SWITCH_MODE_SYNC` 会把新状态 `seek` 到旧状态的位置：`pi.time = current_nti.position; pi.seeked = true;`。实测从 A（位置 0.75）同步切到 B，B 的播放位置仍是 **0.75**；而 `IMMEDIATE` 且 `is_reset` 为假时位置保持不变、为真时归零。

## 目录结构

```
python/statemachine.py            状态机与播放器模型（约 200 行）
python/selfcheck_statemachine.py  72 条断言，全部实跑通过
python/main.py                    演示入口
go/main.go                        Go 侧同协议实现（人工审查 + 静态检查）
```

## 运行

```bash
cd python
python selfcheck_statemachine.py   # 断言总数 72，失败 0
python main.py
```

## 自检覆盖

淡入推进（含 `abs(delta)`、`seek` 不推进、`MIN` 封顶、`CMP_EPSILON` 兜底、曲线采样、零淡入）· 优先级取小与平局取后 · 条件不成立 / 非 AUTO / DISABLED 三条不推进 · travel 绕过条件与优先级、有淡入时一帧一步、无淡入时一帧走完、不可达退化为 teleport、`start` 清淡入 · 环路检测 · SWITCH_MODE_SYNC 与 `is_reset` · `xfade_time < 0` 被拒 · 权重和恒为 1 且恒 > 0 的不变式扫描。

## 踩坑记录

1. **别假设 travel 是「一帧一步」**：只有路径上的过渡带淡入时才会逐帧推进，全无淡入时递归在同一帧走完整条路径。首版断言「第一帧走到 B」直接失败。
2. **`fade_blend` 的兜底是双向的**：新状态被兜到 `CMP_EPSILON`，旧状态的 `1 - fade_blend` 也被兜到 `CMP_EPSILON`，所以两侧权重和会略微超过 1（不变式断言要带 `CMP_EPSILON` 容差）。
3. **优先级的语义是「数值越小越优先」**，且平局取**后**者——和 1D 混合空间「离散模式平局取先者」正好相反，别混。
4. **`travel` 到不可达状态不是报错而是 teleport**：会清掉正在进行的淡入，`start()` 同理。

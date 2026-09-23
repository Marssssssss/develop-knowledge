# Godot 动画轨道：关键帧查找与循环模式

## 这是什么

Godot 4 的 `Animation` 把一条轨道的求值拆成两步：**先二分找到关键帧**（`Animation::_find`），**再按循环模式决定 `idx` 与 `next` 并算插值因子 `c`**（`Animation::_interpolate`）。这两处的边界处理决定了「动画在末尾怎么接回开头」。本 demo 把这两段代码逐行转写成 Python / Go 并断言。

## 权威来源（实际读过）

- godotengine/godot@master `scene/resources/animation.cpp`（224493 B）
  - `Animation::_find(keys, p_time, p_backward, p_limit)`
  - `Animation::track_find_key(p_track, p_time, p_find_mode, p_limit, p_backward)`
  - `Animation::_interpolate(keys, p_time, p_interp, p_loop_wrap, p_ok, p_backward)`
- `core/math/math_defs.h`（5992 B）：`CMP_EPSILON = 0.00001`
- `core/math/math_funcs.h`（27186 B）：`pingpong(v, len) = abs(fract((v-len)/(len*2))*len*2 - len)`

## 核心机制

### 1. `len = _find(keys, length) + 1`：超过 length 的关键帧被丢弃

`_interpolate` 的第一行不是 `keys.size()`，而是用 `length` 再查一次：

```cpp
int len = _find(p_keys, length) + 1; // try to find last key (there may be more past the end)
```

于是「时间轴上排在 `length` 之后的关键帧」根本不参与求值。实测：关键帧 `0 / 0.5 / 1.0 / 2.0`、`length = 1.5` 时，`len = 3`，在 `t = 1.8` 处取到的是 **20**（1.0 那一帧）而不是 2.0 处的 999。`len == 1` 时直接返回首帧值。

### 2. `_find` 的二分与两种「未命中」修正

- 命中用 `is_equal_approx`（差 `< CMP_EPSILON = 1e-5`），命中即**提前 return**——注意这条路径会跳过后面的 `limit` 越界检查
- 未命中时：向前取（`p_backward == false`）若 `keys[middle].time > p_time` 则 `middle--`，因此早于首帧的时间返回 **-1**；向后取则相反
- 空轨道返回 **-2**
- `p_limit` 只在未提前返回时生效：负时间的关键帧、或超过 `length` 的关键帧会被判越界并返回 **-1**

`track_find_key` 在这个结果上再叠一层命中判定：`NEAREST` 不判（返回 -1 之外的结果即可），`APPROX` 用 `is_equal_approx`，`EXACT` 用 `==`。所以 `t = 0.5000001` 在 `EXACT` 下是 -1，在 `APPROX` 下是 1。

### 3. 三种 LoopMode 的 `idx` / `next`

设 `maxi = len - 1`，`is_start_edge`（向前取时）为 `idx == -1`，`is_end_edge` 为 `idx >= maxi`。

| 模式 | start edge 时 idx | next | 端点处的 `delta` / `from` |
| --- | --- | --- | --- |
| `LOOP_NONE` | `0` | `clamp(idx+1, 0, maxi)` | 不赋值（保持 0）⇒ `c = 0`，保持端点值 |
| `LOOP_LINEAR` | `maxi` | `posmod(idx+1, len)` | start：`delta = (length - t_idx) + t_next`，`from = (length - t_idx) + t_c`；end：`delta = (length - t_idx) + t_next`，`from = t_c - t_idx` |
| `LOOP_PINGPONG` | `-1` | `round(pingpong(idx+1+0.5, len) - 0.5)` | end：`delta = 2·length - t_idx - t_next`，`from = t_c - t_idx` |

实测对照（关键帧 `0 → 0` 与 `0.5 → 10`，`length = 1.0`）：

- `t = 0.75`：**LOOP_NONE 得 10**（保持末帧），**LOOP_LINEAR 得 5**（`delta = 0.5`、`from = 0.25` ⇒ `c = 0.5`，绕回首帧做混合）
- `t = -0.1`：LOOP_NONE 得 **0**（`is_start_edge` 使 `delta` 保持 0，不会外插）；LOOP_LINEAR 得 **2**（`c = 0.8`，在 10 → 0 的回绕段上）
- `t = 1.0`：LOOP_NONE 得 **10**（`idx = maxi` 且 `is_end_edge` ⇒ `c = 0`）

pingpong 的下标由 `round(pingpong(i + 0.5, len) - 0.5)` 折成三角波：`len = 5` 时下标 `0..9` 映射到 `[0,1,2,3,4,4,3,2,1,0]`。本 demo 对 pingpong 只断言下标映射与取值范围——它的 `delta/from` 记账按源码直译，不额外做数值断言。

### 4. 插值方式与两个「不插值」开关

- `INTERPOLATION_NEAREST`：返回 `keys[idx].value`
- `INTERPOLATION_LINEAR`：`(1-c)·v_idx + c·v_next`
- `INTERPOLATION_LINEAR_ANGLE`：`fposmod(lerp_angle(a, b, c), TAU)`，跨 2π 走**最短路**：`0.1` 与 `2π-0.1` 的中点是 **0**，而朴素线性会给 `π`
- **轨道的 `update_mode == UPDATE_DISCRETE` 时强制走 NEAREST**（源码：`vt->update_mode == UPDATE_DISCRETE ? INTERPOLATION_NEAREST : vt->interpolation`）
- **关键帧的 `transition == 0` 时直接返回 `keys[idx].value`**，不做任何插值（`if (tr == 0) return p_keys[idx].value;`）

`c` 的计算统一是 `delta` 近似 0 则 `c = 0`，否则 `from / delta`。

## 目录结构

```
python/track.py            _find / _interpolate / pingpong 转写（约 230 行）
python/selfcheck_track.py  55 条断言，全部实跑通过
python/main.py             演示入口
go/main.go                 Go 侧同协议实现（人工审查 + 静态检查）
```

## 运行

```bash
cd python
python selfcheck_track.py   # 断言总数 55，失败 0
python main.py
```

## 自检覆盖

`_find` 的命中/未命中/前后取/空轨道/`limit` 越界 · pingpong 三角波与 `pingpong(1.5,1)=0.5` · 三种 `FindMode` 的命中判定差异 · 超过 `length` 的关键帧被截断 · LOOP_NONE / LOOP_LINEAR 在段内、末段外、首帧前、恰好 `t = length` 四个位置的取值 · NEAREST / LINEAR / LINEAR_ANGLE / UPDATE_DISCRETE · `transition = 0` 的两条路径 · 空轨道与单关键帧。

## 踩坑记录

1. **命中会提前 return，`limit` 检查被跳过**：想测「负时间关键帧被判越界」必须挑一个**不命中**的时间，否则 `_find` 直接返回下标。
2. **`len == 1` 的构造**：关键帧 `[0, 1]` 配 `length = 1` 时 `_find(keys, 1.0)` 会命中末帧给出 `len = 2`；要让末帧被截断必须让它**严格超过** `length`。
3. **回绕段的插值方向是 `lerp(v_idx, v_next)` 而 `v_idx` 是末帧**：`t = -0.1` 时 `c = 0.8` 给出 `lerp(10, 0, 0.8) = 2`，不是 8。
4. **LOOP_NONE 在 `t = length` 处保持末帧值**：`idx = maxi` 命中 `is_end_edge`，`delta` 不会被赋值，`c = 0`。
5. **`transition = 0` 的「取 idx 值」要看 `_find` 落在哪一帧**：`t = 0.99`（末帧在 1.0）的 `_find` 结果是 **0** 而不是 1，断言别想当然写末帧值。

## 取材边界

`INTERPOLATION_CUBIC` / `CUBIC_ANGLE` 需要 `pre/next/post` 三帧与 `pre_t/to_t/post_t` 三段时间记账，且 `Vector3::cubic_interpolate_in_time` 的曲线公式不在本次取材范围内，**未实现**，README 中已注明。

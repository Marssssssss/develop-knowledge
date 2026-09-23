# glTF 动画采样器与四元数插值

## 这是什么

glTF 2.0 只定义**关键帧的存储**，不定义播放行为（循环、自动播放、时间轴映射都由客户端决定）。它定义清楚的是**两个关键帧之间怎么取值**——即 Appendix C 的三种插值模式。本 demo 把这些公式逐条实现并做成断言。

## 权威来源（实际读过）

- KhronosGroup/glTF@main `specification/2.0/Specification.adoc`（172470 B）
  - §Animations（输出访问器类型表、target/path 规则、区间外 clamp、input 的 min/max）
  - Appendix C: Animation Sampler Interpolation Modes（STEP / LINEAR / SLERP / CUBICSPLINE 公式）

## 四种取值公式

设 `n` 关键帧、`t_k` 时间戳、`v_k` 值、`t_c` 当前时间、`t_d = t_{k+1} - t_k`、`t = (t_c - t_k)/t_d`。

| 模式 | 公式 | 适用 |
| --- | --- | --- |
| STEP | `v_t = v_k` | 任意属性 |
| LINEAR（非 rotation） | `v_t = (1-t)·v_k + t·v_{k+1}` | translation / scale / weights |
| LINEAR（rotation） | `v_t = sin(a(1-t))/sin(a)·v_k + s·sin(at)/sin(a)·v_{k+1}`，`a = arccos(|v_k·v_{k+1}|)`，`s = sign(v_k·v_{k+1})` | rotation |
| CUBICSPLINE | 见下 | 任意属性（rotation 结果 MUST 归一化） |

CUBICSPLINE 每帧存三个值 `(a_k, v_k, b_k)`（in-tangent / value / out-tangent）：

```
v_t = (2t³-3t²+1)·v_k + t_d(t³-2t²+t)·b_k
    + (-2t³+3t²)·v_{k+1} + t_d(t³-t²)·a_{k+1}
```

规范还规定：CUBICSPLINE 采样器 **MUST** 至少 2 个关键帧；首帧 in-tangent `a_1` 与末帧 out-tangent `b_n` **SHOULD** 为 0（它们不参与计算）；rotation 用 CUBICSPLINE 时结果 **MUST** 归一化，且导出方应避免写出 `v_k == -v_{k+1}`（否则插值可能产出全零四元数）。

## 三件容易写错的事

### 1. 命中关键帧与区间外都不做插值

- 规范：`When the current (requested) timestamp exists in the animation data, its associated property value **MUST** be used as-is, without interpolation.`
- 规范：区间之外 `output **MUST** be clamped to the nearest end of the input range.`

STEP 模式下这两条尤其明显：`t_c = 5.0` 而最后一帧在 `t = 2.0` 时，结果是 `v_n = 20`，**不是** `v_{n-1} = 10`。首版实现把 clamp 写成「取 `n-2` 段并把 `t` 置 1」，STEP 就错成了 10。

### 2. SLERP 的最短路修正

`a = arccos(|dot|)` 取绝对值、`v_{k+1}` 再乘 `sign(dot)`，这两步合起来保证走**短弧**。四元数 `q` 与 `-q` 表示同一姿态，如果数据里存的是 `-q`（dot < 0）：

- 规范公式：与存 `q` 时**逐分量相同**（demo 实测两者都得到绕 Y +45°）
- 不做修正的朴素 slerp：走长弧，中点得到 **135°** 而不是 45°（demo 成对断言）

### 3. CUBICSPLINE 的切线是「每秒」

公式里切线项带 `t_d` 因子，所以 `b_k` 的单位是**值/秒**，不是「归一化时间」上的斜率。两个推论（都有断言）：

- 把切线设成割线速度 `(v_{k+1}-v_k)/t_d`，CUBICSPLINE **精确退化为 LINEAR**（在 `t_c = 0.26 / 1.0 / 1.77` 三个点上比对）
- 对**真实时间**求导才等于切线：`dv/dt_c |_{t_c=t_k} = b_k`。差分时若再除一次 `t_d` 会得到 `b_k/t_d`
- 段长减半而切线不变，结果会变——但**别拿 `t = 0.5` 做这个实验**：`h10` 与 `h11` 在该点恰好互为相反数，切线贡献相互抵消，看起来"没变"

零切线时 CUBICSPLINE 退化成 smoothstep `-2t³+3t²`，在 `t = 0.5` 处恰好也是 0.5（对称性），但 `t = 0.25` 处是 `0.15625` 而非 LINEAR 的 `0.25`。

## 目录结构

```
python/sampler.py            采样器模型 + 四种插值 + 通道校验（约 215 行）
python/selfcheck_sampler.py  53 条断言，全部实跑通过
python/main.py               演示入口
go/main.go                   Go 侧同协议实现（人工审查 + 静态检查）
```

## 运行

```bash
cd python
python selfcheck_sampler.py   # 断言总数 53，失败 0
python main.py
```

## 自检覆盖

STEP / LINEAR 的取值与 clamp（含端点直出）· SLERP 的角度推进（22.5° / 45°）、单位性、最短路与长弧对照、`dot > 0` 时不触发修正、夹角趋零退化 · CUBICSPLINE 端点值、零切线 = smoothstep、切线=割线速度退化成 LINEAR、`t_d` 缩放、端点切线未使用、数值导数 = 切线、rotation 归一化、单帧非法 · 访问器类型（VEC3/VEC4/SCALAR、float32 限制）、input 的 min/max、时间单调 · 通道校验（重复 `(node, path)`、matrix 节点禁 TRS 但 weights 不受影响、无 morph 节点禁 weights、sampler 越界、类型不匹配）。

## 踩坑记录

1. **STEP 的 clamp 要落在最后一个关键帧上**，「取 `n-2` 段 + `t = 1`」会给出倒数第二个值。
2. **45° 的判据别用等号**：`2·arccos(w) == π/4` 在浮点下不成立，要带 `1e-9` 容差。
3. **切线缩放对比别挑 `t = 0.5`**：对称点上切线项抵消，得出"没变"的错误结论。
4. **差分求导别多除一次 `t_d`**：`t` 已经是归一化的，对真实时间求导只需 `Δ值 / Δt_c`。
5. **SCALAR 路径的值是标量而非一元数组**：`weights` 的输出访问器类型是 `SCALAR`，实现里要按标量走，混用列表会让「translation 必须 VEC3」这类校验失效。

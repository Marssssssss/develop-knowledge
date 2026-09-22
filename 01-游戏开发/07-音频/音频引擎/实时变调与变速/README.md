# 实时变调与变速：Rubber Band 的比例口径与「拉伸 + 重采样」链路

## 一句话

游戏里「子弹时间」「怪物变声」「变速不变调的 BGM」都靠变调/变速库完成。关键不是算法细节，而是**两个比例的口径**与**模式约束**：time ratio 是**时长比**不是速度比，pitch scale 是**频率比**且半音换算为 `2^(S/12)`。

## 一、两个比例（`RubberBandStretcher.h`）

- `setTimeRatio(r)`：`r` = 拉伸后/拉伸前**时长**。原文举例：`2.0` 表示两倍长，也就是**半速**；`0.5` 是半长即倍速。因此速度 = `1/r`。
- `setPitchScale(p)`：`p` = 目标/源**频率比**。原文明确给出半音换算 `p = pow(2.0, S/12.0)`（`S` 为正表示升）。
- `setFormantScale(f)`：默认 `0.0` 表示自动 —— `OptionFormantPreserved` 时按 `1/p` 计算（保持共振峰=不像花栗鼠），`OptionFormantShifted` 时为 `1.0`。**`getFormantScale()` 在 R2 引擎下恒返回 0.0**（不支持）。

命令行对应 `-t <timeratio> -p <semitones>`。

## 二、模式约束（最容易踩）

| | Offline | RealTime |
| --- | --- | --- |
| 比例可变性 | `study()`/`process()` 之前可改，**之后不可再改** | 运行时可随时改（需与 `process()` 同线程或自备锁） |
| 起始填充 | 内部处理，`getPreferredStartPad()` / `getStartDelay()` 恒为 **0** | **不做**自动填充，必须自己补 startPad 静音并裁掉 startDelay 个输出，否则开头丢样本且输入输出样本数不再成线性关系 |

## 三、选项位（32 个标志，同一组的默认值都是 0）

`DefaultOptions = 0`。十一组互斥选项按位或组合：`process / transients / detector / phase / threading / window / smoothing / formant / pitch / channels / engine`。其中 `OptionEngineFaster = 0`、`OptionEngineFiner = 0x20000000`（R2 / R3）。

两条**被忽略**的规则（实测断言）：

- `OptionWindowLong` 在 **R3** 下被忽略（按 `OptionWindowStandard` 处理）；
- `OptionSmoothingOn` 与 `OptionWindowShort` 同时给出时被忽略。

`setFrequencyCutoff` / `getFrequencyCutoff` / `getInputIncrement` **仅 R2 支持**，R3 调用报错。

## 四、变调不变速 = 重采样 + 拉伸

设重采样 `r` 使「时长 ÷r、音高 ×r」，时间拉伸 `t` 使「时长 ×t、音高不变」：

```
pitch_shift(x, p) = stretch(resample(x, p), p)   # 时长 (n/p)*p = n，音高 ×p
time_stretch(x, T) = stretch(x, T)               # 时长 n*T，音高不变
```

本 demo 用 WSOLA（波形相似重叠相加）实现 `stretch`：每帧在与上一帧最相似的偏移处对齐后再 Hann 窗叠加。

> **朴素 OLA 会坏得很明显**：首版不做的相似度对齐时，帧边界相位跳变形成周期性调制，1 秒 440 Hz 正弦拉伸后自相关估频得到 **101 Hz**。改成 WSOLA 后拉伸方向回到 440 Hz 附近（实测 444.4 Hz，误差在自相关的滞后分辨率内）。
>
> **压缩方向仍有偏差**：降 12 半音理论 220 Hz，本 demo 实测 **188.2 Hz** —— 朴素 WSOLA 在 `ratio < 1` 时合成步长过小、相似度搜索会整周期错位。自检把该实测值**记录**下来并附一条「明显低于理论值」的方向性断言，不掩盖。

## 五、运行

```bash
cd python && python selfcheck_pitchshift.py   # 87 断言全绿
python main.py
```

Go 侧（`go/pitchshift.go` + `go/wsola.go`）为同口径移植：选项位常量、`Stretcher` 的模式约束、`EffectiveOptions` 的忽略规则与 WSOLA/自相关估频；本机无 Go 工具链，走人工审查 + 括号/结构静态检查。

## 参考资料（本 README 实读）

- Rubber Band Library 头文件 `rubberband/RubberBandStretcher.h`（Option 枚举全部位值、`setTimeRatio`/`setPitchScale`/`setFormantScale` 语义与半音公式、Offline/RealTime 差异、`getPreferredStartPad`/`getStartDelay`、R2 专用接口与两条忽略规则）：`https://github.com/breakfastquay/rubberband/blob/default/rubberband/RubberBandStretcher.h`
- Rubber Band Library README（`-t`/`-p`、`-2`/`-3` 双引擎、`rubberband` 与 `rubberband-r3` 的默认引擎差异）：`https://github.com/breakfastquay/rubberband/blob/default/README.md`

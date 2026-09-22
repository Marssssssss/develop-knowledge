# HRTF 双耳渲染：SOFA 约定与 libmysofa 的最近邻插值

## 一句话

双耳渲染 = 「按方向挑一条左右耳的 FIR」+「按方向给的延迟对齐两耳」。方向到 FIR 的映射由 SOFA 文件（AES69）承载，libmysofa 提供**最近邻查找 + 反距离加权插值**两条路径；本 demo 把这两条路径的判定分支逐条落成可断言的代码。

## 一、SOFA SimpleFreeFieldHRIR 的默认值

| 字段 | 默认值 | 维度 / 单位 |
| --- | --- | --- |
| `ListenerPosition` | `[0 0 0]` | `IC, MC`，笛卡尔，米 |
| `ListenerView` / `ListenerUp` | `[1 0 0]` / `[0 0 1]` | 笛卡尔，米 |
| `ReceiverPosition` | `[0 0.09 0; 0 -0.09 0]` | `RCI`（R=2 只耳朵），米；头半径默认 0.09 |
| `SourcePosition` | `[0 0 1]` | `IC, MC`，**球坐标**，单位 `degree, degree, metre` |
| `Data.IR` | — | `mRn`（M 测量点 × R 接收器 × N 采样） |
| `Data.SamplingRate` | `48000` | `I, M`，hertz |
| `Data.Delay` | `[0 0]` | `IR, MR` |

## 二、坐标系（libmysofa README 明示）

- **X `(1 0 0)` = 听音正前方**，**Y `(0 1 0)` = 左侧**，**Z `(0 0 1)` = 向上**。
- `phi` 方位角：自 X 轴**逆时针**，单位度；`theta` 仰角：自 X-Y 平面向上。
- `mysofa_s2c`：`x = cosθ·r`，`z = sinθ·r`，`x' = cosφ·x`，`y' = sinφ·x`。
- `mysofa_c2s`：方位角 `fmod(φ·180/π + 360, 360)`（规整到 `[0,360)`），仰角 `atan2(z, √(x²+y²))`。

自检钉住：`(0,0,1) → (1,0,0)`、`(90,0,1) → (0,1,0)`（左）、`(0,90,1) → (0,0,1)`（上）、`(0,-1,0)` 反算方位角 `270`。

## 三、插值的三条分支（`interpolate.c`）

1. **命中直取**：到最近点距离 `fequals(d, 0)`（阈值 `fabs(a-b) < 0.00001`，见 `tools.h`）→ 直接拷贝该测量点的 IR，延迟按 `Data.Delay` 取。
2. **6 邻域按对择优**：邻域槽位 `(0,1)(2,3)(4,5)` 三对。两个槽位都有效且距离**不相等**时取更近者；**相等时两者都不参与**（`!fequals` 分支）；只有一个有效时直接用；`-1` 表示空缺跳过。
3. **反距离加权后归一化**：权重 `w = 1/d`（最近点）与各被选中邻域的 `1/d_i`，累加完成后整体乘 `1/Σw`。延迟同样按权重混合。

由此得到三条容易写错、但一测就暴露的性质：

- 邻域**全空**时，插值结果**退化成最近点**（权重归一化后分子分母同比例）—— 这时"开了插值"和 `nointerp` 输出完全相同。
- 一对候选**等距**时两者都不参与，结果同样退化为最近点。
- 只有在邻域里放进**比最近点更近或不等距的候选**时，输出才真的偏离存储值。

`mysofa_getfilter_float_nointerp` 的绕过方式很反直觉：它不是跳过插值函数，而是**用最近点的坐标覆盖请求坐标**，让距离恒为 0 从而走进第 1 条分支。

## 四、延迟单位（一处规范与实现不一致）

- SOFA 规范：`Data.Delay` 是**样本数**（"in samples, with the time interval as described by SamplingRate"）。
- libmysofa：`mysofa_getfilter_float*` 返回的延迟是**秒**，而 `mysofa_getfilter_short` 内部做 `delay * DataSamplingRate` 后再 `int` 截断成样本。

本 demo 按 libmysofa 的实现口径（内部秒、对外样本），并标注该差异。截断会丢精度：0.3 ms 在 48 kHz 下是 14.4 样本，截断成 14 → 实测 ITD 只有 **291.7 µs**（比真值少 8.33 µs）。

## 五、双耳线索

- **ITD**（耳间时间差）= `(右耳延迟 − 左耳延迟) / fs`，正值表示右耳更晚 = 源在左侧。
- **ILD**（耳间强度差）= `20·log10(rms_right / rms_left)`。自检用一组对称的合成 IR 验证左右两侧 ILD 恰好反号（±6.02 dB，即能量比 2:1 的头部阴影）。

## 六、渲染闭环

单冲激 × 选出的左右 FIR（含延迟对齐）后：左侧源的输出在左耳 `0` 样本处起振、右耳 `14` 样本处起振，输出长度 `输入 + N − 1 + max(delay)`；两耳能量比 `0.5` 与 ILD 的 −6 dB 自洽。

## 七、运行

```bash
cd python && python selfcheck_hrtf.py   # 103 断言全绿
python main.py
```

Go 侧（`go/hrtf.go`）为同口径移植，含 `S2C/C2S`、`Interpolate` 三分支、`DelaySamples` 截断与 `ITDSeconds/ILDdB`；本机无 Go 工具链，走人工审查 + 括号/结构静态检查。

## 参考资料（本 README 实读）

- SOFA conventions — SimpleFreeFieldHRIR v1.2 全字段表：`https://www.sofaconventions.org/mediawiki/index.php/SimpleFreeFieldHRIR`
- SOFA conventions — GeneralFIR（DataType=FIR 的通用约定）：`https://www.sofaconventions.org/mediawiki/index.php/GeneralFIR`
- libmysofa README（坐标系、open/getfilter 用法、delay 单位、nointerp 语义）：`https://github.com/hoene/libmysofa/blob/main/README.md`
- libmysofa `src/hrtf/tools.h`（`fequals` 阈值 1e-5、`distance` 宏）、`src/hrtf/spherical.c`（`mysofa_s2c`/`mysofa_c2s` 调用面）
- libmysofa `src/hrtf/interpolate.c`（命中直取、6 邻域按对择优、1/d 加权归一化）、`src/hrtf/easy.c`（`mysofa_getfilter_short` 的 `delay*SamplingRate`、`SourcePosition.elements != C*M` 报 `MYSOFA_INVALID_FORMAT`）

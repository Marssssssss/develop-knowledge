# IMA ADPCM 自适应差分脉冲编码

## 简介

IMA ADPCM 是交互式多媒体协会(Interactive Multimedia Association)选定的公有领域音频压缩算法:用 4 bit 编码相邻样本间的**预测差分**,并按信号激烈程度**自适应调整量化步长**,把 16-bit PCM 压缩到 1/4。它以极低的复杂度(1993 年即可在 20 MHz 的 386 上实时编解码 44.1 kHz 立体声)换取中等音质,至今仍广泛用于游戏语音、电话(RTP DVI4)、WAV 文件(`WAVE_FORMAT_IMA_ADPCM`)与硬件音频芯片。

- **差分(Differential)**:相邻音频样本高度相似,只编码 `当前样本 - 预测值`
- **预测(Predictive)**:预测器就是上一个重建样本(一阶延迟),简单到无需传递边信息
- **自适应(Adaptive)**:量化步长由 89 级步长表按码字幅值自动升降,安静段精细、突变段粗放
- **4:1 固定压缩比**:16 bit → 4 bit,码率可预测(对游戏内存预算友好)
- 历史:IMA(已于 1997 年解散)为多媒体数据的事实标准而选定了它;ITU-T G.721/G.723 是同期更复杂的电信系 ADPCM

## 原理详解

### 编解码器结构(论文图 2)

```
编码器(内嵌解码器):                    解码器:
                                         
 x[n] ──(+)── diff ──> 自适应量化器        c[n] ──> 自适应反量化器 ──(+)──> x̂[n]
        │-                │ c[n]                        (含 step/8 偏置)   │
        │                 v                                  ^             │
        │          ┌──> 反量化 ──> (+) ──> x̂[n]              │             │
        │          │       ^        ^                        │             │
        └── x̂[n-1] ┘───────┼────────┘ (共用状态) <────────────┼─────────────┘
                            └── 步长自适应(两次查表)                        
```

编码器复用了解码器的反量化与状态推进部件,保证收发两端状态逐样本同步——这是所有 ADPCM 的正确实现方式。

### 逐样本工作流程

1. **求差分**:`diff = x[n] - predictor`(predictor = 上一重建样本)
2. **取符号**:负差分置符号位 `bit3 = 1`,`diff` 取绝对值
3. **三级阈值比较**(论文图 3 量化流程):`diff ≥ step` 则 `bit2=1` 并减去 `step`;再与 `step/2` 比较得 `bit1` 并减去;最后与 `step/4` 比较得 `bit0`
4. **编码器内嵌解码**(推进状态,见上)
5. **解码端重建**:`diff = step>>3 + (bit2?step:0) + (bit1?step/2:0) + (bit0?step/4:0)`,按符号取负,`predictor += diff` 后钳位到 [-32768, 32767]
6. **步长自适应**:`index += index_table[c]`,钳位到 [0, 88],新步长 = `step_table[index]`

`step>>3` 偏置使重建值落在量化区间中部,消除系统性偏差(论文给出重建公式 `Xp[n] = Xp[n-1] + step_size[n]·C'[n]`,其中 `C'` 含"加半步长"的换算)。

### 两张只读表

索引调整表(16 项,按完整 4-bit 码字索引):

| 码字幅值 | 0-3 | 4 | 5 | 6 | 7 | 8-11 | 12 | 13 | 14 | 15 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| index 变化 | -1 | +2 | +4 | +6 | +8 | -1 | +2 | +4 | +6 | +8 |

步长表(89 项,节选):`[0]=7 … [22]=60 [44]=494 [66]=4026 [88]=32767`,相邻项比值约 1.1(近似指数)。幅值小的码字让步长逐级收缩,幅值大的让它最多一次 +8 级——一静一动,完成自适应。

### 状态与核心 API(以本 demo 为例)

| 接口 | 签名 | 说明 |
| --- | --- | --- |
| 编码单样本 | `code = encode_sample(int16 s, state*)` | 返回 4-bit 码字,推进 predictor/index |
| 解码单样本 | `s = decode_sample(uint8 code, state*)` | 重建样本,推进 predictor/index |
| 流式打包 | `bytes = encode_stream(pcm[])` | 偶数样本放低 4 位(IMA WAV 惯例) |
| 块编解码 | `encode_blocks / decode_blocks_from` | 每块 4 字节头 + 128 字节数据 |

### 块头与随机访问(游戏音频的关键)

游戏流式音频需要随时切入码流(跳过、循环、拼接)。IMA/DVI4 用周期性重置状态解决:

```
块头(4 字节,参照 RFC 3551 DVI4):
  [0..1] int16 predictor  块首预测值(小端)
  [2]    uint8  index     块首步长表索引
  [3]    uint8  reserved  发送方置 0
其后:  256 样本 → 128 字节 nibble 数据(低 4 位在前)
```

注:WAV 的 IMA ADPCM 块头存的是**块内第一个样本原值**(随后才接 nibble),而 RTP DVI4 存的是**预测值**且 nibble 顺序相反(高 4 位在前)——两种封装不兼容,这是最常见的移植坑。

## 对比 / 选型

| 方案 | 每样本位数 | 压缩比(16-bit 源) | 复杂度 | 音质 | 典型场景 |
| --- | --- | --- | --- | --- | --- |
| 线性 PCM | 16 | 1:1 | 极低 | 无损基准 | 中间格式 |
| μ-law/A-law (G.711) | 8 | 2:1 | 极低(一次对数变换) | 中 | 电话、北美/日本 ISDN |
| **IMA ADPCM** | **4** | **4:1** | **低(两次查表/样本)** | **中** | **游戏语音、WAV、RTP DVI4** |
| G.726 (G.721/G.723) | 2-5 | ~2.5-8:1 | 中 | 中 | 电信 ADPCM 标准 |
| Vorbis / Opus | 可变 | ~10:1+ | 高(变换/预测编码) | 高 | 背景音乐、发行音频 |

选型原则:语音与 UI 音效用 IMA ADPCM(解码廉价、码率固定、可随机访问);音乐资产用 Vorbis/Opus(质量优先)。运行时按延迟与 CPU 预算取舍。

## 环境准备

- 操作系统:任意(C/Python/Go 均跨平台)
- C:C99(`gcc -std=c99`,链接 libm)
- Python:3.8+(仅标准库 `math`/`struct`)
- Go:1.21+(仅标准库)

## 运行方式

### C

```bash
gcc -O2 -Wall -Wextra -std=c99 c/ima_adpcm.c -lm -o ima_adpcm
./ima_adpcm
```

### Python

```bash
python3 python/ima_adpcm.py
```

### Go

```bash
cd go && go run ima_adpcm.go
```

三语言输出结构一致:流式编解码统计 → 单 nibble 篡改的抗误码演示 → 从第 5 块头部随机访问解码并与全量解码比对。

## 关键代码片段

编码单个样本(C 版,对应"原理详解"步骤 1-4):

```c
static uint8_t adpcm_encode_sample(int16_t sample, AdpcmState *st) {
    int32_t diff = (int32_t)sample - st->predictor;  /* 1. 差分 */
    uint8_t code = 0;
    if (diff < 0) { code = 8; diff = -diff; }        /* 2. 符号位 */
    int32_t step = step_table[st->index];
    if (diff >= step)      { code |= 4; diff -= step; }   /* 3. 三级阈值 */
    if (diff >= step >> 1) { code |= 2; diff -= step >> 1; }
    if (diff >= step >> 2) { code |= 1; }
    adpcm_decode_sample(code, st);  /* 4. 内嵌解码器推进状态 */
    return code;
}
```

解码单个样本(对应步骤 5-6,注意 `step>>3` 偏置与两处钳位):

```c
static int16_t adpcm_decode_sample(uint8_t code, AdpcmState *st) {
    int32_t step = step_table[st->index];
    int32_t diff = step >> 3;                       /* 半步长偏置 */
    if (code & 4) diff += step;
    if (code & 2) diff += step >> 1;
    if (code & 1) diff += step >> 2;
    if (code & 8) diff = -diff;
    st->predictor += diff;
    if (st->predictor > 32767) st->predictor = 32767;   /* 钳位 */
    if (st->predictor < -32768) st->predictor = -32768;
    st->index += index_table[code];
    if (st->index < 0) st->index = 0;                   /* 钳位 0..88 */
    if (st->index > 88) st->index = 88;
    return (int16_t)st->predictor;
}
```

## 性能与边界

- **压缩比**:流式严格 4:1;分块后含头部开销约 3.88:1(256 样本块:512 字节 → 132 字节)
- **计算量**:每样本两次查表 + 少量加减法,O(1)、无乘除(位移实现),单声道 44.1 kHz 解码在 1993 年的 386 上即可实时(论文目标)
- **误差特性**(本 demo 实测,8000 Hz 正弦 + 20 倍幅度突变):静音段最大误差约 412;**突变瞬间最大误差可达数千**(步长需约 3-8 个样本从安静段爬升到能覆盖 12000 的差分,期间只能输出粗略逼近);稳态平均误差约 63
- **码率**:16-bit 44.1 kHz 单声道 → 176.4 kbps;RTP DVI4 静态负载类型 5/6/16/17 分别对应 8k/16k/11.025k/22.05k Hz
- **质量上限**:4-bit 量化决定了它不适合音乐发行资产,瞬态(打击乐、爆音)跟随有可闻失真

## 注意事项与常见坑

- **步长表索引 84 的分歧**:论文 Table 2 印作 `22358`,而 ffmpeg 等标准实现与 IMA 规范镜像为 `22385`。本 demo 以标准实现为准(该位大概率是论文排版笔误,两值仅差 27,音质无可闻差异,但会导致与标准解码器逐字节不兼容)
- **编码器必须内嵌解码器**:若编码端用"理想差分"而非"重建差分"更新状态,收发状态将逐渐发散,误差累积到爆音。现象:单独看编码输出"合理",解码却越走越偏
- **nibble 顺序不统一**:IMA WAV 惯例是偶数样本放低 4 位;RTP DVI4 是高 4 位优先。混用两套封装会得到满幅噪声
- **块头语义不统一**:WAV IMA 块头存块内首样本原值,RTP DVI4 存预测值;且 DVI4 要求每块偶数个样本
- **忘记钳位**:predictor 不钳位会在大信号下回绕(负变正),index 不钳位会越界查表
- **误码特性**(论文分析,本 demo 演示):单 nibble 错误对步长的影响最多 `1.1^8 ≈ 7.45 dB` 的增益误差;连续 88 个小幅值码字即把步长完全收敛回最小值(8 kHz 下仅 11 ms);对 predictor 的错误则表现为**恒定直流偏置**,波形形状不变——解码输出过一次满幅削波即可部分自纠,或用高通滤波去除
- **随机访问实现**:任何"11 ms 低电平"处都是安全切入点的候选;正式实现应周期性写块头而非依赖信号统计

## 参考资料(实际阅读过的权威来源)

- [Davis Yen Pan, "Digital Audio Compression", Digital Technical Journal Vol.5 No.2, Spring 1993](https://www.cs.columbia.edu/~hgs/research/projects/echo-detection/Pan93-acomp.pdf) — DEC 官方技术期刊综述;第 3 节完整给出 IMA ADPCM 的编解码器结构(图 2)、量化流程图(图 3)、步长自适应双查表(图 4)、Table 1 索引调整表、Table 2 步长表与误码恢复数学分析
- [H. Schulzrinne et al., RFC 3551 "RTP Profile for Audio and Video Conferences with Minimal Control"](https://www.rfc-editor.org/rfc/rfc3551) — IETF 标准;§4.5.1 定义 DVI4(IMA ADPCM 的 RTP 封装):4 字节块头(predictor/index/reserved)、nibble 打包顺序及与 IMA WAV 封装的三点差异,§6 表 4 给出静态负载类型

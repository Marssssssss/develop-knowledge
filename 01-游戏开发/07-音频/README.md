# 游戏音频

## 子领域

- **音频引擎**：FMOD / Wwise / 自研（参数自动化、DSP 图、总线与快照）
- **空间音频**：HRTF（头相关传递函数）、Ambisonic
- **MIDI 与合成**
- **音频压缩**：Vorbis / Opus / ADPCM

## 已完成 demo

| demo | 知识点 | 语言 |
| --- | --- | --- |
| [音频压缩/IMA-ADPCM](./音频压缩/IMA-ADPCM/) | IMA ADPCM（自适应差分脉冲编码 + 步长双查表 + 块随机访问） | C / Python / Go |
| [空间音频/Ambisonic 声场与 ACN-SN3D](./空间音频/Ambisonic声场与ACN-SN3D/) | ACN 通道序 `n=l(l+1)+m`、SN3D 归一化、球谐编解码与 `Σ Y²=order+1`、SA3D 盒与 channel_map | Python / Go |
| [空间音频/HRTF 双耳渲染与 SOFA](./空间音频/HRTF双耳渲染与SOFA/) | SOFA SimpleFreeFieldHRIR 字段与坐标系、libmysofa 最近邻 + 反距离插值三分支、延迟单位与 ITD/ILD | Python / Go |
| [音频引擎/参数自动化与包络](./音频引擎/参数自动化与包络/) | W3C AudioParam 五类事件与取值公式、指数斜坡不能到 0、曲线区间互斥、cancel vs hold、a-rate/k-rate | Python / Go |
| [音频引擎/实时变调与变速](./音频引擎/实时变调与变速/) | Rubber Band 选项位、time ratio 是时长比、半音↔频率比 `2^(S/12)`、Offline/RealTime 约束、WSOLA 拉伸 + 重采样链路 | Python / Go |
| [音频压缩/Opus 帧结构与 Ogg 封装](./音频压缩/Opus帧结构与Ogg封装/) | TOC 字节与 32 个配置、四种 code 与帧长度编码、120 ms 上限、Ogg ID 头 / pre-skip / granule 与输出增益 | Python / Go |

> 本子类累计 6 个 demo（首批 IMA-ADPCM + 2026-09-23 音频专题 5 个）。

## 待研究

- [x] HRTF 实现原理 — 见 [HRTF 双耳渲染与 SOFA](./空间音频/HRTF双耳渲染与SOFA/)
- [x] 实时变调（音高保持）— 见 [实时变调与变速](./音频引擎/实时变调与变速/)
- [x] Opus / Ogg 封装结构 — 见 [Opus 帧结构与 Ogg 封装](./音频压缩/Opus帧结构与Ogg封装/)
- [ ] FMOD / Wwise 对比（商业引擎，官方无公开可核对的定量口径，不硬编码默认值）
- [ ] MIDI 1.0 消息编码与 GM 音色表
- [ ] 减法合成与调制矩阵（LFO / 包络路由）
- [ ] 混响：Schroeder 梳状+全通级联（Freeverb）与卷积混响的开销对比
- [ ] 响度与动态：ITU-R BS.1770 K 加权、真实峰值与响度归一化

## 本批新增参考资料（2026-09-23 实读）

- Google《Spatial Audio RFC》— `https://github.com/google/spatial-media/blob/master/docs/spatial-audio-rfc.md`
- SOFA conventions：SimpleFreeFieldHRIR v1.2 / GeneralFIR — `https://www.sofaconventions.org/mediawiki/index.php/SimpleFreeFieldHRIR`
- libmysofa README 与源码（`tools.h` `interpolate.c` `easy.c` `spherical.c`）— `https://github.com/hoene/libmysofa`
- W3C《Web Audio API 1.1》§1.6.2 — `https://www.w3.org/TR/webaudio-1.1/`
- Rubber Band `RubberBandStretcher.h` 与 README — `https://github.com/breakfastquay/rubberband`
- RFC 6716 / RFC 7845 — `https://www.rfc-editor.org/rfc/rfc6716.txt`、`https://www.rfc-editor.org/rfc/rfc7845.txt`

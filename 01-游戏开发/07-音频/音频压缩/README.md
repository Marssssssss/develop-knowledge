# 音频压缩

## 子领域

- **波形编码**:ADPCM(IMA / OKI / G.726)、μ-law / A-law(G.711)
- **感知编码**:Vorbis、Opus、MP3 / AAC
- **游戏选型**:语音与 UI 音效用波形编码(低复杂度、固定码率),音乐资产用感知编码

## 已完成 demo

| demo | 知识点 | 语言 |
| --- | --- | --- |
| [IMA-ADPCM](./IMA-ADPCM/) | IMA ADPCM(自适应差分 + 4-bit 量化 + 步长双查表 + 块随机访问) | C / Python / Go |

## 待研究

- [ ] Opus CELT/SILK 双层结构与帧封装
- [ ] Vorbis 码簿与心理声学模型
- [ ] OKI / MS-ADPCM 变体与 IMA 的差异

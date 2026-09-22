# Opus 帧结构与 Ogg 封装

## 一句话

Opus 包的第一个字节（TOC）就同时决定了**用什么模式编的、多宽带宽、一帧多长、几帧、单声道还是立体声**；Ogg 侧则用「48 kHz 样本」的 granule 和 pre-skip 把解码器延迟显式记账。两篇 RFC 都给了完整表格，可以逐条断言。

## 一、TOC 字节

```
 0 1 2 3 4 5 6 7
+-+-+-+-+-+-+-+-+
|config |s| c |
+-+-+-+-+-+-+-+-+
```

`config`（5 bit）查 Table 2 得到 mode / 带宽 / 帧长，`s`（1 bit）为单声道还是立体声，`c`（2 bit）是帧数编码方式。

## 二、Table 2 配置表（本 demo 直接由表构造，不手写常量）

| 配置 | 模式 | 带宽 | 帧长 |
| --- | --- | --- | --- |
| 0…3 | SILK-only | NB | 10, 20, 40, 60 ms |
| 4…7 | SILK-only | MB | 10, 20, 40, 60 ms |
| 8…11 | SILK-only | WB | 10, 20, 40, 60 ms |
| 12…13 | Hybrid | SWB | 10, 20 ms |
| 14…15 | Hybrid | FB | 10, 20 ms |
| 16…19 / 20…23 / 24…27 / 28…31 | CELT-only | NB / WB / SWB / FB | 2.5, 5, 10, 20 ms |

配置号在段内的**位置**决定帧长（如 config 11 是 SILK WB 的 60 ms）。Table 1 的带宽：NB 4 kHz/8 k、MB 6 k/12 k、WB 8 k/16 k、SWB 12 k/24 k、FB 20 kHz(*)/48 k。

## 三、四种 code

| code | 语义 | 帧长从哪来 |
| --- | --- | --- |
| 0 | 1 帧 | `N-1` |
| 1 | 2 帧**等长** | 各 `(N-1)/2`，奇数载荷非法 |
| 2 | 2 帧**异长** | 首帧长度 N1（1~2 字节），余下给第二帧 |
| 3 | 帧数由字节给出 | 帧数字节 `v|p|M`：M = 低 6 位（不得为 0），p = padding 位，v = VBR 位 |

帧长度编码（RFC 6716 §3.2）：`0` 表示**无帧**（DTX 或丢包），`1…251` 是长度，`252…255` 需要第二字节且 **总长 = 第二字节×4 + 第一字节**，上限 `255×4+255 = 1275`。

code 3 的 padding 记法是「指示字节本身 + 它说的值」：指示字节 `10` 表示另有 10 字节填充，总计 `P = 11`；`255` 表示再加 254 并继续读下一字节（用于把包凑到任意目标大小）。

**120 ms 上限**：包内音频时长不得超过 120 ms，于是 2.5 ms 帧最多 48 帧、20 ms 帧最多 6 帧、60 ms 帧最多 2 帧。

## 四、Ogg 封装（RFC 7845）

ID 头 19 字节、小端：`OpusHead` + version(1) + channel count + pre-skip(16) + input sample rate(32) + output gain(16, Q7.8 dB) + mapping family(8)。

- 输出增益：`sample *= pow(10, output_gain/(20.0*256))`（自检用 `±6 dB ≈ 1.995 / 0.501` 锚定）。
- **granule position 以 48 kHz 样本计，与声道数无关**（立体声不会翻倍）。
- `PCM sample position = granule position − pre-skip`。pre-skip 典型 3840 样本（80 ms），因此流开头的 PCM 位置是**负数**——这些样本要解码但丢弃，用于让预测型解码器收敛。自检复现 RFC 的示例：`granule 59971 / pre-skip 11971 → 48000`。
- ID 头页与 comment 头完成页的 granule **必须为 0**；整页被单个包跨越时填 `-1`。

## 五、运行

```bash
cd python && python selfcheck_opus.py   # 76 断言全绿
python main.py
```

Go 侧（`go/opus.go`）为同口径移植，含配置表构造、四种 code 解析、长度编解码与 ID 头；本机无 Go 工具链，走人工审查 + 括号/结构静态检查。

## 参考资料（本 README 实读）

- RFC 6716《Definition of the Opus Audio Codec》§3.1–3.2.5、Table 1、Table 2、Figure 5：`https://www.rfc-editor.org/rfc/rfc6716.txt`
- RFC 7845《Ogg Encapsulation for the Opus Audio Codec》§4.2–4.5、§5.1 ID 头图与 output gain 公式：`https://www.rfc-editor.org/rfc/rfc7845.txt`

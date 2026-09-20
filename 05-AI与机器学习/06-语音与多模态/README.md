# 语音与多模态

聚焦语音识别(ASR)、语音合成(TTS)、音频特征表示与跨模态模型(视觉-语言/音频-语言)的**原理级**实现。

## 领域简介

语音任务在深度学习生态里由两套基础设施支撑(Hugging Face 官方抽象):

- **特征提取器/处理器(Processor)**:音频不是 token,输入是**波形或音频特征**(如 log-Mel 频谱图),需要信号处理 + 神经网络两段管线,所以 audio 任务的 Processor 地位相当于 NLP 的 Tokenizer;
- **任务模型族(AutoModelFor\*)**:按输出形态分化 —— `AutoModelForCTC`(帧级对齐式 ASR)、`AutoModelForSpeechSeq2Seq`(生成式 ASR,如 Whisper)、`AutoModelForAudioClassification`(音频分类)、`AutoModelForTextToWaveform`(TTS)。

两大主流 ASR 范式(Hugging Face 文档归纳):

| 范式 | 代表模型 | 机制 | 特点 |
| --- | --- | --- | --- |
| CTC(Connectionist Temporal Classification) | Wav2Vec2 / HuBERT / XLS-R | 每个音频帧预测 token 概率,CTC 解码把帧序列折叠成文本 | 快、流式友好;输出不自回归 |
| Seq2Seq(编码器-解码器) | Whisper | 编码器吃 log-Mel 频谱,解码器逐 token 生成,带语言建模能力 | 精度高、多语言/翻译一体;慢 |

Whisper(OpenAI 2022, Radford et al. *Robust Speech Recognition via Large-Scale Weak Supervision*):680k 小时弱标注数据训练的 Transformer encoder-decoder,无需微调即强泛化;上下文 token 序列 `<|startoftranscript|>` → 语言 token → 任务 token(`<|transcribe|>`/`<|translate|>`)控制输出语言与任务。

## 已完成 demo

| ID | 目录 | 核心机制 |
| --- | --- | --- |
| 457 | [01-CTC损失与解码/](./01-CTC损失与解码/) | 输出层 \|L\|+1 个单元(多出的即 blank);B 映射**先合并相邻重复再去 blank**,故 `B("a-a")="aa"` 而 `B("aa")="a"`;l' 长 2\|l\|+1;前向 α 递推在 `l'_s=b` 或 `l'_{s-2}=l'_s` 时不引入 `s−2` 项;`p(l\|x)=α_T(\|l'\|)+α_T(\|l'\|−1)`;零区是**剪枝规则不是恒等式**(不置零则 `Σln C_t` 不等于 `ln p`);`αβ` 对 `y^t_k` 是 2 次 ⇒ 式(15) 出现 `(y_k)²`;best path decoding 不保证最优(实测漏报集非空) |
| 458 | [02-logMel频谱图/](./02-logMel频谱图/) | Whisper 硬编码:16 kHz / n_fft 400 / hop 160 / 30 s = 480000 样本;`center=True` 得 3001 帧,取 `[..., :-1]` 得 3000;`magnitudes=\|STFT\|²` 是功率谱;Hann 周期窗;Slaney mel 是「1000 Hz 以下线性 + 以上对数」的分段函数(`f_sp=200/3`、`logstep=ln(6.4)/27`);三角滤波器取 `n_mels+2` 个端点、跨度 `[mel_f[i], mel_f[i+2]]`、`norm='slaney'` 再乘 `2/(mel_f[i+2]−mel_f[i])`;三步后处理 `clamp(1e-10).log10()` → `max(x, x.max()−8)` → `(x+4)/4` |
| 459 | [03-Whisper解码与失败回退/](./03-Whisper解码与失败回退/) | 6 档温度 (0.0…1.0) 逐档试;失败判据三条,其中 **`no_speech_prob>0.6` 且 `avg_logprob<−1.0` 是豁免而非惩罚**(静音不回退);`compression_ratio` 用 gzip 检测卡住;`avg_logprob` 分母是 `len+1`;`max_candidates=round(beam_size×patience)`;长度归一与 Google NMT 惩罚会选出**不同**序列;`ApplyTimestampRules` 里 `len(seq)<2` 视为「倒数第二个也是时间戳」 |
| 460 | [04-CLIP双塔对比损失/](./04-CLIP双塔对比损失/) | 两路 L2 归一后点积即余弦相似度;`logits=(I_e@T_e.T)·exp(t)`;`axis=0`/`axis=1` 是两个**不同**的 softmax 方向,两个 CE 取平均;τ 初始化 `ln(1/0.07)≈2.659`(即 14.2857)、训练时 clip 到不超 100;**增大 scale 只在正样本已排第一时才降低 loss**,随机特征下反而升高 |
| 461 | [05-Wav2Vec2对比学习与量化/](./05-Wav2Vec2对比学习与量化/) | 掩码 `p=0.065` 是**起点比例**,被掩码比例是 `1−(1−p)^M≈49%`,平均跨度 14.7(因跨度可重叠);`Lm` 的 κ=0.1、K=100 干扰项来自同句其他掩码步(用 `+1` 技巧避开自身 + `neg_is_pos` 兜底);乘积量化 G=2、V=320 ⇒ 102.4k 码字,下标**高位在前**;温度从 2 按 0.999995 衰减到 0.5;`code_ppl`/`prob_ppl` 按组求和满值 2V;脚注 2 的 perplexity 形式与式(4) **方向相反** |

> 2026-09-20 12:00 槽首批 5 个 demo。Python 侧共 **213 条断言实跑全绿**(34+45+51+35+48);Go 侧无本机工具链,走人工审查 + `bracket_check.py` / `go_sanity.py` / `go_crossref.py` 全 OK。

## 待研究

- [x] CTC 损失与解码(空白 token / 折叠规则 / 前向-后向算法)—— 2026-09-20,ID 457
- [x] log-Mel 频谱图(STFT → Mel 滤波器组 → log)—— 2026-09-20,ID 458
- [x] Wav2Vec2 自监督预训练(对比学习 + 量化)—— 2026-09-20,ID 461
- [x] 视觉-语言多模态(CLIP 双塔对比)—— 2026-09-20,ID 460
- [ ] Whisper 上下文 token 与时间戳预测(DTW 对齐)
- [ ] TTS 两段式架构(声学模型 + vocoder,如 SpeechT5 / Vocoder)
- [ ] 音频-语言融合(Qwen-Audio / SALMONN 的窗口级 Q-Former)
- [ ] HuBERT 的掩码预测与聚类迭代(k-means → 软标签)
- [ ] 流式 ASR 的 chunk 注意力与延迟-精度权衡
- [ ] Conformer 的卷积增强与相对位置编码
- [ ] Whisper 时间戳的 DTW 对齐(word_timestamps 实现细节)

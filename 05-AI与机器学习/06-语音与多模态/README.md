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

## 待研究

- [ ] CTC 损失与解码(空白 token / 折叠规则 / 前向-后向算法)
- [ ] log-Mel 频谱图(STFT → Mel 滤波器组 → log)
- [ ] Wav2Vec2 自监督预训练(对比学习 + 量化)
- [ ] Whisper 上下文 token 与时间戳预测(DTW 对齐)
- [ ] TTS 两段式架构(声学模型 + vocoder,如 SpeechT5 / Vocoder)
- [ ] 视觉-语言多模态(CLIP 双塔对比 / Qwen-Audio 音频-语言融合)

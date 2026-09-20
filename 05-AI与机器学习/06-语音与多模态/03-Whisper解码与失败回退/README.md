# 459 · Whisper 解码策略与失败回退

## 简介

Whisper 的解码不是「跑一遍模型拿结果」这么简单。官方 `transcribe()` 默认会对**每个 30 秒窗口**尝试 **6 档温度**（0.0 → 0.2 → … → 1.0），每档结束都用三个判据判断「这次解码是否失败」，失败就换更高温度重来。此外还有一组 **logit filter** 在每一步采样前直接改写 logits，用来保证时间戳合法、禁止空白输出等。

本 demo 把这两套机制按 OpenAI 官方源码（`whisper/decoding.py`、`whisper/transcribe.py`、`whisper/utils.py`）逐行实现，并用 51 条断言把每条判据钉死。

## 原理详解

### 1. 三个失败判据与温度回退

`transcribe.py` 的默认值：

```
temperature                  = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
compression_ratio_threshold  = 2.4
logprob_threshold            = -1.0
no_speech_threshold          = 0.6
condition_on_previous_text   = True
```

`decode_with_fallback` 的循环（源码原样）：

```python
for t in temperatures:
    if t > 0:  kwargs.pop("beam_size"); kwargs.pop("patience")   # 采样时不走 beam
    else:      kwargs.pop("best_of")                             # 贪心/beam 时不走 best-of
    decode_result = model.decode(segment, DecodingOptions(**kwargs, temperature=t))

    needs_fallback = False
    if compression_ratio_threshold is not None and \
       decode_result.compression_ratio > compression_ratio_threshold:
        needs_fallback = True                       # too repetitive
    if logprob_threshold is not None and \
       decode_result.avg_logprob < logprob_threshold:
        needs_fallback = True                       # average log probability is too low
    if no_speech_threshold is not None and \
       decode_result.no_speech_prob > no_speech_threshold and \
       logprob_threshold is not None and \
       decode_result.avg_logprob < logprob_threshold:
        needs_fallback = False                      # silence
    if not needs_fallback:
        break
```

**三条判据的地位并不对等**，这是最容易读漏的地方：

| 判据 | 含义 | 结果 |
| --- | --- | --- |
| `compression_ratio > 2.4` | 文本太重复（模型卡住了） | 换温度 |
| `avg_logprob < -1.0` | 平均对数概率太低（模型没把握） | 换温度 |
| `no_speech_prob > 0.6` **且** `avg_logprob < -1.0` | 是**静音** | **撤销**回退，直接接受 |

第三条是**豁免**而不是惩罚：静音段的 `avg_logprob` 天然很低，如果只看第二条就会白白把 6 档温度全跑一遍。所以源码显式把它置回 `False`。本 demo 用 `B6`（只调用一次、不回退）与 `B7`/`B8`（nsp 高但 logprob 不低 → 不豁免；logprob 低但 nsp 不高 → 一路回退到 1.0）把这四个分支全部覆盖。

**温度与解码器的耦合**：`t == 0` 时禁用 `best_of`（走贪心或 beam search），`t > 0` 时禁用 `beam_size` / `patience`（走 best-of-N 采样）。源码还有一条硬校验：`beam_size` 与 `best_of` **不能同时给**，给了就抛 `ValueError`。

### 2. compression_ratio：用 gzip 检测「卡住」

`whisper/utils.py` 只有三行：

```python
def compression_ratio(text) -> float:
    text_bytes = text.encode("utf-8")
    return len(text_bytes) / len(zlib.compress(text_bytes))
```

思路很朴素：模型一旦陷入循环（反复输出同一句话），文本的**可压缩性**会暴涨。本 demo 实测 `"ab"×200` 的比值远超 2.4，而正常英文句子低于 2.4，两者相差 2 倍以上。

注意它是**无阈值下限**的：很短的文本（例如单个字符）因为 zlib 有固定头部开销，比值可能小于 1，这时判据不会误触发。

### 3. avg_logprob 的分母是 `len + 1`

```python
avg_logprobs = [lp / (len(t) + 1) for t, lp in zip(tokens, sum_logprobs)]
```

多出来的 1 是 EOT。忽略它会让短句的 `avg_logprob` 系统性偏高。

### 4. 两个解码器

**GreedyDecoder**（`temperature == 0` 走 argmax，否则 `Categorical(logits/T)` 采样）：

```python
sum_logprobs += current_logprobs * (tokens[:, -1] != self.eot)
next_tokens[tokens[:, -1] == self.eot] = self.eot
tokens = torch.cat([tokens, next_tokens[:, None]], dim=-1)
completed = (tokens[:, -1] == self.eot).all()
```

三个后果：已经生成 EOT 的行**停止累加**对数概率；这些行继续被填 EOT（padding）；全部行都到 EOT 才算 `completed`。`finalize` 还会再补一个 EOT，保证每条序列至少有一个结尾符。

**BeamSearchDecoder** 的关键参数是 `patience`：

```python
self.patience = patience or 1.0
self.max_candidates = round(beam_size * self.patience)
completed = all(len(seqs) >= self.max_candidates for seqs in self.finished_sequences)
```

`patience > 1` 表示「多等一会儿」：即使已经收集够 `beam_size` 条完整序列，仍继续搜索直到凑够 `beam_size × patience` 条候选，再从中选最好的。默认 `patience=None → 1.0`。本 demo 断言 `round(5×1.5) = 8`（Go 的 `math.Round` 对 .5 是远离零取整，与 Python 的银行家舍入在 7.5 处一致，但 `round(2.5)` 这类会有差异，跨语言复现要留意）。

另有一处容易踩：`finalize` 在收集到的完整序列不足 `beam_size` 时，会**把未完成的 beam 补进去**（补一个 EOT 后当成品），保证永远返回 `beam_size` 条。

### 5. 序列排序：长度归一 vs Google NMT 惩罚

```python
if self.length_penalty is None:
    penalty = length                              # 长度归一
else:
    penalty = ((5 + length) / 6) ** self.length_penalty   # Google NMT
result.append(logprob / penalty)
```

两者**会选出不同序列**。本 demo 构造：`(logprob=-1.0, len=1)` vs `(logprob=-50.0, len=100)` —— 长度归一选长序列（`-0.5 > -1.0`），NMT 惩罚（α=1）选短序列（`-1.0 > -2.857`）。所以调 `length_penalty` 时不要假定它只是「轻微调整」。α=0 时惩罚恒为 1，退化成不归一。

### 6. 三个 LogitFilter

作用在**每一步采样前**，直接把 logits 改成 `-inf`：

| Filter | 规则 |
| --- | --- |
| `SuppressBlank` | 仅在 `len(tokens) == sample_begin`（第一步）时禁掉空格与 EOT，否则模型可能直接输出空串或立即结束 |
| `SuppressTokens` | 禁掉给定 id 列表。默认 `suppress_tokens="-1"` 会被展开成 `tokenizer.non_speech_tokens`，再追加 `transcribe` / `translate` / `sot` / `sot_prev` / `sot_lm` 与 `no_speech` |
| `ApplyTimestampRules` | 见下 |

`ApplyTimestampRules` 四条规则（源码顺序）：

1. 永远禁 `<|notimestamps|>`；
2. **成对**：上一个 token 是时间戳时，若「倒数第二个也是时间戳」则禁掉所有时间戳（下一个必须是文本）；否则禁掉 `[:eot]` 的所有**文本** token（下一个必须还是时间戳）。注意 `penultimate_was_timestamp` 写成 `len(seq) < 2 or seq[-2] >= tb` —— **序列长度不足 2 时视为 True**，所以第一个时间戳之后必须接文本；
3. **单调不减**：禁掉 `tb : timestamp_last`，其中 `timestamp_last` 在「刚出的是时间戳且倒数第二不是」时取 `最后时间戳`，否则取 `最后时间戳 + 1`。取 `+1` 是为了保证每段长度非零，防止无限循环；
4. **首步**：禁掉所有文本 token，并按 `max_initial_timestamp` 限制上界。`max_initial_timestamp=1.0` 时 `max_initial_timestamp_index = round(1.0 / 0.02) = 50`，即首段时间戳不允许超过 `<|1.00|>`。

另有源码里那条「时间戳总概率超过最大文本 token 概率就强制出时间戳」的规则（`logits[k, :timestamp_begin] = -inf`），本 README 记录其存在，demo 未建模。

### 7. 时间精度

`input_stride = N_FRAMES / n_audio_ctx = 3000/1500 = 2`，`time_precision = input_stride * HOP_LENGTH / SAMPLE_RATE = 2*160/16000 = 0.02 s`，即每个输出 token 对应 **20 ms**，与 `TOKENS_PER_SECOND = 50` 一致。

## 对比：贪心 / beam / best-of-N

| 解码器 | 触发条件 | 参数 | 特点 |
| --- | --- | --- | --- |
| Greedy (t=0) | 默认 | — | 每步 argmax，最快，可能陷入重复 |
| Greedy + 采样 (t>0) | `temperature > 0` | `best_of` | 独立采样 N 条后由 ranker 挑最好 |
| BeamSearch (t=0) | 给了 `beam_size` | `beam_size` / `patience` | 保留 top-k 前缀，KV cache 需按 `source_indices` 重排 |

## 环境

Python 3.13（标准库 `math` / `zlib` / `random`）；Go 1.20+（标准库，含 `compress/zlib`）。

## 运行方式

```bash
cd 05-AI与机器学习/06-语音与多模态/03-Whisper解码与失败回退
python selfcheck_whisper.py     # 51 条断言实跑
go run whisper_decode.go selfcheck_whisper.go
```

## 关键代码

```python
if (no_speech_threshold is not None
        and decode_result.no_speech_prob > no_speech_threshold
        and logprob_threshold is not None
        and decode_result.avg_logprob < logprob_threshold):
    needs_fallback = False  # silence
```

这一行是整套回退里最容易读错的地方：它是**把前面判定的失败撤销**，而不是新增一个失败条件。

## 性能边界

- 最坏情况每个窗口要跑 **6 次**完整解码（6 档温度全失败），成本是贪心的 6 倍；`best_of`/`beam_size` 再乘上 N。
- `compression_ratio` 每次都对整段文本跑一次 zlib，文本很长时不是免费的操作。
- `BeamSearchDecoder` 每步对每个 beam 取 `topk(beam_size + 1)`，并调用 `rearrange_kv_cache` 重排 KV cache —— 重排是真拷贝，beam 越大开销越高。
- 阈值 `2.4` / `-1.0` / `0.6` 是 OpenAI 在论文附录里调优出来的经验值，换语言或换域一般要重调。

## 注意事项与常见坑

1. **静音豁免是「与」不是「或」**：必须 `no_speech_prob > 0.6` **且** `avg_logprob < -1.0`，只满足前者不豁免（本 demo `B7`）。
2. **`avg_logprob` 分母是 `len+1`**，漏掉 +1 会让短句偏乐观。
3. **`beam_size` 与 `best_of` 互斥**，同时给会直接抛错；`patience` 只在给了 `beam_size` 时有意义（`patience requires beam_size to be given`）。
4. **`round()` 的舍入在 Python 与 Go 不同**（银行家舍入 vs 远离零），`beam_size × patience` 恰好是 `.5` 时会差 1。
5. **`SuppressBlank` 只在第一步生效**，写成每步都禁会把正常空格一并干掉。
6. **`penultimate_was_timestamp` 在 `len(seq) < 2` 时为 True**，这决定了「第一个时间戳之后必须接文本」，不要按字面理解成「时间戳必须两个两个出现」。
7. `condition_on_previous_text=True` 时，若上一窗口用了 `temperature > 0.5`，源码**不会**把它的输出当作下一窗口的 prompt（`do not feed the prompt tokens if a high temperature was used`），避免把失败输出扩散下去。

## 参考资料

- OpenAI Whisper 官方实现 —— `whisper/decoding.py`（`DecodingOptions` / `DecodingResult` / `GreedyDecoder` / `BeamSearchDecoder` / `MaximumLikelihoodRanker` / `SuppressBlank` / `SuppressTokens` / `ApplyTimestampRules` / `DecodingTask`）：<https://github.com/openai/whisper/blob/main/whisper/decoding.py>
- 同上 —— `whisper/transcribe.py`（`transcribe()` 的默认参数与 `decode_with_fallback`）：<https://github.com/openai/whisper/blob/main/whisper/transcribe.py>
- 同上 —— `whisper/utils.py`（`compression_ratio`）：<https://github.com/openai/whisper/blob/main/whisper/utils.py>
- 同上 —— `whisper/audio.py`（`input_stride` / `time_precision` / `N_FRAMES`）。
- A. Radford et al. *Robust Speech Recognition via Large-Scale Weak Supervision*（Whisper 论文；阈值经验值的出处）。
- 本轮实际读取走 jsDelivr：`cdn.jsdelivr.net/gh/openai/whisper@main/whisper/{decoding,transcribe,utils,audio}.py`

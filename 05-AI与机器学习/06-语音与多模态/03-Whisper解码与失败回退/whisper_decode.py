"""Whisper 解码策略与温度回退（模型层）

全部照抄 OpenAI Whisper 官方实现（jsDelivr 取 main 分支原文）：

`whisper/decoding.py`
- DecodingOptions 默认值：temperature=0.0、best_of=None、beam_size=None、
  patience=None、length_penalty=None、suppress_tokens="-1"、suppress_blank=True、
  without_timestamps=False、max_initial_timestamp=1.0、fp16=True
- GreedyDecoder：t==0 走 argmax，t>0 走 Categorical(logits/T)；
  sum_logprobs += cur * (tokens[:,-1] != eot)；已完成的行继续填 eot；
  completed = 全部行末位是 eot；finalize 补一个 eot
- BeamSearchDecoder：patience or 1.0；max_candidates = round(beam_size*patience)；
  每步对每个 beam 取 topk(beam_size+1) 展开；completed = 每个音频的
  finished 数 >= max_candidates；finalize 在 finished < beam_size 时补未完成的
- MaximumLikelihoodRanker：length_penalty 为 None 用长度归一，否则用
  Google NMT 的 ((5+length)/6) ** length_penalty
- SuppressBlank / SuppressTokens / ApplyTimestampRules 三个 LogitFilter
- avg_logprob = sum_logprob / (len(tokens) + 1)

`whisper/transcribe.py`
- 默认温度序列 (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
- compression_ratio_threshold=2.4、logprob_threshold=-1.0、no_speech_threshold=0.6
- decode_with_fallback：t>0 时禁用 beam_size/patience，t==0 时禁用 best_of；
  compression_ratio 超阈值或 avg_logprob 低于阈值 → 换下一个温度；
  **但 no_speech_prob 超阈值且 avg_logprob 低于阈值时判定为静音，不再回退**
- input_stride = N_FRAMES / n_audio_ctx；time_precision = input_stride*HOP/SR

`whisper/utils.py`
- compression_ratio = len(text.encode("utf-8")) / len(zlib.compress(...))
"""

import math
import zlib
import random

NEG = -math.inf

# ---- 一个够用的假词表（把官方 tokenizer 的关键哨兵位置显式化） ----
A, B, C = 0, 1, 2
EOT = 3
NO_TIMESTAMPS = 4
NO_SPEECH = 5
SOT = 6
TIMESTAMP_BEGIN = 10          # <|0.00|>
TIMESTAMP_END = 10 + 1500


# ---- utils.py: compression_ratio ----

def compression_ratio(text):
    b = text.encode("utf-8")
    return len(b) / len(zlib.compress(b))


# ---- decoding.py: SequenceRanker ----

class MaximumLikelihoodRanker:
    """length_penalty 为 None → 长度归一；否则 Google NMT 的 ((5+L)/6)**α。"""

    def __init__(self, length_penalty=None):
        self.length_penalty = length_penalty

    def score(self, logprob, length):
        if self.length_penalty is None:
            penalty = length
        else:
            penalty = ((5 + length) / 6) ** self.length_penalty
        return logprob / penalty

    def rank(self, seqs, sum_logprobs):
        scores = [self.score(lp, len(t)) for t, lp in zip(seqs, sum_logprobs)]
        return scores.index(max(scores))


# ---- decoding.py: GreedyDecoder ----

class GreedyDecoder:
    def __init__(self, temperature, eot=EOT, rng=None):
        self.temperature = temperature
        self.eot = eot
        self.rng = rng or random.Random(0)

    def update(self, tokens, logits, sum_logprobs):
        if self.temperature == 0:
            nxt = max(range(len(logits)), key=lambda k: logits[k])
        else:
            scaled = [v / self.temperature for v in logits]
            nxt = categorical_sample(scaled, self.rng)
        lp = log_softmax(logits)[nxt]
        if tokens[-1] != self.eot:
            sum_logprobs += lp
        if tokens[-1] == self.eot:
            nxt = self.eot
        tokens = tokens + [nxt]
        completed = (tokens[-1] == self.eot)
        return tokens, sum_logprobs, completed

    def finalize(self, tokens, sum_logprobs):
        return tokens + [self.eot], sum_logprobs


def categorical_sample(logits, rng):
    m = max(logits)
    w = [math.exp(v - m) for v in logits]
    s = sum(w)
    r = rng.random() * s
    acc = 0.0
    for i, v in enumerate(w):
        acc += v
        if r <= acc:
            return i
    return len(w) - 1


def log_softmax(logits):
    m = max(logits)
    z = m + math.log(sum(math.exp(v - m) for v in logits))
    return [v - z for v in logits]


# ---- decoding.py: LogitFilter ----

class SuppressBlank:
    """sample_begin 那一步禁止空格与 eot（否则会直接结束或输出空串）。"""

    def apply(self, logits, tokens, sample_begin, blank_token, eot):
        if len(tokens) == sample_begin:
            logits = list(logits)
            logits[blank_token] = NEG
            logits[eot] = NEG
        return logits


class SuppressTokens:
    def __init__(self, tokens):
        self.tokens = list(tokens)

    def apply(self, logits, tokens, *a):
        logits = list(logits)
        for t in self.tokens:
            logits[t] = NEG
        return logits


class ApplyTimestampRules:
    """时间戳必须成对出现、且单调不减；首步必须是时间戳。"""

    def __init__(self, sample_begin, max_initial_timestamp_index=None):
        self.sample_begin = sample_begin
        self.max_initial_timestamp_index = max_initial_timestamp_index

    def apply(self, logits, tokens, sample_begin=None,
              no_timestamps=NO_TIMESTAMPS, eot=EOT, tb=TIMESTAMP_BEGIN):
        sample_begin = self.sample_begin if sample_begin is None else sample_begin
        logits = list(logits)
        if no_timestamps is not None:
            logits[no_timestamps] = NEG
        seq = tokens[sample_begin:]
        last_ts = len(seq) >= 1 and seq[-1] >= tb
        penult_ts = len(seq) < 2 or seq[-2] >= tb
        if last_ts:
            if penult_ts:
                for k in range(tb, len(logits)):
                    logits[k] = NEG          # 连着两个时间戳 → 下一个必须是文本
            else:
                for k in range(eot):
                    logits[k] = NEG          # 时间戳只出一个 → 下一个还得是时间戳
        tss = [t for t in seq if t >= tb]
        if tss:
            timestamp_last = tss[-1] if (last_ts and not penult_ts) else tss[-1] + 1
            for k in range(tb, min(timestamp_last, len(logits))):
                logits[k] = NEG              # 时间戳不许回退；也保证段长非零
        if len(tokens) == sample_begin:
            for k in range(tb):
                logits[k] = NEG              # 首步必须是时间戳
            if self.max_initial_timestamp_index is not None:
                last_allowed = tb + self.max_initial_timestamp_index
                for k in range(last_allowed + 1, len(logits)):
                    logits[k] = NEG
        return logits


# ---- decoding.py: BeamSearchDecoder（单条音频，去掉 tensor 外壳） ----

class BeamSearchDecoder:
    def __init__(self, beam_size, eot=EOT, patience=None):
        self.beam_size = beam_size
        self.eot = eot
        self.patience = patience or 1.0
        self.max_candidates = round(beam_size * self.patience)
        self.finished = None

    def reset(self):
        self.finished = None

    def update(self, beams, logits_per_beam):
        """beams: [(tokens, sum_logprob)]；返回新的 beams 与 completed。"""
        if self.finished is None:
            self.finished = {}
        scores, sources = {}, {}
        for idx, (tokens, slp) in enumerate(beams):
            lp = log_softmax(logits_per_beam[idx])
            top = sorted(range(len(lp)), key=lambda k: -lp[k])[:self.beam_size + 1]
            for k in top:
                seq = tuple(tokens) + (k,)
                if seq in scores:
                    continue
                scores[seq] = slp + lp[k]
                sources[seq] = idx
        nxt, new_finished = [], {}
        saved = 0
        for seq in sorted(scores, key=scores.get, reverse=True):
            if seq[-1] == self.eot:
                new_finished[seq] = scores[seq]
            else:
                nxt.append((list(seq), scores[seq]))
                saved += 1
                if saved == self.beam_size:
                    break
        for seq in sorted(new_finished, key=new_finished.get, reverse=True):
            if len(self.finished) >= self.max_candidates:
                break
            self.finished[seq] = new_finished[seq]
        completed = len(self.finished) >= self.max_candidates
        return nxt, completed

    def finalize(self):
        cands = dict(self.finished)
        return list(cands.keys()), list(cands.values())


# ---- transcribe.py: decode_with_fallback ----

def decode_with_fallback(decode_fn, temperatures=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
                         compression_ratio_threshold=2.4,
                         logprob_threshold=-1.0,
                         no_speech_threshold=0.6):
    """返回 (result, 实际使用的温度, 是否发生过回退)。"""
    result = None
    fell_back = False
    for i, t in enumerate(temperatures):
        result = decode_fn(t)
        needs = False
        if (compression_ratio_threshold is not None
                and result["compression_ratio"] > compression_ratio_threshold):
            needs = True
        if (logprob_threshold is not None
                and result["avg_logprob"] < logprob_threshold):
            needs = True
        if (no_speech_threshold is not None
                and result["no_speech_prob"] > no_speech_threshold
                and logprob_threshold is not None
                and result["avg_logprob"] < logprob_threshold):
            needs = False
        if not needs:
            return result, t, i > 0
        fell_back = True
    return result, temperatures[-1], fell_back


def avg_logprob_of(sum_logprob, n_tokens):
    return sum_logprob / (n_tokens + 1)

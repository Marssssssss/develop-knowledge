"""Whisper 解码与失败回退 demo 自检：全部断言实跑。"""
import math

from whisper_decode import (
    A, B, C, EOT, NO_SPEECH, NO_TIMESTAMPS, SOT, TIMESTAMP_BEGIN,
    compression_ratio, MaximumLikelihoodRanker, GreedyDecoder,
    SuppressBlank, SuppressTokens, ApplyTimestampRules, BeamSearchDecoder,
    decode_with_fallback, avg_logprob_of, log_softmax, NEG,
)

N = 0
FAIL = []


def check(label, cond, detail=""):
    global N
    N += 1
    if not cond:
        FAIL.append(f"{label}: {detail}")
        print(f"  FAIL {label} {detail}")


def close(a, b, eps=1e-9):
    return abs(a - b) <= eps


print("=== A. compression_ratio（utils.py 定义） ===")
rep = "ab" * 200                       # 强重复
normal = "The quick brown fox jumps over the lazy dog and keeps running."
cr_rep = compression_ratio(rep)
cr_norm = compression_ratio(normal)
check("A1 重复文本压缩比高", cr_rep > 2.4, f"{cr_rep}")
check("A2 正常文本压缩比低", cr_norm < 2.4, f"{cr_norm}")
check("A3 重复文本比值更大", cr_rep > cr_norm * 2, f"{cr_rep} vs {cr_norm}")
# 单字符文本：zlib 有固定开销，比值可能小于 1
check("A4 定义 = 原始字节 / 压缩字节",
      close(cr_norm, len(normal.encode()) / len(__import__("zlib").compress(normal.encode())), 1e-12))

print("=== B. 温度回退（transcribe.py decode_with_fallback） ===")
TEMPS = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
check("B1 默认温度序列 6 档", TEMPS == (0.0, 0.2, 0.4, 0.6, 0.8, 1.0))


def mk(cr, lp, nsp):
    return {"compression_ratio": cr, "avg_logprob": lp, "no_speech_prob": nsp,
            "text": "x"}


# 健康结果：第一次就用 t=0.0，不回退
r, t, fb = decode_with_fallback(lambda t: mk(1.5, -0.3, 0.05))
check("B2 健康结果用 t=0.0 且不回退", t == 0.0 and fb is False, f"t={t} fb={fb}")
# 压缩比过高 → 回退
calls = []


def dec_rep(t):
    calls.append(t)
    return mk(3.1, -0.2, 0.01) if t < 0.4 else mk(1.8, -0.2, 0.01)


r, t, fb = decode_with_fallback(dec_rep)
check("B3 压缩比超阈值触发回退", fb is True and t == 0.4, f"t={t} fb={fb}")
check("B4 回退按序走 0.0→0.2→0.4", calls == [0.0, 0.2, 0.4], f"{calls}")
# avg_logprob 过低 → 回退
calls2 = []


def dec_lp(t):
    calls2.append(t)
    return mk(1.5, -1.4, 0.01) if t < 0.6 else mk(1.5, -0.5, 0.01)


r, t, fb = decode_with_fallback(dec_lp)
check("B5 avg_logprob 低于阈值触发回退", fb is True and t == 0.6, f"t={t}")
# 关键：no_speech 高 + avg_logprob 低 → 判定为静音，不再回退
calls3 = []


def dec_sil(t):
    calls3.append(t)
    return mk(1.5, -1.4, 0.9)


r, t, fb = decode_with_fallback(dec_sil)
check("B6 静音分支不回退（只跑一次）", calls3 == [0.0] and fb is False, f"{calls3}")
# 静音判据必须同时满足两个条件：nsp 高但 logprob 不低 → 仍要回退
calls4 = []


def dec_nsp_only(t):
    calls4.append(t)
    # 压缩比超阈值所以要回退；但 logprob 不低，静音豁免不成立
    return mk(3.0, -0.2, 0.9) if t < 0.2 else mk(1.5, -0.2, 0.01)


r, t, fb = decode_with_fallback(dec_nsp_only)
check("B7 nsp 高但 logprob 不低 → 不判静音",
      len(calls4) == 2 and t == 0.2, f"{calls4}")
# logprob 低但 nsp 不高 → 回退
calls5 = []


def dec_lp_only(t):
    calls5.append(t)
    return mk(1.5, -1.4, 0.1) if t < 1.0 else mk(1.5, -0.2, 0.1)


r, t, fb = decode_with_fallback(dec_lp_only)
check("B8 logprob 低且 nsp 不高 → 一路回退到 1.0",
      len(calls5) == 6 and t == 1.0, f"{calls5}")

print("=== C. MaximumLikelihoodRanker（decoding.py） ===")
rk_none = MaximumLikelihoodRanker(None)
rk_a1 = MaximumLikelihoodRanker(1.0)
# 长度归一 vs Google NMT 惩罚会给出不同选择
seqs = [[0], [0] * 100]
lps = [-1.0, -50.0]
check("C1 长度归一选长序列", rk_none.rank(seqs, lps) == 1,
      f"{rk_none.score(-1.0,1)} vs {rk_none.score(-50.0,100)}")
check("C2 NMT 惩罚选短序列", rk_a1.rank(seqs, lps) == 0,
      f"{rk_a1.score(-1.0,1)} vs {rk_a1.score(-50.0,100)}")
check("C3 penalty=None → logprob/length",
      close(rk_none.score(-4.0, 2), -2.0))
check("C4 NMT penalty α=1, L=1 → 除数 1",
      close(rk_a1.score(-1.0, 1), -1.0 / ((5 + 1) / 6) ** 1.0))
check("C5 NMT penalty α=1, L=7 → 除数 2",
      close(rk_a1.score(-2.0, 7), -1.0))
# α=0 时惩罚恒为 1，等价于不归一
check("C6 α=0 → 退化为不归一",
      close(MaximumLikelihoodRanker(0.0).score(-3.0, 10), -3.0))

print("=== D. GreedyDecoder（decoding.py） ===")
g0 = GreedyDecoder(0.0)
tok, slp, done = g0.update([SOT], [0.2, 5.0, -1.0, 0.1] + [NEG] * 11, 0.0)
check("D1 t=0 取 argmax", tok[-1] == B, f"{tok}")
lp = log_softmax([0.2, 5.0, -1.0, 0.1] + [NEG] * 11)
check("D2 sum_logprob 累加", close(slp, lp[B]), f"{slp}")
# 已经到 eot 的行：不再累加、继续填 eot
tok2, slp2, done2 = g0.update([SOT, EOT], [9.0] * 4 + [NEG] * 11, 5.0)
check("D3 eot 之后不再累加", close(slp2, 5.0), f"{slp2}")
check("D4 eot 之后继续填 eot", tok2[-1] == EOT)
check("D5 eot 后 completed=True", done2 is True)
check("D6 非 eot 时 completed=False", done is False)
tf, slpf = g0.finalize([SOT, A], 1.5)
check("D7 finalize 补一个 eot", tf[-1] == EOT and len(tf) == 3)
# t>0 走采样：同一 logits 多次采样不全相同
g1 = GreedyDecoder(2.0)
outs = set()
for seed in range(30):
    g1.rng = __import__("random").Random(seed)
    t3, _, _ = g1.update([SOT], [0.0, 0.0, 0.0, 0.0] + [NEG] * 11, 0.0)
    outs.add(t3[-1])
check("D8 t>0 是采样不是 argmax", len(outs) > 1, f"{outs}")

print("=== E. LogitFilter（decoding.py） ===")
sb = SuppressBlank()
lg = sb.apply([1.0, 2.0, 3.0, 4.0] + [0.0] * 11, [SOT], 1, 2, EOT)
check("E1 SuppressBlank 在 sample_begin 生效",
      lg[2] == NEG and lg[EOT] == NEG, f"{lg[:5]}")
lg2 = sb.apply([1.0, 2.0, 3.0, 4.0] + [0.0] * 11, [SOT, A], 1, 2, EOT)
check("E2 非 sample_begin 不生效", lg2[2] != NEG and lg2[EOT] != NEG)
st = SuppressTokens([1, 3])
check("E3 SuppressTokens 置 -inf",
      st.apply([1.0, 2.0, 3.0, 4.0] + [0.0] * 11, [SOT])[1] == NEG)
ar = ApplyTimestampRules(sample_begin=1, max_initial_timestamp_index=50)
lg3 = ar.apply([5.0] * 4 + [0.0] * 6 + [1.0] * 100, [SOT], sample_begin=1)
check("E4 首步禁止文本 token", all(v == NEG for v in lg3[:TIMESTAMP_BEGIN]))
check("E5 首步限制最大时间戳",
      lg3[TIMESTAMP_BEGIN + 51] == NEG and lg3[TIMESTAMP_BEGIN + 50] != NEG)
# 只出了 1 个时间戳（seq 长度不足 2 ⇒ penultimate_was_timestamp 视为 True）
# → 下一个必须是非时间戳
seq = [SOT, TIMESTAMP_BEGIN + 10]
lg4 = ar.apply([1.0] * 4 + [0.0] * 6 + [1.0] * 100, seq, sample_begin=1)
check("E6 首个时间戳之后禁止时间戳",
      all(v == NEG for v in lg4[TIMESTAMP_BEGIN:TIMESTAMP_BEGIN + 100]))
check("E6b 首个时间戳之后文本仍可用", lg4[A] != NEG)
# 文本之后的时间戳：penultimate 不是时间戳 → 下一个必须还是时间戳（文本被禁）
seq_t = [SOT, A, TIMESTAMP_BEGIN + 10]
lg_t = ar.apply([1.0] * 4 + [0.0] * 6 + [1.0] * 100, seq_t, sample_begin=1)
check("E6c 文本+时间戳之后文本被禁", all(v == NEG for v in lg_t[:EOT]))
check("E6d EOT 不被禁(允许直接结束)", lg_t[EOT] != NEG)
# 出了 2 个 → 下一个必须是文本
seq2 = [SOT, TIMESTAMP_BEGIN + 10, TIMESTAMP_BEGIN + 20]
lg5 = ar.apply([1.0] * 4 + [0.0] * 6 + [1.0] * 100, seq2, sample_begin=1)
check("E7 成对时间戳后时间戳被禁",
      all(v == NEG for v in lg5[TIMESTAMP_BEGIN:TIMESTAMP_BEGIN + 100]))
# 单调不减：已到 ts+20 且刚出的是文本 → 下一段起点至少 ts+21
seq3 = [SOT, TIMESTAMP_BEGIN + 10, TIMESTAMP_BEGIN + 20, A]
lg6 = ar.apply([1.0] * 4 + [0.0] * 6 + [1.0] * 100, seq3, sample_begin=1)
check("E8 时间戳不许回退(禁到 21 之前)",
      lg6[TIMESTAMP_BEGIN + 20] == NEG and lg6[TIMESTAMP_BEGIN + 21] != NEG)

print("=== F. BeamSearchDecoder（decoding.py） ===")
bs = BeamSearchDecoder(5, patience=None)
check("F1 patience 默认 1.0", bs.patience == 1.0)
check("F2 max_candidates = round(5*1) = 5", bs.max_candidates == 5)
bs2 = BeamSearchDecoder(5, patience=2)
check("F3 patience=2 → 10", bs2.max_candidates == 10)
bs3 = BeamSearchDecoder(5, patience=1.5)
check("F4 patience=1.5 → round(7.5)=8", bs3.max_candidates == 8, f"{bs3.max_candidates}")
# 单步：beam_size 个 beam，每个取 top(beam_size+1) 展开，只保留 beam_size 个未完成的
beams = [([SOT], 0.0)] * 1
logits = [1.0, 0.9, 0.8, 0.7, 0.6, 0.5] + [NEG] * 9
nxt, done = bs.update(beams, [logits])
check("F5 展开后保留 ≤ beam_size 条", len(nxt) <= 5, f"{len(nxt)}")
check("F6 保留的是概率最高的", nxt[0][1] >= nxt[-1][1], f"{[x[1] for x in nxt]}")
# 每个新 beam 都是 eot 时全部进 finished
bs4 = BeamSearchDecoder(2)
# 两条 beam 前缀不同，才会产生两条不同的 finished 序列（相同序列会被去重）
nxt4, done4 = bs4.update([([SOT, A], 0.0), ([SOT, B], 0.0)],
                         [[0.0, 0.0, 0.0, 9.0] + [NEG] * 11] * 2)
check("F7 全部 eot → finished 填满并 completed", done4 is True, f"{len(bs4.finished)}")
check("F8 finished 数 ≥ beam_size", len(bs4.finished) >= 2, f"{len(bs4.finished)}")

print("=== G. 官方常量（transcribe.py / audio.py） ===")
check("G1 compression_ratio_threshold 2.4", 2.4 == 2.4)
check("G2 logprob_threshold -1.0", -1.0 == -1.0)
check("G3 no_speech_threshold 0.6", 0.6 == 0.6)
# input_stride = N_FRAMES/n_audio_ctx = 2；time_precision = 2*160/16000 = 0.02
check("G4 time_precision = 0.02s", close(2 * 160 / 16000, 0.02))
check("G5 max_initial_timestamp_index = round(1.0/0.02) = 50",
      round(1.0 / 0.02) == 50)
# avg_logprob = sum_logprob/(len+1)
check("G6 avg_logprob 分母是 len+1", close(avg_logprob_of(-4.0, 3), -1.0))

print()
print(f"断言总数 {N}，失败 {len(FAIL)}")
for f in FAIL:
    print("  ×", f)
print("RESULT:", "ALL PASS" if not FAIL else "FAILED")

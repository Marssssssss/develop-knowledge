#!/usr/bin/env python3
"""信息熵字段边界检测：纵向列熵(主) + 横向滑窗熵(辅) + 序列启发式。

用法: python entropy_fields.py
模型: BinaryInferno(NDSS 2023) 三探测器简化版——熵差>1bit 边界规则按原文用于
      纵向熵剖面(相邻字节位置跨报文熵), 滑窗熵作为长流上的补充手段。
自检: 内置 assert, 失败即非零退出。
"""
import math
import random
from collections import Counter

GAP = None  # 报文越界哨兵

# ---------- 香农熵 ----------
def shannon(xs):
    n = len(xs)
    if n == 0:
        return 0.0
    h = 0.0
    for c in Counter(xs).values():
        p = c / n
        h -= p * math.log2(p)
    return h

# ---------- 纵向: 跨报文按列熵 (BinaryInferno 主战场) ----------
def col_values(msgs, k):
    return [m[k] if k < len(m) else GAP for m in msgs]

def col_entropies(msgs):
    L = max(len(m) for m in msgs)
    return [shannon(col_values(msgs, k)) for k in range(L)]

def classify_columns(msgs, thresh=1.0):
    """列分类 const/low/high + 熵差>1bit 边界(BinaryInferno 规则)。"""
    H = col_entropies(msgs)
    kinds, bounds = [], []
    for k, h in enumerate(H):
        kinds.append('const' if h == 0.0 else ('low' if h < 2.0 else 'high'))
        if k > 0 and abs(H[k] - H[k - 1]) > thresh:
            bounds.append(k)
    return H, kinds, bounds

# ---------- 横向: 单报文滑窗熵 (长流补充手段) ----------
def entropy_boundaries_stream(msg, w=64, thresh=1.0):
    """相邻窗口熵差>thresh 的位置(边界定位精度 ±w/2; 窗口熵上限 log2(w))。"""
    L = len(msg)
    out = []
    for i in range(w, L - w + 1):
        if abs(shannon(msg[i:i + w]) - shannon(msg[i - w:i])) > thresh:
            out.append(i)
    return out

# ---------- 序列启发式 ----------
def monotonic_prob(msgs, k):
    """第 k 列取值跨报文单调递增概率 (BinaryInferno sequenceHeur)。"""
    vs = [m[k] for m in msgs if k < len(m)]
    if len(vs) < 3:
        return 0.0
    inc = sum(1 for a, b in zip(vs, vs[1:]) if b > a)
    return inc / (len(vs) - 1)

# ---------- 字段段汇总 ----------
def segments(kinds):
    segs, start = [], 0
    for k in range(1, len(kinds) + 1):
        if k == len(kinds) or kinds[k] != kinds[k - 1]:
            segs.append((kinds[start], start, k - 1))
            start = k
    return segs

# ---------- 自检 ----------
def self_test():
    assert shannon(b'AAAA') == 0.0
    assert abs(shannon(b'AB') - 1.0) < 1e-9
    assert abs(shannon(b'AABBCCDD') - 2.0) < 1e-9        # 4 种均匀符号 → 2 bit
    assert shannon(bytes(range(256))) < 8.0 + 1e-9       # 均匀 256 值 → 8 bit
    # 常量列熵 0, 随机列高
    rng = random.Random(0)
    msgs = [bytes([0x01, rng.randrange(256)]) for _ in range(16)]
    H, kinds, _ = classify_columns(msgs)
    assert kinds[0] == 'const' and kinds[1] == 'high'
    assert H[0] == 0.0 and H[1] > 3.0
    # 单调启发: 递增 P=1, 递减 P=0
    inc = [bytes([i]) + b'xxxx' for i in range(1, 9)]
    assert monotonic_prob(inc, 0) == 1.0
    dec = [bytes([9 - i]) + b'xxxx' for i in range(1, 9)]
    assert monotonic_prob(dec, 0) == 0.0

# ---------- 演示 ----------
def demo():
    rng = random.Random(7)
    # 合成协议: magic(5) ver(1) type(1: 3 枚举) seq(2: 单调递增)
    #          nonce(4: 随机) body_len(1) body(6~9 变长) crc(2: 随机)
    def mk(i, t, body):
        seq = i + 1
        nonce = bytes(rng.randrange(256) for _ in range(4))
        crc = bytes(rng.randrange(256) for _ in range(2))
        return (b'PROTO' + bytes([0x02, t]) + bytes([seq & 0xff, (seq >> 8) & 0xff])
                + nonce + bytes([len(body)]) + body + crc)
    msgs = [mk(i, 1 + i % 3, bytes(rng.randrange(0x20, 0x7f)
            for _ in range(6 + i % 4))) for i in range(24)]
    L = max(len(m) for m in msgs)

    print('== 纵向列熵 (N=%d 条报文, 最长 %d 字节) ==' % (len(msgs), L))
    H, kinds, bounds = classify_columns(msgs)
    print('  pos :', ' '.join('%2d' % k for k in range(L)))
    print('  H   :', ' '.join('%4.1f' % h for h in H))
    print('  kind:', ' '.join('%4s' % c[:4] for c in kinds))
    print('  熵差>1bit 边界 @', bounds)
    print()
    print('== 序列启发式 (单调递增概率≥0.9) ==')
    for k in range(L):
        p = monotonic_prob(msgs, k)
        if p >= 0.9:
            print('  col %d: P(monotonic)=%.2f → 计数器/序列号候选' % (k, p))
    print()
    print('== 横向滑窗熵边界 (长流: 明文头+随机体, w=64) ==')
    hdr = b'GET /index.html HTTP/1.1\r\nHost: example.com\r\nUser-Agent: demo-agent/1.0\r\n' * 2
    body = bytes(rng.randrange(256) for _ in range(192))
    hits = entropy_boundaries_stream(hdr + body, w=64)
    print('  流长 %d (明文头 %d + 随机体 %d)' % (len(hdr) + len(body), len(hdr), len(body)))
    print('  命中位置 %s… (真实边界 %d, 定位精度 ±w/2=±32)' % (hits[:6], len(hdr)))
    print()
    print('== 字段段汇总 (纵向分类) ==')
    for kind, st, en in segments(kinds):
        print('  %-5s [%2d..%2d] len=%d' % (kind, st, en, en - st + 1))
    print('  (注: 尾部列被 GAP 占据 → 熵被压低, 即"变长字段位移污染", 见 README 坑1)')

if __name__ == '__main__':
    self_test()
    demo()

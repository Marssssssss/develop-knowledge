#!/usr/bin/env python3
"""报文序列对齐：Needleman-Wunsch 全局对齐 + Smith-Waterman 局部对齐 + consensus 字段切分。

用法: python nw_sw.py
自检: 内置 assert, 任何一条失败即非零退出。
打分: match=3 / mismatch=-1 / gap=-4 (mismatch 远低于 gap: 变长字段宁可错位对齐也不开洞)
"""
import random

M, S, D = 3, -1, -4  # match / mismatch / gap

# ---------- NW 全局对齐 ----------
def nw_align(a: bytes, b: bytes, m=M, s=S, d=D):
    la, lb = len(a), len(b)
    F = [[0] * (lb + 1) for _ in range(la + 1)]
    P = [[''] * (lb + 1) for _ in range(la + 1)]
    for i in range(1, la + 1):
        F[i][0], P[i][0] = F[i - 1][0] + d, 'U'
    for j in range(1, lb + 1):
        F[0][j], P[0][j] = F[0][j - 1] + d, 'L'
    for i in range(1, la + 1):
        ai = a[i - 1]
        row, prev = F[i], F[i - 1]
        for j in range(1, lb + 1):
            best, ptr = prev[j - 1] + (m if ai == b[j - 1] else s), 'D'
            if prev[j] + d > best:
                best, ptr = prev[j] + d, 'U'
            if row[j - 1] + d > best:
                best, ptr = row[j - 1] + d, 'L'
            row[j], P[i][j] = best, ptr
    # 回溯 (优先级 D > U > L 固定, 保证可复现)
    i, j, ra, rb = la, lb, [], []
    while i > 0 or j > 0:
        p = P[i][j]
        if p == 'D':
            ra.append(a[i - 1]); rb.append(b[j - 1]); i -= 1; j -= 1
        elif p == 'U':
            ra.append(a[i - 1]); rb.append(None); i -= 1
        else:
            ra.append(None); rb.append(b[j - 1]); j -= 1
    return F[la][lb], ra[::-1], rb[::-1]

# ---------- SW 局部对齐 ----------
def sw_align(a: bytes, b: bytes, m=M, s=S, d=D):
    la, lb = len(a), len(b)
    F = [[0] * (lb + 1) for _ in range(la + 1)]
    P = [[''] * (lb + 1) for _ in range(la + 1)]
    bi = bj = bs = 0
    for i in range(1, la + 1):
        ai = a[i - 1]
        row, prev = F[i], F[i - 1]
        for j in range(1, lb + 1):
            best, ptr = 0, ''
            v = prev[j - 1] + (m if ai == b[j - 1] else s)
            if v > best:
                best, ptr = v, 'D'
            v = prev[j] + d
            if v > best:
                best, ptr = v, 'U'
            v = row[j - 1] + d
            if v > best:
                best, ptr = v, 'L'
            row[j], P[i][j] = best, ptr
            if best > bs:
                bs, bi, bj = best, i, j
    # 从全矩阵最大值回溯到 0
    i, j, ra, rb = bi, bj, [], []
    while i > 0 and j > 0 and F[i][j] > 0:
        p = P[i][j]
        if p == 'D':
            ra.append(a[i - 1]); rb.append(b[j - 1]); i -= 1; j -= 1
        elif p == 'U':
            ra.append(a[i - 1]); rb.append(None); i -= 1
        else:
            ra.append(None); rb.append(b[j - 1]); j -= 1
    return bs, ra[::-1], rb[::-1]

# ---------- consensus 字段切分 (star alignment: 全部对第一条) ----------
def consensus_columns(msgs):
    """返回逐列取值集合列表。插入列(相对参考多出的字节)合并为一个变量列。"""
    ref = msgs[0]
    base = [{ref[k]} for k in range(len(ref))]          # ref 每个位置一列
    inserts = [set() for _ in range(len(ref) + 1)]      # 位置 k 之前的插入字节
    for msg in msgs[1:]:
        _, ra, rb = nw_align(ref, msg)
        k = 0
        for ca, cb in zip(ra, rb):
            if ca is None:            # msg 比 ref 多出的字节 → 插入列
                inserts[k].add(cb)
            else:
                base[k].add(cb)       # cb 可能为 None(msg 缺失) → gap 也是取值
                k += 1
    cols = []
    for k in range(len(ref) + 1):
        if inserts[k]:
            cols.append(inserts[k])   # 合并为单一变量列
        if k < len(ref):
            cols.append(base[k])
    return cols

def field_segments(cols):
    """常量 run / 变量 run → 字段边界假设 [(kind, start, end)]"""
    segs, start, kind = [], 0, None
    def close(end):
        if kind is not None:
            segs.append((kind, start, end))
    for k, col in enumerate(cols):
        k_cur = 'const' if len(col) == 1 and None not in col else 'var'
        if kind is None:
            kind, start = k_cur, k
        elif k_cur != kind:
            close(k - 1); kind, start = k_cur, k
    close(len(cols) - 1)
    return segs

# ---------- 自检 ----------
def self_test():
    # 1) 相同序列满分
    s, ra, rb = nw_align(b'ABCD', b'ABCD')
    assert s == 4 * M and ra == rb
    # 2) NW 全局: 3 match*3 - 1 gap*4 = 5, 短侧补 None
    s, ra, rb = nw_align(b'ABCD', b'ABC')
    assert s == 5 and ra == [65, 66, 67, 68] and rb == [65, 66, 67, None]
    # 3) SW 局部: 两端垃圾不罚, 只留公共片段
    s, ra, rb = sw_align(b'XXABCDYY', b'QQABCDZZ')
    assert s == 4 * M and bytes(x for x in ra if x is not None) == b'ABCD'
    # 4) SW: "hello"/"shell" 公共子串 "hell" 全 match
    assert sw_align(b'hello', b'shell')[0] == 4 * M
    # 5) consensus: 常量头/尾 + 变量中段
    cols = consensus_columns([b'AAxxAA', b'AAyyAA', b'AAzzAA'])
    kinds = ['const' if len(c) == 1 and None not in c else 'var' for c in cols]
    assert kinds == ['const'] * 2 + ['var'] * 2 + ['const'] * 2

# ---------- 演示 ----------
def demo():
    random.seed(42)
    # 合成协议: magic(2) ver(1) type(1) len(2,小端) payload(len) crc(2)
    def mk(t, payload):
        l = len(payload)
        return b'ZM' + b'\x01' + bytes([t]) + bytes([l & 0xff, l >> 8]) + payload + b'\x12\x34'
    msgs = [mk(0x10, b'PING'), mk(0x10, b'HELLO-WORLD'),
            mk(0x11, b'AUTH'), mk(0x10, b'X'), mk(0x11, b'READ-ME-PLEASE-OK')]
    print('== 全局对齐示例 (短 payload vs 长 payload) ==')
    s, ra, rb = nw_align(msgs[0], msgs[1])
    fmt = lambda xs: ''.join(chr(x) if x is not None and 32 <= x < 127 else
                             ('.' if x is not None else '-') for x in xs)
    print(' a:', fmt(ra))
    print(' b:', fmt(rb))
    print(' score =', s)
    print()
    print('== 局部对齐示例 (只保留最高分公共片段) ==')
    s2, ra2, rb2 = sw_align(msgs[0], msgs[4])
    print(' a:', fmt(ra2)); print(' b:', fmt(rb2)); print(' score =', s2)
    print()
    cols = consensus_columns(msgs)
    print('== consensus 逐列分类 (C=常量 V=变量, 含插入列) ==')
    line = ''.join('C' if len(c) == 1 and None not in c else 'V' for c in cols)
    print(' ', line, ' (len=%d)' % len(line))
    print()
    print('== 字段段切分假设 ==')
    for kind, st, en in field_segments(cols):
        v = list(cols[st])
        shown = ('0x%02x' % v[0]) if len(v) == 1 and v[0] is not None else '{%d 种取值}' % len(v)
        print('  %-5s [%2d..%2d] len=%-2d %s' % (kind, st, en, en - st + 1, shown))

if __name__ == '__main__':
    self_test()
    demo()

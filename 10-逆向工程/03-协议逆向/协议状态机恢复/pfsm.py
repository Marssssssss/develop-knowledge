#!/usr/bin/env python3
"""协议状态机恢复：Veritas(ACNS 2011) P-PSM 骨架实现。

流水线: 消息类型标注(首token+方向) → 逐流转移计数(不跨流) → 概率转移+阈值剪枝 → PFSM。
用法: python pfsm.py   (内置 assert 自检)
"""
from collections import defaultdict

# ---------- 1. 消息类型标注 ----------
def label(msg):
    """(方向, 文本) → 状态标签。方向必须进标签(README 坑1)。"""
    direction, text = msg
    first = text.split(' ', 1)[0].upper()
    return (direction, first)

def label_flow(flow):
    return [label(m) for m in flow]

# ---------- 2. 转移计数 (逐流统计, 禁止跨流边界) ----------
def transitions(flows):
    """flows = [[(dir, text), ...], ...] → 嵌套计数 {src: {dst: count}}。"""
    C = defaultdict(lambda: defaultdict(int))
    for flow in flows:
        seq = label_flow(flow)
        for a, b in zip(seq, seq[1:]):
            C[a][b] += 1
    return C

# ---------- 3. 概率化 + 剪枝 ----------
def pfsm(C, p_min=0.05, c_min=3):
    """转移计数 → 剪枝后的边表 [(src, dst, count, prob)]。"""
    edges = []
    for s, row in C.items():
        tot = sum(row.values())
        for s2, c in row.items():
            if c >= c_min and c / tot >= p_min:
                edges.append((s, s2, c, c / tot))
    return sorted(edges, key=lambda e: (-e[3], e[0], e[1]))

def accepts(edges, seq):
    """流覆盖率回测: 每条相邻转移是否都在剪枝后的 PFSM 边集里。"""
    ok = {(s, d) for s, d, _, _ in edges}
    return all((a, b) in ok for a, b in zip(seq, seq[1:]))

# ---------- 4. (简化)状态合并 ----------
def merge_states(edges, merge_map):
    return [(merge_map.get(s, s), merge_map.get(d, d), c, p)
            for s, d, c, p in edges]

# ---------- 自检 ----------
def self_test():
    flows = [
        [('C', 'A'), ('S', 'B'), ('C', 'A')],
        [('C', 'A'), ('S', 'B'), ('C', 'C')],
    ]
    C = transitions(flows)
    assert C[('C', 'A')][('S', 'B')] == 2
    assert C[('S', 'B')][('C', 'A')] == 1
    # 跨流伪边: 流1结尾 ('C','A') 与 流2开头 ('C','A') 不得产生转移
    assert ('C', 'C') not in C[('C', 'A')]
    edges = pfsm(C, p_min=0.05, c_min=1)
    assert (('C', 'A'), ('S', 'B'), 2, 1.0) in edges
    # 剪枝: A→B 1 次, p=1/100=0.01 < 0.05 → 剪掉
    C2 = transitions([[('X', 'A'), ('X', 'B')]] + [[('X', 'A')] * 100])
    assert not any(s == ('X', 'A') and d == ('X', 'B') for s, d, _, _ in pfsm(C2))
    # 回测
    assert accepts(edges, [('C', 'A'), ('S', 'B'), ('C', 'A')])
    assert not accepts(edges, [('S', 'B'), ('C', 'Z')])

# ---------- 演示: 合成 SMTP 风格流量 ----------
def gen_smtp_flows(n_flows=30, seed=1):
    import random
    rng = random.Random(seed)
    flows = []
    for _ in range(n_flows):
        f = [('C', 'EHLO client.example'), ('S', '250-STARTTLS')]
        f.append(('C', 'MAIL FROM:<a@x>')); f.append(('S', '250 OK'))
        for _ in range(rng.randrange(1, 4)):            # 1~3 个收件人
            f.append(('C', 'RCPT TO:<b@y>'))
            f.append(('S', '250 OK'))
            if rng.random() < 0.2:                      # 偶发 550 拒收
                f.append(('C', 'RCPT TO:<bad@z>'))
                f.append(('S', '550 No such user'))
        f.append(('C', 'DATA')); f.append(('S', '354 Go ahead'))
        f.append(('C', 'body line 1')); f.append(('C', '.')); f.append(('S', '250 Accepted'))
        if rng.random() < 0.15:                         # 偶发重新 EHLO
            f.append(('C', 'EHLO again')); f.append(('S', '250-STARTTLS'))
        f.append(('C', 'QUIT')); f.append(('S', '221 Bye'))
        flows.append(f)
    return flows

def demo():
    flows = gen_smtp_flows()
    print('== 样本: 第一条流的消息类型序列 ==')
    print(' ', ' -> '.join('%s:%s' % (d, t) for d, t in label_flow(flows[0])))
    print()
    C = transitions(flows)
    edges = pfsm(C)
    print('== 剪枝后 PFSM 边表 (p>=0.05 且 count>=3, 共 %d 条边) ==' % len(edges))
    for s, d, c, p in edges:
        print('  %-16s -> %-16s  count=%-3d p=%.2f' % ('%s:%s' % s, '%s:%s' % d, c, p))
    print()
    # 流覆盖率回测 (Veritas 用类似指标衡量保真度)
    n_ok = sum(accepts(edges, label_flow(f)) for f in flows)
    print('== 流覆盖率回测: %d/%d = %.0f%% ==' % (n_ok, len(flows), n_ok / len(flows) * 100))
    print()
    # 状态合并示例: C:BODY 与 C:. 同属 DATA 阶段客户端内容 → 合并
    mmap = {('C', 'BODY'): ('C', 'DATA_BODY'), ('C', '.'): ('C', 'DATA_BODY')}
    region = [e for e in edges if ('C', 'BODY') in e[:2] or ('C', '.') in e[:2]]
    print('== 状态合并示例: DATA 阶段局部边表 ==')
    print('  合并前:')
    for s, d, c, p in region:
        print('    %-16s -> %-16s  p=%.2f' % ('%s:%s' % s, '%s:%s' % d, p))
    print('  合并后 (C:BODY, C:. → C:DATA_BODY):')
    seen = set()
    for s, d, c, p in merge_states(region, mmap):
        if (s, d) in seen:
            continue
        seen.add((s, d))
        print('    %-16s -> %-16s  p=%.2f' % ('%s:%s' % s, '%s:%s' % d, p))

if __name__ == '__main__':
    self_test()
    demo()

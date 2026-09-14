#!/usr/bin/env python3
"""Polyglot(CCS 2007) 污点分析启发式的轨迹驱动复刻。

四个启发式(忠实于论文参数):
  1. token 表 + 分隔符三步提取 (series>=3, 扩展>=4 次, 上限 4 字节)
  2. 关键字提取 (只为真的比较拼串)
  3. 方向字段检测 (间接访问地址由污点计算)
  4. 定长字段合并 (同一指令的污点位置合并)
输入是人工构造的迷你执行轨迹(不含污点引擎)。
用法: python polyglot_heuristics.py
"""
from collections import defaultdict

# ---------- 轨迹事件模型 ----------
class Trace:
    """一条消息的执行轨迹。

    compares: [(pos, token, result)]   位置 pos 的污点字节与常量 token 比较, result 为真假
    indirect: [(src_positions, access_pos)]
        目标地址由 src_positions 位置的污点计算而来的间接访问, 访问到 access_pos
    instrs:  [(positions)]             一条指令同时使用的污点位置(定长合并线索)
    """
    def __init__(self, compares=(), indirect=(), instrs=()):
        self.compares = list(compares)
        self.indirect = list(indirect)
        self.instrs = list(instrs)

# ---------- 1. token 表 (§5.1) ----------
def build_token_tables(trace):
    tokens_at_position = defaultdict(list)   # 列: 位置 → 比较过的 token
    token_series = defaultdict(set)          # 行: token → 出现过比较的位置集合
    true_at_position = defaultdict(list)     # 位置 → 为真的比较 token
    for pos, tok, result in trace.compares:
        tokens_at_position[pos].append(tok)
        token_series[tok].add(pos)
        if result:
            true_at_position[pos].append(tok)
    return tokens_at_position, token_series, true_at_position

def consecutive_runs(positions):
    runs, cur = [], []
    for p in sorted(positions):
        if cur and p == cur[-1] + 1:
            cur.append(p)
        else:
            if cur:
                runs.append(cur)
            cur = [p]
    if cur:
        runs.append(cur)
    return runs

# ---------- 2. 分隔符提取 (三步, §5.1) ----------
def extract_byte_separators(token_series, min_series=3):
    """步骤2: 单字节分隔符 = 与同一常量比较过的连续位置段(长度>=3)。

    "token 在 series 至少一个位置真实出现"的要求由调用方在构造 compares 时
    天然满足(为真的比较也在 token_series 里), 此处按论文保留 series 判定。
    """
    seps = []
    for tok, positions in token_series.items():
        for run in consecutive_runs(positions):
            if len(run) >= min_series:
                seps.append((tok, run))
    return seps

def extend_separators(seps, messages, min_appear=4, max_len=4):
    """步骤3: 前驱/后继一致性 → 多字节分隔符 (出现>=4 次, 长度<=4)。

    left/right 记录当前分隔符相对 tok 出现位置向两侧扩展的跨度;
    前驱检查的是"整个分隔符起点的前一字节", 防止对同一邻居反复扩展。
    """
    out = []
    for tok, ctx in seps:
        sep = bytes([tok])
        left = right = 0
        changed = True
        while changed and len(sep) < max_len:
            changed = False
            for side in (-1, 1):            # -1=查前驱 1=查后继
                neighbors = set()
                n_app = 0
                for msg in messages:
                    for i, b in enumerate(msg):
                        if b != tok:
                            continue
                        j = i + left - 1 if side == -1 else i + right + 1
                        if 0 <= j < len(msg):
                            neighbors.add(msg[j])
                            n_app += 1
                if n_app >= min_appear and len(neighbors) == 1:
                    nb = neighbors.pop()
                    if side == -1:
                        left -= 1
                        sep = bytes([nb]) + sep
                    else:
                        right += 1
                        sep = sep + bytes([nb])
                    changed = True
        out.append((sep, ctx))
    return out

# ---------- 3. 关键字提取 (§5.2) ----------
def extract_keywords(true_at_position, L, seps):
    """按位置升序, 为真的比较拼串; 无为真比较或撞分隔符则断开。"""
    sep_single = set()
    for s, _ in seps:
        sep_single.update(s)
    kws, cur = [], []
    for pos in range(L):
        toks = true_at_position.get(pos, [])
        if toks and toks[0] not in sep_single:
            cur.append((pos, toks[0]))
        else:
            if cur:
                kws.append(cur); cur = []
    if cur:
        kws.append(cur)
    return [(''.join(chr(t) if 32 <= t < 127 else '\\x%02x' % t for _, t in kw),
             kw[0][0], kw[-1][0]) for kw in kws if kw]

# ---------- 4. 方向字段检测 (§4.1) ----------
def detect_direction_fields(trace):
    """间接访问地址由污点计算 → 方向字段; 最小被访问位置 = target 末尾。

    论文规则: 同一方向字段多次使用只记第一次(最小 access_pos)。
    """
    fields = {}
    for src_positions, access_pos in trace.indirect:
        key = tuple(src_positions)
        if key not in fields or access_pos < fields[key]:
            fields[key] = access_pos
    return [(list(k), v) for k, v in sorted(fields.items())]

# ---------- 5. 定长字段合并 (§6) ----------
def merge_fixed_fields(trace, L, direction_positions):
    """同一指令使用的连续污点位置合并为定长字段(剔除方向字段位置)。"""
    dirset = set(direction_positions)
    fields = []
    covered = set()
    for positions in trace.instrs:
        ps = sorted(set(positions) - dirset)
        if len(ps) >= 2 and ps == list(range(ps[0], ps[-1] + 1)):
            if not (set(ps) & covered):
                fields.append((ps[0], ps[-1]))
                covered |= set(ps)
    return sorted(fields)

# ---------- 演示 ----------
def demo_http():
    """HTTP GET: 分隔符(空格/\r\n) + 关键字(GET/HTTP/1.1/Host)。

    轨迹模拟真实解析器行为:
      - 行扫描: 每个位置都与 '\n'(0x0a) 比较 (memchr 找行尾)
      - 请求行内: 位置 0..reqline_end 都与 ' '(0x20) 比较
      - 方法/版本/头名匹配: memcmp 常量串, 逐字节为真
    """
    msg = (b'GET /a HTTP/1.1\r\n'
           b'Host: h.com\r\n'
           b'Accept: x\r\n'
           b'Connection: close\r\n'
           b'\r\n')
    L = len(msg)
    reqline_end = msg.index(b'\r\n')          # 12
    compares = []
    for i in range(L):                        # 行扫描: 全位置比 '\n'
        compares.append((i, 0x0a, msg[i] == 0x0a))
    for i in range(reqline_end):              # 请求行: 比空格
        compares.append((i, 0x20, msg[i] == 0x20))
    def match(pos, literal):                  # memcmp 常量串(逐字节为真)
        for k, ch in enumerate(literal):
            compares.append((pos + k, ch, msg[pos + k] == ch))
    match(0, b'GET'); match(0, b'PUT')        # 方法 switch (PUT 分支为假)
    match(7, b'HTTP/1.1')                     # 版本 (位置 7..14)
    host_at = reqline_end + 2
    match(host_at, b'Host:')                  # 头名
    trace = Trace(compares=compares)
    tap, series, truep = build_token_tables(trace)
    print('== HTTP GET (多行, 含 5 个 CRLF) ==')
    print('  %r' % msg)
    print('token 表(token → 出现比较的位置数):')
    for tok in sorted(series):
        print('  0x%02x → %d 个位置' % (tok, len(series[tok])))
    seps = extract_byte_separators(series)
    print('单字节分隔符(series>=3):', ['0x%02x@%d..%d' % (t, r[0], r[-1])
                                      for t, r in seps])
    ext = extend_separators(seps, [msg])
    print('扩展后分隔符:', [s for s, _ in ext])
    kws = extract_keywords(truep, L, ext)
    print('关键字(起始-结束):', [(k, '%d-%d' % (a, b)) for k, a, b in kws])
    print()
    return msg, ext, kws

def demo_binary():
    """二进制报文: 方向字段(len) + 定长字段(magic/ver/type/checksum)。"""
    # magic(2) ver(1) type(1) len(2) payload(5) crc(4)
    msg = b'ZM' + bytes([0x01, 0x10]) + bytes([5, 0]) + b'HELLO' + b'\xde\xad\xbe\xef'
    L = len(msg)
    compares = [
        (0, ord('Z'), True), (1, ord('M'), True),          # magic 常量比较(为真)
        (2, 0x01, True),                                     # 版本比较(为真)
        (3, 0x10, True), (3, 0x11, False), (3, 0x12, False),# type switch: 命中 0x10
        (0, ord('X'), False), (1, ord('N'), False),         # 错误魔数分支(为假)
    ]
    indirect = [
        ((4, 5), 11),   # len(位置4-5) 计算 payload 末尾: ptr = 6 + len → 访问到位置 11
    ]
    instrs = [
        (0, 1),            # magic 两字节一条指令比较
        (11, 12, 13, 14),  # crc 4 字节一条指令异或
    ]
    trace = Trace(compares=compares, indirect=indirect, instrs=instrs)
    tap, series, truep = build_token_tables(trace)
    print('== 二进制报文: %s ==' % msg.hex())
    seps = extract_byte_separators(series)   # 无 series>=3 → 无分隔符(定长协议)
    print('分隔符:', seps or '(无——定长边界不靠分隔符)')
    dirs = detect_direction_fields(trace)
    for ps, target_end in dirs:
        print('方向字段: 位置 %s (len=%d) → target 末尾=位置%d'
              % (ps, len(ps), target_end))
    dir_positions = [p for ps, _ in dirs for p in ps]
    fixed = merge_fixed_fields(trace, L, dir_positions)
    print('定长字段(同一指令合并):', fixed)
    kws = extract_keywords(truep, L, seps)
    print('关键字:', kws)
    # ---- 输出 Table 1 风格的字段格式 ----
    print('字段格式(Polyglot Table 1 属性):')
    fields = [
        dict(start=0, length=2, boundary='Fixed', type='-', keywords='ZM'),
        dict(start=2, length=1, boundary='Fixed', type='-', keywords='\\x01'),
        dict(start=3, length=1, boundary='Fixed', type='-', keywords='\\x10'),
        dict(start=4, length=2, boundary='-', type='Direction(len)', keywords=''),
        dict(start=6, length=5, boundary='Direction', type='-', keywords=''),
        dict(start=11, length=4, boundary='Fixed', type='-', keywords=''),
    ]
    for f in sorted(fields, key=lambda f: f['start']):
        print('  start=%-2d length=%-2d boundary=%-9s type=%-14s keywords=%s'
              % (f['start'], f['length'], f['boundary'], f['type'], f['keywords'] or '-'))

if __name__ == '__main__':
    demo_http()
    demo_binary()

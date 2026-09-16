#!/usr/bin/env python3
"""mini_bpftrace.py — bpftrace 语言前端最小实现（解析 + 语义 + 执行 + 输出）

复刻 bpftrace 的四件事:
  1. 探针说明符展开(短名 / 通配符)与谓词过滤
  2. 关联数组(map)与聚合函数(count/sum/min/max/avg/stats/hist/lhist)
  3. hist()/lhist()/stats() 的输出格式(列宽按官方样例逐字符对齐)
  4. 事件驱动执行引擎:用 sys_enter + sys_exit 配对给 syscall 延迟打直方图

不依赖任何第三方库。权威来源见 ../README.md「参考资料」。
运行: python mini_bpftrace.py
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- 探针说明符
# 官方 language.md 的 "Short Name" 列。注意 U/fr/rt 等别名大小写敏感。
ALIASES = {"t": "tracepoint", "k": "kprobe", "kr": "kretprobe", "u": "uprobe",
           "ur": "uretprobe", "p": "profile", "i": "interval", "s": "software",
           "h": "hardware", "w": "watchpoint", "it": "iter", "rt": "rawtracepoint",
           "f": "fentry", "fr": "fexit"}
# begin/end 是内置事件而非 provider;官方文档写小写,cheat sheet/man page 写大写,两者都收。
BUILTIN_EVENTS = {"begin", "end", "BEGIN", "END"}


def expand_probe(spec: str) -> str:
    """短名探针展开为全名: k:f -> kprobe:f ; BEGIN -> begin ; begin 原样。"""
    spec = spec.strip()
    if spec in BUILTIN_EVENTS:
        return spec.lower()
    head, sep, rest = spec.partition(":")
    return spec if not sep else ALIASES.get(head, head) + ":" + rest


def probe_matches(pattern: str, actual: str) -> bool:
    """探针名匹配,支持 * 与 ? 通配符(官方: -l 的搜索词支持 glob)。"""
    rx = "^" + re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".") + "$"
    return re.match(rx, actual) is not None


# ---------------------------------------------------------------- 直方图分桶
def hist_index(v: int) -> int:
    """hist() 的桶号(每 2 的幂一个桶): 0/1 -> 0 ; 2..3 -> 1 ; 4..7 -> 2 ; 8..15 -> 3 …"""
    return 0 if v < 2 else v.bit_length() - 1


def fmt_bucket(n: int) -> str:
    """桶边界的刻度写法: >=1024 起用 k/M/G 后缀(官方 tutorial 里出现 [2k, 4k) / [512k, 1M))。"""
    for shift, suf in ((30, "G"), (20, "M"), (10, "k")):
        if n >= (1 << shift):
            return f"{n >> shift}{suf}"
    return str(n)


def hist_label(i: int) -> str:
    """官方 tutorial 输出里首桶写作 [0, 1] —— 右括号是方括号,因为它同时收纳 0 和 1。"""
    return "[0, 1]" if i == 0 else f"[{fmt_bucket(1 << i)}, {fmt_bucket(1 << (i + 1))})"


def lhist_m(lo: int, hi: int, step: int) -> int:
    return (hi - lo) // step


def lhist_index(v: int, lo: int, hi: int, step: int) -> int:
    """(-inf, lo] -> -1 ; [lo, hi) -> 0..M-1 ; [hi, +inf) -> M, M = (hi-lo)/step"""
    if v <= lo:
        return -1
    if v >= hi:
        return lhist_m(lo, hi, step)
    return (v - lo) // step


def lhist_label(k: int, lo: int, hi: int, step: int) -> str:
    if k == -1:
        return f"(...,{lo}]"
    if k == lhist_m(lo, hi, step):
        return f"[{hi},...)"
    return f"[{lo + k * step}, {lo + (k + 1) * step})"


BAR_WIDTH = 52   # 柱区固定宽度,与官方 tutorial / man page 样例输出一致


def span(buckets: dict[int, int]):
    """只渲染到最后一个非空桶(上方空桶不打印),中间空桶保留为 0 行。"""
    if not buckets or not any(buckets.values()):
        return []
    last = max(i for i, c in buckets.items() if c)
    return [(i, buckets.get(i, 0)) for i in range(min(buckets), last + 1)]


def render_rows(rows) -> list[str]:
    """列宽:标签左对齐 15 列 + 计数右对齐 9 列 + ' |' + 柱区(52) + '|'"""
    rows = list(rows)
    if not rows:
        return []
    maxc = max(c for _, c in rows) or 1
    out = []
    for label, c in rows:
        bar = max(1, c * BAR_WIDTH // maxc) if c else 0
        out.append(f"{label:<15}{c:>9} |{('@' * bar):<{BAR_WIDTH}}|")
    return out


class Aggregator:
    """一个 map 键对应的聚合状态。hist/lhist 用稀疏 dict,其余用标量。"""

    def __init__(self, name: str, args: tuple):
        self.name, self.args = name, args
        self.n = 0            # count() / 参与 avg、stats 的次数
        self.total = 0        # sum() / avg() / stats()
        self.val = args[0] if name in ("min", "max") else None
        self.buckets: dict[int, int] = {}
        if name == "hist":
            if len(args) > 1 and args[1] != 0:
                raise ValueError("本 demo 只实现 hist(x) 的 k=0(每个 2 的幂区间一个桶)")
            self.buckets = {0: 0}          # 桶 0 恒存在,与 bpftrace 一样从 [0, 1] 开始打印
        elif name == "lhist":
            _v, lo, hi, step = args
            if step <= 0 or hi <= lo:
                raise ValueError("lhist: 需要 step > 0 且 min < max")
            self.lo, self.hi, self.step = lo, hi, step
            self.buckets = {k: 0 for k in range(-1, lhist_m(lo, hi, step) + 1)}

    def update(self, v=None) -> None:
        n = self.name
        if n == "count":
            self.n += 1
        elif n in ("sum", "avg", "stats"):
            self.n += 1
            self.total += v
        elif n == "min":
            self.val = min(self.val, v)
        elif n == "max":
            self.val = max(self.val, v)
        elif n == "hist":
            i = hist_index(v)
            self.buckets[i] = self.buckets.get(i, 0) + 1
        elif n == "lhist":
            self.n += 1
            self.buckets[lhist_index(v, self.lo, self.hi, self.step)] += 1
        else:
            raise AssertionError(n)

    def render(self) -> list[str]:
        n = self.name
        if n in ("count", "sum"):
            return [str(self.n if n == "count" else self.total)]
        if n in ("min", "max"):
            return [str(self.val)]
        if n == "avg":
            return [str(self.total // self.n) if self.n else "0"]
        if n == "stats":
            avg = (self.total // self.n) if self.n else 0
            return [f"count {self.n}, average {avg}, total {self.total}"]
        if n == "hist":
            return render_rows((hist_label(i), c) for i, c in span(self.buckets))
        if n == "lhist":
            return render_rows((lhist_label(k, self.lo, self.hi, self.step), c)
                               for k, c in span(self.buckets))
        raise AssertionError(n)


# ---------------------------------------------------------------- 词法
TOKEN_RX = re.compile(r"""
    (?P<ws>\s+)
  | (?P<num>\d+)
  | (?P<str>"(?:[^"\\]|\\.)*")
  | (?P<id>[A-Za-z_][A-Za-z_0-9]*)
  | (?P<op>==|!=|<=|>=|&&|\|\||[{}()\[\];,.:?=+\-*/%<>!@$])
""", re.X)


def tokenize(src: str) -> list[tuple[str, str]]:
    toks, pos = [], 0
    while pos < len(src):
        m = TOKEN_RX.match(src, pos)
        if not m:
            raise SyntaxError(f"无法识别的字符 {src[pos]!r} @{pos}")
        pos = m.end()
        if m.lastgroup != "ws":
            toks.append((m.lastgroup, m.group()))
    toks.append(("eof", ""))
    return toks


# ---------------------------------------------------------------- 语法
class Parser:
    """只实现 demo 用得到的子集:表达式 + map 聚合赋值 + 函数调用语句。"""

    LEVELS = [["||"], ["&&"], ["==", "!="], ["<", ">", "<=", ">="], ["+", "-"], ["*", "/", "%"]]

    def __init__(self, toks):
        self.t, self.i = toks, 0

    def peek(self, *vals):
        k, v = self.t[self.i]
        return (k, v) if (k in vals or v in vals) else None

    def take(self):
        tok = self.t[self.i]
        self.i += 1
        return tok

    def expect(self, val):
        k, v = self.take()
        if v != val:
            raise SyntaxError(f"期望 {val!r},得到 {v!r}")

    def expr(self):
        return self.ternary()

    def ternary(self):
        c = self.binary(1)
        if self.peek("?"):
            self.take()
            a = self.ternary()
            self.expect(":")
            return ("tern", c, a, self.ternary())
        return c

    def binary(self, lvl):
        if lvl > len(self.LEVELS):
            return self.unary()
        node = self.binary(lvl + 1)
        while True:
            p = self.peek(*self.LEVELS[lvl - 1])
            if not p:
                return node
            self.take()
            node = ("bin", p[1], node, self.binary(lvl + 1))

    def unary(self):
        if self.peek("!", "-"):
            op = self.take()[1]
            return ("un", op, self.unary())
        return self.primary()

    def primary(self):
        k, v = self.take()
        if k == "num":
            return ("num", int(v))
        if k == "str":
            return ("str", v[1:-1].replace('\\n', '\n').replace('\\"', '"'))
        if v == "(":
            e = self.expr()
            self.expect(")")
            return e
        if v == "$":
            k2, v2 = self.take()
            if k2 == "num":
                return ("pos", int(v2))
            if k2 != "id":
                raise SyntaxError(f"$ 后应为名字或序号,得到 {v2!r}")
            return ("scratch", v2)
        if v == "@":
            name = ""
            nxt = self.take()
            if nxt[0] == "id":
                name = nxt[1]
            elif nxt[1] == "[":
                self.i -= 1                      # 无名字 map: @[k] = ...
            else:
                raise SyntaxError(f"@ 后应为 map 名或 [,得到 {nxt[1]!r}")
            keys = []
            if self.peek("["):
                self.take()
                while True:
                    keys.append(self.expr())
                    if not self.peek(","):
                        break
                    self.take()
                self.expect("]")
            return ("map", name, tuple(keys))
        if k == "id":
            if v in BUILTIN_EVENTS:
                return ("str", v.lower())
            if self.peek("("):
                self.take()
                args = []
                if not self.peek(")"):
                    while True:
                        args.append(self.expr())
                        if not self.peek(","):
                            break
                        self.take()
                self.expect(")")
                return ("call", v, tuple(args))
            if self.peek("."):                   # args.filename / ctx.task
                self.take()
                return ("field", ("var", v), self.take()[1])
            return ("var", v)
        raise SyntaxError(f"无法解析的 token {v!r}")


def split_blocks(src: str):
    """按大括号把程序切成 (header, body);header = 'probe[,probe] [/predicate/]'。"""
    blocks, i = [], 0
    while i < len(src):
        if src[i].isspace():
            i += 1
            continue
        j = src.find("{", i)
        if j < 0:
            break
        depth, k = 0, j
        while k < len(src):
            if src[k] == "{":
                depth += 1
            elif src[k] == "}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        blocks.append((src[i:j].strip(), src[j + 1:k]))
        i = k + 1
    return blocks


def parse_header(header: str):
    """返回 (探针全名列表, 谓词 AST 或 None)。谓词由 header 末尾的一对 / 包裹。"""
    pred = None
    m = re.search(r"/(.+)/\s*$", header)
    if m:
        pred = Parser(tokenize(m.group(1))).expr()
        header = header[:m.start()]
    return [expand_probe(s) for s in header.split(",") if s.strip()], pred


def parse_stmt(p: Parser):
    left = p.expr()
    AGGS = ("count", "sum", "min", "max", "avg", "stats", "hist", "lhist")
    if p.peek("="):
        p.take()
        right = p.expr()
        p.expect(";")
        if right[0] == "call" and right[1] in AGGS:
            return ("agg", left, right[1], right[2])
        return ("assign", left, right)
    p.expect(";")
    return ("call", left)


def parse(src: str):
    prog = []
    for header, body in split_blocks(src):
        specs, predicate = parse_header(header)
        p, stmts = Parser(tokenize(body)), []
        while p.peek("eof") is None:
            stmts.append(parse_stmt(p))
        prog.append((specs, predicate, stmts))
    return prog


# ---------------------------------------------------------------- 执行引擎
class Unset:
    """读不到的变量。既当 0 用,又当假用(谓词 /@start[tid]/ 靠它挡掉未配对的退出事件)。"""

    def __bool__(self):
        return False

    def __str__(self):
        return "0"


class Engine:
    """事件驱动:按事件顺序喂入,匹配探针 -> 求谓词 -> 执行动作块(每块一个 scratch 作用域)。"""

    def __init__(self, prog):
        self.prog = prog
        self.maps: dict = {}
        self.agg: dict = {}
        self.scopes: list[dict] = [{}]
        self.out: list[str] = []
        self.stopped = False

    # ---- 表达式求值
    def get(self, node, ctx):
        k = node[0]
        if k in ("num", "str"):
            return node[1]
        if k == "var":
            return ctx.get(node[1], 0)
        if k == "field":
            return ctx.get(node[1][1] + "." + node[2], 0)
        if k == "pos":
            return 0
        if k == "scratch":
            for sc in reversed(self.scopes):
                if node[1] in sc:
                    return sc[node[1]]
            return Unset()
        if k == "map":
            key = tuple(self.get(e, ctx) for e in node[2])
            return self.maps.get((node[1], key), Unset())
        if k == "bin":
            return self.binop(node[1], self.get(node[2], ctx), self.get(node[3], ctx))
        if k == "un":
            return (not self.get(node[2], ctx)) if node[1] == "!" else -self.get(node[2], ctx)
        if k == "tern":
            return self.get(node[2], ctx) if self.get(node[1], ctx) else self.get(node[3], ctx)
        raise SyntaxError(f"{node[1]}() 只能出现在聚合赋值右侧或语句位置")

    @staticmethod
    def binop(op, a, b):
        if isinstance(a, Unset):
            a = 0
        if isinstance(b, Unset):
            b = 0
        if op == "==" or op == "!=":
            return (a == b) if op == "==" else (a != b)
        if op == "&&" or op == "||":
            return (bool(a) and bool(b)) if op == "&&" else (bool(a) or bool(b))
        if isinstance(a, str) or isinstance(b, str):
            raise SyntaxError(f"字符串不参与算术运算: {op}")
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            return a // b if b else 0
        if op == "%":
            return a % b if b else 0
        return {"<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]

    def set_(self, node, value, ctx):
        if node[0] == "scratch":
            self.scopes[-1][node[1]] = value
        elif node[0] == "map":
            self.maps[(node[1], tuple(self.get(e, ctx) for e in node[2]))] = value
        elif node[0] == "var":
            ctx[node[1]] = value
        else:
            raise SyntaxError(f"不可赋值 {node!r}")

    # ---- 语句
    def run_stmt(self, stmt, ctx):
        if stmt[0] == "agg":
            target, fn, args = stmt[1], stmt[2], stmt[3]
            key = (target[1], tuple(self.get(e, ctx) for e in target[2]))
            vals = tuple(self.get(a, ctx) for a in args)
            if any(isinstance(v, Unset) for v in vals):
                return                       # 参数读不到(未配对)时不记账
            if key not in self.agg:
                self.agg[key] = Aggregator(fn, vals)
            self.agg[key].update(vals[0] if vals else None)
            return
        if stmt[0] == "assign":
            self.set_(stmt[1], self.get(stmt[2], ctx), ctx)
            return
        node = stmt[1]
        if node[0] != "call":
            self.out.append(str(self.get(node, ctx)))
            return
        fn, args = node[1], node[2]
        if fn == "printf":
            self.out.append(printf(self.get(args[0], ctx), [self.get(a, ctx) for a in args[1:]]))
        elif fn == "print":
            self.out.append(str(self.get(args[0], ctx)))
        elif fn == "delete":
            # bpftrace 的写法是 delete(@map, key): map 不写方括号,键作为独立实参传入
            m = args[0]
            self.maps.pop((m[1], tuple(self.get(e, ctx) for e in args[1:])), None)
        elif fn == "clear":
            for k in [k for k in self.maps if k[0] == args[0][1]]:
                self.maps.pop(k)
        elif fn == "exit":
            self.stopped = True
        else:
            raise SyntaxError(f"未实现的语句函数 {fn}()")

    # ---- 事件
    def feed(self, probe_name, ctx):
        if self.stopped:
            return
        full = dict(ctx)
        full["probe"] = probe_name
        for specs, predicate, stmts in self.prog:
            if not any(probe_matches(s, probe_name) for s in specs):
                continue
            if predicate is not None and not self.get(predicate, full):
                continue
            self.scopes.append({})
            try:
                for s in stmts:
                    self.run_stmt(s, full)
            finally:
                self.scopes.pop()

    def report(self) -> list[str]:
        lines = []
        for (name, key), agg in sorted(self.agg.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
            lines.append(f"@{name}{key_str(key)}: " + "\n".join(agg.render()))
        for (name, key), v in sorted(self.maps.items(), key=lambda kv: str(kv[0])):
            lines.append(f"@{name}{key_str(key)}: {v}")
        return lines


def key_str(key: tuple) -> str:
    return f"[{', '.join(str(k) for k in key)}]" if key else ""


def printf(fmt: str, vals) -> str:
    out, pos, vi = "", 0, 0
    for m in re.finditer(r"%-?(\d+)?[dsx]", fmt):
        out += fmt[pos:m.start()] + (str(vals[vi]) if m.group(0)[-1] in "ds"
                                     else format(int(vals[vi]), "x"))
        pos, vi = m.end(), vi + 1
    return out + fmt[pos:]


# ---------------------------------------------------------------- 自检
def check(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  <- {detail}" if detail else ""))
    return bool(cond)


def synthetic_events():
    """合成「应用线程被上游队列顶住」的 syscall 序列(单位 ns),确定性、无随机数。"""
    ev = [("read", d) for d in (4000, 5200, 6800, 8000, 9500, 11000, 13000, 14500)]
    ev += [("openat", d) for d in (16_000_000, 20_000_000, 24_000_000, 31_000_000)]
    ev += [("write", d) for d in (700, 1300, 2600)]
    out, ts = [], 0
    for i, (name, d) in enumerate(ev):
        tid = 100 + (i % 2)
        ts += 1000
        out.append((f"tracepoint:syscalls:sys_enter_{name}",
                    {"tid": tid, "pid": tid, "comm": "app", "nsecs": ts}))
        ts += d
        out.append((f"tracepoint:syscalls:sys_exit_{name}",
                    {"tid": tid, "pid": tid, "comm": "app", "nsecs": ts, "ret": 1024}))
    return out


SRC = """
BEGIN { printf("Tracing syscall latency... Hit Ctrl-C to end.\\n"); }
tracepoint:syscalls:sys_enter_* /comm == "app"/ { @start[tid] = nsecs; }
tracepoint:syscalls:sys_exit_* /comm == "app" && @start[tid]/
  { @ns[comm] = hist(nsecs - @start[tid]); delete(@start, tid); }
tracepoint:raw_syscalls:sys_enter { @[comm] = count(); }
"""


def main() -> int:
    ok = True

    print("== 1. 探针说明符:短名展开 ==")
    ok &= check("t:syscalls:sys_enter_read -> tracepoint:...",
                expand_probe("t:syscalls:sys_enter_read") == "tracepoint:syscalls:sys_enter_read")
    ok &= check("k:f -> kprobe:f", expand_probe("k:f") == "kprobe:f")
    ok &= check("kr:vfs_read -> kretprobe:vfs_read",
                expand_probe("kr:vfs_read") == "kretprobe:vfs_read")
    ok &= check("i:s:5 -> interval:s:5", expand_probe("i:s:5") == "interval:s:5")
    ok &= check("BEGIN 是内置事件、不是 provider(统一成小写 begin)",
                expand_probe("BEGIN") == "begin" and expand_probe("begin") == "begin")
    ok &= check("未知 provider 原样保留(交给 bpftrace 报错)",
                expand_probe("nope:foo") == "nope:foo")

    print("== 2. 通配符匹配 ==")
    ok &= check("tracepoint:sched:sched* 命中 sched_wakeup",
                probe_matches("tracepoint:sched:sched*", "tracepoint:sched:sched_wakeup"))
    ok &= check("同一模式不命中 sys_enter_read",
                not probe_matches("tracepoint:sched:sched*", "tracepoint:syscalls:sys_enter_read"))
    ok &= check("? 只匹配单字符(tcp_? 命中 tcp_a)",
                probe_matches("kprobe:tcp_?", "kprobe:tcp_a"))
    ok &= check("? 不匹配多字符", not probe_matches("kprobe:tcp_?", "kprobe:tcp_abc"))

    print("== 3. hist() 分桶(每 2 的幂一个桶) ==")
    got = [hist_index(v) for v in (0, 1, 2, 3, 4, 7, 8, 127, 128, 1023)]
    ok &= check("0/1->0 2..3->1 4..7->2 8..15->3 127->6 128->7 1023->9",
                got == [0, 0, 1, 1, 2, 2, 3, 6, 7, 9], f"got {got}")
    ok &= check("首桶标签是 [0, 1](右括号为方括号,同时收纳 0 与 1)", hist_label(0) == "[0, 1]")
    ok &= check("后续桶左闭右开", hist_label(7) == "[128, 256)")
    ok &= check(">=1024 的边界用 k/M/G 刻度(tutorial Lesson 7 的 [2k, 4k) / [512k, 1M))",
                hist_label(11) == "[2k, 4k)" and hist_label(19) == "[512k, 1M)"
                and hist_label(20) == "[1M, 2M)", f"{hist_label(11)} {hist_label(19)}")

    print("== 4. 输出列宽与官方 tutorial 样例逐字符一致 ==")
    rows = render_rows([("[0, 1]", 12), ("[2, 4)", 18), ("[128, 256)", 1)])
    ok &= check("`[0, 1]` 行前 24 列 == '[0, 1]'+16 空格+'12'",
                rows[0][:24] == "[0, 1]" + " " * 16 + "12", repr(rows[0][:24]))
    ok &= check("`[2, 4)` 行前 24 列 == '[2, 4)'+16 空格+'18'",
                rows[1][:24] == "[2, 4)" + " " * 16 + "18", repr(rows[1][:24]))
    ok &= check("`[128, 256)` 行前 24 列 == '[128, 256)'+13 空格+'1'",
                rows[2][:24] == "[128, 256)" + " " * 13 + "1", repr(rows[2][:24]))
    ok &= check("柱区总宽恒为 52", all(len(r.split("|")[1]) == BAR_WIDTH for r in rows))
    ok &= check("最大桶占满 52 个 @", rows[1].count("@") == BAR_WIDTH, str(rows[1].count("@")))

    print("== 5. lhist(): 桶数 = (max-min)/step + 2,越界值各占一桶 ==")
    ag = Aggregator("lhist", (0, 0, 2000, 200))
    ok &= check("lhist(_,0,2000,200) 共 12 桶(M=10 + 2)", len(ag.buckets) == 12,
                str(len(ag.buckets)))
    ok &= check("低于下界 -> (...,0]", lhist_label(-1, 0, 2000, 200) == "(...,0]")
    ok &= check("高于上界 -> [2000,...)", lhist_label(10, 0, 2000, 200) == "[2000,...)")
    ok &= check("区间标签 -> [1800, 2000)", lhist_label(9, 0, 2000, 200) == "[1800, 2000)")
    ok &= check("-5 与 0 同落 (...,0] 桶",
                lhist_index(-5, 0, 2000, 200) == lhist_index(0, 0, 2000, 200) == -1)
    ok &= check("2000 与 99999 同落 [2000,...) 桶",
                lhist_index(2000, 0, 2000, 200) == lhist_index(99999, 0, 2000, 200) == 10)

    print("== 6. 解析 + 执行:sys_enter/sys_exit 配对 ==")
    prog = parse(SRC)
    ok &= check("解析出 4 个动作块", len(prog) == 4, str(len(prog)))
    ok &= check("第 3 块带谓词", prog[2][1] is not None)
    ok &= check("第 2 块探针通配符已展开",
                prog[1][0] == ["tracepoint:syscalls:sys_enter_*"], str(prog[1][0]))
    eng = Engine(prog)
    eng.feed("begin", {})
    for name, ctx in synthetic_events():
        eng.feed(name, ctx)
    ok &= check("BEGIN 打印出表头", bool(eng.out) and "Tracing" in eng.out[0], repr(eng.out))
    ns = eng.agg[("ns", ("app",))]
    ok &= check("15 条 syscall 延迟全部配对成功", sum(ns.buckets.values()) == 15,
                str(sum(ns.buckets.values())))
    ok &= check("呈双峰: 4k~8k 段有人、16ms~32ms 段也有人",
                ns.buckets.get(hist_index(6800), 0) > 0
                and ns.buckets.get(hist_index(16_000_000), 0) > 0)
    ok &= check("delete(@start, tid) 已清空 15 个 @start 键",
                sum(1 for (n, _k) in eng.maps if n == "start") == 0)
    rpt = "\n".join(eng.report())
    ok &= check("报告里出现 @ns[app]: 与直方图行", "@ns[app]:" in rpt and "[4k, 8k)" in rpt, rpt[:80])

    print("== 7. 未配对时不应记账 ==")
    e2 = Engine(parse(SRC))
    e2.feed("tracepoint:syscalls:sys_exit_read", {"tid": 7, "pid": 7, "comm": "other", "nsecs": 5})
    ok &= check("comm 不匹配 -> 不建 @ns", ("ns", ("other",)) not in e2.agg)
    e3 = Engine(parse(SRC))
    e3.feed("tracepoint:syscalls:sys_exit_read", {"tid": 7, "pid": 7, "comm": "app", "nsecs": 5})
    ok &= check("comm 匹配但缺 @start[tid] -> 谓词挡住,不建 @ns",
                not any(n == "ns" for (n, _k) in e3.agg))
    e4 = Engine(parse("kretprobe:vfs_read { @b = hist(retval); }"))
    e4.feed("kretprobe:vfs_read", {"retval": 100})
    ok &= check("真值 100 落进 [64, 128) 桶且只计 1 次",
                e4.agg[("b", ())].buckets == {0: 0, 6: 1}, str(e4.agg[("b", ())].buckets))

    print("== 8. count() 与 stats() 的输出格式 ==")
    e5 = Engine(parse("tracepoint:raw_syscalls:sys_enter { @[comm] = count(); }"
                      "kprobe:vfs_read { @bytes[comm] = stats(arg2); }"))
    for c in ("bpftrace", "systemd", "snmp-pass", "snmp-pass", "sshd"):
        e5.feed("tracepoint:raw_syscalls:sys_enter", {"comm": c})
    for n in (7, 832, 886):
        e5.feed("kprobe:vfs_read", {"comm": "bash", "arg2": n})
    rep = "\n".join(e5.report())
    ok &= check("无名 map 渲染为 @[snmp-pass]: 2(与官方 tutorial 一致)",
                "@[snmp-pass]: 2" in rep, rep)
    ok &= check("stats() 渲染为 count/average/total 三件套",
                "@bytes[bash]: count 3, average 575, total 1725" in rep, rep)

    print("== 9. 作用域与 map 生命周期 ==")
    e6 = Engine(parse("kprobe:vfs_read { $x = 1; @g = 2; } kprobe:vfs_write { @g = @g + 1; }"))
    e6.feed("kprobe:vfs_read", {})
    ok &= check("$scratch 出块即不可见", isinstance(e6.get(("scratch", "x"), {}), Unset))
    e6.feed("kprobe:vfs_write", {})
    ok &= check("@map 跨块存活并被累加(@g: 2 -> 3)", e6.maps.get(("g", ())) == 3, str(e6.maps))

    print("== 10. 三元运算符与算术 ==")
    e7 = Engine(parse("BEGIN { @m = avg(arg0); }"))
    for v in (10, 20, 33):
        e7.feed("begin", {"arg0": v})
    ok &= check("avg(10,20,33) = 63//3 = 21", e7.agg[("m", ())].render() == ["21"],
                str(e7.agg[("m", ())].render()))
    bt = Engine(parse('BEGIN { @r = count(); }'))
    bt.feed("begin", {})
    ok &= check("count() 无参时计 1 次", bt.agg[("r", ())].render() == ["1"])

    print("\n" + ("全部通过" if ok else "存在失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

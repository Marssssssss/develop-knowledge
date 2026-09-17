# -*- coding: utf-8 -*-
"""net/http/pprof 在线采集最小复刻：模拟 DefaultServeMux 注册 + Index 路由 + 参数语义。

口径：路由与参数语义逐条对照 golang/go src/net/http/pprof/pprof.go（master，本轮实读）：
  - init() 在 DefaultServeMux 注册 5 条路径："/debug/pprof/"→Index、cmdline、profile、symbol、trace
    （Go 1.22 起 pattern 带 "GET " 前缀，非 GET 由 ServeMux 判 405）
  - Index：CutPrefix "/debug/pprof/"，name 非空 → handler(name)；name 空 → HTML 索引页
    （列出 runtime/pprof.Profiles() + 4 个包内特殊端点，按名排序，附 count 与描述）
  - handler(name)：pprof.Lookup(name) 为 nil → 404 "Unknown profile"
  - 有 seconds → delta 路径：非法/非正整数 → 400；不在 profileSupportsDelta → 400；
    与 debug 并用 → 400；否则 收集 p0 → 睡 N 秒 → 收集 p1 → p0.Scale(-1) → merge
  - gc 参数仅对 heap 生效（源码 `if name == "heap" && gc > 0`）
  - debug!=0 → text/plain；否则 octet-stream + attachment filename=name
  - Profile：seconds ParseInt，<=0 或解析错 → 默认 30；StartCPUProfile 失败 → 500
  - Trace：ParseFloat，默认 1
  - Cmdline：os.Args 用 "\x00" 连接
"""
import json

SPECIAL = ("cmdline", "profile", "symbol", "trace")
# 源码 profileSupportsDelta（master 已含 goroutineleak）
DELTA_OK = {"allocs", "block", "goroutineleak", "goroutine", "heap", "mutex", "threadcreate"}


class Runtime:
    """假运行时：profile 注册表 + GC 计数 + CPU 剖析状态 + 符号表。"""

    def __init__(self, argv, symbols):
        self.argv = argv
        self.symbols = symbols            # pc(int) -> 函数名
        self.profiles = {"goroutine": 3, "heap": 0, "allocs": 0,
                         "threadcreate": 1, "block": 0, "mutex": 0}
        self.gc_runs = 0
        self.cpu_busy = False             # StartCPUProfile 已在跑

    def lookup(self, name):
        return self.profiles.get(name)    # None = 未知 profile（pprof.Lookup 返回 nil）

    def gc(self):
        self.gc_runs += 1


class Resp:
    def __init__(self, status, body, ctype="text/plain; charset=utf-8",
                 disposition=None, actions=None):
        self.status, self.body, self.ctype = status, body, ctype
        self.disposition = disposition
        self.actions = actions or []      # 副作用序列，供断言

    def ok(self):
        return 200 <= self.status < 300


def parse_int(s):
    """strconv.ParseInt(_, 10, _) 的复刻：严格十进制，非数字/空 → None。"""
    try:
        return int(s, 10)
    except (TypeError, ValueError):
        return None


def parse_uint0(s):
    """strconv.ParseUint(_, 0, _) 的复刻：base 0 自动识别 0x/0o/0b 前缀。"""
    try:
        v = int(s, 0)
        return v if v >= 0 else None
    except (TypeError, ValueError):
        return None


def parse_float(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def serve_handler(rt, name, query):
    """源码 handler(name).ServeHTTP 的语义复刻。"""
    if rt.lookup(name) is None:
        return Resp(404, "Unknown profile\n")
    sec = query.get("seconds", "")
    if sec != "":
        n = parse_int(sec)
        if n is None or n <= 0:
            return Resp(400, 'invalid value for "seconds" - must be a positive integer\n')
        if name not in DELTA_OK:
            return Resp(400, '"seconds" parameter is not supported for this profile type\n')
        if parse_int(query.get("debug", "0") or "0") != 0:
            return Resp(400, "seconds and debug params are incompatible\n")
        # 源码：collectProfile(p0) -> 定时 sec 秒 -> collectProfile(p1)
        #       -> p0.Scale(-1) -> profile.Merge([p0,p1])，文件名 "<name>-delta"
        return Resp(200, "delta-profile:%s:%ds" % (name, n),
                    ctype="application/octet-stream",
                    disposition='attachment; filename="%s-delta"' % name,
                    actions=["collect", "sleep %ds" % n, "collect",
                             "scale -1", "merge", "write"])
    gc = parse_int(query.get("gc", "0") or "0") or 0
    if name == "heap" and gc > 0:
        rt.gc()                            # 源码：runtime.GC() 仅对 heap
    debug = parse_int(query.get("debug", "0") or "0") or 0
    if debug != 0:
        return Resp(200, "legacy-text:%s:debug=%d" % (name, debug),
                    ctype="text/plain; charset=utf-8")
    return Resp(200, "pb-snapshot:%s" % name,
                ctype="application/octet-stream",
                disposition='attachment; filename="%s"' % name,
                actions=["WriteTo debug=0"])


def serve_index(rt, query, path):
    """源码 Index：name 空 → HTML 索引；非空 → 委托 handler(name)。"""
    name = path[len("/debug/pprof/"):]
    if name != "":
        return serve_handler(rt, name, query)
    entries = [{"name": n, "count": c} for n, c in rt.profiles.items()]
    entries += [{"name": n, "count": None} for n in SPECIAL]
    entries.sort(key=lambda e: e["name"])  # 源码 slices.SortFunc 按名排序
    body = "INDEX " + json.dumps(entries, sort_keys=True)
    return Resp(200, body, ctype="text/html; charset=utf-8")


def serve_profile(rt, query):
    """源码 Profile：CPU 剖析，seconds 缺省 30，期间流式写出。"""
    sec = parse_int(query.get("seconds"))
    if sec is None or sec <= 0:
        sec = 30
    if rt.cpu_busy:
        return Resp(500, "Could not enable CPU profiling: CPU profiling already in use\n")
    rt.cpu_busy = True
    acts = ["StartCPUProfile(stream)", "sleep %ds" % sec, "StopCPUProfile"]
    rt.cpu_busy = False
    return Resp(200, "cpu-profile:%ds" % sec, ctype="application/octet-stream",
                disposition='attachment; filename="profile"', actions=acts)


def serve_trace(rt, query):
    """源码 Trace：ParseFloat（可带小数），缺省 1 秒。"""
    sec = parse_float(query.get("seconds"))
    if sec is None or sec <= 0:
        sec = 1
    return Resp(200, "trace:%g" % sec, ctype="application/octet-stream",
                disposition='attachment; filename="trace"',
                actions=["trace.Start", "sleep %gs" % sec, "trace.Stop"])


def serve_cmdline(rt):
    """源码 Cmdline：os.Args 以 NUL 连接。"""
    return Resp(200, "\x00".join(rt.argv))


def serve_symbol(rt, query, body=""):
    """源码 Symbol：'num_symbols: 1' + 每行 '0x.. 函数名'；PC 用 '+' 分隔。"""
    raw = body if body else query.get("__raw_query__", "")
    out = ["num_symbols: 1"]
    for word in raw.split("+"):
        if not word:
            continue
        pc = parse_uint0(word)
        if pc and pc in rt.symbols:
            out.append("%#x %s" % (pc, rt.symbols[pc]))
    return Resp(200, "\n".join(out) + "\n")


def route(rt, method, path, query=None, body=""):
    """DefaultServeMux 精确匹配优先：cmdline/profile/symbol/trace 四条路径
    比 "/debug/pprof/" 更具体，直接命中各自 handler，不经 Index。"""
    query = query or {}
    if method != "GET":
        return Resp(405, "Method Not Allowed\n")
    if not path.startswith("/debug/pprof/"):
        return Resp(404, "404 page not found\n")
    exact = {"cmdline": lambda: serve_cmdline(rt),
             "profile": lambda: serve_profile(rt, query),
             "symbol": lambda: serve_symbol(rt, query, body),
             "trace": lambda: serve_trace(rt, query)}
    tail = path[len("/debug/pprof/"):]
    if tail in exact:                       # 更具体的 pattern 胜出
        return exact[tail]()
    return serve_index(rt, query, path)


def main():
    rt = Runtime(["./app", "-port", "8080"], {0x401000: "main.work", 0x402000: "main.main"})

    # 1. 索引页：runtime profiles + 4 特殊端点，按名排序
    r = route(rt, "GET", "/debug/pprof/")
    assert r.ok() and "text/html" in r.ctype
    assert '"name": "allocs"' in r.body and '"name": "trace"' in r.body
    names = [e["name"] for e in json.loads(r.body[6:])]
    assert names == sorted(names) and "goroutine" in names and "cmdline" in names
    # 2. heap 快照：二进制 protobuf、attachment 文件名
    r = route(rt, "GET", "/debug/pprof/heap")
    assert r.ok() and r.ctype == "application/octet-stream"
    assert r.disposition == 'attachment; filename="heap"' and r.body == "pb-snapshot:heap"
    # 3. gc 参数仅 heap 生效
    route(rt, "GET", "/debug/pprof/heap", {"gc": "1"})
    assert rt.gc_runs == 1
    route(rt, "GET", "/debug/pprof/mutex", {"gc": "1"})
    assert rt.gc_runs == 1, "gc 只应对 heap 触发 runtime.GC()"
    # 4. debug=1 → 明文 legacy text
    r = route(rt, "GET", "/debug/pprof/heap", {"debug": "1"})
    assert "text/plain" in r.ctype and r.body == "legacy-text:heap:debug=1"
    # 5. delta：合法 seconds
    r = route(rt, "GET", "/debug/pprof/heap", {"seconds": "2"})
    assert r.disposition == 'attachment; filename="heap-delta"'
    assert r.actions == ["collect", "sleep 2s", "collect", "scale -1", "merge", "write"]
    # 6. delta：非法/非正 seconds → 400
    assert route(rt, "GET", "/debug/pprof/heap", {"seconds": "abc"}).status == 400
    assert route(rt, "GET", "/debug/pprof/heap", {"seconds": "-1"}).status == 400
    # 7. 自定义 profile 不支持 delta（不在 profileSupportsDelta）→ 400
    rt.profiles["mycompany.custom"] = 0
    r = route(rt, "GET", "/debug/pprof/mycompany.custom", {"seconds": "1"})
    assert r.status == 400 and "not supported" in r.body
    # 8. seconds 与 debug 并用 → 400
    assert route(rt, "GET", "/debug/pprof/heap", {"seconds": "1", "debug": "1"}).status == 400
    # 9. CPU profile：默认 30s；StartCPUProfile 已占用 → 500
    r = route(rt, "GET", "/debug/pprof/profile")
    assert r.body == "cpu-profile:30s" and "sleep 30s" in r.actions
    r = route(rt, "GET", "/debug/pprof/profile", {"seconds": "5"})
    assert r.body == "cpu-profile:5s"
    rt.cpu_busy = True
    assert route(rt, "GET", "/debug/pprof/profile").status == 500
    rt.cpu_busy = False
    # 10. trace：ParseFloat 支持小数，缺省 1s
    r = route(rt, "GET", "/debug/pprof/trace", {"seconds": "2.5"})
    assert r.body == "trace:2.5"
    assert route(rt, "GET", "/debug/pprof/trace").body == "trace:1"
    # 11. cmdline：NUL 连接
    r = route(rt, "GET", "/debug/pprof/cmdline")
    assert r.body == "./app\x00-port\x008080"
    # 12. symbol：PC→函数名，'+' 分隔
    r = route(rt, "GET", "/debug/pprof/symbol",
              {"__raw_query__": "0x401000+0x402000+0x403000"})
    assert "num_symbols: 1" in r.body and "main.work" in r.body and "main.main" in r.body
    assert "0x403000" not in r.body, "未知 PC 不应出现"
    # 13. 未知 profile → 404 "Unknown profile"
    r = route(rt, "GET", "/debug/pprof/nosuch")
    assert r.status == 404 and r.body == "Unknown profile\n"
    # 14. 非 GET（Go 1.22 "GET " pattern）→ 405
    assert route(rt, "POST", "/debug/pprof/heap").status == 405
    # 15. mux 精确匹配：/debug/pprof/cmdline 直达 Cmdline；若经 Index 委托
    #     handler("cmdline") 会 Lookup 失败 → 404，这正是四条独立注册存在的意义
    rt2 = Runtime(["x"], {})
    assert route(rt2, "GET", "/debug/pprof/cmdline").body == "x"
    assert serve_handler(rt2, "cmdline", {}).status == 404

    print("pprof_http: 15 组断言全部通过")


if __name__ == "__main__":
    main()

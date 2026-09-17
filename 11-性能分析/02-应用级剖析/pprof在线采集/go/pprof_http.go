// net/http/pprof 在线采集最小复刻：模拟 DefaultServeMux 注册 + Index 路由 + 参数语义。
//
// 口径：路由与参数语义逐条对照 golang/go src/net/http/pprof/pprof.go（master，本轮实读）：
//   - init() 注册 5 条路径："/debug/pprof/"→Index、cmdline、profile、symbol、trace
//   - Index：CutPrefix "/debug/pprof/"，name 非空 → handler(name)；空 → HTML 索引页
//   - handler(name)：Lookup(name) 为 nil → 404 "Unknown profile"
//   - seconds → delta：非法 → 400；不在 profileSupportsDelta → 400；与 debug 并用 → 400
//   - gc 参数仅 heap 生效（源码 if name == "heap" && gc > 0）
//   - Profile 缺省 30s（ParseInt 基 10）；Trace 缺省 1s（ParseFloat，支持小数）
//   - Cmdline：os.Args 以 NUL 连接
package main

import (
	"fmt"
	"sort"
	"strconv"
	"strings"
)

var deltaOK = map[string]bool{ // 源码 profileSupportsDelta
	"allocs": true, "block": true, "goroutineleak": true, "goroutine": true,
	"heap": true, "mutex": true, "threadcreate": true,
}

// Runtime 假运行时：profile 注册表 + GC 计数 + CPU 剖析状态 + 符号表。
type Runtime struct {
	argv     []string
	symbols  map[uint64]string
	profiles map[string]int
	gcRuns   int
	cpuBusy  bool
}

// Resp 模拟 HTTP 响应。
type Resp struct {
	status      int
	body        string
	ctype       string
	disposition string
	actions     []string
}

func newRuntime() *Runtime {
	return &Runtime{
		argv:    []string{"./app", "-port", "8080"},
		symbols: map[uint64]string{0x401000: "main.work", 0x402000: "main.main"},
		profiles: map[string]int{"goroutine": 3, "heap": 0, "allocs": 0,
			"threadcreate": 1, "block": 0, "mutex": 0},
	}
}

// lookup 返回 profile 计数；第二个返回值 false 等价 pprof.Lookup 返回 nil。
func (rt *Runtime) lookup(name string) (int, bool) {
	c, ok := rt.profiles[name]
	return c, ok
}

// query 模拟 URL 查询参数；缺席返回 ("", false)。
type query map[string]string

func (q query) get(k string) string { return q[k] }

func parseInt10(s string) (int64, bool) {
	v, err := strconv.ParseInt(s, 10, 64)
	return v, err == nil
}

// serveHandler 复刻源码 handler(name).ServeHTTP 的语义。
func serveHandler(rt *Runtime, name string, q query) *Resp {
	if _, ok := rt.lookup(name); !ok {
		return &Resp{status: 404, body: "Unknown profile\n", ctype: "text/plain; charset=utf-8"}
	}
	if sec := q.get("seconds"); sec != "" {
		n, ok := parseInt10(sec)
		if !ok || n <= 0 {
			return &Resp{status: 400, body: `invalid value for "seconds" - must be a positive integer` + "\n"}
		}
		if !deltaOK[name] {
			return &Resp{status: 400, body: `"seconds" parameter is not supported for this profile type` + "\n"}
		}
		d, _ := parseInt10(q.get("debug"))
		if d != 0 {
			return &Resp{status: 400, body: "seconds and debug params are incompatible\n"}
		}
		// 源码：collect p0 → sleep sec → collect p1 → p0.Scale(-1) → Merge
		return &Resp{status: 200, body: fmt.Sprintf("delta-profile:%s:%ds", name, n),
			ctype: "application/octet-stream",
			disposition: fmt.Sprintf(`attachment; filename="%s-delta"`, name),
			actions: []string{"collect", fmt.Sprintf("sleep %ds", n), "collect", "scale -1", "merge", "write"}}
	}
	gc, _ := parseInt10(q.get("gc"))
	if name == "heap" && gc > 0 {
		rt.gcRuns++ // 源码：runtime.GC() 仅对 heap
	}
	debug, _ := parseInt10(q.get("debug"))
	if debug != 0 {
		return &Resp{status: 200, body: fmt.Sprintf("legacy-text:%s:debug=%d", name, debug),
			ctype: "text/plain; charset=utf-8"}
	}
	return &Resp{status: 200, body: "pb-snapshot:" + name, ctype: "application/octet-stream",
		disposition: fmt.Sprintf(`attachment; filename="%s"`, name),
		actions: []string{"WriteTo debug=0"}}
}

// serveIndex 复刻源码 Index：name 空 → HTML 索引；非空 → 委托 handler(name)。
func serveIndex(rt *Runtime, q query, path string) *Resp {
	name := strings.TrimPrefix(path, "/debug/pprof/")
	if name != "" {
		return serveHandler(rt, name, q)
	}
	names := make([]string, 0, len(rt.profiles)+4)
	for n := range rt.profiles {
		names = append(names, n)
	}
	names = append(names, "cmdline", "profile", "symbol", "trace")
	sort.Strings(names) // 源码 slices.SortFunc 按名排序
	var b strings.Builder
	b.WriteString("INDEX")
	for _, n := range names {
		b.WriteString(" ")
		b.WriteString(n)
	}
	return &Resp{status: 200, body: b.String(), ctype: "text/html; charset=utf-8"}
}

// serveProfile 复刻源码 Profile：CPU 剖析，seconds 缺省 30。
func serveProfile(rt *Runtime, q query) *Resp {
	sec, ok := parseInt10(q.get("seconds"))
	if !ok || sec <= 0 {
		sec = 30
	}
	if rt.cpuBusy {
		return &Resp{status: 500, body: "Could not enable CPU profiling: CPU profiling already in use\n"}
	}
	return &Resp{status: 200, body: fmt.Sprintf("cpu-profile:%ds", sec),
		ctype: "application/octet-stream", disposition: `attachment; filename="profile"`,
		actions: []string{"StartCPUProfile(stream)", fmt.Sprintf("sleep %ds", sec), "StopCPUProfile"}}
}

// serveTrace 复刻源码 Trace：ParseFloat 支持小数，缺省 1。
func serveTrace(q query) *Resp {
	sec, err := strconv.ParseFloat(q.get("seconds"), 64)
	if err != nil || sec <= 0 {
		sec = 1
	}
	return &Resp{status: 200, body: fmt.Sprintf("trace:%g", sec),
		ctype: "application/octet-stream", disposition: `attachment; filename="trace"`,
		actions: []string{"trace.Start", fmt.Sprintf("sleep %gs", sec), "trace.Stop"}}
}

// serveCmdline 复刻源码 Cmdline：os.Args 以 NUL 连接。
func serveCmdline(rt *Runtime) *Resp {
	return &Resp{status: 200, body: strings.Join(rt.argv, "\x00")}
}

// serveSymbol 复刻源码 Symbol：'num_symbols: 1' + '0x.. 函数名'；PC 用 '+' 分隔。
func serveSymbol(rt *Runtime, raw string) *Resp {
	var b strings.Builder
	b.WriteString("num_symbols: 1\n")
	for _, word := range strings.Split(raw, "+") {
		if word == "" {
			continue
		}
		pc, err := strconv.ParseUint(word, 0, 64) // base 0：识别 0x 前缀，与源码一致
		if err != nil {
			continue
		}
		if fn, ok := rt.symbols[pc]; ok {
			fmt.Fprintf(&b, "%#x %s\n", pc, fn)
		}
	}
	return &Resp{status: 200, body: b.String()}
}

// route 模拟 DefaultServeMux：cmdline/profile/symbol/trace 四条路径比
// "/debug/pprof/" 更具体，直接命中各自 handler，不经 Index。
func route(rt *Runtime, method, path string, q query) *Resp {
	if method != "GET" {
		return &Resp{status: 405, body: "Method Not Allowed\n"}
	}
	if !strings.HasPrefix(path, "/debug/pprof/") {
		return &Resp{status: 404, body: "404 page not found\n"}
	}
	tail := strings.TrimPrefix(path, "/debug/pprof/")
	switch tail {
	case "cmdline":
		return serveCmdline(rt)
	case "profile":
		return serveProfile(rt, q)
	case "symbol":
		return serveSymbol(rt, q.get("__raw_query__"))
	case "trace":
		return serveTrace(q)
	}
	return serveIndex(rt, q, path)
}

func check(label string, cond bool) {
	if !cond {
		fmt.Println("FAIL:", label)
		panic("assertion failed: " + label)
	}
}

func main() {
	rt := newRuntime()

	// 1. 索引页：runtime profiles + 4 特殊端点，按名排序
	r := route(rt, "GET", "/debug/pprof/", nil)
	check("index ok", r.status == 200 && strings.Contains(r.ctype, "text/html"))
	check("index sorted", strings.HasPrefix(r.body, "INDEX allocs block cmdline goroutine heap mutex profile symbol threadcreate trace"))

	// 2. heap 快照：octet-stream + attachment
	r = route(rt, "GET", "/debug/pprof/heap", nil)
	check("heap snapshot", r.status == 200 && r.ctype == "application/octet-stream" &&
		r.disposition == `attachment; filename="heap"` && r.body == "pb-snapshot:heap")

	// 3. gc 参数仅 heap 生效
	route(rt, "GET", "/debug/pprof/heap", query{"gc": "1"})
	check("gc on heap", rt.gcRuns == 1)
	route(rt, "GET", "/debug/pprof/mutex", query{"gc": "1"})
	check("gc ignored for mutex", rt.gcRuns == 1)

	// 4. debug=1 → 明文 legacy text
	r = route(rt, "GET", "/debug/pprof/heap", query{"debug": "1"})
	check("debug=1 plaintext", strings.Contains(r.ctype, "text/plain") && r.body == "legacy-text:heap:debug=1")

	// 5. delta：合法 seconds
	r = route(rt, "GET", "/debug/pprof/heap", query{"seconds": "2"})
	check("delta filename", r.disposition == `attachment; filename="heap-delta"`)
	check("delta actions", len(r.actions) == 6 && r.actions[1] == "sleep 2s" && r.actions[3] == "scale -1")

	// 6. delta：非法/非正 seconds → 400
	check("seconds abc 400", route(rt, "GET", "/debug/pprof/heap", query{"seconds": "abc"}).status == 400)
	check("seconds -1 400", route(rt, "GET", "/debug/pprof/heap", query{"seconds": "-1"}).status == 400)

	// 7. 自定义 profile 不支持 delta → 400
	rt.profiles["mycompany.custom"] = 0
	r = route(rt, "GET", "/debug/pprof/mycompany.custom", query{"seconds": "1"})
	check("custom no delta", r.status == 400 && strings.Contains(r.body, "not supported"))

	// 8. seconds 与 debug 并用 → 400
	check("seconds+debug 400", route(rt, "GET", "/debug/pprof/heap",
		query{"seconds": "1", "debug": "1"}).status == 400)

	// 9. CPU profile：默认 30s；已占用 → 500
	r = route(rt, "GET", "/debug/pprof/profile", nil)
	check("cpu default 30", r.body == "cpu-profile:30s" && r.actions[1] == "sleep 30s")
	r = route(rt, "GET", "/debug/pprof/profile", query{"seconds": "5"})
	check("cpu 5s", r.body == "cpu-profile:5s")
	rt.cpuBusy = true
	check("cpu busy 500", route(rt, "GET", "/debug/pprof/profile", nil).status == 500)
	rt.cpuBusy = false

	// 10. trace：小数支持，缺省 1s
	r = route(rt, "GET", "/debug/pprof/trace", query{"seconds": "2.5"})
	check("trace 2.5", r.body == "trace:2.5")
	check("trace default 1", route(rt, "GET", "/debug/pprof/trace", nil).body == "trace:1")

	// 11. cmdline：NUL 连接
	r = route(rt, "GET", "/debug/pprof/cmdline", nil)
	check("cmdline nul", r.body == "./app\x00-port\x008080")

	// 12. symbol：PC→函数名；未知 PC 不出现
	r = route(rt, "GET", "/debug/pprof/symbol", query{"__raw_query__": "0x401000+0x402000+0x403000"})
	check("symbol maps", strings.Contains(r.body, "num_symbols: 1") &&
		strings.Contains(r.body, "main.work") && strings.Contains(r.body, "main.main"))
	check("symbol unknown pc", !strings.Contains(r.body, "0x403000"))

	// 13. 未知 profile → 404
	r = route(rt, "GET", "/debug/pprof/nosuch", nil)
	check("unknown 404", r.status == 404 && r.body == "Unknown profile\n")

	// 14. 非 GET → 405（Go 1.22 起 pattern 带 "GET " 前缀）
	check("post 405", route(rt, "POST", "/debug/pprof/heap", nil).status == 405)

	// 15. mux 精确匹配：cmdline 直达；若经 Index 委托 handler("cmdline") 会 Lookup 失败
	rt2 := newRuntime()
	check("cmdline direct", route(rt2, "GET", "/debug/pprof/cmdline", nil).body == "./app\x00-port\x008080")
	check("index delegate cmdline 404", serveHandler(rt2, "cmdline", nil).status == 404)

	fmt.Println("pprof_http: 15 组断言全部通过")
}

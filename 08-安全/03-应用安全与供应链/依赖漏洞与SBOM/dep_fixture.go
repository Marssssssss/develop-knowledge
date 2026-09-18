package main

// 依赖图 / 调用图 / CVE 数据集（与 Python 版 fixture 一致）。
// 单独成文件以满足单源文件 <=300 行的约束。

// ---- 依赖图（与 Python 版 REGISTRY / DEPS / ROOT 一致）----

var registry = map[string][]string{
	"httpkit":  {"2.3.0", "2.4.0", "3.0.0"},
	"codec":    {"1.4.2", "1.4.3", "1.5.0"},
	"compress": {"0.2.7"}, // 上游没发修复版 → 只能停在受影响版本
	"logfmt":   {"1.0.1", "1.0.2"},
	"orm":      {"3.1.0"},
}

var deps = map[string]map[string]string{
	"httpkit@2.3.0":  {"codec": "~1.4.0"},
	"httpkit@2.4.0":  {"codec": "~1.4.2"},
	"httpkit@3.0.0":  {"codec": "^1.5.0"},
	"codec@1.4.2":    {"compress": "~0.2.0"},
	"codec@1.4.3":    {"compress": "~0.2.0"},
	"codec@1.5.0":    {"compress": "^0.3.0"},
	"compress@0.2.7": {},
	"logfmt@1.0.1":   {},
	"logfmt@1.0.2":   {},
	"orm@3.1.0":      {},
}

var root = map[string]string{
	"httpkit": "^2.0.0", "logfmt": "^1.0.0", "orm": "^3.0.0",
}

// ---- 可达性 ----

var callgraph = map[string][]string{
	"app.main":         {"httpkit.Handler", "logfmt.Format", "orm.Find"},
	"httpkit.Handler":  {"codec.Decode"},
	"codec.Decode":     {},
	"compress.Inflate": {},
	"orm.Find":         {"orm.RawQuery"},
	"orm.RawQuery":     {},
	"logfmt.Format":    {},
}

const entry = "app.main"

func reachable(entry string) map[string]bool {
	seen := map[string]bool{}
	stack := []string{entry}
	for len(stack) > 0 {
		f := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		if seen[f] {
			continue
		}
		seen[f] = true
		stack = append(stack, callgraph[f]...)
	}
	return seen
}

type cve struct {
	id, pkg, spec, sym string
}

var cves = []cve{
	{"CVE-COMPRESS", "compress", ">=0.2.0 <0.2.8", "compress.Inflate"},
	{"CVE-CODEC", "codec", ">=1.4.0 <1.5.0", "codec.Decode"},
	{"CVE-ORM", "orm", ">=3.0.0 <3.1.1", "orm.RawQuery"},
	{"CVE-LOGFMT", "logfmt", ">=1.0.0 <1.0.2", "logfmt.Format"},
}

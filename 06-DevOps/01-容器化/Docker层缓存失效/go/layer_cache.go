// Docker / BuildKit 构建缓存失效规则的最小模拟器(Go 实现)。
//
// 规则依据 Docker 官方文档 "Build cache invalidation":
// https://docs.docker.com/build/cache/invalidation/
//
// 与 python/layer_cache.py 同题同算法,便于跨语言对照:
//   1. 缓存键 = 父层键 + 本指令摘要(hash 链,父层变则级联失效)
//   2. RUN 摘要只看命令字符串
//   3. COPY/ADD 摘要是文件"元数据"校验和,且 mtime 不参与
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"sort"
	"strings"
)

// Entry 是一个文件的元数据:权限位、内容、修改时间。
// mtime 显式建模,但**不参与校验和** —— 这正是官方规则。
type Entry struct {
	Mode    uint32
	Content string
	Mtime   float64
}

type FileSet map[string]Entry

func sha16(parts ...string) string {
	h := sha256.New()
	for _, p := range parts {
		h.Write([]byte(p))
		h.Write([]byte{0})
	}
	return hex.EncodeToString(h.Sum(nil))[:16]
}

// MetadataChecksum 只用 (路径, 权限位, 字节数, 内容哈希),刻意丢弃 Mtime。
func MetadataChecksum(files FileSet) string {
	paths := make([]string, 0, len(files))
	for p := range files {
		paths = append(paths, p)
	}
	sort.Strings(paths)

	h := sha256.New()
	for _, p := range paths {
		e := files[p]
		body := sha256.Sum256([]byte(e.Content))
		h.Write([]byte(fmt.Sprintf("%s\x00%o\x00%d\x00", p, e.Mode, len(e.Content))))
		h.Write([]byte(hex.EncodeToString(body[:])))
		h.Write([]byte{0})
	}
	return hex.EncodeToString(h.Sum(nil))[:16]
}

// Step 是一条 Dockerfile 指令。
type Step struct {
	Kind   string // FROM / WORKDIR / COPY / RUN / ARG
	Text   string
	Files  FileSet
	Secret *SecretRef
}

// SecretRef 对应 RUN --mount=type=secret:内容不进缓存,ID 与挂载路径进缓存。
type SecretRef struct {
	ID      string
	Content string
}

func (s Step) digest(args map[string]string, sourceDateEpoch string) string {
	switch s.Kind {
	case "COPY", "ADD":
		return sha16("copy", s.Text, MetadataChecksum(s.Files))
	case "RUN":
		if s.Secret != nil {
			return sha16("run-secret", s.Text, s.Secret.ID)
		}
		// 官方:仅用命令字符串本身找匹配,不检查容器文件系统
		return sha16("run", s.Text)
	case "WORKDIR":
		return sha16("workdir", s.Text, sourceDateEpoch)
	case "ARG":
		name := strings.Fields(s.Text)[0]
		return sha16("arg", name, args[name])
	default:
		return sha16(s.Kind, s.Text)
	}
}

// CacheStore 是内容寻址的层缓存。
type CacheStore struct {
	keys map[string]bool
}

func NewCacheStore() *CacheStore { return &CacheStore{keys: map[string]bool{}} }

func (c *CacheStore) Lookup(key string) bool {
	if c.keys[key] {
		return true
	}
	c.keys[key] = true
	return false
}

// BuildResult 记录一次构建的命中情况。
type BuildResult struct {
	Hits, Misses, RunMisses int
	Trace                   []string
}

func (r BuildResult) Summary() string {
	return fmt.Sprintf("%d hit / %d miss (RUN 重跑 %d)", r.Hits, r.Misses, r.RunMisses)
}

// Build 模拟一次 docker build。每步的键基于上一步的键,故父层 miss 会级联。
func Build(steps []Step, store *CacheStore, args map[string]string, sourceDateEpoch string) BuildResult {
	var res BuildResult
	parent := sha16("scratch")
	for _, st := range steps {
		key := sha16(parent, st.digest(args, sourceDateEpoch))
		if store.Lookup(key) {
			res.Hits++
			res.Trace = append(res.Trace, fmt.Sprintf("CACHED  %-8s %s", st.Kind, st.Text))
		} else {
			res.Misses++
			if st.Kind == "RUN" {
				res.RunMisses++
			}
			res.Trace = append(res.Trace, fmt.Sprintf("REBUILD %-8s %s", st.Kind, st.Text))
		}
		parent = key
	}
	return res
}

var deps = FileSet{"package.json": {Mode: 0o644, Content: `{"deps":["left-pad"]}`, Mtime: 1.7e9}}

func app(content string) FileSet {
	return FileSet{
		"app.py":  {Mode: 0o644, Content: content, Mtime: 1.7e9},
		"util.py": {Mode: 0o644, Content: "X = 1", Mtime: 1.7e9},
	}
}

func merge(parts ...FileSet) FileSet {
	out := FileSet{}
	for _, p := range parts {
		for k, v := range p {
			out[k] = v
		}
	}
	return out
}

// BadOrder 与 GoodOrder 是同一份源码的两种 Dockerfile 写法。
func BadOrder(files FileSet) []Step {
	return []Step{
		{Kind: "FROM", Text: "python:3.12-slim"},
		{Kind: "WORKDIR", Text: "/app"},
		{Kind: "COPY", Text: "COPY . .", Files: merge(deps, files)},
		{Kind: "RUN", Text: "pip install -r package.json"},
		{Kind: "RUN", Text: "python -m compileall ."},
	}
}

func GoodOrder(files FileSet) []Step {
	return []Step{
		{Kind: "FROM", Text: "python:3.12-slim"},
		{Kind: "WORKDIR", Text: "/app"},
		{Kind: "COPY", Text: "COPY package.json ./", Files: deps},
		{Kind: "RUN", Text: "pip install -r package.json"},
		{Kind: "COPY", Text: "COPY . .", Files: merge(deps, files)},
		{Kind: "RUN", Text: "python -m compileall ."},
	}
}

func main() {
	fmt.Println("== Docker 层缓存失效规则模拟(Go) ==")

	v1, v2 := app("print('v1')"), app("print('v2')")

	// 场景 1:好顺序 —— 只改源码时依赖层被保住
	store := NewCacheStore()
	cold := Build(GoodOrder(v1), store, nil, "0")
	fmt.Printf("好顺序 冷启动   : %s\n", cold.Summary())
	warm := Build(GoodOrder(v2), store, nil, "0")
	fmt.Printf("好顺序 改源码后 : %s\n", warm.Summary())

	// 场景 2:坏顺序 —— 同一改动代价翻倍
	bad := NewCacheStore()
	Build(BadOrder(v1), bad, nil, "0")
	warmBad := Build(BadOrder(v2), bad, nil, "0")
	fmt.Printf("坏顺序 改源码后 : %s\n", warmBad.Summary())

	// 场景 3:mtime 不参与校验和
	restamped := FileSet{}
	for k, e := range v1 {
		e.Mtime += 86400 * 30
		restamped[k] = e
	}
	fmt.Printf("只平移 mtime    : 校验和相同=%v\n",
		MetadataChecksum(v1) == MetadataChecksum(restamped))

	// 场景 4:chmod 改元数据 -> COPY 及后续失效
	chmodded := FileSet{}
	for k, e := range v1 {
		chmodded[k] = e
	}
	e := chmodded["app.py"]
	e.Mode = 0o755
	chmodded["app.py"] = e
	fmt.Printf("chmod 后        : 校验和相同=%v\n",
		MetadataChecksum(v1) == MetadataChecksum(chmodded))

	// 场景 5:构建密钥轮换不失效
	secStore := NewCacheStore()
	Build([]Step{{Kind: "RUN", Text: "some-command", Secret: &SecretRef{"TOKEN", "tkn_v1"}}},
		secStore, nil, "0")
	rotated := Build([]Step{{Kind: "RUN", Text: "some-command", Secret: &SecretRef{"TOKEN", "tkn_v2"}}},
		secStore, nil, "0")
	renamed := Build([]Step{{Kind: "RUN", Text: "some-command", Secret: &SecretRef{"TOKEN2", "tkn_v1"}}},
		secStore, nil, "0")
	fmt.Printf("密钥内容轮换    : %s | 密钥 ID 改名: %s\n", rotated.Summary(), renamed.Summary())

	// 场景 6:ARG 参与缓存 -> 可用来强制失效
	argStore := NewCacheStore()
	steps := []Step{{Kind: "ARG", Text: "CACHEBUST = 1"}, {Kind: "RUN", Text: "echo build"}}
	Build(steps, argStore, map[string]string{"CACHEBUST": "1"}, "0")
	same := Build(steps, argStore, map[string]string{"CACHEBUST": "1"}, "0")
	diff := Build(steps, argStore, map[string]string{"CACHEBUST": "2"}, "0")
	fmt.Printf("ARG 相同        : %s | ARG 改变: %s\n", same.Summary(), diff.Summary())
}

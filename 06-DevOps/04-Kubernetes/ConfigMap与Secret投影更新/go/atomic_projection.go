// ConfigMap / Secret 卷投影的原子写入 (Go),对齐 kubelet 的 AtomicWriter。
//
// 权威来源(实际读过):
//   1. https://kubernetes.io/docs/concepts/configuration/configmap/
//   2. https://cdn.jsdelivr.net/gh/kubernetes/kubernetes@master/pkg/volume/util/atomic_writer.go
//
// 关键点:更新 = 换 `..data` 符号链接的指向(rename 原子);payload 未变则不新建时间戳目录;
// 可见链接只为路径第一段建且只建一次;rename 必须在建可见链接之前。
package main

import (
	"fmt"
	"strings"
)

var checks int

func ck(cond bool, msg string) {
	if !cond {
		panic("assert failed: " + msg)
	}
	checks++
}

func main() {
	bad := func(p string) bool { return validatePath(p) != "" }
	ck(bad(""), "空路径应被拒")
	ck(bad("/etc/passwd"), "绝对路径应被拒")
	ck(bad("a/../b"), "含 .. 元素应被拒")
	ck(bad("..data/x"), "以 .. 开头且长度>2 应被拒")
	ck(bad(".."), "单独的 '..' 应被拒")
	ck(bad("..2026_01_01_00_00_00.1/x"), "时间戳目录名保留给 AtomicWriter")
	ck(!bad("foo/bar"), "正常相对路径放行")

	long255 := strings.Repeat("a", 255)
	ck(!bad(long255), "255 字符文件名放行")
	ck(bad(strings.Repeat("a", 256)), "256 字符文件名被拒")
	parts := make([]string, 16)
	for i := range parts {
		parts[i] = long255
	}
	longOK := strings.Join(parts, "/")
	parts2 := make([]string, 17)
	for i := range parts2 {
		parts2[i] = long255
	}
	longBad := strings.Join(parts2, "/")
	ck(len(longOK) == 4095 && len(longBad) == 4351, "测例长度符合预期")
	ck(!bad(longOK), "4095 字符路径放行")
	ck(bad(longBad), "4351 字符路径被拒")

	// 首次写入
	fs := NewFS()
	w := &AtomicWriter{fs: fs, target: "/mnt/cfg"}
	w.Write(map[string]string{"app.yml": "replicas: 1"})
	ts1, ok := fs.Readlink("/mnt/cfg/..data")
	ck(ok && strings.HasPrefix(ts1, ".."), "..data 应指向 .. 开头的时间戳目录")
	ck(fs.Links["/mnt/cfg/app.yml"] == "..data/app.yml", "可见文件软链到 ..data/app.yml")
	v, _ := w.ReadVisible("app.yml")
	ck(v == "replicas: 1", "读者读到内容")

	// rename 早于可见链接创建
	ck(fs.IndexOfTrace("rename /mnt/cfg/..data_tmp") < fs.IndexOfTrace("symlink /mnt/cfg/app.yml"),
		"rename 必须先于可见链接创建")

	// 内容未变 → no-op
	before := len(fs.Trace)
	w.Write(map[string]string{"app.yml": "replicas: 1"})
	ck(len(fs.Trace)-before == 1 && fs.Trace[len(fs.Trace)-1] == "noop: payload unchanged",
		"内容未变应 no-op")
	ts1b, _ := fs.Readlink("/mnt/cfg/..data")
	ck(ts1b == ts1, "no-op 后 ..data 指向不变")

	// 内容变化 → 换目录 + 清旧目录
	w.Write(map[string]string{"app.yml": "replicas: 2"})
	ts2, _ := fs.Readlink("/mnt/cfg/..data")
	ck(ts2 != ts1, "内容变化应换时间戳目录")
	v, _ = w.ReadVisible("app.yml")
	ck(v == "replicas: 2", "读者读到新内容")
	stale := false
	for p := range fs.Files {
		if strings.HasPrefix(p, "/mnt/cfg/"+ts1) {
			stale = true
		}
	}
	ck(!stale, "旧时间戳目录应被删除")

	// 可见链接只建一次
	cnt := 0
	for _, t := range fs.Trace {
		if strings.HasPrefix(t, "symlink /mnt/cfg/app.yml") {
			cnt++
		}
	}
	ck(cnt == 1, fmt.Sprintf("可见链接只应创建一次, 实得 %d", cnt))

	// 新增 / 删除 key
	w.Write(map[string]string{"app.yml": "replicas: 2", "extra.txt": "hi"})
	_, ok = w.ReadVisible("extra.txt")
	ck(ok, "新增 key 应可读")
	w.Write(map[string]string{"app.yml": "replicas: 3"})
	v, _ = w.ReadVisible("app.yml")
	ck(v == "replicas: 3", "删除 extra 后 app.yml 仍可读")
	_, ok = w.ReadVisible("extra.txt")
	ck(!ok, "被删 key 不应可读")

	// 嵌套路径只为第一段建链接
	fs2 := NewFS()
	w2 := &AtomicWriter{fs: fs2, target: "/mnt/cfg2"}
	w2.Write(map[string]string{"dir/a.yml": "a", "dir/b.yml": "b"})
	ck(fs2.Links["/mnt/cfg2/dir"] == "..data/dir", "嵌套只为第一段建链接")
	ck(fs2.Links["/mnt/cfg2/dir/a.yml"] == "", "不应为第二段建独立链接")

	// 原子性:每轮切换后 ..data 指向的目录都是完整的
	fs3 := NewFS()
	w3 := &AtomicWriter{fs: fs3, target: "/mnt/cfg3"}
	for i := 0; i < 5; i++ {
		payload := map[string]string{}
		for j := 0; j < 3; j++ {
			payload[fmt.Sprintf("k%d", j)] = fmt.Sprintf("v%d-%d", j, i)
		}
		w3.Write(payload)
		ts, _ := fs3.Readlink("/mnt/cfg3/..data")
		full := true
		for j := 0; j < 3; j++ {
			if _, ok := fs3.Files[fmt.Sprintf("/mnt/cfg3/%s/k%d", ts, j)]; !ok {
				full = false
			}
		}
		ck(full, fmt.Sprintf("第 %d 轮切换后目录必须完整", i))
	}

	// subPath / env 不更新
	sp := &SubPathMount{Data: "replicas: 1"}
	sp.Refresh("replicas: 99")
	ck(sp.Data == "replicas: 1", "subPath 挂载不应收到更新")
	env := &EnvInjection{Values: map[string]string{"LOG_LEVEL": "info"}}
	env.Refresh(map[string]string{"LOG_LEVEL": "debug"})
	ck(env.Values["LOG_LEVEL"] == "info", "env 注入不应收到更新")

	// 1 MiB 与延迟公式
	ck(configMapMaxBytes == 1048576, "1 MiB = 1048576")
	ck(totalUpdateDelay(60, "Get", 30, 0.5) == 60, "Get → 传播延迟 0")
	ck(totalUpdateDelay(60, "Watch", 30, 0.5) == 60.5, "Watch → +0.5")
	ck(totalUpdateDelay(60, "TTL", 30, 0.5) == 90, "TTL → +30")
	ck(totalUpdateDelay(60, "Get", 30, 0.5) < totalUpdateDelay(60, "Watch", 30, 0.5), "Get 更快")

	fmt.Printf("atomic_projection(go): %d assertions passed\n", checks)
}

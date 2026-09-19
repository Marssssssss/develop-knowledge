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

const (
	maxPathLength     = 4096
	maxFileNameLength = 255
	dataDirName       = "..data"
	newDataDirName    = "..data_tmp"
	configMapMaxBytes = 1024 * 1024 // 1 MiB
)

func validatePath(p string) string {
	if p == "" {
		return "invalid path: must be relative path"
	}
	if strings.HasPrefix(p, "/") {
		return "invalid path: must be relative path: " + p
	}
	if len(p) > maxPathLength {
		return "invalid path: must be less than or equal to 4096 characters"
	}
	items := strings.Split(p, "/")
	for _, it := range items {
		if it == ".." {
			return "invalid path: must not contain '..': " + p
		}
		if len(it) > maxFileNameLength {
			return "invalid path: filenames must be less than or equal to 255 characters"
		}
	}
	if strings.HasPrefix(items[0], "..") && len(items[0]) > 2 {
		return "invalid path: must not start with '..': " + p
	}
	return ""
}

// FS:够用的内存文件系统。
type FS struct {
	Files map[string]string
	Links map[string]string
	Trace []string
}

func NewFS() *FS {
	return &FS{Files: map[string]string{}, Links: map[string]string{}}
}
func (fs *FS) WriteFile(p, data string) { fs.Files[p] = data; fs.Trace = append(fs.Trace, "write "+p) }
func (fs *FS) Symlink(target, link string) {
	fs.Links[link] = target
	fs.Trace = append(fs.Trace, "symlink "+link+" -> "+target)
}
func (fs *FS) Rename(old, new string) {
	if v, ok := fs.Links[old]; ok {
		delete(fs.Links, old)
		fs.Links[new] = v
	} else if v, ok := fs.Files[old]; ok {
		delete(fs.Files, old)
		fs.Files[new] = v
	}
	fs.Trace = append(fs.Trace, "rename "+old+" -> "+new)
}
func (fs *FS) Readlink(p string) (string, bool) { v, ok := fs.Links[p]; return v, ok }
func (fs *FS) Remove(p string) {
	delete(fs.Files, p)
	delete(fs.Links, p)
	fs.Trace = append(fs.Trace, "remove "+p)
}
func (fs *FS) RemoveTree(prefix string) {
	for p := range fs.Files {
		if strings.HasPrefix(p, prefix+"/") {
			fs.Remove(p)
		}
	}
	for p := range fs.Links {
		if strings.HasPrefix(p, prefix+"/") {
			fs.Remove(p)
		}
	}
}
func (fs *FS) IndexOfTrace(prefix string) int {
	for i, t := range fs.Trace {
		if strings.HasPrefix(t, prefix) {
			return i
		}
	}
	return -1
}

type AtomicWriter struct {
	fs     *FS
	target string
	tsSeq  int
}

func (w *AtomicWriter) p(rel string) string { return w.target + "/" + rel }

func (w *AtomicWriter) newTimestampDir() string {
	w.tsSeq++
	name := fmt.Sprintf("..2026_09_19_16_40_05.%08d", w.tsSeq)
	w.fs.Trace = append(w.fs.Trace, "mkdir "+w.p(name))
	return name
}

func (w *AtomicWriter) shouldWrite(payload map[string]string, oldTs string, hasOld bool) bool {
	if !hasOld {
		return true
	}
	for k, v := range payload {
		if cur, ok := w.fs.Files[w.p(oldTs+"/"+k)]; !ok || cur != v {
			return true
		}
	}
	return false
}

func (w *AtomicWriter) Write(payload map[string]string) {
	for k := range payload {
		if err := validatePath(k); err != "" {
			panic(err)
		}
	}
	oldTs, hasOld := w.fs.Readlink(w.p(dataDirName))
	if !w.shouldWrite(payload, oldTs, hasOld) {
		w.fs.Trace = append(w.fs.Trace, "noop: payload unchanged")
		return
	}
	newTs := w.newTimestampDir()
	for k, v := range payload {
		w.fs.WriteFile(w.p(newTs+"/"+k), v)
	}
	w.fs.Symlink(newTs, w.p(newDataDirName))
	w.fs.Rename(w.p(newDataDirName), w.p(dataDirName)) // 原子切换
	visible := map[string]bool{}
	for k := range payload {
		first := strings.Split(k, "/")[0]
		visible[first] = true
		if _, ok := w.fs.Readlink(w.p(first)); !ok {
			if _, isFile := w.fs.Files[w.p(first)]; !isFile {
				w.fs.Symlink(dataDirName+"/"+first, w.p(first))
			}
		}
	}
	for p := range w.fs.Links {
		rel := strings.TrimPrefix(p, w.target+"/")
		if !strings.Contains(rel, "/") && !strings.HasPrefix(rel, "..") && !visible[rel] {
			w.fs.Remove(p)
		}
	}
	if hasOld {
		w.fs.RemoveTree(w.p(oldTs))
	}
}

func (w *AtomicWriter) ReadVisible(name string) (string, bool) {
	first := strings.Split(name, "/")[0]
	if _, ok := w.fs.Readlink(w.p(first)); !ok {
		return "", false
	}
	ts, ok := w.fs.Readlink(w.p(dataDirName))
	if !ok {
		return "", false
	}
	v, ok := w.fs.Files[w.p(ts+"/"+name)]
	return v, ok
}

func totalUpdateDelay(syncPeriod float64, strategy string, ttl, watchDelay float64) float64 {
	switch strategy {
	case "Watch":
		return syncPeriod + watchDelay
	case "TTL":
		return syncPeriod + ttl
	case "Get":
		return syncPeriod
	}
	panic("unknown strategy: " + strategy)
}

type SubPathMount struct{ Data string }

func (s *SubPathMount) Refresh(string) {} // 文档:subPath 挂载不接收更新

type EnvInjection struct{ Values map[string]string }

func (e *EnvInjection) Refresh(map[string]string) {} // 文档:env 注入不更新

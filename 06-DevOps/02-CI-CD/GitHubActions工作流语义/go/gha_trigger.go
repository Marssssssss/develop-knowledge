// Package main —— GitHub Actions `on:` 触发过滤与 `strategy.matrix` 展开语义模型(Go 对照实现)。
//
// 语义依据同 python/gha_trigger.py 头部的官方文档引用。与 Python 版的差异只在两处:
//  1. 轴顺序显式用 []Axis 声明, 不依赖 map 遍历顺序(Go 的 map 顺序随机);
//  2. 模式编译结果按需缓存, 避免在循环里重复 MustCompile。
package main

import (
	"fmt"
	"regexp"
	"strings"
)

var patCache = map[string]*regexp.Regexp{}

// PatternToRegex 把 GitHub 过滤器模式编译成正则。
// `*` -> 不含 `/` 的任意串; `**` -> 任意串; `?`/`+` 是修饰前一个字符的量词; `\` 转义。
func PatternToRegex(pat string) *regexp.Regexp {
	if re, ok := patCache[pat]; ok {
		return re
	}
	var b strings.Builder
	b.WriteString("^")
	emitted := 0
	for i := 0; i < len(pat); i++ {
		c := pat[i]
		switch {
		case c == '\\' && i+1 < len(pat):
			b.WriteString(regexp.QuoteMeta(string(pat[i+1])))
			i++
			emitted++
		case c == '*':
			if i+1 < len(pat) && pat[i+1] == '*' {
				b.WriteString(".*")
				i++
			} else {
				b.WriteString("[^/]*")
			}
			emitted++
		case c == '?' || c == '+':
			if emitted > 0 {
				b.WriteByte(c) // 量词: 修饰前一个已发出的原子
			} else {
				b.WriteString(regexp.QuoteMeta(string(c)))
				emitted++
			}
		default:
			b.WriteString(regexp.QuoteMeta(string(c)))
			emitted++
		}
	}
	b.WriteString("$")
	re := regexp.MustCompile(b.String())
	patCache[pat] = re
	return re
}

// MatchOrdered 正向列表的顺序语义: 最后命中的模式胜出。
func MatchOrdered(patterns []string, value string) bool {
	decision := false
	for _, p := range patterns {
		neg := strings.HasPrefix(p, "!")
		body := p
		if neg {
			body = p[1:]
		}
		if PatternToRegex(body).MatchString(value) {
			decision = !neg
		}
	}
	return decision
}

// MatchAny `*-ignore` 列表: 命中任意一条即排除。
func MatchAny(patterns []string, value string) bool {
	for _, p := range patterns {
		if PatternToRegex(p).MatchString(value) {
			return true
		}
	}
	return false
}

// Event 描述一次触发事件。
type Event struct {
	Name        string
	Ref         string
	RefType     string // "branch" | "tag"
	Types       []string
	Paths       []string
	CommitCount int
	FileCount   int
}

// Filter 是 on.<event> 下的过滤器声明。
type Filter struct {
	Types         []string
	Branches      []string
	BranchesIgnore []string
	Tags          []string
	TagsIgnore    []string
	Paths         []string
	PathsIgnore   []string
}

var refEvents = map[string]bool{"push": true, "pull_request": true, "pull_request_target": true}

// Evaluate 返回 (是否运行, 原因)。校验类冲突返回 error。
func Evaluate(name string, f *Filter, ev Event) (bool, string, error) {
	if f == nil {
		return true, "事件无过滤器, 直接运行", nil
	}
	if len(f.Types) > 0 && len(ev.Types) > 0 {
		hit := false
		for _, a := range f.Types {
			for _, b := range ev.Types {
				if a == b {
					hit = true
				}
			}
		}
		if !hit {
			return false, "types 与事件活动类型无交集", nil
		}
	}
	if !refEvents[name] {
		return true, "该事件无 ref/路径过滤器, 直接运行", nil
	}

	if ev.RefType == "tag" {
		if len(f.Tags) > 0 && len(f.TagsIgnore) > 0 {
			return false, "", fmt.Errorf("tags 与 tags-ignore 不能同时出现")
		}
		if (len(f.Branches) > 0 || len(f.BranchesIgnore) > 0) &&
			len(f.Tags) == 0 && len(f.TagsIgnore) == 0 {
			return false, "只声明了 branches*, 标签推送不触发", nil
		}
		if len(f.Tags) > 0 && !MatchOrdered(f.Tags, ev.Ref) {
			return false, "tags 模式未命中", nil
		}
		if len(f.TagsIgnore) > 0 && MatchAny(f.TagsIgnore, ev.Ref) {
			return false, "命中 tags-ignore", nil
		}
		return true, "标签推送通过; 路径过滤不适用于标签", nil
	}

	if len(f.Branches) > 0 && len(f.BranchesIgnore) > 0 {
		return false, "", fmt.Errorf("branches 与 branches-ignore 不能同时出现")
	}
	if (len(f.Tags) > 0 || len(f.TagsIgnore) > 0) &&
		len(f.Branches) == 0 && len(f.BranchesIgnore) == 0 {
		return false, "只声明了 tags*, 分支推送不触发", nil
	}
	if len(f.Branches) > 0 && !MatchOrdered(f.Branches, ev.Ref) {
		return false, "branches 模式未命中", nil
	}
	if len(f.BranchesIgnore) > 0 && MatchAny(f.BranchesIgnore, ev.Ref) {
		return false, "命中 branches-ignore", nil
	}

	if len(f.Paths) > 0 && len(f.PathsIgnore) > 0 {
		return false, "", fmt.Errorf("paths 与 paths-ignore 不能同时出现")
	}
	if len(f.Paths) == 0 && len(f.PathsIgnore) == 0 {
		return true, "无路径过滤", nil
	}
	if ev.CommitCount > 1000 {
		return true, "提交数 > 1000 -> 总是运行(官方行为)", nil
	}
	if len(f.PathsIgnore) > 0 {
		all := len(ev.Paths) > 0
		for _, p := range ev.Paths {
			if !MatchAny(f.PathsIgnore, p) {
				all = false
			}
		}
		if all {
			return false, "全部变更路径命中 paths-ignore", nil
		}
		return true, "存在未命中 paths-ignore 的路径", nil
	}
	for _, p := range ev.Paths {
		if MatchOrdered(f.Paths, p) {
			return true, "存在命中 paths 的路径", nil
		}
	}
	if ev.FileCount > 3000 {
		return true, "变更文件 > 3000 且匹配项可能不在前 3000 个", nil
	}
	return false, "无路径命中 paths(且未超 3000 文件)", nil
}

// ---------- strategy.matrix ----------

// Axis 显式声明一个矩阵轴(避免依赖 map 遍历顺序)。
type Axis struct {
	Name   string
	Values []string
}

// Matrix 保存轴与 include/exclude。
type Matrix struct {
	Axes    []Axis
	Include []map[string]string
	Exclude []map[string]string
}

// Expand 按官方 include/exclude 语义展开矩阵。
func Expand(m Matrix) []map[string]string {
	combos := []map[string]string{{}}
	for _, ax := range m.Axes {
		var next []map[string]string
		for _, base := range combos {
			for _, v := range ax.Values {
				c := map[string]string{}
				for k, val := range base {
					c[k] = val
				}
				c[ax.Name] = v
				next = append(next, c)
			}
		}
		combos = next
	}
	origins := make([]map[string]string, len(combos))
	for i, c := range combos {
		o := map[string]string{}
		for k, v := range c {
			o[k] = v
		}
		origins[i] = o
	}

	if len(m.Exclude) > 0 {
		var keepC []map[string]string
		var keepO []map[string]string
		for i, c := range combos {
			drop := false
			for _, ex := range m.Exclude {
				matched := true
				for k, v := range ex {
					if origins[i][k] != v {
						matched = false
					}
				}
				if matched {
					drop = true
				}
			}
			if !drop {
				keepC = append(keepC, c)
				keepO = append(keepO, origins[i])
			}
		}
		combos, origins = keepC, keepO
	}

	for _, inc := range m.Include {
		if len(inc) == 0 {
			continue
		}
		applied := false
		for i, c := range combos {
			o := origins[i]
			if o == nil {
				continue // include 新建的组合不再被后续 include 合并
			}
			ok := true
			for k, v := range inc {
				if ov, exists := o[k]; exists && ov != v {
					ok = false
				}
			}
			if ok {
				for k, v := range inc {
					c[k] = v
				}
				applied = true
			}
		}
		if !applied {
			c := map[string]string{}
			for k, v := range inc {
				c[k] = v
			}
			combos = append(combos, c)
			origins = append(origins, nil)
		}
	}
	return combos
}

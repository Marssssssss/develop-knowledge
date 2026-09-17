// SemVer 2.0.0 解析与优先级比较(Go 对照实现)。零第三方依赖, 仅标准库。
//
// 权威依据: semver.org 规范原文(semver/semver 仓库的 semver.md, 本 demo 实读):
// 前导零非法、预发布低于正规版本、构建元数据不参与比较、数字标识符恒低于非数字、
// 前缀相等时标识符多者优先级更高。官方给的带编号捕获组正则原样使用。
package main

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
)

// semverRE 是官方给出的「带编号捕获组」正则(ECMA/PCRE/Python/Go 通用)。
var semverRE = regexp.MustCompile(
	`^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)` +
		`(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)` +
		`(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?` +
		`(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$`)

// Version 是一个合法的语义化版本;Build 不参与优先级比较。
type Version struct {
	Major int
	Minor int
	Patch int
	Pre   []string
	Build []string
}

// ParseVersion 解析并校验;非法版本一律报错(不静默降级)。
func ParseVersion(text string) (Version, error) {
	m := semverRE.FindStringSubmatch(text)
	if m == nil {
		return Version{}, fmt.Errorf("不是合法的 SemVer 2.0.0: %q", text)
	}
	major, err1 := strconv.Atoi(m[1])
	minor, err2 := strconv.Atoi(m[2])
	patch, err3 := strconv.Atoi(m[3])
	if err1 != nil || err2 != nil || err3 != nil {
		return Version{}, fmt.Errorf("版本号数值溢出: %q", text)
	}
	v := Version{Major: major, Minor: minor, Patch: patch}
	if m[4] != "" {
		v.Pre = strings.Split(m[4], ".")
	}
	if m[5] != "" {
		v.Build = strings.Split(m[5], ".")
	}
	return v, nil
}

// MustVersion 供断言使用: 解析失败直接 panic, 让测试立刻炸出来。
func MustVersion(text string) Version {
	v, err := ParseVersion(text)
	if err != nil {
		panic(err)
	}
	return v
}

func (v Version) String() string {
	out := fmt.Sprintf("%d.%d.%d", v.Major, v.Minor, v.Patch)
	if len(v.Pre) > 0 {
		out += "-" + strings.Join(v.Pre, ".")
	}
	if len(v.Build) > 0 {
		out += "+" + strings.Join(v.Build, ".")
	}
	return out
}

// IsPrerelease 报告是否有预发布标识符。
func (v Version) IsPrerelease() bool { return len(v.Pre) > 0 }

// Compare 按官方优先级规则返回 -1/0/1;构建元数据不参与。
func (v Version) Compare(o Version) int {
	if c := cmpInt(v.Major, o.Major); c != 0 {
		return c
	}
	if c := cmpInt(v.Minor, o.Minor); c != 0 {
		return c
	}
	if c := cmpInt(v.Patch, o.Patch); c != 0 {
		return c
	}
	if len(v.Pre) == 0 && len(o.Pre) == 0 {
		return 0
	}
	// 有预发布的一方优先级更低
	if len(v.Pre) == 0 {
		return 1
	}
	if len(o.Pre) == 0 {
		return -1
	}
	n := len(v.Pre)
	if len(o.Pre) < n {
		n = len(o.Pre)
	}
	for i := 0; i < n; i++ {
		if c := CompareIdentifier(v.Pre[i], o.Pre[i]); c != 0 {
			return c
		}
	}
	// 前缀全相等: 标识符更多的一方优先级更高
	return cmpInt(len(v.Pre), len(o.Pre))
}

// CompareIdentifier 实现官方标识符比较: 纯数字按数值; 数字恒低于非数字; 否则 ASCII 序。
func CompareIdentifier(a, b string) int {
	aNum, bNum := isDigits(a), isDigits(b)
	switch {
	case aNum && bNum:
		ai, _ := strconv.Atoi(a)
		bi, _ := strconv.Atoi(b)
		return cmpInt(ai, bi)
	case aNum:
		return -1
	case bNum:
		return 1
	}
	return strings.Compare(a, b)
}

// Bump 按官方递增规则产生下一个版本;递增时清空预发布与构建元数据。
func (v Version) Bump(kind string) (Version, error) {
	switch kind {
	case "major":
		return Version{Major: v.Major + 1}, nil
	case "minor":
		return Version{Major: v.Major, Minor: v.Minor + 1}, nil
	case "patch":
		return Version{Major: v.Major, Minor: v.Minor, Patch: v.Patch + 1}, nil
	case "prerelease":
		// 本规范未规定预发布递增;这里是工程约定: 下一个 patch 的 -0
		return Version{Major: v.Major, Minor: v.Minor, Patch: v.Patch + 1,
			Pre: []string{"0"}}, nil
	}
	return Version{}, fmt.Errorf("未知的递增类型: %s", kind)
}

// Highest 返回优先级最高的版本;includePrerelease 为 false 时忽略预发布。
func Highest(texts []string, includePrerelease bool) (Version, bool) {
	var best Version
	found := false
	for _, t := range texts {
		v, err := ParseVersion(t)
		if err != nil {
			continue
		}
		if v.IsPrerelease() && !includePrerelease {
			continue
		}
		if !found || v.Compare(best) > 0 {
			best, found = v, true
		}
	}
	return best, found
}

func cmpInt(a, b int) int {
	switch {
	case a > b:
		return 1
	case a < b:
		return -1
	}
	return 0
}

// isDigits 只认 ASCII 0-9(与规范 identifier 字符集一致)。
func isDigits(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}

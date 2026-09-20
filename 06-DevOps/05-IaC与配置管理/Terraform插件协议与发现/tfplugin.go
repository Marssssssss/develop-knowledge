// Terraform 插件协议、发现与版本选择模型。
// 口径与 Python 版一致，来自实际读过的官方原文（见 README 参考资料）。
package main

import (
	"fmt"
	"sort"
	"strconv"
	"strings"
)

var GoodRPCs = []string{"GetProviderSchema", "ConfigureProvider", "ValidateResourceTypeConfig",
	"ReadResource", "PlanResourceChange", "ApplyResourceChange",
	"ImportResourceState", "ReadDataSource"}

func ParseVersion(s string) []int {
	var out []int
	for _, p := range strings.Split(s, ".") {
		n, _ := strconv.Atoi(strings.TrimSpace(p))
		out = append(out, n)
	}
	return out
}

func cmpVer(a, b []int) int {
	for i := 0; i < len(a) || i < len(b); i++ {
		x, y := 0, 0
		if i < len(a) {
			x = a[i]
		}
		if i < len(b) {
			y = b[i]
		}
		if x < y {
			return -1
		}
		if x > y {
			return 1
		}
	}
	return 0
}

// upperOfPessimistic：`~>` 允许最右侧分量递增：~>1.0.4 → <1.1.0；~>1.0 → <2.0。
func upperOfPessimistic(parts []int) []int {
	if len(parts) < 2 {
		return nil
	}
	up := make([]int, len(parts)-1)
	copy(up, parts[:len(parts)-1])
	up[len(up)-1]++
	return up
}

// Satisfies 支持 = != > >= < <= ~>，逗号分隔表示多条都要满足。
func Satisfies(ver, constraint string) (bool, error) {
	v := ParseVersion(ver)
	for _, raw := range strings.Split(constraint, ",") {
		c := strings.TrimSpace(raw)
		if c == "" {
			continue
		}
		if strings.HasPrefix(c, "~>") {
			low := ParseVersion(strings.TrimSpace(c[2:]))
			up := upperOfPessimistic(low)
			if cmpVer(v, low) < 0 {
				return false, nil
			}
			if up != nil && cmpVer(v, up) >= 0 {
				return false, nil
			}
			continue
		}
		matched := false
		for _, op := range []string{"!=", ">=", "<=", "=", ">", "<"} {
			if !strings.HasPrefix(c, op) {
				continue
			}
			o := ParseVersion(strings.TrimSpace(c[len(op):]))
			var ok bool
			switch op {
			case "=":
				ok = cmpVer(v, o) == 0
			case "!=":
				ok = cmpVer(v, o) != 0
			case ">":
				ok = cmpVer(v, o) > 0
			case ">=":
				ok = cmpVer(v, o) >= 0
			case "<":
				ok = cmpVer(v, o) < 0
			case "<=":
				ok = cmpVer(v, o) <= 0
			}
			if !ok {
				return false, nil
			}
			matched = true
			break
		}
		if !matched {
			return false, fmt.Errorf("无法识别的约束: %q", c)
		}
	}
	return true, nil
}

// CliProtocolMajors：v6 兼容 CLI 1.0+，v5 兼容 0.12+。
func CliProtocolMajors(cliVersion string) map[int]bool {
	v := ParseVersion(cliVersion)
	majors := map[int]bool{}
	if cmpVer(v, []int{0, 12}) >= 0 {
		majors[5] = true
	}
	if cmpVer(v, []int{1, 0}) >= 0 {
		majors[6] = true
	}
	return majors
}

// Negotiate：主版本划分兼容性 → 取双方共有的最大主版本；
// 次版本叠加 → 有效次版本取双方较小者（本 demo 口径，README 已标注）。
func Negotiate(cliMax []int, pluginProtos [][2]int) ([2]int, error) {
	cliMajors := map[int]bool{5: true}
	if cliMax[0] >= 6 {
		cliMajors[6] = true
	}
	common := []int{}
	for m := range cliMajors {
		for _, p := range pluginProtos {
			if p[0] == m {
				common = append(common, m)
				break
			}
		}
	}
	if len(common) == 0 {
		var plug []int
		for _, p := range pluginProtos {
			plug = append(plug, p[0])
		}
		return [2]int{}, fmt.Errorf("incompatible protocol version: 插件提供 %v", plug)
	}
	sort.Ints(common)
	major := common[len(common)-1]
	cliMinor := 0
	if len(cliMax) > 1 {
		cliMinor = cliMax[1]
	}
	plugMinor := 0
	for _, p := range pluginProtos {
		if p[0] == major && p[1] > plugMinor {
			plugMinor = p[1]
		}
	}
	minor := cliMinor
	if plugMinor < minor {
		minor = plugMinor
	}
	return [2]int{major, minor}, nil
}

type RegEntry struct {
	Version  string
	Protocol int
}

func acceptable(cands []string, constraint string) ([]string, error) {
	var out []string
	for _, c := range cands {
		ok, err := Satisfies(c, constraint)
		if err != nil {
			return nil, err
		}
		if ok {
			out = append(out, c)
		}
	}
	sort.Slice(out, func(i, j int) bool { return cmpVer(ParseVersion(out[i]), ParseVersion(out[j])) < 0 })
	return out, nil
}

// SelectVersion 返回 (version, source)；source ∈ locked/installed/registry/failed。
// 官方三条规则：lock 优先 → 已安装里最新的可接受（即使 registry 有更新的）
// → registry 最新的可接受 → 都无则 failed。
func SelectVersion(constraint string, installed []string, registry []RegEntry,
	lock string, hasLock bool) (string, string, error) {
	if hasLock {
		ok, err := Satisfies(lock, constraint)
		if err != nil {
			return "", "", err
		}
		if ok {
			return lock, "locked", nil
		}
	}
	ins, err := acceptable(installed, constraint)
	if err != nil {
		return "", "", err
	}
	if len(ins) > 0 {
		return ins[len(ins)-1], "installed", nil
	}
	var regs []string
	for _, r := range registry {
		regs = append(regs, r.Version)
	}
	reg, err := acceptable(regs, constraint)
	if err != nil {
		return "", "", err
	}
	if len(reg) > 0 {
		return reg[len(reg)-1], "registry", nil
	}
	return "", "failed", nil
}

// SelectWithProtocol：registry 发现时把「协议版本」当作额外兼容性元数据参与筛选。
func SelectWithProtocol(constraint string, installed []string, registry []RegEntry,
	cliVersion string, lock string, hasLock bool) (string, string, error) {
	m := CliProtocolMajors(cliVersion)
	var visible []RegEntry
	for _, r := range registry {
		if m[r.Protocol] {
			visible = append(visible, r)
		}
	}
	return SelectVersion(constraint, installed, visible, lock, hasLock)
}

// Handshake：go-plugin 先校验 magic cookie，再校验 protocol version。
func Handshake(pluginStdout, cookieKey, cookieValue string, pluginProto, hostProto int) (bool, string) {
	want := cookieKey + "=" + cookieValue
	found := false
	for _, ln := range strings.Split(pluginStdout, "\n") {
		if strings.TrimSpace(ln) == want {
			found = true
			break
		}
	}
	if !found {
		return false, "magic cookie 不匹配：这不是一个能被本宿主识别的插件"
	}
	if pluginProto != hostProto {
		return false, fmt.Sprintf("incompatible protocol version: 插件 %d，宿主 %d", pluginProto, hostProto)
	}
	return true, "ok"
}

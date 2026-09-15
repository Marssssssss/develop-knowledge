// cgroup v2 层级 + eBPF 程序附加 + BPF token 委派(Go 实现)。
//
// 来源:
//   https://docs.kernel.org/admin-guide/cgroup-v2.html
//   https://docs.kernel.org/bpf/libbpf/program_types.html
//   http://docs.ebpf.io/linux/concepts/token/
package main

import (
	"fmt"
	"sort"
	"strings"
)

// sectionTable 是 libbpf 的 ELF section 名 -> (程序类型, attach 类型) 映射(节选 cgroup 部分)。
var sectionTable = map[string][2]string{
	"cgroup/dev":         {"BPF_PROG_TYPE_CGROUP_DEVICE", "BPF_CGROUP_DEVICE"},
	"cgroup/skb":         {"BPF_PROG_TYPE_CGROUP_SKB", "BPF_CGROUP_INET_INGRESS"},
	"cgroup_skb/ingress": {"BPF_PROG_TYPE_CGROUP_SKB", "BPF_CGROUP_INET_INGRESS"},
	"cgroup_skb/egress":  {"BPF_PROG_TYPE_CGROUP_SKB", "BPF_CGROUP_INET_EGRESS"},
	"cgroup/getsockopt":  {"BPF_PROG_TYPE_CGROUP_SOCKOPT", "BPF_CGROUP_GETSOCKOPT"},
	"cgroup/connect4":    {"BPF_PROG_TYPE_CGROUP_SOCK_ADDR", "BPF_CGROUP_INET4_CONNECT"},
	"cgroup/sendmsg4":    {"BPF_PROG_TYPE_CGROUP_SOCK_ADDR", "BPF_CGROUP_UDP4_SENDMSG"},
	"cgroup/post_bind4":  {"BPF_PROG_TYPE_CGROUP_SOCK", "BPF_CGROUP_INET4_POST_BIND"},
	"cgroup/sock_create": {"BPF_PROG_TYPE_CGROUP_SOCK", "BPF_CGROUP_INET_SOCK_CREATE"},
	"cgroup/sysctl":      {"BPF_PROG_TYPE_CGROUP_SYSCTL", "BPF_CGROUP_SYSCTL"},
	"sockops":            {"BPF_PROG_TYPE_SOCK_OPS", "BPF_CGROUP_SOCK_OPS"},
}

var nonCgroupSections = []string{"xdp", "tc", "classifier", "socket", "kprobe"}

// domainControllers 是"域控制器"集合:含进程的 cgroup 不能启用它们。
var domainControllers = map[string]bool{
	"cpu": true, "cpuset": true, "io": true, "memory": true, "pids": true, "hugetlb": true,
}

// ResolveSection 解析 SEC() 名。非 cgroup 类程序不能附加到 cgroup。
func ResolveSection(section string) ([2]string, error) {
	for _, s := range nonCgroupSections {
		if s == section {
			return [2]string{}, fmt.Errorf("EINVAL: %s 不是 cgroup 类程序,不能附加到 cgroup", section)
		}
	}
	v, ok := sectionTable[section]
	if !ok {
		return [2]string{}, fmt.Errorf("EINVAL: 未知的 section 名 %q", section)
	}
	return v, nil
}

// Cgroup 是一个 cgroup v2 节点。
type Cgroup struct {
	Path        string
	Procs       []int
	Controllers []string            // cgroup.controllers(只读)
	Subtree     []string            // cgroup.subtree_control
	Attached    map[string][]string // attach_type -> 程序名(按挂载顺序)
	Children    []*Cgroup
}

func NewCgroup(path string, controllers ...string) *Cgroup {
	return &Cgroup{Path: path, Controllers: controllers, Attached: map[string][]string{}}
}

// AttachProgram 对应 bpf(BPF_PROG_ATTACH) 到 cgroup fd。
func AttachProgram(cg *Cgroup, section, name string) ([2]string, error) {
	t, err := ResolveSection(section)
	if err != nil {
		return t, err
	}
	cg.Attached[t[1]] = append(cg.Attached[t[1]], name)
	return t, nil
}

// DetachProgram 对应 bpf(BPF_PROG_DETACH)。
func DetachProgram(cg *Cgroup, attachType, name string) error {
	lst := cg.Attached[attachType]
	for i, n := range lst {
		if n == name {
			cg.Attached[attachType] = append(lst[:i], lst[i+1:]...)
			return nil
		}
	}
	return fmt.Errorf("ENOENT: %s 未挂在 %s 上", name, attachType)
}

func contains(xs []string, s string) bool {
	for _, x := range xs {
		if x == s {
			return true
		}
	}
	return false
}

func childEnabled(parent *Cgroup, name string) bool {
	for _, c := range parent.Children {
		if contains(c.Subtree, name) {
			return true
		}
	}
	return false
}

// WriteSubtreeControl 模拟 `echo '+cpu +memory -io' > cgroup.subtree_control`。
// H1 只能启用 cgroup.controllers 中的;H2 全有或全无;H3 重复项最后一个生效;
// H4 子仍启用时不得禁用;H5 含进程时不得启用域控制器(根豁免);H6 影响子节点接口文件。
func WriteSubtreeControl(cg *Cgroup, ops string, isRoot bool) ([]string, error) {
	tokens := strings.Fields(ops)
	if len(tokens) == 0 {
		return cg.Subtree, fmt.Errorf("EINVAL: 空操作")
	}
	final := map[string]bool{}
	order := []string{}
	for _, t := range tokens {
		if t[0] != '+' && t[0] != '-' {
			return cg.Subtree, fmt.Errorf("EINVAL: 操作必须以 + 或 - 开头: %s", t)
		}
		name := t[1:]
		if _, seen := final[name]; !seen {
			order = append(order, name)
		}
		final[name] = t[0] == '+'
	}
	// H1 + H5:先全部校验
	for name, enable := range final {
		if !enable {
			continue
		}
		if !contains(cg.Controllers, name) {
			return cg.Subtree, fmt.Errorf("EINVAL: %s 不在 cgroup.controllers 中", name)
		}
		if !isRoot && len(cg.Procs) > 0 && domainControllers[name] {
			return cg.Subtree, fmt.Errorf("EBUSY: cgroup 含 %d 个进程,不能启用域控制器 %s",
				len(cg.Procs), name)
		}
	}
	// H4
	for name, enable := range final {
		if !enable && contains(cg.Subtree, name) && childEnabled(cg, name) {
			return cg.Subtree, fmt.Errorf("EBUSY: 子节点仍启用 %s,父节点不能禁用", name)
		}
	}
	result := append([]string{}, cg.Subtree...)
	for _, name := range order { // H3:按首次出现顺序处理,值取最后一次
		if final[name] && !contains(result, name) {
			result = append(result, name)
		} else if !final[name] && contains(result, name) {
			idx := 0
			for i, x := range result {
				if x == name {
					idx = i
				}
			}
			result = append(result[:idx], result[idx+1:]...)
		}
	}
	cg.Subtree = result
	// H6:为子节点补接口文件
	for _, c := range cg.Children {
		set := map[string]bool{}
		for _, x := range c.Controllers {
			set[x] = true
		}
		for _, x := range result {
			set[x] = true
		}
		merged := []string{}
		for x := range set {
			merged = append(merged, x)
		}
		sort.Strings(merged)
		c.Controllers = merged
	}
	return result, nil
}

// Userns 是用户命名空间。
type Userns struct {
	Name   string
	Caps   map[string]bool
	IsInit bool
}

// BpfToken 从带委派挂载选项的 BPF FS 派生。
type BpfToken struct {
	OwningUserns string
	Progs        map[string]bool
	Cmds         map[string]bool
	Maps         map[string]bool
	Attachs      map[string]bool
}

// progCapRequirement:TC/XDP 需 CAP_BPF+CAP_NET_ADMIN;tracing 需 CAP_BPF+CAP_PERFMON。
var progCapRequirement = map[string][]string{
	"BPF_PROG_TYPE_CGROUP_SKB":       {"CAP_BPF", "CAP_NET_ADMIN"},
	"BPF_PROG_TYPE_CGROUP_SOCK_ADDR": {"CAP_BPF", "CAP_NET_ADMIN"},
	"BPF_PROG_TYPE_CGROUP_DEVICE":    {"CAP_BPF", "CAP_SYS_ADMIN"},
	"BPF_PROG_TYPE_CGROUP_SYSCTL":    {"CAP_BPF", "CAP_SYS_ADMIN"},
	"BPF_PROG_TYPE_KPROBE":           {"CAP_BPF", "CAP_PERFMON"},
}

// CanLoad 判定能否加载程序类型:无 token 时能力须在 init userns;
// 有 token 时改用 ns_capable(token 所属 userns),但仍要求进程在该 userns 内有能力。
func CanLoad(progType string, caller Userns, token *BpfToken) bool {
	need, ok := progCapRequirement[progType]
	if !ok {
		return false
	}
	if token == nil {
		if !caller.IsInit {
			return false
		}
	} else {
		if token.OwningUserns != caller.Name {
			return false // 跨 userns 无效
		}
		if !token.Progs[progType] {
			return false // 未委派该程序类型
		}
		if !caller.Caps["CAP_BPF"] {
			return false // token 不足以单独授权
		}
	}
	for _, c := range need {
		if !caller.Caps[c] {
			return false
		}
	}
	return true
}

func main() {
	fmt.Println("== cgroup v2 + eBPF 附加模型(Go) ==")

	for _, sec := range []string{"cgroup_skb/ingress", "cgroup/connect4", "cgroup/sysctl", "sockops"} {
		t, err := ResolveSection(sec)
		if err != nil {
			fmt.Println(err)
			continue
		}
		fmt.Printf("%-20s -> %s / %s\n", sec, t[0], t[1])
	}
	if _, err := ResolveSection("xdp"); err != nil {
		fmt.Println(err)
	}

	cg := NewCgroup("/sys/fs/cgroup/demo", "cpu", "io", "memory", "pids")
	AttachProgram(cg, "cgroup_skb/ingress", "prog_a")
	AttachProgram(cg, "cgroup_skb/ingress", "prog_b")
	fmt.Printf("附加点 BPF_CGROUP_INET_INGRESS = %v\n", cg.Attached["BPF_CGROUP_INET_INGRESS"])
	DetachProgram(cg, "BPF_CGROUP_INET_INGRESS", "prog_a")
	fmt.Printf("detach 后 = %v\n", cg.Attached["BPF_CGROUP_INET_INGRESS"])

	root := NewCgroup("/sys/fs/cgroup", "cpu", "io", "memory", "pids")
	leaf := NewCgroup("/sys/fs/cgroup/leaf")
	root.Children = []*Cgroup{leaf}
	WriteSubtreeControl(root, "+memory +pids", true)
	fmt.Printf("root.subtree_control = %v,leaf.controllers = %v\n", root.Subtree, leaf.Controllers)
	if _, err := WriteSubtreeControl(root, "+cpuset", true); err != nil {
		fmt.Println(err)
	}
	if _, err := WriteSubtreeControl(root, "+cpu +nope", true); err != nil {
		fmt.Println(err)
	}
	fmt.Printf("H2 失败后状态未变: %v\n", root.Subtree)
	WriteSubtreeControl(root, "+cpu -cpu", true)
	fmt.Printf("H3 重复项最后一个生效,净效果 cpu 未启用: %v\n", root.Subtree)

	busy := NewCgroup("/sys/fs/cgroup/busy", "cpu", "memory")
	busy.Procs = []int{1, 2, 3}
	if _, err := WriteSubtreeControl(busy, "+memory", false); err != nil {
		fmt.Println(err)
	}
	busy.Procs = nil
	WriteSubtreeControl(busy, "+memory", false)
	fmt.Printf("迁走进程后可启用: %v\n", busy.Subtree)

	// BPF token
	userns := Userns{Name: "userns-of-container",
		Caps: map[string]bool{"CAP_BPF": true, "CAP_NET_ADMIN": true}}
	tok := &BpfToken{OwningUserns: "userns-of-container",
		Progs:   map[string]bool{"BPF_PROG_TYPE_CGROUP_SKB": true},
		Cmds:    map[string]bool{"BPF_PROG_LOAD": true},
		Maps:    map[string]bool{"BPF_MAP_TYPE_ARRAY": true},
		Attachs: map[string]bool{"BPF_CGROUP_INET_INGRESS": true}}
	fmt.Printf("带 token 加载 cgroup_skb = %v\n", CanLoad("BPF_PROG_TYPE_CGROUP_SKB", userns, tok))
	other := userns
	other.Name = "another-userns"
	fmt.Printf("token 跨 userns = %v\n", CanLoad("BPF_PROG_TYPE_CGROUP_SKB", other, tok))
	fmt.Printf("无 token 在容器 userns = %v\n", CanLoad("BPF_PROG_TYPE_CGROUP_SKB", userns, nil))
	initU := Userns{Name: "init-userns", IsInit: true,
		Caps: map[string]bool{"CAP_BPF": true, "CAP_NET_ADMIN": true}}
	fmt.Printf("无 token 在 init userns = %v\n", CanLoad("BPF_PROG_TYPE_CGROUP_SKB", initU, nil))
	perf := Userns{Name: "init-userns", IsInit: true,
		Caps: map[string]bool{"CAP_BPF": true, "CAP_PERFMON": true}}
	fmt.Printf("kprobe 需 CAP_BPF+CAP_PERFMON: %v(容器 userns 用 CAP_NET_ADMIN 不行=%v)\n",
		CanLoad("BPF_PROG_TYPE_KPROBE", perf, nil),
		CanLoad("BPF_PROG_TYPE_KPROBE", userns, nil))
}

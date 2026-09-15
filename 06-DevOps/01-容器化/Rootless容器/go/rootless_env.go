// rootless 容器的启动前置、能力降级与诊断映射模型(Go 实现)。
//
// 规则依据 Docker 官方 rootless 文档:
//   https://docs.docker.com/engine/security/rootless/
//   https://docs.docker.com/engine/security/rootless/tips
//   https://docs.docker.com/engine/security/rootless/troubleshoot/
package main

import (
	"fmt"
	"strings"
)

const (
	MinSubIDs = 65536 // 官方:"at least 65,536 subordinate UIDs/GIDs"
)

var (
	unsupported        = []string{"AppArmor", "Checkpoint", "Overlay network", "SCTP ports"}
	defaultDelegated   = []string{"memory", "pids"}
	allDelegated       = []string{"cpu", "cpuset", "io", "memory", "pids"}
	errUnknown         = "未收录的报错,需人工排查"
	errorMap           = map[string]string{
		"failed to start the child: fork/exec /proc/self/exe: operation not permitted": "kernel.unprivileged_userns_clone 为 0",
		"failed to start the child: fork/exec /proc/self/exe: no space left on device":  "user.max_user_namespaces 太小",
		"failed to setup UID/GID map: failed to compute uid/gid map: No subuid ranges found for user": "/etc/subuid 与 /etc/subgid 未配置",
		"could not get XDG_RUNTIME_DIR": "$XDG_RUNTIME_DIR 未设置",
	}
)

// Env 是一台主机的 rootless 相关环境。
type Env struct {
	KernelMajor, KernelMinor int
	HasNewuidmap             bool
	SubuidCount, SubgidCount int
	UnprivilegedUsernsClone  bool
	MaxUserNamespaces        int
	XDGRuntimeDir            string
	HasRuntimeDir            bool
	FuseOverlayfsInstalled   bool
	HasSystemd               bool
	CgroupV2                 bool
	Delegated                []string
	RootlesskitNetBind       bool
	IPUnprivilegedPortStart  int
}

// DefaultEnv 返回一台"标准可用"的主机。
func DefaultEnv() Env {
	return Env{
		KernelMajor: 5, KernelMinor: 15,
		HasNewuidmap: true, SubuidCount: MinSubIDs, SubgidCount: MinSubIDs,
		UnprivilegedUsernsClone: true, MaxUserNamespaces: 28633,
		XDGRuntimeDir: "/run/user/1001", HasRuntimeDir: true,
		FuseOverlayfsInstalled: true, HasSystemd: true, CgroupV2: true,
		Delegated:               append([]string{}, defaultDelegated...),
		IPUnprivilegedPortStart: 1024,
	}
}

func (e Env) KernelAtLeast(major, minor int) bool {
	if e.KernelMajor != major {
		return e.KernelMajor > major
	}
	return e.KernelMinor >= minor
}

// CheckPrerequisites 返回阻断性问题列表(空 = 可以启动)。
func CheckPrerequisites(e Env) []string {
	var p []string
	if !e.HasNewuidmap {
		p = append(p, "newuidmap/newgidmap 缺失(由 uidmap 包提供)")
	}
	if e.SubuidCount < MinSubIDs {
		p = append(p, fmt.Sprintf("/etc/subuid 只有 %d 个从属 ID,少于 %d", e.SubuidCount, MinSubIDs))
	}
	if e.SubgidCount < MinSubIDs {
		p = append(p, fmt.Sprintf("/etc/subgid 只有 %d 个从属 ID,少于 %d", e.SubgidCount, MinSubIDs))
	}
	if !e.UnprivilegedUsernsClone {
		p = append(p, "kernel.unprivileged_userns_clone=0")
	}
	if e.MaxUserNamespaces <= 0 {
		p = append(p, "user.max_user_namespaces 过小")
	}
	if !e.HasRuntimeDir {
		p = append(p, "$XDG_RUNTIME_DIR 未设置")
	}
	return p
}

// PickStorageDriver 按官方白名单挑驱动。
func PickStorageDriver(e Env) (string, error) {
	switch {
	case e.KernelAtLeast(5, 11):
		return "overlay2", nil
	case e.KernelAtLeast(4, 18) && e.FuseOverlayfsInstalled:
		return "fuse-overlayfs", nil
	case e.KernelAtLeast(4, 18):
		return "btrfs", nil
	default:
		return "vfs", nil
	}
}

// CgroupSupport 返回 cgroup 是否可用与实际被委派的控制器。
func CgroupSupport(e Env) (bool, []string) {
	if !(e.CgroupV2 && e.HasSystemd) {
		return false, nil
	}
	return true, e.Delegated
}

// FlagEffective 判断 docker run 的资源参数是否真的生效。
func FlagEffective(flag string, e Env) bool {
	ok, controllers := CgroupSupport(e)
	if !ok {
		return false
	}
	need := map[string]string{"--cpus": "cpu", "--memory": "memory", "--pids-limit": "pids"}[flag]
	for _, c := range controllers {
		if c == need {
			return true
		}
	}
	return false
}

// CanBindPrivilegedPort 判断能否绑定 <1024 端口。
func CanBindPrivilegedPort(port int, e Env) bool {
	if port >= 1024 {
		return true
	}
	return e.RootlesskitNetBind || e.IPUnprivilegedPortStart == 0
}

// BuildUidMap 按 subuid 分配构造 uid_map 文本。
func BuildUidMap(start, count, ownUID int, keepOwnUID bool) (string, error) {
	if count < MinSubIDs {
		return "", fmt.Errorf("EPERM: subuid 区间只有 %d,少于 %d", count, MinSubIDs)
	}
	var b strings.Builder
	fmt.Fprintf(&b, "0 %d %d\n", start, count)
	if keepOwnUID {
		// 保留宿主自身 UID 的单项映射,便于访问挂载进来的用户文件
		fmt.Fprintf(&b, "1 %d 1\n", ownUID)
	}
	return b.String(), nil
}

// Diagnose 把官方报错原文映射到根因。
func Diagnose(line string) string {
	for pattern, cause := range errorMap {
		if strings.Contains(line, pattern) {
			return cause
		}
	}
	return errUnknown
}

// RuntimeDirWarnings 检查 XDG_RUNTIME_DIR 的路径风险。
func RuntimeDirWarnings(path string, set bool) []string {
	var w []string
	if !set {
		return append(w, "未设置 -> could not get XDG_RUNTIME_DIR")
	}
	if strings.HasPrefix(path, "/tmp") {
		w = append(w, "放在 /tmp 下易受 TOCTOU 攻击(官方明确不建议)")
	}
	if strings.Contains(path, " ") {
		w = append(w, "路径含空格会破坏 socket 路径解析")
	}
	return w
}

func main() {
	fmt.Println("== rootless 容器环境检查(Go) ==")

	std := DefaultEnv()
	fmt.Printf("标准环境阻断问题: %v\n", CheckPrerequisites(std))
	bad := DefaultEnv()
	bad.SubuidCount = MinSubIDs - 1
	fmt.Printf("subuid 少 1 个: %v\n", CheckPrerequisites(bad))

	text, err := BuildUidMap(231072, 65536, 1001, true)
	fmt.Printf("uid_map:\n%s", text)
	if err != nil {
		fmt.Println(err)
	}
	if _, err := BuildUidMap(231072, 1000, 1001, true); err != nil {
		fmt.Printf("subuid 太少 -> %v\n", err)
	}

	for _, k := range [][3]int{{5, 15, 1}, {5, 11, 1}, {5, 10, 1}, {5, 10, 0}, {4, 14, 1}} {
		e := DefaultEnv()
		e.KernelMajor, e.KernelMinor = k[0], k[1]
		e.FuseOverlayfsInstalled = k[2] == 1
		d, _ := PickStorageDriver(e)
		fmt.Printf("kernel %d.%d fuse=%v -> %s\n", k[0], k[1], k[2] == 1, d)
	}

	fmt.Printf("默认委派控制器: %v\n", defaultDelegated)
	fmt.Printf("--memory 生效=%v  --pids-limit 生效=%v  --cpus 生效=%v\n",
		FlagEffective("--memory", std), FlagEffective("--pids-limit", std), FlagEffective("--cpus", std))
	all := DefaultEnv()
	all.Delegated = append([]string{}, allDelegated...)
	fmt.Printf("委派全部后 --cpus 生效=%v\n", FlagEffective("--cpus", all))
	noSysd := DefaultEnv()
	noSysd.HasSystemd = false
	fmt.Printf("无 systemd 时 --cpus 生效=%v\n", FlagEffective("--cpus", noSysd))

	withCap := std
	withCap.RootlesskitNetBind = true
	fmt.Printf("绑定 80 默认=%v  加 CAP_NET_BIND_SERVICE 后=%v\n",
		CanBindPrivilegedPort(80, std), CanBindPrivilegedPort(80, withCap))

	fmt.Printf("不支持的特性: %v\n", unsupported)

	fmt.Printf("诊断1 -> %s\n", Diagnose("failed to start the child: fork/exec /proc/self/exe: operation not permitted"))
	fmt.Printf("诊断2 -> %s\n", Diagnose("could not get XDG_RUNTIME_DIR"))
	fmt.Printf("诊断3 -> %s\n", Diagnose("something else"))

	fmt.Printf("XDG_RUNTIME_DIR=/tmp/xrd -> %v\n", RuntimeDirWarnings("/tmp/xrd", true))
	fmt.Println("socket=$XDG_RUNTIME_DIR/docker.sock  data=~/.local/share/docker  cfg=~/.config/docker")
}

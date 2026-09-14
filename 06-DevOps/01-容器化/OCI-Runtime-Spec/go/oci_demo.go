// oci_demo.go — OCI Runtime Spec config.json + 4 状态机 演示(Go)
//
// 参考资料:
//   OCI runtime-spec:                https://github.com/opencontainers/runtime-spec
//   OCI Configuration Examples:      https://deepwiki.com/opencontainers/runtime-spec/7-configuration-examples
//
// 用法:
//   go run oci_demo.go dump                 # 打印默认 config
//   go run oci_demo.go lifecycle            # 4 状态机
//   go run oci_demo.go capabilities         # Docker 默认 cap 集合

//go:build linux

package main

import (
	"encoding/json"
	"fmt"
	"os"
)

// Docker 默认 14 个 cap 集合
var dockerDefaultCaps = []string{
	"CAP_CHOWN", "CAP_DAC_OVERRIDE", "CAP_FOWNER", "CAP_FSETID", "CAP_KILL",
	"CAP_NET_BIND_SERVICE", "CAP_NET_RAW", "CAP_SETFCAP", "CAP_SETGID",
	"CAP_SETPCAP", "CAP_SETUID", "CAP_SYS_CHROOT", "CAP_MKNOD", "CAP_AUDIT_WRITE",
}

// 默认 maskedPaths / readonlyPaths (runc sources defaults.go)
var defaultMasked = []string{
	"/proc/asound", "/proc/acpi", "/proc/kcore", "/proc/keys",
	"/proc/latency_stats", "/proc/timer_list", "/proc/timer_stats",
	"/proc/sched_debug", "/proc/scsi",
	"/proc/sysrq-trigger", "/sys/firmware",
	"/proc/devices", "/proc/mounts", "/proc/swaps",
}
var defaultReadonly = []string{
	"/proc/bus", "/proc/fs", "/proc/irq", "/proc/sys",
	"/proc/sysrq-trigger", "/proc/kcore", "/proc/keys",
}

type OciConfig struct {
	OciVersion string                 `json:"ociVersion"`
	Process    OciProcess             `json:"process"`
	Root       map[string]interface{} `json:"root"`
	Hostname   string                 `json:"hostname"`
	Mounts     []map[string]interface{} `json:"mounts"`
	Linux      map[string]interface{} `json:"linux"`
}
type OciProcess struct {
	Terminal         bool                   `json:"terminal"`
	User             map[string]int         `json:"user"`
	Args             []string               `json:"args"`
	Env              []string               `json:"env"`
	Cwd              string                 `json:"cwd"`
	NoNewPrivileges  bool                   `json:"noNewPrivileges"`
	Capabilities     map[string][]string   `json:"capabilities"`
	Rlimits          []map[string]interface{} `json:"rlimits,omitempty"`
}

func makeDefaultConfig() *OciConfig {
	caps := map[string][]string{
		"bounding":   dockerDefaultCaps,
		"effective":  dockerDefaultCaps,
		"permitted":  dockerDefaultCaps,
		"inheritable": {},
		"ambient":     {},
	}
	return &OciConfig{
		OciVersion: "1.2.1",
		Process: OciProcess{
			Terminal:        true,
			User:            map[string]int{"uid": 0, "gid": 0},
			Args:            []string{"sh"},
			Env:             []string{"PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "TERM=xterm"},
			Cwd:             "/",
			NoNewPrivileges: true,
			Capabilities:    caps,
			Rlimits: []map[string]interface{}{
				{"type": "RLIMIT_NOFILE", "hard": 1024, "soft": 1024},
			},
		},
		Root:     map[string]interface{}{"path": "rootfs", "readonly": true},
		Hostname: "runc",
		Mounts: []map[string]interface{}{
			{"destination": "/proc", "type": "proc", "source": "proc"},
			{"destination": "/dev", "type": "tmpfs", "source": "tmpfs",
				"options": []string{"nosuid", "strictatime", "mode=755", "size=65536k"}},
			{"destination": "/dev/pts", "type": "devpts", "source": "devpts",
				"options": []string{"nosuid", "noexec", "newinstance", "ptmxmode=0660", "gid=5"}},
			{"destination": "/dev/shm", "type": "tmpfs", "source": "shm",
				"options": []string{"nosuid", "nodev", "mode=1777", "size=65536k"}},
			{"destination": "/sys", "type": "none", "source": "/sys",
				"options": []string{"rbind", "nosuid", "noexec", "nodev", "ro"}},
		},
		Linux: map[string]interface{}{
			"namespaces": []map[string]string{
				{"type": "pid"}, {"type": "network"}, {"type": "ipc"},
				{"type": "uts"},  {"type": "mount"},
			},
			"maskedPaths":  defaultMasked,
			"readonlyPaths": defaultReadonly,
		},
	}
}

func cmdDump() {
	cfg := makeDefaultConfig()
	b, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		fmt.Println("marshal:", err)
		return
	}
	fmt.Println(string(b))
}

func cmdLifecycle() {
	fmt.Println(`# OCI 容器 4 状态机 (runtime-spec §Lifecycle):

       create
creating ───────── created
  ↑                 │
  │ delete          │ start
  │                 ↓
  │              running ──── pause/resume ──── paused
  │                 │
  │                 │ kill / exit
  │                 ↓
  └──────────── stopped
                    │
                    │ delete
                    ↓
                (destroyed)

# runc CLI 命令对应:
  runc create --bundle <bundle> <id>   # → created
  runc start <id>                      # → running
  runc state <id>                      # 查状态
  runc kill <id> SIGTERM               # → stopped
  runc delete <id>                     # → destroyed
`)
}

func cmdCapabilities() {
	fmt.Println("# Docker / runc 默认保留 14 个 capability:")
	for _, c := range dockerDefaultCaps {
		fmt.Printf("  + %s\n", c)
	}
	fmt.Println()
	fmt.Println("# 最小 cap 集合(nginx / 服务容器常见, 6 个):")
	minimal := []string{
		"CAP_CHOWN", "CAP_NET_BIND_SERVICE", "CAP_NET_RAW",
		"CAP_SYS_CHROOT", "CAP_SETUID", "CAP_SETGID",
	}
	for _, c := range minimal {
		fmt.Printf("  + %s\n", c)
	}
}

func main() {
	if len(os.Args) < 2 {
		fmt.Println("usage: oci_demo [dump|lifecycle|capabilities]")
		os.Exit(1)
	}
	switch os.Args[1] {
	case "dump":
		cmdDump()
	case "lifecycle":
		cmdLifecycle()
	case "capabilities":
		cmdCapabilities()
	default:
		fmt.Println("unknown:", os.Args[1])
		os.Exit(1)
	}
}

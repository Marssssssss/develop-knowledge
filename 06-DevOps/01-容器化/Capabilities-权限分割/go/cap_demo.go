// cap_demo.go — Linux Capabilities 演示(Go)
//
// 参考资料:
//   man7 capabilities(7): https://man7.org/linux/man-pages/man7/capabilities.7.html
//   man7 prctl(2)
//
// 用法:
//   go run cap_demo.go [inspect|list|drop <cap_name>|try-bind]

//go:build linux

package main

import (
	"fmt"
	"os"
	"runtime"
	"syscall"
	"unsafe"
)

// cap 名 → bit 位(man7 capabilities(7) 截至 5.9 = BIT(41))
var capNames = []string{
	"CHOWN", "DAC_OVERRIDE", "DAC_READ_SEARCH", "FOWNER", "FSETID",
	"KILL", "SETGID", "SETUID", "SETPCAP", "LINUX_IMMUTABLE",
	"NET_BIND_SERVICE", "NET_BROADCAST", "NET_ADMIN", "NET_RAW", "IPC_LOCK",
	"IPC_OWNER", "SYS_MODULE", "SYS_RAWIO", "SYS_CHROOT", "SYS_PTRACE",
	"SYS_PACCT", "SYS_ADMIN", "SYS_BOOT", "SYS_NICE", "SYS_RESOURCE",
	"SYS_TIME", "SYS_TTY_CONFIG", "MKNOD", "LEASE", "AUDIT_WRITE",
	"AUDIT_CONTROL", "SETFCAP", "MAC_OVERRIDE", "MAC_ADMIN", "SYSLOG",
	"WAKE_ALARM", "BLOCK_SUSPEND", "AUDIT_READ", "PERFMON", "BPF",
	"CHECKPOINT_RESTORE",
}

func decodeMask(mask uint64) []string {
	out := []string{}
	for i := 0; i < 64; i++ {
		if (mask>>i)&1 == 1 {
			if i < len(capNames) {
				out = append(out, fmt.Sprintf("CAP_%s (bit %d)", capNames[i], i))
			} else {
				out = append(out, fmt.Sprintf("BIT(%d)", i))
			}
		}
	}
	return out
}

// cap_user_header_t (v3):version=0x20080522 (Linux 2.6.25+)
type capUserHeader struct {
	Version uint32
	Pid     int32
}

// cap_user_data_t v3:两个数据元素(effective/permitted/inheritable)
type capUserData struct {
	Effective   uint32
	Permitted   uint32
	Inheritable uint32
}

const LINUX_CAPABILITY_VERSION_3 = 0x20080522

const SYS_CAPGET = 125 // x86_64

func capGet() (eff, prm, inh uint64, err error) {
	if runtime.GOOS != "linux" {
		return 0, 0, 0, fmt.Errorf("not linux")
	}
	var hdr capUserHeader = capUserHeader{Version: LINUX_CAPABILITY_VERSION_3, Pid: 0}
	var data [2]capUserData
	if _, _, e := syscall.Syscall(SYS_CAPGET, uintptr(unsafe.Pointer(&hdr)),
		uintptr(unsafe.Pointer(&data[0])), 0); e != 0 {
		return 0, 0, 0, e
	}
	eff = uint64(data[0].Effective) | uint64(data[1].Effective)<<32
	prm = uint64(data[0].Permitted) | uint64(data[1].Permitted)<<32
	inh = uint64(data[0].Inheritable) | uint64(data[1].Inheritable)<<32
	return
}

// PR_CAPBSET_READ (Linux 2.6.25+) 读 bounding set 中某位
const PR_CAPBSET_READ = 23

func capBndRead(bit int) (bool, error) {
	if runtime.GOOS != "linux" {
		return false, fmt.Errorf("not linux")
	}
	v, _, e := syscall.Syscall6(syscall.SYS_PRCTL, uintptr(PR_CAPBSET_READ),
		uintptr(bit), 0, 0, 0, 0)
	if e != 0 {
		return false, e
	}
	return v == 1, nil
}

func cmdInspect() {
	eff, prm, inh, err := capGet()
	if err != nil {
		fmt.Println("capget error:", err)
		return
	}
	fmt.Printf("# pid=%d Cap 集合\n", os.Getpid())
	printSet("Eff", eff)
	printSet("Prm", prm)
	printSet("Inh", inh)

	fmt.Println("\n# Bounding(逐 bit 读):")
	for i := 0; i < len(capNames); i++ {
		yes, _ := capBndRead(i)
		if yes {
			fmt.Printf("  CAP_%s (bit %d)\n", capNames[i], i)
		}
	}
}

func printSet(label string, m uint64) {
	items := decodeMask(m)
	fmt.Printf("  %s: 0x%016x →\n", label, m)
	for _, s := range items {
		fmt.Printf("    - %s\n", s)
	}
}

func cmdList() {
	fmt.Println("# Linux Capabilities (按 man7 顺序,截至 CHECKPOINT_RESTORE)")
	for i, n := range capNames {
		fmt.Printf("  BIT(%2d)  CAP_%-22s\n", i, n)
	}
}

func main() {
	if len(os.Args) < 2 {
		fmt.Println("usage: cap_demo [inspect|list]")
		os.Exit(1)
	}
	switch os.Args[1] {
	case "inspect":
		cmdInspect()
	case "list":
		cmdList()
	default:
		fmt.Println("unknown:", os.Args[1])
		os.Exit(1)
	}
}

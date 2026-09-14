// seccomp_demo.go — Go 版 Seccomp-BPF 演示(无 root + 安装 BPF 演示)
//
// 参考资料:
//   kernel.org userspace-api/seccomp_filter.html
//   man7 seccomp(2)
//   go.syscall.Syscall / SYS_SECCOMP / SYS_PRCTL
//
// 用法:
//   go run seccomp_demo.go dump [whitelist|errno-tcp]
//   go run seccomp_demo.go actions               # 打印 7 种 RET 值
//   go run seccomp_demo.go arch                  # arch 枚举

//go:build linux

package main

import (
	"encoding/binary"
	"fmt"
	"os"
	"runtime"
)

// BPF 指令对齐 linux/filter.h:struct sock_filter { u16 code; u8 jt, jf; u32 k }
type sockFilter struct {
	Code uint16
	Jt   uint8
	Jf   uint8
	K    uint32
}

// SECCOMP_RET_* 来自 kernel.org userspace-api/seccomp_filter.html
const (
	SECCOMP_RET_KILL_PROCESS uint32 = 0x80000000
	SECCOMP_RET_KILL_THREAD  uint32 = 0x00000000
	SECCOMP_RET_TRAP         uint32 = 0x00030000
	SECCOMP_RET_ERRNO        uint32 = 0x00050000
	SECCOMP_RET_USER_NOTIF   uint32 = 0x7FC00000
	SECCOMP_RET_LOG          uint32 = 0x7FC00000
	SECCOMP_RET_ALLOW        uint32 = 0x7FFF0000
)

// 常用 prctl + seccomp 常量(linux/prctl.h + seccomp.h)
const (
	SYS_SECCOMP                 = 317 // x86_64
	SYS_PRCTL                   = 157
	PR_SET_NO_NEW_PRIVS         = 38
	PR_SET_SECCOMP              = 22
	SECCOMP_MODE_FILTER         = 2
	SECCOMP_SET_MODE_FILTER     = 1
	AUDIT_ARCH_X86_64           = 0xC000003E
	AUDIT_ARCH_AARCH64          = 0xC00000B7
	AUDIT_ARCH_I386             = 0x40000003
)

// BPF helper constant
const (
	BPF_LD  = 0x00
	BPF_JMP = 0x05
	BPF_RET = 0x06
	BPF_W   = 0x00
	BPF_ABS = 0x20
	BPF_JEQ = 0x10
	BPF_K   = 0x00
)

// genWhitelist 白名单 BPF 程序(与 Python / C 版同结构)
func genWhitelist(allowed []uint32) []sockFilter {
	out := []sockFilter{
		// Load arch
		{Code: BPF_LD | BPF_W | BPF_ABS, K: 4}, // offsetof(seccomp_data, arch)
		{Code: BPF_JMP | BPF_JEQ | BPF_K, Jt: 1, K: AUDIT_ARCH_X86_64},
		{Code: BPF_RET | BPF_K, K: SECCOMP_RET_KILL_PROCESS},
		// Load nr
		{Code: BPF_LD | BPF_W | BPF_ABS, K: 0},
	}
	for _, nr := range allowed {
		out = append(out, sockFilter{Code: BPF_JMP | BPF_JEQ | BPF_K, Jf: 1, K: nr})
		out = append(out, sockFilter{Code: BPF_RET | BPF_K, K: SECCOMP_RET_ALLOW})
	}
	out = append(out, sockFilter{Code: BPF_RET | BPF_K, K: SECCOMP_RET_KILL_PROCESS})
	return out
}

// printBPF 程序以 Go 数组形式可移植
func printBPF(label string, prog []sockFilter) {
	fmt.Printf("// %s\n// struct sock_filter %s[] = {\n", label, label)
	for i, ins := range prog {
		fmt.Printf("    {Code: %#06x, Jt: %d, Jf: %d, K: %#x},  /* #%d */\n",
			ins.Code, ins.Jt, ins.Jf, ins.K, i)
	}
	fmt.Println("// };")
	// 编码到 bytes(便于贴到 C 文件)
	buf := make([]byte, 0, len(prog)*8)
	for _, ins := range prog {
		buf = binary.LittleEndian.AppendUint16(buf, ins.Code)
		buf = append(buf, ins.Jt, ins.Jf)
		buf = binary.LittleEndian.AppendUint32(buf, ins.K)
	}
	fmt.Printf("// bytes (LE) = %d 字节: % x\n\n", len(buf), buf)
}

func cmdDump(which string) {
	switch which {
	case "whitelist":
		printBPF("whitelist", genWhitelist([]uint32{0, 1, 60, 231, 15}))
	case "errno-tcp":
		// connect (nr=42) → SECCOMP_RET_ERRNO | (ENOSYS=38 << 16)
		prog := []sockFilter{
			{Code: BPF_LD | BPF_W | BPF_ABS, K: 4},
			{Code: BPF_JMP | BPF_JEQ | BPF_K, Jt: 1, K: AUDIT_ARCH_X86_64},
			{Code: BPF_RET | BPF_K, K: SECCOMP_RET_KILL_PROCESS},
			{Code: BPF_LD | BPF_W | BPF_ABS, K: 0},
			{Code: BPF_JMP | BPF_JEQ | BPF_K, Jf: 1, K: 42},
			{Code: BPF_RET | BPF_K, K: SECCOMP_RET_ERRNO | (38 << 16)},
			{Code: BPF_RET | BPF_K, K: SECCOMP_RET_ALLOW},
		}
		printBPF("errno_tcp", prog)
	default:
		fmt.Println("usage: dump whitelist|errno-tcp")
	}
}

func cmdActions() {
	fmt.Println("# SECCOMP_RET_* 值(kernel.org userspace-api/seccomp_filter.html §Return values)")
	fmt.Printf("  SECCOMP_RET_KILL_PROCESS = %#010x\n", SECCOMP_RET_KILL_PROCESS)
	fmt.Printf("  SECCOMP_RET_KILL_THREAD  = %#010x\n", SECCOMP_RET_KILL_THREAD)
	fmt.Printf("  SECCOMP_RET_TRAP         = %#010x\n", SECCOMP_RET_TRAP)
	fmt.Printf("  SECCOMP_RET_ERRNO        = %#010x  // 低 16 位放 errno\n", SECCOMP_RET_ERRNO)
	fmt.Printf("  SECCOMP_RET_USER_NOTIF   = %#010x  // 5.0+\n", SECCOMP_RET_USER_NOTIF)
	fmt.Printf("  SECCOMP_RET_LOG          = %#010x  // 4.14+\n", SECCOMP_RET_LOG)
	fmt.Printf("  SECCOMP_RET_ALLOW        = %#010x\n", SECCOMP_RET_ALLOW)
	fmt.Println()
	fmt.Println("# 优先级(多个 filter 同时):KILL_PROCESS > KILL_THREAD > TRAP > ERRNO > LOG/NOTIF > ALLOW")
}

func cmdArch() {
	fmt.Println("# seccomp_data.arch 常见值(linux/audit.h AUDIT_ARCH_*)")
	fmt.Printf("  AUDIT_ARCH_X86_64    = %#010x\n", AUDIT_ARCH_X86_64)
	fmt.Printf("  AUDIT_ARCH_I386      = %#010x\n", AUDIT_ARCH_I386)
	fmt.Printf("  AUDIT_ARCH_AARCH64   = %#010x\n", AUDIT_ARCH_AARCH64)
}

func main() {
	if len(os.Args) < 2 {
		fmt.Println("usage: seccomp_demo [dump|actions|arch]")
		os.Exit(1)
	}
	switch os.Args[1] {
	case "dump":
		if len(os.Args) < 3 {
			fmt.Println("usage: dump whitelist|errno-tcp")
			os.Exit(1)
		}
		cmdDump(os.Args[2])
	case "actions":
		cmdActions()
	case "arch":
		cmdArch()
	default:
		if runtime.GOOS != "linux" {
			fmt.Println("(此 demo 设计仅在 Linux 跑实 syscall;非 Linux 平台仅 dump)")
		}
		fmt.Println("unknown:", os.Args[1])
		os.Exit(1)
	}
}

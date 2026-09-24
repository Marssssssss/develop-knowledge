// runc create 双进程流程 —— Go 侧演示入口（与 python/main.py 同题）。
package main

import (
	"fmt"
	"strings"
)

func events(e []string) string { return strings.Join(e, " → ") }

func main() {
	base := func() *Config {
		return &Config{Namespaces: []string{"NEWNS"},
			ListenerPath: "/run/seccomp.sock", PassedFilesCount: 0}
	}

	fmt.Println("== 1. 默认流程（无 seccomp）==")
	p, c, s := Run(base())
	fmt.Println("   子进程:", events(c.Events))
	fmt.Println("   父进程:", events(p.Events))
	fmt.Println("   握手:  ", strings.Join(s.Log, " → "))
	fmt.Printf("   结果:   ierr=%q state=%s\n", p.Ierr, p.State)

	fmt.Println("\n== 2. seccomp + 无 NoNewPrivileges（尽早，丢 cap 之前）==")
	cfg := base()
	cfg.Seccomp = true
	_, c2, _ := Run(cfg)
	fmt.Printf("   seccomp_init 在 finalize_namespace 之前: %v\n",
		index(c2.Events, "seccomp_init") < index(c2.Events, "finalize_namespace"))

	fmt.Println("\n== 3. seccomp + NoNewPrivileges（尽可能晚，紧贴 execve）==")
	cfg2 := base()
	cfg2.Seccomp = true
	cfg2.NoNewPrivs = true
	_, c3, _ := Run(cfg2)
	fmt.Printf("   seccomp_init 在 lookpath 之后: %v\n",
		index(c3.Events, "seccomp_init") > index(c3.Events, "lookpath"))

	fmt.Println("\n== 4. procReady 之前死掉 ==")
	cfg3 := base()
	cfg3.DieBeforeReady = true
	p4, c4, _ := Run(cfg3)
	fmt.Println("   子进程停在:", c4.Events[len(c4.Events)-1])
	fmt.Printf("   父进程: ierr=%q state=%s\n", p4.Ierr, p4.State)

	fmt.Println("\n== 5. seccomp 但没配 listenerPath ==")
	cfg4 := base()
	cfg4.Seccomp = true
	cfg4.ListenerPath = ""
	p5, _, _ := Run(cfg4)
	fmt.Printf("   父进程: ierr=%q\n", p5.Ierr)

	fmt.Println("\n== 6. exec fifo 与 fd 收紧 ==")
	cfg5 := base()
	cfg5.PassedFilesCount = 2
	_, c6, _ := Run(cfg5)
	fmt.Println("   fifo 写入:", c6.FifoWrites)
	fmt.Println("   UnsafeCloseFrom 起点: unsafe_close_from",
		cfg5.PassedFilesCount+3)
}

func index(s []string, v string) int {
	for i, x := range s {
		if x == v {
			return i
		}
	}
	return -1
}

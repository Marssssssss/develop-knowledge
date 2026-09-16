// fork 与僵尸进程:Go 视角。
//
// Go 没有 fork(2) 原语:goroutine 模型与 fork+线程混合不安全。
// 本程序用 os/exec 自 exec 一个子进程,演示:
//   1. Run 内部完成 wait —— 进程退出后立即被收尸,无僵尸残留
//   2. ExitCode() 解码自 wait 状态:退出参数只保留低 8 位(300 -> 44)
//
// 运行(在 Linux 下行为与 wait(2) 完全一致;Windows 下无 8 位截断):
//   go run .
package main

import (
	"errors"
	"flag"
	"fmt"
	"os"
	"os/exec"
)

// runSelfExec 启动自身的一个子进程并让其在 child 模式下以 code 退出,
// 返回父进程观测到的退出码。
func runSelfExec(self string, code int) (int, error) {
	cmd := exec.Command(self, "-child", fmt.Sprintf("-code=%d", code))
	err := cmd.Run() // Run 包含 Wait:子进程结束后立即被回收
	if err == nil {
		return 0, nil // 退出码 0:非 *exec.ExitError
	}
	var ee *exec.ExitError
	if errors.As(err, &ee) {
		return ee.ExitCode(), nil // 正常非零退出
	}
	return -1, err // 启动失败(子进程根本没跑起来)
}

func main() {
	childMode := flag.Bool("child", false, "child mode: exit immediately")
	exitCode := flag.Int("code", 42, "exit code used in child mode")
	flag.Parse()

	if *childMode {
		os.Exit(*exitCode)
	}

	self, err := os.Executable()
	if err != nil {
		fmt.Fprintln(os.Stderr, "os.Executable:", err)
		os.Exit(1)
	}

	// 场景 1:常规退出码 42
	got, err := runSelfExec(self, 42)
	if err != nil {
		fmt.Fprintln(os.Stderr, "exec:", err)
		os.Exit(1)
	}
	fmt.Printf("child _exit(42)   -> parent sees exit code %d\n", got)
	if got != 42 {
		fmt.Println("FAIL: expected 42")
		os.Exit(1)
	}

	// 场景 2:退出参数超出 8 位 —— wait(2) 规则只保留低 8 位。
	// Linux: 300 = 0x12C -> 0x2C = 44(与 C/Python 版一致)
	got, err = runSelfExec(self, 300)
	if err != nil {
		fmt.Fprintln(os.Stderr, "exec:", err)
		os.Exit(1)
	}
	fmt.Printf("child _exit(300)  -> parent sees exit code %d (low 8 bits)\n", got)
	expected := 300 & 0xff
	if got != expected {
		fmt.Printf("FAIL: expected %d\n", expected)
		os.Exit(1)
	}

	// Run 返回即子进程已被 wait 收尸:不存在需要用户手工 waitpid 的窗口
	fmt.Println("PASS: exec.Cmd.Run reaps the child; exit codes decode per wait(2)")
}

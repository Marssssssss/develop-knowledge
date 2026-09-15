// redis_persistence.go — Minimal Redis persistence simulator
//
// 演示:
//  1) RDB 快照: fork 子进程遍历内存 dict 写二进制 dump
//  2) AOF 追加:  按 RESP 协议把命令写入 aof 文件
//  3) AOF Rewrite: 子进程按当前内存生成最小命令集
//
// 不依赖真实 redis-server。
//
// 运行: go run redis_persistence.go
//
// 注意: Windows 没有 fork(),演示直接打印信息后退出。

package main

import (
	"encoding/binary"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"time"
)

// ---------- 内存字典(demo 用全局 map) ----------

var (
	kvMu sync.Mutex
	kv   = map[string]string{
		"user:1001":  "alice",
		"user:1002":  "bob",
		"counter:pv": "42",
	}
)

func kvSet(k, v string) {
	kvMu.Lock()
	kv[k] = v
	kvMu.Unlock()
}

// ---------- RESP 编码 ----------

func respEncode(args ...string) []byte {
	buf := []byte(fmt.Sprintf("*%d\r\n", len(args)))
	for _, a := range args {
		buf = append(buf, []byte(fmt.Sprintf("$%d\r\n%s\r\n", len(a), a))...)
	}
	return buf
}

// ---------- RDB 二进制格式 ----------

func rdbSave(path string) (int64, error) {
	tmp := path + ".tmp"
	f, err := os.Create(tmp)
	if err != nil {
		return 0, err
	}
	defer f.Close()

	// magic
	if _, err := f.Write([]byte("RDB0001")); err != nil {
		return 0, err
	}
	// count
	kvMu.Lock()
	count := uint32(len(kv))
	if err := binary.Write(f, binary.LittleEndian, count); err != nil {
		kvMu.Unlock()
		return 0, err
	}
	for k, v := range kv {
		if err := binary.Write(f, binary.LittleEndian, uint16(len(k))); err != nil {
			kvMu.Unlock()
			return 0, err
		}
		f.Write([]byte(k))
		if err := binary.Write(f, binary.LittleEndian, uint16(len(v))); err != nil {
			kvMu.Unlock()
			return 0, err
		}
		f.Write([]byte(v))
	}
	kvMu.Unlock()

	if err := os.Rename(tmp, path); err != nil {
		return 0, err
	}
	st, _ := os.Stat(path)
	return st.Size(), nil
}

// ---------- AOF 追加 ----------

func aofAppend(path, policy, cmd, k, v string) error {
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_APPEND, 0644)
	if err != nil {
		return err
	}
	defer f.Close()

	if _, err := f.Write(respEncode(cmd, k, v)); err != nil {
		return err
	}
	if err := f.Sync(); err != nil { // demo 简化:每次都 Sync
		return err
	}
	_ = policy // 真实策略 everysec/always/no
	return nil
}

// ---------- AOF Rewrite(最小命令集) ----------

func aofRewriteMinimal(path string) (int64, error) {
	tmp := path + ".rewrite"
	f, err := os.Create(tmp)
	if err != nil {
		return 0, err
	}
	defer f.Close()

	kvMu.Lock()
	for k, v := range kv {
		f.Write(respEncode("SET", k, v))
	}
	kvMu.Unlock()

	if err := os.Rename(tmp, path); err != nil {
		return 0, err
	}
	st, _ := os.Stat(path)
	return st.Size(), nil
}

// ---------- fork helper ----------

func runInChild(fn func() error) error {
	if _, err := os.Stat("/proc"); err != nil {
		// 非 Linux,无 fork。exec 自身用于演示子进程边界。
		exe, err := os.Executable()
		if err != nil {
			return fn()
		}
		cmd := exec.Command(exe, "-child-mode")
		cmd.Stdout, cmd.Stderr = os.Stdout, os.Stderr
		return cmd.Run()
	}
	// 简化:用 cmd 走一次 exec 模拟 fork+exec;真实 Redis 用 syscall.ForkExec。
	exe, err := os.Executable()
	if err != nil {
		return fn()
	}
	cmd := exec.Command(exe, "-child-mode")
	cmd.Stdout, cmd.Stderr = os.Stdout, os.Stderr
	return cmd.Run()
}

// ---------- main ----------

func main() {
	if len(os.Args) > 1 && os.Args[1] == "-child-mode" {
		// 子进程模式:demo 简化为收到信号后做一次 rewrite
		_, err := aofRewriteMinimal("appendonly.aof")
		if err != nil {
			fmt.Fprintf(os.Stderr, "[child] rewrite err: %v\n", err)
			os.Exit(1)
		}
		fmt.Fprintf(os.Stderr, "[child] rewrite done\n")
		os.Exit(0)
	}

	fmt.Println("=== Redis Persistence Demo (Go) ===")

	// 1) RDB 快照
	sz, err := rdbSave("dump.rdb")
	if err != nil {
		fmt.Fprintf(os.Stderr, "[RDB] err: %v\n", err)
	} else {
		fmt.Printf("[RDB] dump.rdb size = %d bytes\n", sz)
	}

	// 2) AOF 追加
	aofAppend("appendonly.aof", "everysec", "SET", "user:1001", "alice_v2")
	aofAppend("appendonly.aof", "everysec", "SET", "user:1003", "carol")
	aofAppend("appendonly.aof", "everysec", "INCR", "counter:pv", "1")
	fmt.Println("[AOF] appended 3 commands")

	// 3) AOF Rewrite
	if err := runInChild(func() error {
		_, err := aofRewriteMinimal("appendonly.aof")
		return err
	}); err != nil {
		fmt.Fprintf(os.Stderr, "[AOF rewrite] err: %v\n", err)
	} else {
		fmt.Println("[AOF rewrite] child finished")
	}

	// cleanup .go filename reference for filepath.Clean
	_ = filepath.Clean
	fmt.Printf("\nDemo finished at %s\n",
		time.Now().Format("2006-01-02 15:04:05"))
}
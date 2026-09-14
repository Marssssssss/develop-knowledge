// overlay_demo.go — Go 版 OverlayFS 演示(无 root 解析 + mount syscall 包装)
//
// 参考资料:
//   kernel.org overlayfs.rst: https://sources.debian.org/src/linux/6.9.7-1/Documentation/filesystems/overlayfs.rst/
//   man 2 mount
//
// 演示 inspect 解析 /proc/self/mounts + 数据结构演示 + 真 mount 尝试(需 CAP_SYS_ADMIN)
//
// 用法: go run overlay_demo.go [inspect|mount-demo]

//go:build linux

package main

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"syscall"
)

type overlayOpts struct {
	Lowerdirs  []string
	Upperdir   string
	Workdir    string
	Metacopy   bool
	RedirDir   string
}

func parseOverlayOpts(opts string) overlayOpts {
	out := overlayOpts{}
	for _, kv := range strings.Split(opts, ",") {
		parts := strings.SplitN(kv, "=", 2)
		if len(parts) != 2 {
			continue
		}
		k, v := parts[0], parts[1]
		switch k {
		case "lowerdir":
			out.Lowerdirs = strings.Split(v, ":")
		case "upperdir":
			out.Upperdir = v
		case "workdir":
			out.Workdir = v
		case "metacopy":
			out.Metacopy = v == "on"
		case "redirect_dir":
			out.RedirDir = v
		}
	}
	return out
}

// listOverlayMounts 扫描 /proc/self/mounts,提取所有 type=overlay 的挂载。
// Docker/Podman 通常以独立 mount namespace 运行,这里看到的只是当前 ns 的挂载。
func listOverlayMounts() ([]string, map[string]overlayOpts, error) {
	raws := []string{}
	parsed := map[string]overlayOpts{}
	f, err := os.Open("/proc/self/mounts")
	if err != nil {
		return nil, nil, err
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		fields := strings.Fields(sc.Text())
		if len(fields) < 4 {
			continue
		}
		mp, fstype, opts := fields[1], fields[2], fields[3]
		if fstype != "overlay" {
			continue
		}
		raws = append(raws, fmt.Sprintf("%s -> %s", mp, opts))
		parsed[mp] = parseOverlayOpts(opts)
	}
	return raws, parsed, nil
}

// lookup 模拟内核 ovl_lookup 查找逻辑:
//   1. upper 存在则返回("upper")  [覆盖 lower]
//   2. 否则按 lowerdirs 从上到下(数组从头到尾)找  [右到左 = build 顺序]
//   3. 都没有 → "MISS"
func lookup(rel string, o overlayOpts) string {
	if o.Upperdir != "" {
		if _, err := os.Stat(filepath.Join(o.Upperdir, rel)); err == nil {
			return "upper:" + rel
		}
	}
	for i, l := range o.Lowerdirs {
		if _, err := os.Stat(filepath.Join(l, rel)); err == nil {
			return fmt.Sprintf("lower[%d]:%s", i, rel)
		}
	}
	return "MISS"
}

// mountOverlay syscall.Mount(2) 在标准库:
func mountOverlay(lower, upper, work, target string) error {
	opts := fmt.Sprintf("lowerdir=%s,upperdir=%s,workdir=%s", lower, upper, work)
	return syscall.Mount("overlay", target, "overlay", 0, opts)
}

func cmdInspect() {
	raws, parsed, err := listOverlayMounts()
	if err != nil {
		fmt.Println("open mounts err:", err)
		return
	}
	if len(raws) == 0 {
		fmt.Println("当前进程 namespace 内未发现 overlay 挂载(若有 Docker 容器,通常在主机 /proc/1/mounts)")
		return
	}
	sort.Strings(raws)
	for _, r := range raws {
		fmt.Println("overlay:", r)
	}
	for mp, o := range parsed {
		fmt.Printf("\n[mp=%s]\n", mp)
		fmt.Printf("  lower  (%d layers): %v\n", len(o.Lowerdirs), o.Lowerdirs)
		fmt.Printf("  upper  : %s\n", o.Upperdir)
		fmt.Printf("  work   : %s\n", o.Workdir)
		fmt.Printf("  metacopy=%v, redirect_dir=%s\n", o.Metacopy, o.RedirDir)
		// 演示查找
		fmt.Printf("  -> lookup('release.txt') = %s\n", lookup("release.txt", o))
	}
}

func cmdMountDemo() {
	base := "/tmp/ovl_go"
	os.RemoveAll(base)
	for _, sub := range []string{"lower_app", "lower_base", "upper", "work", "merged"} {
		os.MkdirAll(base+"/"+sub, 0755)
	}
	// lower 层
	os.WriteFile(base+"/lower_app/app.conf", []byte("APP=1"), 0644)
	os.WriteFile(base+"/lower_base/release.txt", []byte("from_base"), 0644)

	o := overlayOpts{
		Lowerdirs: []string{base + "/lower_app", base + "/lower_base"},
		Upperdir:  base + "/upper",
		Workdir:   base + "/work",
	}
	fmt.Printf("栈:\n  upper=%s\n  lower=%v\n", o.Upperdir, o.Lowerdirs)
	fmt.Printf("  lookup /app.conf       = %s\n", lookup("app.conf", o))
	fmt.Printf("  lookup /release.txt    = %s\n", lookup("release.txt", o))
	fmt.Printf("  lookup /missing.txt    = %s\n", lookup("missing.txt", o))

	if err := mountOverlay(o.Lowerdirs[1], o.Upperdir, o.Workdir, base+"/merged"); err != nil {
		fmt.Printf("\nmount(2) 失败: %v\n", err)
		switch err {
		case syscall.EPERM:
			fmt.Println("  → 需要 CAP_SYS_ADMIN / root")
		case syscall.EINVAL:
			fmt.Println("  → workdir 与 upperdir 不同 fs")
		case syscall.ENODEV:
			fmt.Println("  → 内核无 overlay 模块 (CONFIG_OVERLAY_FS)")
		}
	} else {
		fmt.Println("\nmount 成功,可读写", base+"/merged")
	}
}

func main() {
	if len(os.Args) < 2 {
		fmt.Println("usage: overlay_demo [inspect|mount-demo]")
		os.Exit(1)
	}
	switch os.Args[1] {
	case "inspect":
		cmdInspect()
	case "mount-demo":
		cmdMountDemo()
	default:
		fmt.Println("unknown cmd:", os.Args[1])
		os.Exit(1)
	}
}

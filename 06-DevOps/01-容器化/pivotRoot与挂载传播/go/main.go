// pivot_root 与 shared subtree —— Go 侧演示入口（与 python/main.py 同题）。
package main

import "fmt"

func demoTree(mountRootfs bool) *MountTree {
	t := NewMountTree()
	t.Add("/", "ext4", msPrivate, "/")
	t.Add("/mnt", "ext4", msPrivate, "/")
	if mountRootfs {
		t.Add("/mnt/rootfs", "ext4", msPrivate, "/")
	} else {
		t.Dirs["/mnt/rootfs"] = true
	}
	t.Dirs["/mnt/rootfs/.old"] = true
	return t
}

func main() {
	fmt.Println("== 1. 六条限制的 errno 分派 ==")
	t1 := demoTree(true)
	t1.Dirs["/mnt/plain"] = true
	_, _, err := t1.PivotRoot("/mnt/plain", "/mnt/rootfs/.old", "/")
	fmt.Printf("   new_root 不是挂载点        -> %v\n", err)
	_, _, err = t1.PivotRoot("/", "/mnt/rootfs/.old", "/")
	fmt.Printf("   new_root 是 /              -> %v\n", err)
	t2 := demoTree(true)
	t2.Dirs["/tmp/x"] = true
	_, _, err = t2.PivotRoot("/mnt/rootfs", "/tmp/x", "/")
	fmt.Printf("   put_old 不在 new_root 之下 -> %v\n", err)
	t3 := demoTree(true)
	t3.SetPropagation("/mnt", msShared)
	_, _, err = t3.PivotRoot("/mnt/rootfs", "/mnt/rootfs/.old", "/")
	fmt.Printf("   父挂载是 MS_SHARED         -> %v\n", err)

	fmt.Println("\n== 2. 合法调用后的树变换 ==")
	t4 := demoTree(true)
	nr, old, err := t4.PivotRoot("/mnt/rootfs", "/mnt/rootfs/.old", "/")
	fmt.Printf("   新根=%s 旧根=%s err=%v\n", nr.Mountpoint, old.Mountpoint, err)

	fmt.Println("\n== 3. 四种传播类型 ==")
	t5 := NewMountTree()
	t5.Add("/", "ext4", msPrivate, "/")
	a := t5.Add("/a", "ext4", msPrivate, "/")
	t5.SetPropagation("/a", msShared)
	b := t5.Add("/b", "ext4", msPrivate, "/")
	t5.SetPropagation("/b", msShared)
	b.PeerGroup = a.PeerGroup
	s := t5.Add("/s", "ext4", msPrivate, "/")
	s.Master, s.Prop = a.PeerGroup, msSlave
	p := t5.Add("/p", "ext4", msPrivate, "/")
	u := t5.Add("/u", "ext4", msPrivate, "/")
	t5.SetPropagation("/u", msUnbindable)
	for _, m := range []*Mount{a, s, p, u} {
		fmt.Printf("   %-3s %-11s → %v\n", m.Mountpoint, propName(m.Prop),
			t5.Propagate(m, "/x"))
	}

	fmt.Println("\n== 4. runc 的 pivotRoot（\".\", \".\"）==")
	t6 := demoTree(false)
	applied, bind, _ := t6.PrepareRoot("/mnt/rootfs", 0)
	fmt.Printf("   / 的传播=%s 父挂载去共享=%s bind 后是挂载点=%v\n",
		propName(t6.MountAt("/").Prop), applied, t6.IsMountpoint(bind.Mountpoint))
	if _, err := t6.RuncPivotRoot(bind); err != nil {
		fmt.Println("   err:", err)
	}
	fmt.Printf("   事件=%v 切根后 / = %s\n", t6.Events, t6.CurrentRoot().Mountpoint)

	fmt.Println("\n== 5. --no-pivot 的 msMoveRoot ==")
	t7 := NewMountTree()
	t7.Add("/", "ext4", msPrivate, "/")
	t7.Add("/proc", "proc", msPrivate, "/")
	t7.Add("/sys", "sysfs", msPrivate, "/")
	t7.Add("/var/lib/proc", "proc", msPrivate, "/sub")
	t7.Add("/rootfs", "ext4", msPrivate, "/")
	var names []string
	for _, m := range t7.FullPseudoRoots("/rootfs") {
		names = append(names, m.Mountpoint)
	}
	fmt.Println("   需要遮掉:", names)
	fmt.Println("   rootless 遮罩:", t7.MsMoveRoot("/rootfs", true))
	fmt.Println("   事件尾部:", t7.Events[len(t7.Events)-3:])
}

// pivot_root 之后的 runc 切根路径：prepareRoot / pivotRoot / msMoveRoot。
// 从 pivot_model.go 拆出，避免单文件超 300 行。
package main

import (
	"fmt"
	"strings"
)

// RootfsParentMountPropagationFlags 对应 rootfsParentMountPropagationFlags。
func RootfsParentMountPropagationFlags(rootPropagation int) int {
	if rootPropagation&msSlave != 0 {
		return msSlave
	}
	return msPrivate
}

// RootfsParentMountPropagation 沿父目录上溯直到碰到挂载点。
func (t *MountTree) RootfsParentMountPropagation(path string, flags int) (string, *PivotError) {
	for {
		if t.IsMountpoint(path) {
			if _, err := t.SetPropagation(path, flags); err != nil {
				return "", err
			}
			return path, nil
		}
		if path == "/" {
			return "", &PivotError{"EINVAL", "remount-propagation failed at /"}
		}
		idx := strings.LastIndex(path, "/")
		if idx <= 0 {
			path = "/"
		} else {
			path = path[:idx]
		}
	}
}

// PrepareRoot 对应 prepareRoot：/ 变 rslave → 父挂载去共享 → rootfs bind 自身。
func (t *MountTree) PrepareRoot(rootfs string, rootPropagation int) (string, *Mount, *PivotError) {
	flags := msSlave | msRec
	if rootPropagation != 0 {
		flags = rootPropagation
	}
	if _, err := t.SetPropagation("/", flags); err != nil {
		return "", nil, err
	}
	applied, err := t.RootfsParentMountPropagation(rootfs,
		RootfsParentMountPropagationFlags(rootPropagation))
	if err != nil {
		return "", nil, err
	}
	src := t.MountAt(rootfs)
	bind := &Mount{ID: t.NextID + 1, Mountpoint: rootfs, Fstype: "ext4",
		Root: "/", Prop: msPrivate}
	if src != nil {
		bind.Fstype = src.Fstype
	}
	t.NextID++
	t.Mounts[bind.ID] = bind
	t.Dirs[rootfs] = true
	return applied, bind, nil
}

// RuncPivotRoot 对应 runc 的 pivotRoot：pivot(".", ".") → rslave → MNT_DETACH → chdir。
func (t *MountTree) RuncPivotRoot(rootfs *Mount) (*Mount, *PivotError) {
	nr, old, err := t.PivotRoot(".", ".", rootfs.Mountpoint)
	if err != nil {
		return nil, err
	}
	if _, err := t.SetPropagation(old.Mountpoint, msSlave|msRec); err != nil {
		return nil, err
	}
	t.Events = append(t.Events, "stacked="+fmt.Sprint(old.Stacked),
		"umount2(.,MNT_DETACH)", "chdir(/)")
	delete(t.Mounts, old.ID)
	return nr, nil
}

// FullPseudoRoots 挑出 msMoveRoot 要遮掉的完整 procfs / sysfs。
func (t *MountTree) FullPseudoRoots(rootfs string) []*Mount {
	var out []*Mount
	for _, m := range t.Mounts {
		if m.Root == "/" && (m.Fstype == "proc" || m.Fstype == "sysfs") &&
			!strings.HasPrefix(m.Mountpoint, rootfs) {
			out = append(out, m)
		}
	}
	return out
}

// MsMoveRoot 对应 --no-pivot 的退路。
func (t *MountTree) MsMoveRoot(rootfs string, rootless bool) []string {
	var masked []string
	for _, m := range t.FullPseudoRoots(rootfs) {
		t.SetPropagation(m.Mountpoint, msSlave|msRec)
		if rootless {
			m.Fstype = "tmpfs"
			t.Events = append(t.Events, "cover "+m.Mountpoint+" tmpfs")
		} else {
			delete(t.Mounts, m.ID)
			t.Events = append(t.Events, "umount2 "+m.Mountpoint+" MNT_DETACH")
		}
		masked = append(masked, m.Mountpoint)
	}
	t.Events = append(t.Events, "mount "+rootfs+" / (MS_MOVE)", "chroot(.)", "chdir(/)")
	return masked
}

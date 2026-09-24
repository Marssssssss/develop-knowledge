// pivot_root 与 shared subtree 传播（Python 模型的 Go 复刻）。
//
// 对照：
//
//	man7 pivot_root(2)                    六条限制与 errno 分派、pivot_root(".", ".")
//	opencontainers/runc rootfs_linux.go   prepareRoot / pivotRoot / msMoveRoot
package main

import (
	"fmt"
	"strings"
)

const (
	msUnbindable = 1 << 17
	msPrivate    = 1 << 18
	msSlave      = 1 << 19
	msShared     = 1 << 20
	msRec        = 1 << 14
	msBind       = 1 << 12
	msMove       = 1 << 13
	msRemount    = 1 << 5
	msRdonly     = 1
	mntDetach    = 2
)

func propName(prop int) string {
	switch prop {
	case msShared:
		return "shared"
	case msSlave:
		return "slave"
	case msPrivate:
		return "private"
	case msUnbindable:
		return "unbindable"
	}
	return "unknown"
}

type PivotError struct {
	Errno string
	Why   string
}

func (e *PivotError) Error() string { return e.Errno + ": " + e.Why }

type Mount struct {
	ID         int
	Mountpoint string
	Fstype     string
	Root       string
	Prop       int
	PeerGroup  int
	Master     int
	Stacked    bool
}

type MountTree struct {
	Mounts  map[int]*Mount
	Dirs    map[string]bool
	NextID  int
	NextPeer int
	RootID  int
	Events  []string
}

func NewMountTree() *MountTree {
	return &MountTree{Mounts: map[int]*Mount{}, Dirs: map[string]bool{"/": true},
		NextID: 1, NextPeer: 1}
}

func (t *MountTree) Add(mountpoint, fstype string, prop int, root string) *Mount {
	t.NextID++
	m := &Mount{ID: t.NextID, Mountpoint: mountpoint, Fstype: fstype,
		Prop: prop, Root: root}
	if fstype == "" {
		m.Fstype = "ext4"
	}
	if root == "" {
		m.Root = "/"
	}
	t.Mounts[m.ID] = m
	t.Dirs[mountpoint] = true
	if mountpoint == "/" {
		t.RootID = m.ID
	}
	return m
}

func (t *MountTree) CurrentRoot() *Mount { return t.Mounts[t.RootID] }

func (t *MountTree) MountAt(path string) *Mount {
	for _, m := range t.Mounts {
		if m.Mountpoint == path {
			return m
		}
	}
	return nil
}

func (t *MountTree) IsMountpoint(path string) bool { return t.MountAt(path) != nil }

// ParentMount 取挂载点最长前缀里本身是挂载点的那个。
func (t *MountTree) ParentMount(m *Mount) *Mount {
	best := t.CurrentRoot()
	for _, o := range t.Mounts {
		if o.ID == m.ID {
			continue
		}
		if o.Mountpoint == "/" {
			continue
		}
		if strings.HasPrefix(m.Mountpoint, o.Mountpoint+"/") &&
			len(o.Mountpoint) > len(best.Mountpoint) {
			best = o
		}
	}
	return best
}

// SetPropagation 支持 MS_REC 递归。
func (t *MountTree) SetPropagation(path string, flags int) ([]*Mount, *PivotError) {
	m := t.MountAt(path)
	if m == nil {
		return nil, &PivotError{"EINVAL", path + " is not a mount point"}
	}
	targets := []*Mount{m}
	if flags&msRec != 0 {
		prefix := strings.TrimSuffix(path, "/") + "/"
		for _, c := range t.Mounts {
			if c.ID != m.ID && strings.HasPrefix(c.Mountpoint, prefix) {
				targets = append(targets, c)
			}
		}
	}
	for _, x := range targets {
		switch {
		case flags&msShared != 0:
			if x.PeerGroup == 0 {
				x.PeerGroup = t.NextPeer
				t.NextPeer++
			}
			x.Prop = msShared
		case flags&msPrivate != 0:
			x.PeerGroup, x.Master, x.Prop = 0, 0, msPrivate
		case flags&msSlave != 0:
			x.Master = x.PeerGroup
			x.PeerGroup = 0
			x.Prop = msSlave
		case flags&msUnbindable != 0:
			x.PeerGroup, x.Master, x.Prop = 0, 0, msUnbindable
		}
	}
	return targets, nil
}

// Propagate 返回事件扩散到的挂载点列表。
func (t *MountTree) Propagate(src *Mount, eventPath string) []string {
	var out []string
	if src.Prop != msShared || src.PeerGroup == 0 {
		return out
	}
	for _, p := range t.Mounts {
		if p.ID != src.ID && p.PeerGroup == src.PeerGroup {
			out = append(out, p.Mountpoint+eventPath)
		}
	}
	for _, s := range t.Mounts {
		if s.Master == src.PeerGroup {
			out = append(out, s.Mountpoint+eventPath)
		}
	}
	return out
}

// PivotRoot 实现 man 页的六条限制与成功后的树变换。
func (t *MountTree) PivotRoot(newRoot, putOld, cwd string) (*Mount, *Mount, *PivotError) {
	resolvedNew, resolvedOld := newRoot, putOld
	if newRoot == "." {
		resolvedNew = cwd
	}
	if putOld == "." {
		resolvedOld = cwd
	}
	if !t.Dirs[resolvedNew] || !t.Dirs[resolvedOld] {
		return nil, nil, &PivotError{"ENOTDIR", "new_root or put_old is not a directory"}
	}
	nr := t.MountAt(resolvedNew)
	if nr == nil {
		return nil, nil, &PivotError{"EINVAL", "new_root is not a mount point"}
	}
	if resolvedNew == "/" {
		return nil, nil, &PivotError{"EBUSY", "new_root is /"}
	}
	cur := t.CurrentRoot()
	if po := t.MountAt(resolvedOld); po != nil && po.ID == cur.ID {
		return nil, nil, &PivotError{"EBUSY", "put_old is on the current root mount"}
	}
	if resolvedOld != resolvedNew && !strings.HasPrefix(resolvedOld, resolvedNew+"/") {
		return nil, nil, &PivotError{"EINVAL", "put_old is not at or underneath new_root"}
	}
	if t.ParentMount(nr).Prop == msShared || t.ParentMount(cur).Prop == msShared {
		return nil, nil, &PivotError{"EINVAL", "parent mount has propagation type MS_SHARED"}
	}
	if po := t.MountAt(resolvedOld); po != nil && po.Prop == msShared {
		return nil, nil, &PivotError{"EINVAL", "put_old is a mount point and has MS_SHARED"}
	}
	old := cur
	old.Mountpoint = resolvedOld
	old.Stacked = resolvedOld == resolvedNew
	nr.Mountpoint = "/"
	t.RootID = nr.ID
	t.Events = append(t.Events, fmt.Sprintf("pivot_root(%s,%s)", newRoot, putOld))
	return nr, old, nil
}

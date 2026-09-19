// writeback.go — /proc/sys/vm/dirty_* 的阈值与回写时机
//
// 运行: go run .
//
// 与 python/writeback.py 同题：available memory 口径、*_bytes 与 *_ratio 互斥、
// dirty_bytes 的两页下限、后台阈值 vs 限流阈值、过期时间与定期回写的开关。

package main

import "fmt"

const page = 4096

// VmSysctl 是 /proc/sys/vm/ 里与回写相关的几个旋钮。
type VmSysctl struct {
	DirtyBackgroundRatio  int
	DirtyBackgroundBytes  int64
	DirtyRatio            int
	DirtyBytes            int64
	DirtyExpireCentisecs  int
	DirtyWritebackCentics int
	DirtytimeExpireSecs   int
}

func NewVmSysctl() *VmSysctl {
	return &VmSysctl{
		DirtyBackgroundRatio:  10,
		DirtyRatio:            20,
		DirtyExpireCentisecs:  3000, // 30 秒
		DirtyWritebackCentics: 500,  // 5 秒
		DirtytimeExpireSecs:   43200,
	}
}

// Write 处理 counterpart 互斥与最小值约束，返回是否被接受。
func (v *VmSysctl) Write(name string, value int64) bool {
	switch name {
	case "dirty_bytes":
		if value != 0 && value < 2*page {
			return false // 文档：低于两页直接忽略
		}
		v.DirtyBytes, v.DirtyRatio = value, 0
		return true
	case "dirty_ratio":
		v.DirtyRatio, v.DirtyBytes = int(value), 0
		return true
	case "dirty_background_bytes":
		if value != 0 && value < 2*page {
			return false
		}
		v.DirtyBackgroundBytes, v.DirtyBackgroundRatio = value, 0
		return true
	case "dirty_background_ratio":
		v.DirtyBackgroundRatio, v.DirtyBackgroundBytes = int(value), 0
		return true
	case "dirty_expire_centisecs":
		v.DirtyExpireCentisecs = int(value)
		return true
	case "dirty_writeback_centisecs":
		v.DirtyWritebackCentics = int(value)
		return true
	case "dirtytime_expire_seconds":
		v.DirtytimeExpireSecs = int(value)
		return true
	}
	return false
}

func (v *VmSysctl) PeriodicWritebackEnabled() bool {
	return v.DirtyWritebackCentics != 0
}

// Machine 是一台内存可记账的机器。
type Machine struct {
	TotalPages        int64
	FreePages         int64
	ReclaimablePages  int64
	VM                *VmSysctl
	Dirty             [][2]int64 // [bytes, age_centisecs]
	Written, Blocked  int64
	Elapsed           int64
	nextWakeup        int64
}

func NewMachine(totalMB, freeMB, reclaimMB int64) *Machine {
	mb := int64(1024 * 1024)
	m := &Machine{
		TotalPages:       totalMB * mb / page,
		FreePages:        freeMB * mb / page,
		ReclaimablePages: reclaimMB * mb / page,
		VM:               NewVmSysctl(),
	}
	m.nextWakeup = int64(m.VM.DirtyWritebackCentics)
	return m
}

// AvailableBytes 文档口径：free pages + reclaimable pages，不是总内存。
func (m *Machine) AvailableBytes() int64 {
	return (m.FreePages + m.ReclaimablePages) * page
}

func (m *Machine) DirtyBytes() int64 {
	var s int64
	for _, d := range m.Dirty {
		s += d[0]
	}
	return s
}

func (m *Machine) BackgroundThresh() int64 {
	if m.VM.DirtyBackgroundBytes != 0 {
		return m.VM.DirtyBackgroundBytes
	}
	return m.AvailableBytes() * int64(m.VM.DirtyBackgroundRatio) / 100
}

func (m *Machine) DirtyThresh() int64 {
	if m.VM.DirtyBytes != 0 {
		return m.VM.DirtyBytes
	}
	return m.AvailableBytes() * int64(m.VM.DirtyRatio) / 100
}

// Write 进程写数据，越过 dirty 阈值时写者自己回写（被限流）。
func (m *Machine) Write(n int64) {
	m.Dirty = append(m.Dirty, [2]int64{n, 0})
	if m.DirtyBytes() >= m.DirtyThresh() {
		m.Blocked++
		m.flushUntilBelow(m.DirtyThresh())
	}
}

func (m *Machine) flushUntilBelow(thresh int64) {
	for len(m.Dirty) > 0 && m.DirtyBytes() >= thresh {
		chunk := m.Dirty[len(m.Dirty)-1][0]
		m.Dirty = m.Dirty[:len(m.Dirty)-1]
		m.Written += chunk
		m.FreePages += chunk / page
	}
}

// FlusherWakeup 先写过期的，再看是否因超后台阈值而继续。
func (m *Machine) FlusherWakeup() []int64 {
	flushed := []int64{}
	keep := [][2]int64{}
	for _, d := range m.Dirty {
		if d[1] >= int64(m.VM.DirtyExpireCentisecs) {
			flushed = append(flushed, d[0])
			m.Written += d[0]
			m.FreePages += d[0] / page
		} else {
			keep = append(keep, d)
		}
	}
	m.Dirty = keep
	if m.DirtyBytes() >= m.BackgroundThresh() {
		before := m.DirtyBytes()
		m.flushUntilBelow(m.BackgroundThresh())
		flushed = append(flushed, before-m.DirtyBytes())
	}
	return flushed
}

// Tick 推进 dt 个 centisecs，到点唤醒 flusher。
func (m *Machine) Tick(dt int64) []int64 {
	m.Elapsed += dt
	for i := range m.Dirty {
		m.Dirty[i][1] += dt
	}
	if !m.VM.PeriodicWritebackEnabled() || m.Elapsed < m.nextWakeup {
		return nil
	}
	m.nextWakeup = m.Elapsed + int64(m.VM.DirtyWritebackCentics)
	return m.FlusherWakeup()
}

// DropCaches 只回收干净页与可回收 slab，脏页不受影响。
func (m *Machine) DropCaches() int64 {
	freed := m.ReclaimablePages * page
	m.FreePages += m.ReclaimablePages
	m.ReclaimablePages = 0
	return freed
}

func sum(xs []int64) int64 {
	var s int64
	for _, x := range xs {
		s += x
	}
	return s
}

func main() {
	mb := int64(1024 * 1024)
	m := NewMachine(8192, 6144, 1024)
	fmt.Printf("total=%d GiB available=%d GiB（分母不是总内存）\n",
		m.TotalPages*page/mb/1024, m.AvailableBytes()/mb/1024)
	fmt.Printf("background=%d MiB dirty=%d MiB\n",
		m.BackgroundThresh()/mb, m.DirtyThresh()/mb)

	v := NewVmSysctl()
	v.Write("dirty_bytes", 512*mb)
	fmt.Printf("写 dirty_bytes=512MiB 后 dirty_ratio=%d（counterpart 归零）\n",
		v.DirtyRatio)
	ok := v.Write("dirty_bytes", 4096)
	fmt.Printf("再写 4096（< 两页）被接受? %v, dirty_bytes 仍为 %d MiB\n",
		ok, v.DirtyBytes/mb)

	m2 := NewMachine(8192, 6144, 1024)
	m2.VM.Write("dirty_bytes", 100*mb)
	m2.Write(60 * mb)
	m2.Write(60 * mb)
	fmt.Printf("写 120MiB（阈值 100MiB）→ 被限流 %d 次, 剩余脏页 %d MiB\n",
		m2.Blocked, m2.DirtyBytes()/mb)

	m3 := NewMachine(8192, 6144, 1024)
	m3.VM.Write("dirty_background_bytes", 9999*mb) // 抬到够不着
	m3.VM.Write("dirty_expire_centisecs", 3000)
	m3.Write(10 * mb)
	fmt.Printf("刚写完: 回写 %d 字节\n", sum(m3.FlusherWakeup()))
	fmt.Printf("10 秒后: 回写 %d 字节\n", sum(m3.Tick(1000)))
	fmt.Printf("35 秒后: 回写 %d 字节（过期 30 秒）\n", sum(m3.Tick(2500)))

	m4 := NewMachine(8192, 6144, 1024)
	m4.VM.Write("dirty_writeback_centisecs", 0)
	m4.Write(10 * mb)
	fmt.Printf("关掉定期回写后 tick 1000 秒: 回写 %d 字节, 脏页仍有 %d MiB\n",
		sum(m4.Tick(100000)), m4.DirtyBytes()/mb)

	m5 := NewMachine(8192, 6144, 1024)
	m5.Write(10 * mb)
	dirtyBefore := m5.DirtyBytes()
	fmt.Printf("drop_caches 回收 %d MiB, 脏页 %d → %d MiB, available 仍 %d GiB\n",
		m5.DropCaches()/mb, dirtyBefore/mb, m5.DirtyBytes()/mb,
		m5.AvailableBytes()/mb/1024)
}

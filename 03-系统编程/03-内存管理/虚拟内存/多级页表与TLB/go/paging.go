// Package main —— 多级页表 / TLB / 缺页中断的 Go 镜像（与 python/main.py 同构）。
//
// 地址常量取自 Linux 内核文档 arch/x86/x86_64/mm.rst 与 5level-paging.rst。
package main

// ---- 页与页表 ----
const (
	PageBits         = 12
	PageSize         = 1 << PageBits // 4096
	EntriesPerLevel  = 512
	LevelBits        = 9
	HugePageSize     = 2 * 1024 * 1024
)

// ---- 内核文档里的地址布局 ----
const (
	UserEnd4L    uint64 = 0x00007ffffffff000
	GuardHole4L  uint64 = 0x00007fffffffffff
	UserEnd5L    uint64 = 0x00fffffffffff000
	DirectMap4L  uint64 = 0xffff888000000000
	Vmalloc4L    uint64 = 0xffffc90000000000
	Vmemmap4L    uint64 = 0xffffea0000000000
	KernelText4L uint64 = 0xffffffff80000000
	DirectMap5L  uint64 = 0xff11000000000000
	Vmemmap5L    uint64 = 0xffd4000000000000
)

// ---- 4 级 / 5 级的地址空间上限 ----
const (
	VaLimit4L = 256 * (1 << 40) // 256 TiB
	PaLimit4L = 64 * (1 << 40)  // 64 TiB
	VaLimit5L = 128 * (1 << 50) // 128 PiB
	PaLimit5L = 4 * (1 << 50)   // 4 PiB
)

// ---- userfaultfd 注册模式 ----
const (
	UffdModeMissing = 1 << 0
	UffdModeWP      = 1 << 1
	UffdModeMinor   = 1 << 2
	UffdModeRWP     = 1 << 3
)

// IsCanonical 判断 x86-64 规范地址：高位必须是符号扩展。
func IsCanonical(va uint64, levels int) bool {
	bits := 48
	if levels != 4 {
		bits = 57
	}
	signBits := uint(64 - bits + 1)
	top := va >> uint(bits-1)
	return top == 0 || top == (uint64(1)<<signBits)-1
}

// VPNIndices 返回自顶向下的各级页表下标（每级 9 位）。
func VPNIndices(va uint64, levels int) []int {
	out := make([]int, 0, levels)
	shift := uint(PageBits + LevelBits*(levels-1))
	for i := 0; i < levels; i++ {
		out = append(out, int((va>>shift)&(EntriesPerLevel-1)))
		shift -= LevelBits
	}
	return out
}

// PageOffset 返回页内偏移。
func PageOffset(va uint64) uint64 { return va & (PageSize - 1) }

// BuildVA 由各级下标拼出虚拟地址。
func BuildVA(indices []int, offset uint64, levels int) uint64 {
	va := offset & (PageSize - 1)
	shift := uint(PageBits)
	for i := levels - 1; i >= 0; i-- {
		va |= uint64(indices[i]&(EntriesPerLevel-1)) << shift
		shift += LevelBits
	}
	return va
}

// PageTable 只建模遍历代价。
type PageTable struct {
	Levels        int
	WalkAccesses  int
}

// Walk 返回下标序列与本次访问所需的内存访问次数（级数 + 1 次数据访问）。
func (p *PageTable) Walk(va uint64) ([]int, int) {
	idx := VPNIndices(va, p.Levels)
	p.WalkAccesses += p.Levels + 1
	return idx, p.Levels + 1
}

// TLBReach 返回 TLB 能覆盖的虚拟内存量。
func TLBReach(entries int, pageSize int) int { return entries * pageSize }

// TLB 极简 TLB：FIFO 替换，记录 hit/miss 与陈旧条目放行次数。
type TLB struct {
	Cap          int
	Store        map[int]bool
	Order        []int
	Perms        map[int]bool
	Hits         int
	Misses       int
	StaleAllowed int
}

// NewTLB 构造一个 entries 项的 TLB。
func NewTLB(entries int) *TLB {
	return &TLB{Cap: entries, Store: map[int]bool{}, Perms: map[int]bool{}}
}

// Lookup 查询 vpn；needWrite 表示本次是写访问。
func (t *TLB) Lookup(vpn int, needWrite bool) bool {
	if _, ok := t.Store[vpn]; ok {
		if !needWrite || t.Perms[vpn] {
			t.Hits++
			return true
		}
		t.StaleAllowed++ // 权限已改但 TLB 未刷新 -> 陈旧条目放行
		t.Hits++
		return true
	}
	t.Misses++
	if len(t.Order) >= t.Cap && len(t.Order) > 0 {
		old := t.Order[0]
		t.Order = t.Order[1:]
		delete(t.Store, old)
		delete(t.Perms, old)
	}
	t.Store[vpn] = true
	t.Perms[vpn] = true
	t.Order = append(t.Order, vpn)
	return false
}

// Downgrade 模拟 mprotect 降权：只改页表不改 TLB。
func (t *TLB) Downgrade(vpn int) { t.Perms[vpn] = false }

// Shootdown 刷新 TLB；vpn < 0 表示全刷。
func (t *TLB) Shootdown(vpn int) {
	if vpn < 0 {
		t.Store = map[int]bool{}
		t.Perms = map[int]bool{}
		t.Order = nil
		return
	}
	delete(t.Store, vpn)
	delete(t.Perms, vpn)
}

// ---- 缺页类型 ----
const (
	Minor   = "minor"
	Major   = "major"
	Sigsegv = "SIGSEGV"
	Uffd    = "uffd"
)

// VMA 一段匿名私有映射。
type VMA struct {
	Npages      int
	Prot        string
	PteWritable []bool // PTE 级写位：COW 只清它，不动 Prot
	Resident    []bool
	Cow         []bool
	Data        []int
	Uffd        int
	Faults      []string
}

// NewVMA 构造 npages 页的映射。
func NewVMA(npages int, prot string) *VMA {
	v := &VMA{Npages: npages, Prot: prot,
		PteWritable: make([]bool, npages), Resident: make([]bool, npages),
		Cow: make([]bool, npages), Data: make([]int, npages)}
	for i := 0; i < npages; i++ {
		v.PteWritable[i] = true
	}
	return v
}

// RegisterUffd 注册 userfaultfd；WP 与 RWP 不能同时注册。
func (v *VMA) RegisterUffd(modes int) error {
	if modes&UffdModeWP != 0 && modes&UffdModeRWP != 0 {
		return errEINVAL
	}
	v.Uffd = modes
	return nil
}

// Access 返回本次访问的缺页类型，空串表示不缺页。
func (v *VMA) Access(idx int, write bool) string {
	add := func(kind string) string { v.Faults = append(v.Faults, kind); return kind }
	if !v.Resident[idx] {
		if v.Uffd&UffdModeMissing != 0 {
			return add(Uffd)
		}
		v.Resident[idx] = true
		return add(Minor)
	}
	if v.Prot == "none" {
		return add(Sigsegv)
	}
	if write && v.Prot == "r" {
		return add(Sigsegv)
	}
	if write && v.Cow[idx] && !v.PteWritable[idx] {
		v.Cow[idx] = false
		v.PteWritable[idx] = true
		return add(Minor)
	}
	if write && v.Uffd&UffdModeWP != 0 {
		return add(Uffd)
	}
	return ""
}

// ForkCow fork：双方 PTE 写位清掉并标记 COW，VMA 权限不变。
func (v *VMA) ForkCow() *VMA {
	c := NewVMA(v.Npages, v.Prot)
	copy(c.Resident, v.Resident)
	copy(c.Data, v.Data)
	for i := 0; i < v.Npages; i++ {
		c.Cow[i] = true
		v.Cow[i] = true
		v.PteWritable[i] = false
		c.PteWritable[i] = false
	}
	return c
}

// MadviseDontneed 对匿名私有映射丢弃内容，之后访问得到零页。
func (v *VMA) MadviseDontneed() {
	for i := 0; i < v.Npages; i++ {
		v.Resident[i] = false
		v.Data[i] = 0
		v.Cow[i] = false
	}
}

// Mincore 返回各页的 resident 位图。
func (v *VMA) Mincore() []bool { return v.Resident }

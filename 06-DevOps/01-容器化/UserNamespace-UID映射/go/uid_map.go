// user namespace 的 uid_map/gid_map 解析、校验与双向翻译(Go 实现)。
//
// 规则依据 man7.org 的 user_namespaces(7) 与 newuidmap(1):
//   https://man7.org/linux/man-pages/man7/user_namespaces.7.html
//   https://man7.org/linux/man-pages/man1/newuidmap.1.html
//
// 与 python/uid_map_model.py、c/uid_map.c 同题同规则,便于三语言对照。
package main

import (
	"fmt"
	"strconv"
	"strings"
)

const (
	PageSize     = 4096
	MaxRanges    = 340  // Linux 4.16+
	LegacyRanges = 5    // Linux 4.14 之前
	OverflowID   = 65534
	NoID         = 4294967295 // (uid_t)-1
	MaxNesting   = 32
)

// Status 与内核 errno 名字对应。
type Status string

const (
	Ok    Status = "OK"
	Eperm Status = "EPERM"
	Einval Status = "EINVAL"
	Eusers Status = "EUSERS"
)

// Range 是一行映射:inside 与 outside 各是长度 size 的连续区间。
type Range struct{ Inside, Outside, Size uint32 }

// UidMap 是一个命名空间的 uid_map / gid_map。
type UidMap struct {
	Name            string
	Ranges          []Range
	Written         bool
	IsGid           bool
	SetgroupsDenied bool
}

// Parse 解析 uid_map 文本。规则:至少一行、每行三个数字、
// 必须以换行结尾、总字节 < 一页、行数不超上限。
func Parse(text string, modern bool) ([]Range, Status) {
	if text == "" || !strings.HasSuffix(text, "\n") {
		return nil, Einval
	}
	if len(text) >= PageSize {
		return nil, Einval
	}
	var out []Range
	for _, line := range strings.Split(strings.TrimSuffix(text, "\n"), "\n") {
		f := strings.Fields(line)
		if len(f) != 3 {
			return nil, Einval
		}
		var v [3]uint64
		for i, s := range f {
			n, err := strconv.ParseUint(s, 10, 32)
			if err != nil {
				return nil, Einval
			}
			v[i] = n
		}
		if len(out) >= MaxRanges {
			return nil, Einval
		}
		out = append(out, Range{uint32(v[0]), uint32(v[1]), uint32(v[2])})
	}
	limit := MaxRanges
	if !modern {
		limit = LegacyRanges
	}
	if len(out) == 0 || len(out) > limit {
		return nil, Einval
	}
	return out, Ok
}

func overlap(a, b Range) bool {
	return a.Inside < b.Inside+b.Size && b.Inside < a.Inside+a.Size ||
		a.Outside < b.Outside+b.Size && b.Outside < a.Outside+a.Size
}

// Write 做完整的写入校验并落盘。writerCaps:写入者在父 ns 是否有 CAP_SETUID/SETGID。
func (m *UidMap) Write(text string, writerCaps bool, writerEuid, creatorEuid uint32, offset int) Status {
	if m.Written {
		return Eperm // 只能写一次
	}
	if offset != 0 {
		return Einval // 必须从偏移 0 写
	}
	rs, st := Parse(text, true)
	if st != Ok {
		return st
	}
	for i := 0; i < len(rs); i++ {
		for j := i + 1; j < len(rs); j++ {
			if overlap(rs[i], rs[j]) {
				return Einval
			}
		}
	}
	if !writerCaps {
		// 无特权:只能一行、长度必须为 1、必须映射自身有效 UID
		if len(rs) != 1 || rs[0].Size != 1 {
			return Eperm
		}
		if rs[0].Outside != writerEuid || writerEuid != creatorEuid {
			return Eperm
		}
		if m.IsGid && !m.SetgroupsDenied {
			return Eperm // 写 gid_map 前须先 deny setgroups
		}
	}
	m.Ranges, m.Written = rs, true
	return Ok
}

// ToOutside 做 inside -> outside 翻译;未映射返回 overflow 65534。
func (m *UidMap) ToOutside(id uint32) uint32 {
	for _, r := range m.Ranges {
		if id >= r.Inside && id < r.Inside+r.Size {
			return r.Outside + (id - r.Inside)
		}
	}
	return OverflowID
}

// FromOutside 做 outside -> inside 翻译(stat(2) 视角)。
func (m *UidMap) FromOutside(id uint32) uint32 {
	for _, r := range m.Ranges {
		if id >= r.Outside && id < r.Outside+r.Size {
			return r.Inside + (id - r.Outside)
		}
	}
	return OverflowID
}

// SecondFieldOrNoID 是读 uid_map 的例外:未映射显示 4294967295 而非 65534。
func (m *UidMap) SecondFieldOrNoID(id uint32) uint32 {
	if v := m.ToOutside(id); v != OverflowID {
		return v
	}
	return NoID
}

// ToHost 把最内层命名空间的 UID 逐层翻译到初始命名空间。
// chain[0] 是最内层(其 outside 空间 = chain[1] 的 inside 空间)。
func ToHost(chain []*UidMap, uid uint32) uint32 {
	cur := uid
	for _, ns := range chain {
		cur = ns.ToOutside(cur)
		if cur == OverflowID {
			return OverflowID
		}
	}
	return cur
}

// CheckNesting 校验嵌套深度上限。
func CheckNesting(depth int) Status {
	if depth > MaxNesting {
		return Eusers
	}
	return Ok
}

// PseudoMap 构造内核为初始命名空间直接给出的映射(不是"写"出来的)。
func PseudoMap(name string, rs ...Range) *UidMap {
	return &UidMap{Name: name, Ranges: rs, Written: true}
}

func main() {
	const subStart, subCount uint32 = 231072, 65536

	fmt.Println("== uid_map 模型(Go) ==")
	ns := &UidMap{Name: "ns1"}
	fmt.Printf("写入单项映射: %s\n",
		ns.Write(fmt.Sprintf("0 %d %d\n", subStart, subCount), true, 1000, 1000, 0))
	fmt.Printf("容器 uid 0 -> 宿主 %d\n", ns.ToOutside(0))
	fmt.Printf("区间末位 65535 -> 宿主 %d\n", ns.ToOutside(65535))
	fmt.Printf("越界 65536 -> %d(overflow)\n", ns.ToOutside(65536))
	fmt.Printf("宿主 231072 -> 容器 %d\n", ns.FromOutside(231072))
	fmt.Printf("宿主 231071(未映射)-> %d\n", ns.FromOutside(231071))
	fmt.Printf("二次写入 -> %s\n", ns.Write("0 0 1\n", true, 1000, 1000, 0))

	u := &UidMap{Name: "u"}
	fmt.Printf("无特权/两行 -> %s\n", u.Write("0 1000 1\n1 2000 1\n", false, 1000, 1000, 0))
	fmt.Printf("无特权/映射他人 -> %s\n", u.Write("0 2000 1\n", false, 1000, 1000, 0))
	fmt.Printf("无特权/映射自身 -> %s (inside 0 -> 父 ns %d)\n",
		u.Write("0 1000 1\n", false, 1000, 1000, 0), u.ToOutside(0))

	g := &UidMap{Name: "g", IsGid: true}
	fmt.Printf("gid_map 未 deny -> %s\n", g.Write("0 1000 1\n", false, 1000, 1000, 0))
	g.SetgroupsDenied = true
	fmt.Printf("gid_map deny 后 -> %s\n", g.Write("0 1000 1\n", false, 1000, 1000, 0))

	t := &UidMap{Name: "t"}
	fmt.Printf("不以换行结尾 -> %s\n", t.Write("0 1000 1", true, 1000, 1000, 0))
	t2 := &UidMap{Name: "t2"}
	fmt.Printf("非零偏移 -> %s\n", t2.Write("0 1000 1\n", true, 1000, 1000, 8))
	t3 := &UidMap{Name: "t3"}
	fmt.Printf("区间重叠 -> %s\n", t3.Write("0 1000 100\n50 2000 100\n", true, 1000, 1000, 0))

	// 嵌套三层:ns2(1000 个)-> ns1(65536 个)-> 初始命名空间
	n1 := &UidMap{Name: "ns1"}
	n1.Write(fmt.Sprintf("0 %d %d\n", subStart, subCount), true, 1000, 1000, 0)
	n2 := &UidMap{Name: "ns2"}
	n2.Write("0 0 1000\n", true, 1000, 1000, 0)
	host := PseudoMap("init", Range{0, 0, NoID})
	chain := []*UidMap{n2, n1, host}
	fmt.Printf("嵌套: ns2 0 -> 宿主 %d\n", ToHost(chain, 0))
	fmt.Printf("嵌套: ns2 999 -> 宿主 %d\n", ToHost(chain, 999))
	fmt.Printf("嵌套: ns2 1000(本层越界)-> %d\n", ToHost(chain, 1000))
	fmt.Printf("嵌套深度 32 -> %s,33 -> %s\n", CheckNesting(32), CheckNesting(33))

	init := PseudoMap("init", Range{0, 0, NoID})
	fmt.Printf("读 uid_map 未映射第二字段 -> %d(而非 %d)\n",
		init.SecondFieldOrNoID(NoID), OverflowID)
}

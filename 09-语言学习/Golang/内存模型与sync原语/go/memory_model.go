// Go 内存模型：happens-before 关系的可执行模型（与 python/memory_model.py 同构）。
//
// 依据 https://go.dev/ref/mem（2022-06-06）：synchronized before 由映射 W 导出，
// happens before = sequenced ∪ synchronized 的传递闭包，数据竞争 = 同位置、至少一个
// 非同步、且在 happens-before 下不可比；DRF-SC = 无竞争程序等价于某顺序交错。
package main

var readLike = map[string]bool{
	"read": true, "atomic_read": true, "lock": true, "recv": true,
}

var writeLike = map[string]bool{
	"write": true, "atomic_write": true, "unlock": true, "send": true, "close": true,
}

type op struct {
	gid   int
	label string
	kind  string
	loc   string
}

func (o *op) isRead() bool  { return readLike[o.kind] }
func (o *op) isWrite() bool { return writeLike[o.kind] }

// isSync 只认「同步操作」：普通 read / write 不算同步操作，
// 因此「两个同步操作之间不算竞争」这条豁免不能把普通读写也豁免掉。
func (o *op) isSync() bool {
	if o.kind == "read" || o.kind == "write" {
		return false
	}
	return o.isRead() || o.isWrite()
}

type execution struct {
	ops   []*op
	syncs [][2]*op
}

func (e *execution) add(gid int, label, kind, loc string) *op {
	o := &op{gid: gid, label: label, kind: kind, loc: loc}
	e.ops = append(e.ops, o)
	return o
}

func (e *execution) sync(a, b *op) *execution {
	e.syncs = append(e.syncs, [2]*op{a, b})
	return e
}

func (e *execution) index(o *op) int {
	for i, x := range e.ops {
		if x == o {
			return i
		}
	}
	return -1
}

// happensBefore 返回传递闭包矩阵与下标映射。
func (e *execution) happensBefore() ([][]bool, map[*op]int) {
	n := len(e.ops)
	idx := map[*op]int{}
	for i, o := range e.ops {
		idx[o] = i
	}
	hb := make([][]bool, n)
	for i := range hb {
		hb[i] = make([]bool, n)
	}
	for i := 0; i < n; i++ {
		for j := 0; j < n; j++ {
			a, b := e.ops[i], e.ops[j]
			if i != j && a.gid == b.gid && i < j {
				hb[i][j] = true // sequenced before
			}
		}
	}
	for _, s := range e.syncs {
		hb[idx[s[0]]][idx[s[1]]] = true
	}
	for k := 0; k < n; k++ {
		for i := 0; i < n; i++ {
			if !hb[i][k] {
				continue
			}
			for j := 0; j < n; j++ {
				if hb[k][j] {
					hb[i][j] = true
				}
			}
		}
	}
	return hb, idx
}

func (e *execution) ordered(a, b *op) bool {
	hb, idx := e.happensBefore()
	i, j := idx[a], idx[b]
	return hb[i][j] || hb[j][i]
}

func (e *execution) isRace(a, b *op) bool {
	if a.loc == "" || a.loc != b.loc {
		return false
	}
	if !(a.isRead() || a.isWrite()) || !(b.isRead() || b.isWrite()) {
		return false
	}
	if a.isRead() && b.isRead() {
		return false // 读-读不构成竞争
	}
	if a.isSync() && b.isSync() {
		return false
	}
	return !e.ordered(a, b)
}

func (e *execution) races() [][2]string {
	out := [][2]string{}
	for i, a := range e.ops {
		for _, b := range e.ops[i+1:] {
			if e.isRace(a, b) {
				out = append(out, [2]string{a.label, b.label})
			}
		}
	}
	return out
}

// visibleWrites 按 Requirement 3：发生在 r 之前、且没有被其他「也发生在 r 之前」的写覆盖。
func (e *execution) visibleWrites(rd *op) []*op {
	hb, idx := e.happensBefore()
	i := idx[rd]
	cands := []*op{}
	for k, o := range e.ops {
		if hb[k][i] && o.isWrite() && o.loc == rd.loc {
			cands = append(cands, o)
		}
	}
	vis := []*op{}
	for _, w := range cands {
		covered := false
		for _, w2 := range cands {
			if w2 != w && hb[idx[w]][idx[w2]] {
				covered = true
			}
		}
		if !covered {
			vis = append(vis, w)
		}
	}
	return vis
}

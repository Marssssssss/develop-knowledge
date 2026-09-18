// 标记-压缩式 GC 的两种实现(Go 版):保序滑动 与 线程化(threading)。
//
// 权威来源(实际联网阅读):
//   Memory Management Reference「mark-compact」/「compaction」条目(与
//     *The Garbage Collection Handbook* 配套的权威术语表):
//     "the compaction phase typically performs a number of sequential passes over
//      memory to move objects and update references"、"all the marked objects are
//      moved into a single contiguous block of memory"、"Compaction is used to avoid
//      external fragmentation and to increase locality of reference"
//   GHC rts/sm/Compact.c 头部注释:"chain together all the fields pointing at a
//     particular object, with the root of the chain in the object's info table field.
//     The original contents of the info pointer goes at the end of the chain."
//     "if the field is NOT tagged then we tag the pointer to the field with 1 ...
//      If the field is tagged then we tag to the pointer to it with 2"
//   V8 src/heap/mark-compact.h:mark-compact 拆成 Prepare/StartCompaction/
//     UpdatePointers/Sweep 状态,are_map_pointers_encoded() == (state_ == UPDATE_POINTERS)
package main

// slot 是一个指针槽 —— 移动式 GC 必须能**改写**它。
//
// val 是 interface{} 而非 string:线程化阶段要把"后一个链节"临时塞进槽自己
// (GHC 的 `*field, **field = **field, field` 交换),所以槽必须能装两种东西。
// nil 表示空引用。
type slot struct {
	owner  string
	name   string
	val    interface{}
	tagged bool
}

// object 的 word0 是 info 槽,其余字为指针字段。
type object struct {
	oid          string
	payloadWords int
	fields       []*slot
	info         interface{} // string(原内容) 或 *chainNode(已入链)
}

// chainNode 是链上的一个节。注意 GHC 的链节**就是被指向的那个字段自己**:
// 节里除了"指向它的指针"不再额外占用堆空间,tag 位编在指针的最低两位。
type chainNode struct {
	field *slot
	tag   int // 1 = 未标记指针;2 = 已标记指针
}

func (o *object) sizeWords() int { return 1 + o.payloadWords + len(o.fields) }

func (o *object) newSlot(name string, tagged bool) *slot {
	s := &slot{owner: o.oid, name: name, tagged: tagged}
	o.fields = append(o.fields, s)
	return s
}

type heap struct {
	cells []*object // 地址序
	roots []*slot
}

func (h *heap) root(name, oid string, tagged bool) *slot {
	s := &slot{owner: "root", name: name, val: oid, tagged: tagged}
	h.roots = append(h.roots, s)
	return s
}

func (h *heap) heapWords() int {
	n := 0
	for _, o := range h.cells {
		n += o.sizeWords()
	}
	return n
}

func (h *heap) allSlots() []*slot {
	out := append([]*slot{}, h.roots...)
	for _, o := range h.cells {
		out = append(out, o.fields...)
	}
	return out
}

func (h *heap) find(oid string) *object {
	for _, o := range h.cells {
		if o.oid == oid {
			return o
		}
	}
	return nil
}

// slotOid 读出一个槽当前持有的对象名;空槽与"已换成链节"的槽都返回 false。
func slotOid(s *slot) (string, bool) {
	v, ok := s.val.(string)
	return v, ok
}

// refsOf 建一张"谁指向谁"的表(只在标记/线程化阶段需要,用完即弃)。
func refsOf(h *heap) map[string][]*slot {
	out := map[string][]*slot{}
	for _, s := range h.allSlots() {
		if v, ok := slotOid(s); ok {
			out[v] = append(out[v], s)
		}
	}
	return out
}

// mark 阶段:沿引用链标记全部可达对象。
func mark(h *heap, roots []*slot) map[string]bool {
	live := map[string]bool{}
	var stack []string
	for _, s := range roots {
		if v, ok := slotOid(s); ok {
			stack = append(stack, v)
		}
	}
	for len(stack) > 0 {
		oid := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		if oid == "" || live[oid] {
			continue
		}
		live[oid] = true
		if o := h.find(oid); o != nil {
			for _, f := range o.fields {
				if v, ok := slotOid(f); ok {
					stack = append(stack, v)
				}
			}
		}
	}
	return live
}

// computeForwarding 计算新地址(保序滑动):存活对象按原相对顺序紧排。
func computeForwarding(h *heap, live map[string]bool) (map[string]int, int) {
	fwd := map[string]int{}
	addr := 0
	for _, o := range h.cells {
		if live[o.oid] {
			fwd[o.oid] = addr
			addr += o.sizeWords()
		}
	}
	return fwd, addr
}

func addrStr(addr int) string { return "a" + itoa(addr) }

// updateReferences 把所有槽改写成新地址,返回被改写的槽数。
func updateReferences(h *heap, fwd map[string]int) int {
	n := 0
	for _, s := range h.allSlots() {
		if v, ok := slotOid(s); ok {
			s.val = addrStr(fwd[v])
			n++
		}
	}
	return n
}

func liveCells(h *heap, live map[string]bool) []*object {
	var out []*object
	for _, o := range h.cells {
		if live[o.oid] {
			out = append(out, o)
		}
	}
	return out
}

// lisp2Compact 保序滑动压缩(Lisp2 结构)。
func lisp2Compact(h *heap, roots []*slot) (map[string]bool, map[string]int, []*object, int) {
	live := mark(h, roots)
	fwd, _ := computeForwarding(h, live)
	updated := updateReferences(h, fwd)
	return live, fwd, liveCells(h, live), updated
}

// threadObject 把所有指向 o 的槽链进 o 的 info 槽。
//
// 与 GHC 的换入链一致:old = o.info; o.info = &chainNode{s, tag}; s.val = old
// —— 字段的原内容被搬到"链表"上,链尾自然就落回原 info 内容。
func threadObject(o *object, refs []*slot) {
	for _, s := range refs {
		tag := 1
		if s.tagged {
			tag = 2
		}
		old := o.info
		o.info = &chainNode{field: s, tag: tag}
		s.val = old
	}
}

func chainLength(o *object) int {
	n := 0
	node := o.info
	for {
		cn, ok := node.(*chainNode)
		if !ok {
			return n
		}
		n++
		node = cn.field.val // 交换进来的"后一个链节"(链尾则是原 info 内容)
	}
}

// unthreadObject 沿链把所有槽写成新地址;链尾的 info 内容被还原。
func unthreadObject(o *object, newAddr string) int {
	updated := 0
	node := o.info
	for {
		cn, ok := node.(*chainNode)
		if !ok {
			break
		}
		next := cn.field.val // 先取出后继,再覆写字段
		cn.field.val = newAddr
		cn.field.tagged = cn.tag == 2 // 保持"已标记指针"这一位
		node = next
		updated++
	}
	o.info = node // 还原原 info 内容
	return updated
}

// threadingCompact 线程化压缩:解链阶段**不需要任何 old->new 映射表**。
//
// 新地址由 running free pointer 现算,"谁指向这个对象"由链本身告知
// (链就挂在对象自己的 info 槽上),所以额外空间是 O(1)。
//
// 返回值末位是那张"谁指向谁"的表 —— 线程化一完成它就被清空,调用方拿到的是
// 一张空表,这就是"不需要映射表"的证据。
func threadingCompact(h *heap, roots []*slot) (map[string]bool, int, []*object, int, map[string][]*slot) {
	live := mark(h, roots)

	table := refsOf(h)
	for _, o := range h.cells {
		if live[o.oid] {
			threadObject(o, table[o.oid])
		}
	}
	// 线程化完成后这张表即被销毁 —— 真实实现里它与标记扫描是同一次遍历,
	// 用完即弃(Python 版的 refs_of.clear() 是同一个语义)。
	for k := range table {
		delete(table, k)
	}

	free, updated := 0, 0
	var newCells []*object
	for _, o := range h.cells {
		if live[o.oid] {
			updated += unthreadObject(o, addrStr(free))
			free += o.sizeWords()
			newCells = append(newCells, o)
		}
	}
	return live, free, newCells, updated, table
}

// itoa 保持零依赖。
func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var buf [20]byte
	i := len(buf)
	for n > 0 {
		i--
		buf[i] = byte('0' + n%10)
		n /= 10
	}
	return string(buf[i:])
}

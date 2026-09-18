// 分代式 GC 模型(Go 版):eden + 两个 survivor + 老年代 + 写屏障/记忆集。
//
// 权威来源(实际联网阅读,与 Python 版同源):
//   Oracle《Java SE 8 HotSpot VM GC Tuning Guide》第 3 章 *Generations*:
//     "the weak generational hypothesis, which states that most objects survive for
//      only a short period of time"; "The young generation consists of eden and two
//      survivor spaces"; "Objects are copied between survivor spaces in this way until
//      they are old enough to be tenured"; "a minor collection in which only the young
//      generation is collected"; "a major collection, in which the entire heap is
//      collected"; "The costs of such collections are, to the first order, proportional
//      to the number of live objects being collected"
//   V8 博客 *Orinoco: young generation garbage collection*(nursery / intermediate /
//     old generation 三级):"Old-to-young generation references are roots for the young
//     generation garbage collection. These references are recorded to provide efficient
//     root identification and reference updates when objects are moved."
//
// 建模简化(README 已声明):对象在各空间之间"搬动"用切片归属表示,不模拟真实地址;
// 卡表粒度 = 一个槽(真实实现里是一张卡 = 512 B / 一页)。
package main

const (
	tenuringThreshold = 2 // 对应 HotSpot 的 MaxTenuringThreshold
	edenCapacity      = 6
	survivorCapacity  = 6
	youngCapacity     = edenCapacity + survivorCapacity
	oldCapacity       = 24
)

// obj:gen 0 = 新生代(eden/survivor),1 = 老年代。
type obj struct {
	oid    string
	words  int
	age    int
	gen    int
	fields map[string]*obj
}

func newObj(oid string, words int) *obj {
	return &obj{oid: oid, words: words, fields: map[string]*obj{}}
}

// slotRef = 记忆集里的一个槽(老年代对象里的某个字段)。
type slotRef struct {
	owner *obj
	name  string
}

type genHeap struct {
	eden     []*obj
	surv     [2][]*obj // 只有 surv[cur] 是"活"的那个
	cur      int
	old      []*obj
	roots    []*obj
	remember []slotRef
	cards    map[string]bool
	tenuring int
	barrier  bool
	genera   bool
	youngCap int
	oldCap   int
	heapCap  int

	minorGCs    int
	majorGCs    int
	promoted    int
	tracedWords int // 累计被扫描的字数 = 成本代理量
}

func newHeap(tenuring int, barrier, generational bool, youngCap, oldCap int) *genHeap {
	return &genHeap{
		cards:    map[string]bool{},
		tenuring: tenuring,
		barrier:  barrier,
		genera:   generational,
		youngCap: youngCap,
		oldCap:   oldCap,
		heapCap:  youngCap + oldCap,
	}
}

func defaultHeap() *genHeap {
	return newHeap(tenuringThreshold, true, true, youngCapacity, oldCapacity)
}

// --------------------------------------------------------------------------
// 分配与写
// --------------------------------------------------------------------------

func (h *genHeap) alloc(oid string, words int) *obj {
	o := newObj(oid, words)
	h.eden = append(h.eden, o)
	return o
}

// store 是**带写屏障**的存储:老 -> 新 的引用必须进记忆集,否则 minor GC 会漏掉它。
func (h *genHeap) store(owner *obj, name string, target *obj) {
	owner.fields[name] = target
	if h.barrier && target != nil && owner.gen == 1 && target.gen == 0 {
		h.cards[owner.oid+"."+name] = true
		h.addRemembered(owner, name)
	}
}

// rawStore 是无屏障写(对照实验用)。
func (h *genHeap) rawStore(owner *obj, name string, target *obj) {
	owner.fields[name] = target
}

func (h *genHeap) addRemembered(owner *obj, name string) {
	for _, r := range h.remember {
		if r.owner == owner && r.name == name {
			return
		}
	}
	h.remember = append(h.remember, slotRef{owner: owner, name: name})
}

// --------------------------------------------------------------------------
// 空间与统计
// --------------------------------------------------------------------------

func (h *genHeap) young() []*obj {
	out := append([]*obj{}, h.eden...)
	return append(out, h.surv[h.cur]...)
}

func (h *genHeap) youngWords() int {
	n := 0
	for _, o := range h.young() {
		n += o.words
	}
	return n
}

func (h *genHeap) oldWords() int {
	n := 0
	for _, o := range h.old {
		n += o.words
	}
	return n
}

func (h *genHeap) resident() map[*obj]bool {
	out := map[*obj]bool{}
	for _, o := range h.young() {
		out[o] = true
	}
	for _, o := range h.old {
		out[o] = true
	}
	return out
}

// danglingRef 描述一个"仍被引用、却不在任何空间里"的槽。
type danglingRef struct {
	owner  string
	field  string
	target string
}

func (h *genHeap) dangling() []danglingRef {
	res := h.resident()
	var out []danglingRef
	for _, o := range append(h.young(), h.old...) {
		for _, name := range sortedKeys(o.fields) {
			if t := o.fields[name]; t != nil && !res[t] {
				out = append(out, danglingRef{o.oid, name, t.oid})
			}
		}
	}
	for _, r := range h.roots {
		if r != nil && !res[r] {
			out = append(out, danglingRef{"root", "-", r.oid})
		}
	}
	return out
}

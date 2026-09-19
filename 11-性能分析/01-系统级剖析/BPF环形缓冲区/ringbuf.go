// BPF ring buffer（BPF_MAP_TYPE_RINGBUF）—— Go 侧实现
// （无工具链，人工审查 + 机械核查）。
//
// 口径同 Python 版，来自 Linux 内核文档 "BPF ring buffer"：
//   reserve 在自旋锁下串行推进 producer ⇒ 保留严格有序；commit 无锁；
//   记录按保留顺序可见，但必须等前面所有记录都提交过；空间不足直接失败不阻塞；
//   discard 只打标记让消费者跳过；自节流通知。
package main

import "fmt"

const (
	hdrSize    = 8
	pageSize   = 4096
	busyBit    = 1 << 31
	discardBit = 1 << 30
	lenMask    = (1 << 30) - 1

	rbAvailData = 0
	rbRingSize  = 1
	rbConsPos   = 2
	rbProdPos   = 3

	rbNoWakeup    = 1 << 0
	rbForceWakeup = 1 << 1
)

func Align8(n int) int { return (n + 7) & ^7 }

func IsPow2(n int) bool { return n > 0 && n&(n-1) == 0 }

// Record 是一条记录的元数据（载荷内容本 demo 不关心）。
type Record struct {
	Pos, Length, Total int
	State              string // reserved / committed / discarded
	PgOff              int
}

// RingBuf 是 MPSC 环形缓冲的简化模型。
type RingBuf struct {
	Size          int
	Producer      int // 已保留的数据总量
	Consumer      int
	Records       map[int]*Record
	LockHeld      bool // 模拟 reserve 的自旋锁
	Notifications int
}

func NewRingBuf(maxEntries int) (*RingBuf, error) {
	if !IsPow2(maxEntries) {
		return nil, fmt.Errorf("max_entries 必须是 2 的幂: %d", maxEntries)
	}
	return &RingBuf{Size: maxEntries, Records: map[int]*Record{}}, nil
}

func (b *RingBuf) putHdr(pos, length, flags int) int {
	off := pos & (b.Size - 1)
	pg := off / pageSize
	_ = (length & lenMask) | flags // 真实实现会写进 mmap 数据区，此处省略
	_ = pg
	return pg
}

// Reserve：预留一段空间，返回记录头指针；空间不足或 NMI 抢不到锁时返回 false。
func (b *RingBuf) Reserve(length int, nmi bool) (int, bool) {
	if nmi && b.LockHeld {
		return 0, false // NMI 里拿不到自旋锁 ⇒ 即使没满也失败
	}
	total := Align8(hdrSize + length)
	if b.Producer-b.Consumer+total > b.Size {
		return 0, false // 空间不足：**不阻塞**
	}
	b.LockHeld = true
	pos := b.Producer
	b.Producer += total
	pg := b.putHdr(pos, length, busyBit)
	b.Records[pos] = &Record{Pos: pos, Length: length, Total: total,
		State: "reserved", PgOff: pg}
	b.LockHeld = false
	return pos, true
}

func (b *RingBuf) submit(ptr int, discarded bool, flags int) bool {
	rec, ok := b.Records[ptr]
	if !ok || rec.State != "reserved" {
		return false
	}
	if discarded {
		rec.State = "discarded"
		b.putHdr(ptr, rec.Length, discardBit)
	} else {
		rec.State = "committed"
		b.putHdr(ptr, rec.Length, 0)
	}
	caughtUp := b.Consumer >= ptr
	if flags&rbForceWakeup != 0 || (caughtUp && flags&rbNoWakeup == 0) {
		b.Notifications++
	}
	return true
}

func (b *RingBuf) Commit(ptr, flags int) bool  { return b.submit(ptr, false, flags) }
func (b *RingBuf) Discard(ptr, flags int) bool { return b.submit(ptr, true, flags) }

// ready 按保留顺序遍历可见记录；遇到未提交的就停（它挡住后面所有）。
func (b *RingBuf) ready() []*Record {
	var out []*Record
	pos := b.Consumer
	for pos < b.Producer {
		rec, ok := b.Records[pos]
		if !ok {
			return out
		}
		if rec.State == "reserved" {
			return out
		}
		out = append(out, rec)
		pos += rec.Total
	}
	return out
}

func (b *RingBuf) AvailData() int {
	s := 0
	for _, r := range b.ready() {
		s += r.Total
	}
	return s
}

func (b *RingBuf) ConsumeOne() *Record {
	rs := b.ready()
	if len(rs) == 0 {
		return nil
	}
	rec := rs[0]
	b.Consumer += rec.Total
	delete(b.Records, rec.Pos)
	return rec
}

func (b *RingBuf) Query(what int) int {
	switch what {
	case rbAvailData:
		return b.AvailData()
	case rbRingSize:
		return b.Size
	case rbConsPos:
		return b.Consumer
	case rbProdPos:
		return b.Producer
	}
	return -1
}

func main() {
	b, err := NewRingBuf(4096)
	if err != nil {
		fmt.Println("new error:", err)
		return
	}
	if _, err := NewRingBuf(3000); err != nil {
		fmt.Println("非 2 的幂被拒:", err)
	}

	// 慢生产者：后保留的先提交，仍不可见，直到前一条提交
	a, _ := b.Reserve(16, false)
	c, _ := b.Reserve(16, false)
	b.Commit(c, 0)
	fmt.Printf("后保留先提交 ⇒ avail=%d（应为 0）\n", b.AvailData())
	b.Commit(a, 0)
	fmt.Printf("前一条提交后 ⇒ avail=%d，通知次数=%d\n", b.AvailData(), b.Notifications)

	// 填满后不阻塞
	small, _ := NewRingBuf(64)
	small.Reserve(24, false)
	p, ok := small.Reserve(24, false)
	fmt.Printf("填满后再 reserve ⇒ ok=%v ptr=%d prod=%d\n", ok, p, small.Query(rbProdPos))

	// NMI 抢锁失败
	nmi, _ := NewRingBuf(256)
	nmi.LockHeld = true
	_, ok = nmi.Reserve(8, true)
	fmt.Printf("NMI 下 reserve ⇒ ok=%v（即使没满也失败）\n", ok)
}

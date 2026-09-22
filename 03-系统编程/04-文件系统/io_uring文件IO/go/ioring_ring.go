package main

import "fmt"

// Ring 是 SQ/CQ 环的最小模型。
type Ring struct {
	SQEntries int
	CQEntries int
	SQPoll    bool
	Overflow  bool
	SQHead    int
	SQTail    int
	CQHead    int
	CQTail    int
	SQArray   []int
	CQEs      []*CQE
	Backlog   []*CQE
	Dropped   int
}

// NewRing 建一个环，CQ 默认 2 倍。
func NewRing(entries int, sqpoll, overflow bool) *Ring {
	if entries > ioRingMaxEntries {
		entries = ioRingMaxEntries
	}
	cq := 2 * entries
	if cq > ioRingMaxCQEntries {
		cq = ioRingMaxCQEntries
	}
	return &Ring{
		SQEntries: entries, CQEntries: cq, SQPoll: sqpoll, Overflow: overflow,
		SQArray: make([]int, entries), CQEs: make([]*CQE, cq),
	}
}

func (r *Ring) SQPending() int { return r.SQTail - r.SQHead }
func (r *Ring) CQPending() int { return r.CQTail - r.CQHead }
func (r *Ring) SQFull() bool   { return r.SQPending() >= r.SQEntries }
func (r *Ring) CQFull() bool   { return r.CQPending() >= r.CQEntries }

// GetSQE 取一个 SQE 槽位，SQ 满返回 nil。
func (r *Ring) GetSQE() *SQE {
	if r.SQFull() {
		return nil
	}
	idx := r.SQTail & (r.SQEntries - 1)
	r.SQArray[idx] = idx
	r.SQTail++
	return &SQE{}
}

// FillCQ 模拟内核投递 CQE。
func (r *Ring) FillCQ(c *CQE) bool {
	if r.CQFull() {
		if r.Overflow {
			r.Backlog = append(r.Backlog, c)
			return true
		}
		r.Dropped++
		return false
	}
	r.CQEs[r.CQTail&(r.CQEntries-1)] = c
	r.CQTail++
	return true
}

// Reap 收割 CQE，随后回填 backlog。
func (r *Ring) Reap(n int) []*CQE {
	out := []*CQE{}
	for r.CQHead < r.CQTail && len(out) < n {
		i := r.CQHead & (r.CQEntries - 1)
		out = append(out, r.CQEs[i])
		r.CQEs[i] = nil
		r.CQHead++
	}
	for len(r.Backlog) > 0 && !r.CQFull() {
		r.CQEs[r.CQTail&(r.CQEntries-1)] = r.Backlog[0]
		r.Backlog = r.Backlog[1:]
		r.CQTail++
	}
	return out
}

// NeedSyscall 判断是否需要 io_uring_enter。
func (r *Ring) NeedSyscall(minComplete int) bool {
	if minComplete > 0 {
		return true
	}
	return !r.SQPoll
}

// RegisteredBuffers 是 IORING_REGISTER_BUFFERS 的模型。
type RegisteredBuffers struct {
	Buffers [][]byte
	Tags    []uint64
}

func (rb *RegisteredBuffers) Register(bufs [][]byte) error {
	if len(rb.Buffers) > 0 {
		return fmt.Errorf("EBUSY: already registered")
	}
	rb.Buffers = bufs
	rb.Tags = make([]uint64, len(bufs))
	return nil
}

// Update 只能替换已有槽位，不能扩容。
func (rb *RegisteredBuffers) Update(idx int, buf []byte, tag uint64) error {
	if idx < 0 || idx >= len(rb.Buffers) {
		return fmt.Errorf("EINVAL: index out of range")
	}
	rb.Buffers[idx] = buf
	rb.Tags[idx] = tag
	return nil
}

// PrepReadFixed 构造 READ_FIXED：addr 是索引。
func PrepReadFixed(fd int32, index uint16, length uint32, off uint64,
	userData uint64) *SQE {
	return &SQE{Opcode: ioRingOpReadFixed, FD: fd, Addr: uint64(index),
		Len: length, Off: off, UserData: userData, BufIndex: index}
}

// PrepRead 构造普通 READ：addr 是地址。
func PrepRead(fd int32, addr uint64, length uint32, off uint64,
	userData uint64) *SQE {
	return &SQE{Opcode: ioRingOpRead, FD: fd, Addr: addr, Len: length,
		Off: off, UserData: userData}
}

func main() {
	fmt.Println("== mmap 偏移与上限 ==")
	fmt.Printf("  SQ_RING=0x%X CQ_RING=0x%X SQES=0x%X PBUF=0x%X\n",
		ioRingOffSQRing, ioRingOffCQRing, ioRingOffSQEs, ioRingOffPbufRing)
	fmt.Printf("  MAX_ENTRIES=%d MAX_CQ_ENTRIES=%d\n",
		ioRingMaxEntries, ioRingMaxCQEntries)
	for _, e := range []int{8, 4096, 32768} {
		r, _ := Setup(e, 0, 0)
		fmt.Printf("  entries=%-6d → sq=%-6d cq=%-6d sq_ring=%-7d sqes=%-7d cq_ring=%d\n",
			e, r.SQEntries, r.CQEntries,
			MmapLength("sq_ring", r.SQEntries, r.CQEntries, cqeSize, sqeSize),
			MmapLength("sqes", r.SQEntries, r.CQEntries, cqeSize, sqeSize),
			MmapLength("cq_ring", r.SQEntries, r.CQEntries, cqeSize, sqeSize))
	}
	n, _ := ClampEntries(65536, ioRingSetupClamp)
	fmt.Printf("  entries=65536 有 CLAMP → %d\n", n)
	_, err := ClampEntries(65536, 0)
	fmt.Printf("  entries=65536 无 CLAMP → %v\n", err)

	fmt.Println("== 注册缓冲区：addr 是索引不是指针 ==")
	fixed := PrepReadFixed(3, 2, 4096, 4096, 1)
	plain := PrepRead(3, 0x7F1234560000, 4096, 4096, 2)
	fmt.Printf("  %-11s opcode=%d addr=0x%X buf_index=%d fixedBuf=%v\n",
		opName[fixed.Opcode], fixed.Opcode, fixed.Addr, fixed.BufIndex,
		fixed.UsesFixedBuffer())
	fmt.Printf("  %-11s opcode=%d addr=0x%X buf_index=%d fixedBuf=%v\n",
		opName[plain.Opcode], plain.Opcode, plain.Addr, plain.BufIndex,
		plain.UsesFixedBuffer())

	fmt.Println("== CQE 的 res 单通道 ==")
	for _, res := range []int32{4096, 0, -5, -11} {
		c := CQE{UserData: 1, Res: res}
		fmt.Printf("  res=%-6d → ok=%-5v errno=%d\n", res, c.OK(), c.Errno())
	}

	fmt.Println("== CQ 溢出 ==")
	for _, ov := range []bool{false, true} {
		r := NewRing(1, false, ov)
		for i := uint64(1); i <= 3; i++ {
			r.FillCQ(&CQE{UserData: i, Res: 1})
		}
		fmt.Printf("  overflow=%-5v → ring %d, backlog %d, dropped %d\n",
			ov, r.CQPending(), len(r.Backlog), r.Dropped)
	}

	fmt.Println("== 是否需要 io_uring_enter ==")
	for _, sp := range []bool{false, true} {
		r := NewRing(8, sp, false)
		fmt.Printf("  SQPOLL=%-5v → 提交 %v, 等完成 %v\n", sp,
			r.NeedSyscall(0), r.NeedSyscall(1))
	}
}

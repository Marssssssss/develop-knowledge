// futex_model.go —— futex 内核侧语义的 Go 复刻（无本机 Linux 环境，人工审查，不实跑）
//
// 语义来源与 Python 版同源：futex(2) / futex(7) / FUTEX_WAIT(2const) /
// include/uapi/linux/futex.h / kernel/futex/waitwake.c。
// 关键不变式：
//   1. word 恒 32 位；WAIT 是「原子比较-并-阻塞」，值不符立即 EAGAIN；
//   2. WAKE 返回实际唤醒数；
//   3. WAKE_OP 无条件唤醒 uaddr1 上的 nwake1 个，仅当 uaddr2 的旧值满足比较才唤醒 uaddr2；
//   4. PI futex 的 word 只能是 0 / TID / (FUTEX_WAITERS|TID)，无主却置 WAITERS 非法。
package main

import "fmt"

const (
	mask32         = 0xFFFFFFFF
	futexWaiters   = 0x80000000
	futexOwnerDied = 0x40000000
	futexTIDMask   = 0x3FFFFFFF

	eagain    = 11
	etimedout = 110
	eperm     = 1

	opSet  = 0
	opAdd  = 1
	opOr   = 2
	opAndN = 3
	opXor  = 4

	cmpEQ = 0
	cmpNE = 1
	cmpLT = 2
	cmpLE = 3
	cmpGT = 4
	cmpGE = 5
)

func futexOpEncode(op, oparg, cmp, cmparg int) uint32 {
	return uint32(((op&0xF)<<28)|((cmp&0xF)<<24)|((oparg&0xFFF)<<12)|(cmparg&0xFFF))
}

// signExtend32 与内核 sign_extend32(v, 11) 对齐
func signExtend32(v, bits int) int {
	sign := 1 << bits
	return (v & (sign - 1)) - (v & sign)
}

func futexOpDecode(enc uint32) (op, oparg, cmp, cmparg int) {
	op = int((enc & 0x70000000) >> 28) // 只取低 3 位
	cmp = int((enc & 0x0F000000) >> 24)
	oparg = signExtend32(int((enc&0x00FFF000)>>12), 11)
	if enc&(8<<28) != 0 { // FUTEX_OP_OPARG_SHIFT
		oparg = 1 << (oparg & 31)
	}
	return op, oparg, cmp, int(enc & 0xFFF)
}

func futexOpApply(op, oparg int, old uint32) uint32 {
	switch op {
	case opSet:
		return uint32(oparg) & mask32
	case opAdd:
		return (old + uint32(oparg)) & mask32
	case opOr:
		return (old | uint32(oparg)) & mask32
	case opAndN:
		return (old & ^uint32(oparg)) & mask32
	case opXor:
		return (old ^ uint32(oparg)) & mask32
	}
	panic("bad futex op")
}

type word struct {
	name  string
	value uint32
}

func (w *word) cas(expected, next uint32) bool {
	if w.value == expected {
		w.value = next & mask32
		return true
	}
	return false
}

func (w *word) tid() uint32      { return w.value & futexTIDMask }
func (w *word) hasWaiters() bool { return w.value&futexWaiters != 0 }

type waiter struct {
	tid      int
	woken    bool
	timedout bool
}

type kernel struct {
	waitq    map[*word][]*waiter
	now      int
	syscalls int
	wakeups  int
}

func newKernel() *kernel {
	return &kernel{waitq: map[*word][]*waiter{}}
}

// futexWait 是「比较-并-阻塞」：值不符立即 EAGAIN，这正是防丢失唤醒的那一步
func (k *kernel) futexWait(w *word, val uint32, tid int) (*waiter, int) {
	k.syscalls++
	if w.value != val {
		return nil, eagain
	}
	wt := &waiter{tid: tid}
	k.waitq[w] = append(k.waitq[w], wt)
	return wt, 0
}

func (k *kernel) futexWake(w *word, n int) int {
	k.syscalls++
	q := k.waitq[w]
	cnt := 0
	for len(q) > 0 && cnt < n {
		q[0].woken = true
		q = q[1:]
		cnt++
	}
	k.waitq[w] = q
	k.wakeups += cnt
	return cnt
}

// futexWakeOp 无条件唤醒 uaddr1，再按 uaddr2 的旧值决定是否唤醒 uaddr2
func (k *kernel) futexWakeOp(w1 *word, nwake1 int, w2 *word, nwake2 int, enc uint32) (int, int, uint32) {
	k.syscalls++
	op, oparg, cmp, cmparg := futexOpDecode(enc)
	old := w2.value
	w2.value = futexOpApply(op, oparg, old)
	woke1 := k.futexWake(w1, nwake1)
	woke2 := 0
	if cmpHold(cmp, old, uint32(cmparg)) {
		woke2 = k.futexWake(w2, nwake2)
	}
	return woke1, woke2, old
}

func cmpHold(cmp int, old, cmparg uint32) bool {
	switch cmp {
	case cmpEQ:
		return old == cmparg
	case cmpNE:
		return old != cmparg
	case cmpLT:
		return old < cmparg
	case cmpLE:
		return old <= cmparg
	case cmpGT:
		return old > cmparg
	}
	return old >= cmparg
}

// piFutex：word = 0 | TID | (FUTEX_WAITERS|TID) [| FUTEX_OWNER_DIED]
type piFutex struct {
	k *kernel
	w *word
}

func (p *piFutex) trylock(tid uint32) bool {
	return p.w.cas(0, tid) // 无竞争：用户态 cmpxchg，零系统调用
}

func (p *piFutex) lockPI(tid uint32) (*waiter, int) {
	if p.trylock(tid) {
		return nil, 0
	}
	p.w.value |= futexWaiters
	p.k.syscalls++
	wt := &waiter{tid: int(tid)}
	p.k.waitq[p.w] = append(p.k.waitq[p.w], wt)
	return wt, 0
}

func (p *piFutex) unlockPI(tid uint32) (uint32, int) {
	p.k.syscalls++
	if p.w.tid() != tid {
		return 0, eperm // 非持有者释放
	}
	q := p.k.waitq[p.w]
	if len(q) > 0 {
		nxt := q[0]
		nxt.woken = true
		p.k.waitq[p.w] = q[1:]
		p.k.wakeups++
		v := nxt.tid
		if len(p.k.waitq[p.w]) > 0 {
			v |= futexWaiters
		}
		p.w.value = uint32(v)
		return uint32(nxt.tid), 0
	}
	p.w.value = 0
	return 0, 0
}

func (p *piFutex) ownerDies() uint32 {
	q := p.k.waitq[p.w]
	if len(q) == 0 {
		p.w.value = 0
		return 0
	}
	nxt := q[0]
	nxt.woken = true
	p.k.waitq[p.w] = q[1:]
	v := uint32(nxt.tid) | futexOwnerDied
	if len(p.k.waitq[p.w]) > 0 {
		v |= futexWaiters
	}
	p.w.value = v
	return uint32(nxt.tid)
}

func piStateValid(w *word) bool {
	if w.value&futexWaiters != 0 && w.tid() == 0 {
		return false // 无主却有 WAITERS：非法
	}
	return true
}

func main() {
	k := newKernel()
	w := &word{name: "lock", value: 1}
	w.value = 0
	fmt.Println("释放方 WAKE(无等待者) ->", k.futexWake(w, 1))
	_, err := k.futexWait(w, 1, 2) // 拿旧值 1 来等
	fmt.Println("迟到者 FUTEX_WAIT -> EAGAIN?", err == eagain)

	u1, u2 := &word{name: "u1"}, &word{name: "u2", value: 3}
	k.futexWait(u1, 0, 1)
	k.futexWait(u2, 3, 3)
	enc := futexOpEncode(opAdd, 1, cmpEQ, 4)
	w1, w2, old := k.futexWakeOp(u1, 1, 1, u2, enc)
	fmt.Printf("WAKE_OP old=%d 唤醒u1=%d 唤醒u2=%d u2=%d\n", old, w1, w2, u2.value)

	p := &piFutex{k: k, w: &word{name: "pi"}}
	fmt.Println("PI 无竞争取锁:", p.trylock(11), "系统调用:", k.syscalls)
	p.lockPI(12)
	fmt.Printf("PI word=0x%08x 合法=%v\n", p.w.value, piStateValid(p.w))
	_, err = p.unlockPI(13)
	fmt.Println("非持有者 unlock -> EPERM?", err == eperm)
	next, _ := p.unlockPI(11)
	fmt.Println("交接给:", next)
}

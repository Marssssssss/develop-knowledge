// scm_model.go — SCM_RIGHTS 的尺寸算术与 fd 表模型（与 python/scm_model.py 同构）。
//
// 分两半：
//   * cmsg 尺寸算术 —— 直接对应 cmsg(3) 的 CMSG_ALIGN / CMSG_LEN / CMSG_SPACE；
//   * fd 表 + open file description 模型 —— 用来演示"传的是 OFD 引用"、
//     MSG_CTRUNC 截断、RLIMIT_NOFILE 与在途 fd 的记账。
//
// 真实系统调用路径在 main.go。
package main

import "fmt"

const (
	// man7 unix(7)：Linux >= 2.6.38 为 253，更早为 255；超出报 EINVAL。
	scmMaxFd    = 253
	scmMaxFdOld = 255

	// x86-64 上 struct cmsghdr = size_t(8) + int(4) + int(4)。
	// Go 里没有这个结构体，只能自己算 —— 这也是最容易写错的地方。
	cmsgHdrSize = 16
	cmsgAlignTo = 8
)

// msgFlags 位（recvmsg 返回的 msg_flags）
const (
	msgCtrunc       = 0x08
	msgTrunc        = 0x20
	msgCmsgCloexec  = 0x40000000
)

// ---------------------------------------------------------------- cmsg 算术

// cmsgAlign 等价 CMSG_ALIGN：按 sizeof(long) 向上取整。
func cmsgAlign(length int) int {
	return (length + cmsgAlignTo - 1) &^ (cmsgAlignTo - 1)
}

// cmsgLen 等价 CMSG_LEN：写进 cmsg_len 的值，**不含**尾部填充。
func cmsgLen(dataLen int) int {
	return cmsgHdrSize + dataLen
}

// cmsgSpace 等价 CMSG_SPACE：这样一个控制项**实际占用**的缓冲字节数，**含**填充。
func cmsgSpace(dataLen int) int {
	return cmsgAlign(cmsgHdrSize + dataLen)
}

// fdsSpace 传 n 个 fd 需要预留的 msg_control 缓冲。
func fdsSpace(nfds int) int {
	return cmsgSpace(4 * nfds)
}

// fdsFit 在 ctrlBuf 字节里最多装得下几个 fd（依据 CMSG_SPACE）。
func fdsFit(ctrlBuf, nfds int) int {
	n := 0
	for n < nfds && fdsSpace(n+1) <= ctrlBuf {
		n++
	}
	return n
}

// ---------------------------------------------------------------- OFD / fd 表

// openFileDescription 就是内核里的"打开文件描述"：偏移量与引用计数都在这一层。
type openFileDescription struct {
	oid      int
	kind     string
	data     []byte
	offset   int
	refcount int
}

func newOFD(kind string, payload []byte) *openFileDescription {
	ofdCount++
	return &openFileDescription{oid: ofdCount, kind: kind, data: append([]byte(nil), payload...)}
}

var ofdCount int

func (o *openFileDescription) read(n int) []byte {
	end := o.offset + n
	if end > len(o.data) {
		end = len(o.data)
	}
	chunk := o.data[o.offset:end]
	o.offset = end
	return chunk
}

// fdEntry 是 fd 表里的一项：一个 OFD 引用 + 该编号自己的 CLOEXEC 标志。
type fdEntry struct {
	ofd     *openFileDescription
	cloexec bool
}

// fdTable 一个进程的 fd 表；limit 即 RLIMIT_NOFILE。
type fdTable struct {
	name    string
	limit   int
	entries map[int]*fdEntry
}

func newFdTable(name string, limit int) *fdTable {
	return &fdTable{name: name, limit: limit, entries: map[int]*fdEntry{}}
}

// allocFd 内核取"从 0 开始最小的空位"，0/1/2 已被 stdin/stdout/stderr 占用。
func (t *fdTable) allocFd() int {
	fd := 3
	for {
		if _, used := t.entries[fd]; !used {
			return fd
		}
		fd++
	}
}

// install 往表里装一个引用；表满返回 errTooManyOpenFiles。
func (t *fdTable) install(ofd *openFileDescription, cloexec bool) (int, error) {
	if len(t.entries) >= t.limit {
		return -1, errTooManyOpenFiles
	}
	fd := t.allocFd()
	ofd.refcount++
	t.entries[fd] = &fdEntry{ofd: ofd, cloexec: cloexec}
	return fd, nil
}

func (t *fdTable) get(fd int) (*fdEntry, error) {
	e, ok := t.entries[fd]
	if !ok {
		return nil, errBadFd
	}
	return e, nil
}

func (t *fdTable) close(fd int) error {
	e, ok := t.entries[fd]
	if !ok {
		return errBadFd
	}
	delete(t.entries, fd)
	e.ofd.refcount--
	return nil
}

func (t *fdTable) size() int { return len(t.entries) }

// ---------------------------------------------------------------- 错误

type scmError struct {
	errno string
	why   string
}

func (e *scmError) Error() string { return fmt.Sprintf("%s: %s", e.errno, e.why) }

var (
	errTooManyOpenFiles = &scmError{"EMFILE", "已达 RLIMIT_NOFILE"}
	errBadFd            = &scmError{"EBADF", "描述符不存在"}
	errInvalid          = &scmError{"EINVAL", "参数不合法"}
	errTooManyRefs      = &scmError{"ETOOMANYREFS", "在途 fd 超过 RLIMIT_NOFILE"}
	errNotConn          = &scmError{"ENOTCONN", "socket 没有对端"}
)

// ---------------------------------------------------------------- socket 模型

type message struct {
	data []byte
	fds  []*openFileDescription
}

// socketModel 只模拟队列与在途 fd 记账两件事，不碰真实内核。
type socketModel struct {
	kind  string // "stream" / "dgram"
	peer  *socketModel
	queue []*message
}

func newPair(kind string) (*socketModel, *socketModel) {
	a := &socketModel{kind: kind}
	b := &socketModel{kind: kind, peer: a}
	a.peer = b
	return a, b
}

// sendmsg 返回写入的字节数；fds 是发送方表里的 fd 号。
func (s *socketModel) sendmsg(sender *fdTable, data []byte, fds []int, capSysResource bool) (int, error) {
	if s.peer == nil {
		return 0, errNotConn
	}
	if len(fds) > scmMaxFd {
		return 0, &scmError{"EINVAL", fmt.Sprintf("一次最多 %d 个 fd（收到 %d）", scmMaxFd, len(fds))}
	}
	if s.kind == "stream" && len(data) == 0 {
		// man7 unix(7): "At least one byte of real data should be sent …"
		return 0, &scmError{"EINVAL", "流式 socket 传 fd 必须附带 >=1 字节真实数据"}
	}
	ofds := make([]*openFileDescription, 0, len(fds))
	for _, fd := range fds {
		e, err := sender.get(fd)
		if err != nil {
			return 0, err
		}
		ofds = append(ofds, e.ofd)
	}
	if !capSysResource && s.peer.pendingFds()+len(fds) > sender.limit {
		return 0, &scmError{"ETOOMANYREFS",
			fmt.Sprintf("在途 %d 超过 RLIMIT_NOFILE=%d 且无 CAP_SYS_RESOURCE", s.peer.pendingFds()+len(fds), sender.limit)}
	}
	for _, o := range ofds {
		o.refcount++ // 队列持有的"在途引用"
	}
	s.peer.queue = append(s.peer.queue, &message{data: append([]byte(nil), data...), fds: ofds})
	return len(data), nil
}

// pendingFds 本 socket **接收队列**里挂着、还没被本进程接走的 fd 数。
func (s *socketModel) pendingFds() int {
	n := 0
	for _, m := range s.queue {
		n += len(m.fds)
	}
	return n
}

// inFlight 本方已发出、对端还没接走的 fd 数（man7 unix(7) 里 ETOOMANYREFS
// 说明中定义的"in-flight file descriptor"）。
func (s *socketModel) inFlight() int {
	if s.peer == nil {
		return s.pendingFds()
	}
	return s.peer.pendingFds()
}

type recvOutcome struct {
	data         []byte
	fds          []int
	msgFlags     int
	droppedTrunc int
	droppedRlim  int
}

// recvmsg 一次收取。bufSize 是数据缓冲，ctrlBuf 是控制缓冲尺寸。
func (s *socketModel) recvmsg(recv *fdTable, bufSize, ctrlBuf int, flags int) *recvOutcome {
	if len(s.queue) == 0 {
		return nil
	}
	out := &recvOutcome{}
	var anc []*openFileDescription
	for len(s.queue) > 0 && len(out.data) < bufSize {
		m := s.queue[0]
		room := bufSize - len(out.data)
		take := len(m.data)
		if take > room {
			take = room
		}
		out.data = append(out.data, m.data[:take]...)
		m.data = m.data[take:]
		if len(m.fds) > 0 {
			anc = m.fds // 屏障：本条控制数据随本次返回，后面的字节留到下次
			m.fds = nil
			s.queue = s.queue[1:]
			break
		}
		if len(m.data) > 0 {
			break
		}
		s.queue = s.queue[1:]
	}
	if len(anc) == 0 {
		return out
	}
	fit := fdsFit(ctrlBuf, len(anc))
	if fit < len(anc) {
		// "the excess file descriptors are automatically closed in the
		//  receiving process" —— man7 unix(7)
		out.msgFlags |= msgCtrunc
		out.droppedTrunc = len(anc) - fit
	}
	cloexec := flags&msgCmsgCloexec != 0
	for _, o := range anc {
		o.refcount-- // 释放队列持有的在途引用
	}
	for _, o := range anc[:fit] {
		fd, err := recv.install(o, cloexec)
		if err != nil {
			out.droppedRlim++
			continue
		}
		out.fds = append(out.fds, fd)
	}
	return out
}

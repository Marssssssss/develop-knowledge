// 非阻塞 IO 与 EAGAIN 语义 —— Go 版模型
//
// 与 nbio_model.py 同题异构：把 read(2)/recv(2)/socket(7) 的语义搬到 Go 的
// 显式错误返回风格里，用来对照「errno 单通道」与「(值, error) 双通道」两种
// 错误建模方式的差异。
//
// 依据：
//   read(2)   EAGAIN / EAGAIN or EWOULDBLOCK 两条 ERRORS 条目
//   recv(2)   短读语义 + MSG_DONTWAIT 按调用覆盖
//   socket(7) O_NONBLOCK 由 fcntl(2) 设置，之后「所有会阻塞的操作」返回 EAGAIN
package main

import (
	"errors"
	"fmt"
)

// Linux/x86-64 的 errno 编号
const (
	EAGAIN      = 11
	EWOULDBLOCK = 11
	EINPROGRESS = 115
	EALREADY    = 114
	EISCONN     = 106
	ECONNRESET  = 104
)

const (
	O_NONBLOCK   = 0x800
	MSG_DONTWAIT = 0x40
)

// Errno 是一个可比较的错误类型，让调用方能精确判断「是不是 EAGAIN」
type Errno int

func (e Errno) Error() string {
	switch int(e) {
	case EAGAIN:
		return "resource temporarily unavailable"
	case EINPROGRESS:
		return "operation now in progress"
	case EALREADY:
		return "operation already in progress"
	case EISCONN:
		return "transport endpoint is already connected"
	}
	return fmt.Sprintf("errno %d", int(e))
}

// Sock 建模一个 socket 文件描述符
type Sock struct {
	Fl       int    // O_NONBLOCK 标志位
	Kind     string // "socket" | "pipe"
	Rcv      []byte // 已到达内核、未被应用读走
	Incoming [][]byte
	SndRoom  int
	Backlog  []string
	State    string
	SoError  int
}

func (s *Sock) Nonblock() bool { return s.Fl&O_NONBLOCK != 0 }

// Net 统计系统调用与挂起次数
type Net struct {
	Syscalls int
	Waits    int
}

func (n *Net) enter() { n.Syscalls++ }

// Arrive 模拟网卡 + 内核下半部投递数据，不占用应用线程
func (n *Net) Arrive(s *Sock, data []byte) {
	s.Rcv = append(s.Rcv, data...)
}

// block 阻塞等待：把网络侧排队的数据搬进内核缓冲
func (n *Net) block(s *Sock) {
	n.Waits++
	for i := 0; i < len(s.Incoming); i++ {
		s.Rcv = append(s.Rcv, s.Incoming[i]...)
	}
	s.Incoming = nil
	if s.State == "SYN_SENT" {
		s.State = "ESTABLISHED"
	}
	if s.SndRoom == 0 {
		s.SndRoom = 4096
	}
}

func nonblockingNow(s *Sock, flags int) bool {
	return s.Nonblock() || flags&MSG_DONTWAIT != 0
}

// Recv 对应 recv(2)：缓冲为空时按阻塞属性决定挂起还是 EAGAIN，
// 有数据时「有多少给多少」，绝不等满 n
func Recv(n *Net, s *Sock, want int, flags int) ([]byte, error) {
	n.enter()
	if len(s.Rcv) == 0 {
		if nonblockingNow(s, flags) {
			return nil, Errno(EAGAIN)
		}
		n.block(s)
		if len(s.Rcv) == 0 {
			return nil, Errno(EAGAIN)
		}
	}
	k := want
	if len(s.Rcv) < k {
		k = len(s.Rcv)
	}
	out := make([]byte, k)
	copy(out, s.Rcv[:k])
	s.Rcv = s.Rcv[k:]
	return out, nil
}

// Send 对应 send(2)：返回实际接收的字节数（可能小于请求，即部分写）
func Send(n *Net, s *Sock, data []byte, flags int) (int, error) {
	n.enter()
	if s.SndRoom == 0 {
		if nonblockingNow(s, flags) {
			return 0, Errno(EAGAIN)
		}
		n.block(s)
		if s.SndRoom == 0 {
			return 0, Errno(EAGAIN)
		}
	}
	k := len(data)
	if s.SndRoom < k {
		k = s.SndRoom
	}
	s.SndRoom -= k
	return k, nil
}

// Accept 对应 accept(2)：非阻塞且完成队列为空时返回 EAGAIN
func Accept(n *Net, s *Sock) (string, error) {
	n.enter()
	if len(s.Backlog) == 0 {
		if s.Nonblock() {
			return "", Errno(EAGAIN)
		}
		n.block(s)
		if len(s.Backlog) == 0 {
			return "", Errno(EAGAIN)
		}
	}
	c := s.Backlog[0]
	s.Backlog = s.Backlog[1:]
	return c, nil
}

// Connect 对应 connect(2)：非阻塞一律先回 EINPROGRESS
func Connect(n *Net, s *Sock) error {
	n.enter()
	switch s.State {
	case "ESTABLISHED":
		return Errno(EISCONN)
	case "SYN_SENT":
		return Errno(EALREADY)
	}
	if s.Nonblock() {
		s.State = "SYN_SENT"
		return Errno(EINPROGRESS)
	}
	s.State = "ESTABLISHED"
	return nil
}

// SoError 对应 getsockopt(SO_ERROR)：非阻塞 connect 的成败只能从这里取
func SoError(n *Net, s *Sock) int {
	n.enter()
	return s.SoError
}

// ErrnoWording 复现 read(2) 按 fd 类型区分的措辞
func ErrnoWording(s *Sock) string {
	if s.Kind == "socket" {
		return "EAGAIN or EWOULDBLOCK"
	}
	return "EAGAIN"
}

// ---------------------------------------------------------------- 自检

var nAssert, nFail int

func check(label string, cond bool, detail string) {
	nAssert++
	if cond {
		fmt.Printf("ok   %-56s %s\n", label, detail)
		return
	}
	nFail++
	fmt.Printf("FAIL %-56s %s\n", label, detail)
}

func main() {
	fmt.Println("== errno 编号与措辞 ==")
	check("Linux 上 EAGAIN 与 EWOULDBLOCK 同值", EAGAIN == EWOULDBLOCK && EAGAIN == 11,
		fmt.Sprintf("EAGAIN=%d", EAGAIN))
	check("socket fd 报错措辞含 EWOULDBLOCK",
		ErrnoWording(&Sock{Kind: "socket"}) == "EAGAIN or EWOULDBLOCK", "")
	check("非 socket fd 报错措辞只有 EAGAIN",
		ErrnoWording(&Sock{Kind: "pipe"}) == "EAGAIN", "")

	fmt.Println("\n== 读：阻塞 vs 非阻塞 ==")
	net1 := &Net{}
	sb := &Sock{Incoming: [][]byte{[]byte("hello")}}
	b, err := Recv(net1, sb, 16, 0)
	check("阻塞 recv 挂起后拿到数据", err == nil && string(b) == "hello", fmt.Sprintf("%q", b))
	check("阻塞路径挂起一次", net1.Waits == 1, fmt.Sprintf("waits=%d", net1.Waits))

	net2 := &Net{}
	sn := &Sock{Fl: O_NONBLOCK}
	b2, err2 := Recv(net2, sn, 16, 0)
	check("非阻塞 recv 空缓冲返回错误", err2 != nil && len(b2) == 0, fmt.Sprintf("%v", err2))
	var en Errno
	check("错误可精确判定为 EAGAIN", errors.As(err2, &en) && int(en) == EAGAIN,
		fmt.Sprintf("errno=%d", int(en)))
	check("非阻塞路径零挂起", net2.Waits == 0, "")

	fmt.Println("\n== 短读 ==")
	net3 := &Net{}
	s3 := &Sock{Fl: O_NONBLOCK}
	net3.Arrive(s3, []byte("abcd"))
	b3, _ := Recv(net3, s3, 10, 0)
	check("请求 10 只给 4", len(b3) == 4 && string(b3) == "abcd", fmt.Sprintf("%q", b3))
	_, err3 := Recv(net3, s3, 10, 0)
	check("读空后再读 → EAGAIN", errors.As(err3, &en) && int(en) == EAGAIN, "")

	fmt.Println("\n== MSG_DONTWAIT 按调用覆盖 ==")
	net4 := &Net{}
	s4 := &Sock{} // 阻塞 fd
	_, err4 := Recv(net4, s4, 16, MSG_DONTWAIT)
	check("阻塞 fd + MSG_DONTWAIT → EAGAIN", errors.As(err4, &en) && int(en) == EAGAIN, "")
	check("MSG_DONTWAIT 不挂起", net4.Waits == 0, "")
	net4.Arrive(s4, []byte("x"))
	b4, err4b := Recv(net4, s4, 16, MSG_DONTWAIT)
	check("有数据时 MSG_DONTWAIT 正常返回", err4b == nil && string(b4) == "x", "")

	fmt.Println("\n== 写：部分写与 EAGAIN ==")
	net5 := &Net{}
	s5 := &Sock{Fl: O_NONBLOCK, SndRoom: 100}
	k5, _ := Send(net5, s5, make([]byte, 300), 0)
	check("只剩 100 时部分写返回 100", k5 == 100, fmt.Sprintf("k=%d", k5))
	check("部分写后剩余空间归零", s5.SndRoom == 0, "")
	_, err5 := Send(net5, s5, []byte("z"), 0)
	check("缓冲满 + 非阻塞 → EAGAIN", errors.As(err5, &en) && int(en) == EAGAIN, "")

	fmt.Println("\n== accept 空队列 ==")
	net6 := &Net{}
	l6 := &Sock{Fl: O_NONBLOCK, State: "LISTEN"}
	_, err6 := Accept(net6, l6)
	check("非阻塞 accept 空队列 → EAGAIN", errors.As(err6, &en) && int(en) == EAGAIN, "")
	l6.Backlog = append(l6.Backlog, "conn-1")
	c6, err6b := Accept(net6, l6)
	check("队列有连接 → 立即返回", err6b == nil && c6 == "conn-1", c6)

	fmt.Println("\n== 非阻塞 connect 三态 ==")
	net7 := &Net{}
	c7 := &Sock{Fl: O_NONBLOCK}
	check("首次 connect → EINPROGRESS", errors.As(Connect(net7, c7), &en) && int(en) == EINPROGRESS, "")
	check("返回 EINPROGRESS 时处于 SYN_SENT", c7.State == "SYN_SENT", c7.State)
	check("未完成再 connect → EALREADY", errors.As(Connect(net7, c7), &en) && int(en) == EALREADY, "")
	net7.block(c7)
	check("挂起后进入 ESTABLISHED", c7.State == "ESTABLISHED", c7.State)
	check("已建立再 connect → EISCONN", errors.As(Connect(net7, c7), &en) && int(en) == EISCONN, "")
	c7b := &Sock{Fl: O_NONBLOCK, SoError: ECONNRESET}
	check("失败只能从 SO_ERROR 取", SoError(net7, c7b) == ECONNRESET, fmt.Sprintf("%d", SoError(net7, c7b)))

	fmt.Printf("\n---- %d 项断言，失败 %d 项 ----\n", nAssert, nFail)
	if nFail > 0 {
		panic("有断言失败")
	}
	fmt.Println("ALL PASS")
}

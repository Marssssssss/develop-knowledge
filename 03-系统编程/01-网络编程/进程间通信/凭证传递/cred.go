// Unix domain socket 凭证传递 —— Go 版模型（模型层）
//
// 与 cred_model.py 同题。Go 侧重点：凭证是不可变的 struct，
// 天然体现 unix(7) 里「connect 时刻快照」的语义 —— 只要一次取值、
// 之后不去重读，就不会被对端的 setuid 影响。
package main

import "errors"

// socket 层与选项名
const (
	SolSocket      = 1
	SoPasscred     = 16
	SoPeercred     = 17
	SoRcvbuf       = 8
	SoSndbuf       = 7
	ScmCredentials = 2
	ScmRights      = 1
	MsgOob         = 1
	MsgMore        = 0x8000
)

// AutobindLimit 5 个 hex 字符 → 16^5 = 2^20
const AutobindLimit = 1 << 20

const autobindChars = "0123456789abcdef"

// Ucred 对应 struct ucred：pid / uid / gid
type Ucred struct {
	Pid int
	UID int
	GID int
}

// Proc 进程凭证；区分 real 与 effective
type Proc struct {
	Pid  int
	UID  int
	GID  int
	EUID int
	EGID int
}

// DefaultCred unix(7)：默认凭证取 **real** user ID / **real** group ID
func (p *Proc) DefaultCred() Ucred { return Ucred{p.Pid, p.UID, p.GID} }

// Sock AF_UNIX socket
type Sock struct {
	Kind     string // "stream" | "dgram"
	Proc     *Proc
	Passcred bool
	Addr     string
	Abstract bool
	Peer     *Sock
	PeerCred *Ucred // connect / socketpair 时刻的快照
	Inbox    []Msg
	SndBuf   int
	RcvBuf   int
}

// Msg 一条消息：数据 + ancillary（cmsg 列表）
type Msg struct {
	Data  []byte
	Cmsgs []Cmsg
}

// Cmsg 一项控制消息
type Cmsg struct {
	Level int
	Type  int
	Cred  *Ucred
	Fds   []int
}

// SetSockopt 对应 setsockopt(2)
func (s *Sock) SetSockopt(level, optname, value int) (bool, error) {
	if level != SolSocket {
		return false, errors.New("level 必须是 SOL_SOCKET")
	}
	switch optname {
	case SoPasscred:
		s.Passcred = value != 0
		if s.Passcred && s.Addr == "" && s.Peer == nil {
			s.autobind(0)
		}
		return true, nil
	case SoSndbuf:
		s.SndBuf = value
		return true, nil
	case SoRcvbuf:
		return false, nil // unix(7)：对 UNIX socket 无效
	}
	return false, errors.New("未知选项")
}

// GetSockopt 对应 getsockopt(2)；SO_PEERCRED 只读
func (s *Sock) GetSockopt(level, optname int) (*Ucred, error) {
	if level != SolSocket {
		return nil, errors.New("level 必须是 SOL_SOCKET")
	}
	if optname == SoPeercred {
		if s.PeerCred == nil {
			return nil, errors.New(
				"SO_PEERCRED 只适用于已连接的 stream socket 与 socketpair")
		}
		return s.PeerCred, nil
	}
	return nil, errors.New("未知选项")
}

func (s *Sock) autobind(seq int) string {
	b := make([]byte, 0, 6)
	b = append(b, 0)
	for i := 0; i < 5; i++ {
		b = append(b, autobindChars[(seq>>(4*i))&0xF])
	}
	s.Addr = string(b)
	s.Abstract = true
	return s.Addr
}

// Bind 显式绑定；addrlen == sizeof(sa_family_t) 时触发 autobind
func (s *Sock) Bind(path string, addrlenIsFamilyOnly bool) string {
	if path == "" && addrlenIsFamilyOnly {
		return s.autobind(0)
	}
	s.Addr = path
	s.Abstract = false
	return s.Addr
}

// Connect unix(7)：SO_PEERCRED 取 connect 时刻的凭证，之后不再更新
func (s *Sock) Connect(other *Sock) {
	s.Peer = other
	other.Peer = s
	if other.Proc != nil {
		c := other.Proc.DefaultCred()
		s.PeerCred = &c
	}
	if s.Proc != nil {
		c := s.Proc.DefaultCred()
		other.PeerCred = &c
	}
}

// SocketPair 两端在创建时就互相信任
func SocketPair(kind string, a, b *Proc) (*Sock, *Sock) {
	x := &Sock{Kind: kind, Proc: a, SndBuf: 2048, RcvBuf: 2048}
	y := &Sock{Kind: kind, Proc: b, SndBuf: 2048, RcvBuf: 2048}
	x.Peer = y
	y.Peer = x
	if b != nil {
		c := b.DefaultCred()
		x.PeerCred = &c
	}
	if a != nil {
		c := a.DefaultCred()
		y.PeerCred = &c
	}
	return x, y
}

// SendMsg 对应 sendmsg(2)；AF_UNIX 不支持 MSG_OOB / MSG_MORE
func SendMsg(s *Sock, data []byte, cmsgs []Cmsg, flags int) (int, error) {
	if flags&MsgOob != 0 {
		return 0, errors.New("AF_UNIX 不支持带外数据（MSG_OOB）")
	}
	if flags&MsgMore != 0 {
		return 0, errors.New("AF_UNIX 不支持 MSG_MORE")
	}
	if s.Peer == nil {
		return 0, errors.New("未连接")
	}
	hasCred := false
	for _, c := range cmsgs {
		if c.Type == ScmCredentials {
			hasCred = true
		}
	}
	if !hasCred && s.Proc != nil {
		// unix(7)：未指定时填「发送方 PID、real UID、real GID」
		cred := s.Proc.DefaultCred()
		cmsgs = append(cmsgs, Cmsg{Level: SolSocket,
			Type: ScmCredentials, Cred: &cred})
	}
	s.Peer.Inbox = append(s.Peer.Inbox, Msg{Data: data, Cmsgs: cmsgs})
	return len(data), nil
}

// RecvMsg 对应 recvmsg(2)；SO_PASSCRED 决定是否看到 SCM_CREDENTIALS
func RecvMsg(s *Sock, flags int) (Msg, error) {
	if flags&MsgOob != 0 {
		return Msg{}, errors.New("AF_UNIX 不支持带外数据（MSG_OOB）")
	}
	if len(s.Inbox) == 0 {
		return Msg{}, errors.New("无数据")
	}
	m := s.Inbox[0]
	s.Inbox = s.Inbox[1:]
	if !s.Passcred {
		kept := []Cmsg{}
		for _, c := range m.Cmsgs {
			if c.Type != ScmCredentials {
				kept = append(kept, c)
			}
		}
		m.Cmsgs = kept
	}
	return m, nil
}

// PeerCred getsockopt(SO_PEERCRED) 的便捷入口
func PeerCred(s *Sock) (*Ucred, error) {
	return s.GetSockopt(SolSocket, SoPeercred)
}

// 跨服 RPC(Go 版):req_id 关联/超时/错误传播/单向推送。
// 帧:[u32 BE 帧长][u8 类型][u32 LE req_id][u8 路径长][路径][载荷];
// 类型 1=REQ 2=RESP(载荷前 1 字节状态码) 3=PUSH(req_id 恒 0)。
package main

import (
	"encoding/binary"
	"fmt"
	"os"
	"sort"
)

const (
	msgReq  = 1
	msgResp = 2
	msgPush = 3

	statusOK          = 0
	statusTimeout     = 1
	statusUnknownPath = 2
)

type frame struct {
	msgType uint8
	reqID   uint32
	path    string
	payload []byte
	status  uint8
}

func encodeFrame(f frame) []byte {
	p := []byte(f.path)
	n := 1 + 4 + 1 + len(p) + len(f.payload)
	if f.msgType == msgResp {
		n++
	}
	out := make([]byte, 4, 4+n)
	binary.BigEndian.PutUint32(out[0:], uint32(n)) // 大端长度前缀,同 gRPC
	out = append(out, f.msgType, byte(f.reqID), byte(f.reqID>>8),
		byte(f.reqID>>16), byte(f.reqID>>24), uint8(len(p)))
	out = append(out, p...)
	if f.msgType == msgResp {
		out = append(out, f.status)
	}
	return append(out, f.payload...)
}

func decodeFrame(data []byte) (frame, error) {
	if len(data) < 4 {
		return frame{}, fmt.Errorf("缺长度前缀")
	}
	n := int(binary.BigEndian.Uint32(data[0:4]))
	if len(data)-4 != n {
		return frame{}, fmt.Errorf("帧长 %d != 实际 %d", n, len(data)-4)
	}
	pos := 4
	msgType := data[pos]
	reqID := binary.LittleEndian.Uint32(data[pos+1:])
	plen := int(data[pos+5])
	pos += 6
	if msgType != msgReq && msgType != msgResp && msgType != msgPush {
		return frame{}, fmt.Errorf("未知消息类型 %d", msgType)
	}
	if pos+plen > len(data) {
		return frame{}, fmt.Errorf("路径截断")
	}
	f := frame{msgType: msgType, reqID: reqID, path: string(data[pos : pos+plen])}
	pos += plen
	if msgType == msgResp {
		f.status = data[pos]
		pos++
	}
	f.payload = data[pos:]
	return f, nil
}

// ---------------- 客户端:req_id 关联 + 超时 + 晚到丢弃 ----------------

type pending struct {
	path     string
	deadline int
	done     bool
	result   []byte
	errCode  uint8
}

type rpcClient struct {
	out     chan []byte
	pending map[uint32]*pending
	nextID  uint32
	pushes  []frame
}

func newClient(out chan []byte) *rpcClient {
	return &rpcClient{out: out, pending: map[uint32]*pending{}}
}

func (c *rpcClient) call(path string, timeoutMs int) uint32 {
	c.nextID++
	c.pending[c.nextID] = &pending{path: path, deadline: timeoutMs}
	c.out <- encodeFrame(frame{msgType: msgReq, reqID: c.nextID, path: path})
	return c.nextID
}

func (c *rpcClient) onFrame(raw []byte) string {
	f, err := decodeFrame(raw)
	if err != nil {
		panic(err)
	}
	if f.msgType == msgPush {
		c.pushes = append(c.pushes, f)
		return "push"
	}
	e, ok := c.pending[f.reqID]
	if !ok || e.done { // 超时后晚到的响应:静默丢弃
		return "dropped"
	}
	e.done = true
	if f.status == statusOK {
		e.result = f.payload
	} else {
		e.errCode = f.status
	}
	return "resolved"
}

func (c *rpcClient) expire(now int) {
	for _, e := range c.pending {
		if !e.done && now >= e.deadline {
			e.done, e.errCode = true, statusTimeout
		}
	}
}

func (c *rpcClient) drain(in chan []byte) {
	for {
		select {
		case raw := <-in:
			c.onFrame(raw)
			continue
		default:
		}
		return
	}
}

// ---------------- 服务端:handler 表 + 模拟延迟 ----------------

type handler struct {
	fn      func([]byte) ([]byte, uint8)
	delayMs int
}

func okFn(fn func([]byte) []byte) func([]byte) ([]byte, uint8) {
	return func(p []byte) ([]byte, uint8) { return fn(p), statusOK }
}

type scheduled struct {
	due     int
	reqID   uint32
	status  uint8
	payload []byte
}

type rpcServer struct {
	out      chan []byte
	handlers map[string]handler
	sched    []scheduled
}

func newServer(out chan []byte) *rpcServer {
	return &rpcServer{out: out, handlers: map[string]handler{}}
}

func (s *rpcServer) register(path string, fn func([]byte) ([]byte, uint8), delayMs int) {
	s.handlers[path] = handler{fn: fn, delayMs: delayMs}
}

func (s *rpcServer) pump(in chan []byte, now int) {
	for {
		select {
		case raw := <-in:
			f, err := decodeFrame(raw)
			if err != nil || f.msgType != msgReq {
				panic("服务端只处理 REQ")
			}
			h, known := s.handlers[f.path]
			if !known {
				s.sched = append(s.sched, scheduled{now, f.reqID, statusUnknownPath, nil})
				continue
			}
			payload, code := h.fn(f.payload)
			s.sched = append(s.sched, scheduled{now + h.delayMs, f.reqID, code, payload})
		default:
		}
		sort.SliceStable(s.sched, func(i, j int) bool { return s.sched[i].due < s.sched[j].due })
		s.emit(now)
		return
	}
}

func (s *rpcServer) emit(now int) {
	for len(s.sched) > 0 && s.sched[0].due <= now {
		sc := s.sched[0]
		s.sched = s.sched[1:]
		s.out <- encodeFrame(frame{msgType: msgResp, reqID: sc.reqID,
			payload: sc.payload, status: sc.status})
	}
}

func (s *rpcServer) push(path string, payload []byte) {
	s.out <- encodeFrame(frame{msgType: msgPush, path: path, payload: payload})
}

var failures int

func check(label string, cond bool) {
	if !cond {
		failures++
		fmt.Printf("[FAIL] %s\n", label)
		return
	}
	fmt.Printf("[ok] %s\n", label)
}

func takeOne(in chan []byte) []byte {
	select {
	case raw := <-in:
		return raw
	default:
	}
	return nil
}

func main() {
	// ---- 1. 帧编解码 ----
	raw := encodeFrame(frame{msgType: msgResp, reqID: 42,
		path: "/ZoneService/KickPlayer", payload: []byte("bye")})
	d, err := decodeFrame(raw)
	check("帧往返:类型/req_id/路径/载荷",
		err == nil && d.msgType == msgResp && d.reqID == 42
			&& d.path == "/ZoneService/KickPlayer" && string(d.payload) == "bye")
	check("长度前缀为 4 字节大端",
		binary.BigEndian.Uint32(raw[:4]) == uint32(len(raw)-4))
	if _, err = decodeFrame(raw[:len(raw)-1]); err == nil {
		check("帧长不符应报错", false)
	} else {
		check("帧长不符报错", true)
	}
	// ---- 2. 乱序响应按 req_id 正确关联 ----
	zoneToGlobal := make(chan []byte, 16) // zone -> global(请求方向)
	globalToZone := make(chan []byte, 16) // global -> zone(响应/推送方向)
	cli, srv := newClient(zoneToGlobal), newServer(globalToZone)
	srv.register("/Global/QueryRank", okFn(func([]byte) []byte { return []byte("rank-1") }), 30)
	srv.register("/Global/QueryMail", okFn(func([]byte) []byte { return []byte("mail-7") }), 5)
	srv.register("/Global/Ping", okFn(func([]byte) []byte { return []byte("pong") }), 0)

	a := cli.call("/Global/QueryRank", 200) // 慢
	b := cli.call("/Global/QueryMail", 200)
	c := cli.call("/Global/Ping", 200)
	srv.pump(zoneToGlobal, 0)      // 入队三响应,emit 只放行 due=0 的 Ping
	cli.onFrame(takeOne(globalToZone))
	check("乱序到达仍正确关联(Ping 最先回)",
		string(cli.pending[c].result) == "pong" && cli.pending[a].result == nil)
	srv.emit(30) // 30ms 时 mail(5) 与 rank(30) 都到期
	cli.drain(globalToZone)
	check("三个并发调用全部配对成功",
		string(cli.pending[a].result) == "rank-1"
			&& string(cli.pending[b].result) == "mail-7"
			&& string(cli.pending[c].result) == "pong")
	// ---- 3. 超时 + 晚到响应丢弃 ----
	srv.register("/Global/SlowEcho", okFn(func(p []byte) []byte { return p }), 200)
	d2 := cli.call("/Global/SlowEcho", 100)
	srv.pump(zoneToGlobal, 0)
	cli.expire(110)
	check("110ms 判 TIMEOUT", cli.pending[d2].errCode == statusTimeout)
	srv.emit(200) // 200ms 响应才到
	late := cli.onFrame(takeOne(globalToZone))
	check("晚到响应被静默丢弃", late == "dropped")
	// ---- 4. 错误传播(状态码)与未知路径 ----
	srv.register("/Global/Boom", func([]byte) ([]byte, uint8) { return nil, 7 }, 0)
	e := cli.call("/Global/Boom", 100)
	u := cli.call("/Global/NoSuch", 100)
	srv.pump(zoneToGlobal, 0)
	srv.emit(0)
	cli.drain(globalToZone)
	check("业务错误按状态码 7 传播", cli.pending[e].errCode == 7)
	check("未知路径返回状态码 2", cli.pending[u].errCode == statusUnknownPath)
	// ---- 5. 单向推送 ----
	srv.push("/ZoneService/KickPlayer", []byte("uid=1001"))
	cli.onFrame(takeOne(globalToZone))
	check("PUSH 无配对直达推送队列",
		len(cli.pushes) == 1 && cli.pushes[0].path == "/ZoneService/KickPlayer"
			&& string(cli.pushes[0].payload) == "uid=1001")
	if failures > 0 {
		fmt.Printf("\n%d 项断言失败\n", failures)
		os.Exit(1)
	}
	fmt.Println("\n全部断言通过")
}

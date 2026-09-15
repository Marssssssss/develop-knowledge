// backlog_model.go — 常量、自检脚手架与监听套接字状态机（从 main.go 拆出）。
//
// 与 main.go 同属 package main，同包多文件共享命名空间，搬运零语义变化。
// 只用到 fmt；os/exec/runtime 那些是 main.go 里活体检查用的，不要抄过来
// —— Go 的未使用 import 是**编译错误**。
package main

import "fmt"

const (
	bytesPerSynRecv = 304  // 内核文档：单个 SYN_RECV 约 304 字节
	synBacklogFloor = 128  // 低内存机器上 tcp_max_syn_backlog 最小值
	somaxconnModern = 4096 // Linux 5.4 起 somaxconn 默认值（更早 128）
	synackRetries   = 5    // tcp_synack_retries 默认 5
)

var failed, checks int

func check(cond bool, msg string) {
	checks++
	if !cond {
		fmt.Printf("  [FAIL] %s\n", msg)
		failed++
	}
}

// action 是内核在连接建立过程中的动作
type action int

const (
	actSynackSent action = iota // SYN 进半连接队列，回 SYN+ACK
	actSyncookieSent            // 半连接队列满 + syncookies 开启
	actSynDropped               // 半连接队列满 + syncookies 关闭
	actMovedToAccept            // 最终 ACK 到达且全连接队列有位置
	actRST                      // 全连接队列满 + abort_on_overflow=1
	actAckIgnored               // 全连接队列满 + 默认策略：静默忽略
)

type stats struct {
	synReceived, synDropped, syncookieIssued, syncookieCompleted int
	established, overflow, rstSent, accepted, recovered, gaveUp  int
	maxSynQ, maxAcceptQ                                          int
}

type listener struct {
	backlog, somaxconn, tcpMaxSynBacklog int
	syncookies, abortOnOverflow          bool
	synQ, acceptQ                        int
	ignored                              []bool
	st                                   stats
}

func newListener(nClients, backlog, somaxconn, synBacklog int,
	syncookies, abortOnOverflow bool) *listener {
	return &listener{
		backlog: backlog, somaxconn: somaxconn, tcpMaxSynBacklog: synBacklog,
		syncookies: syncookies, abortOnOverflow: abortOnOverflow,
		ignored: make([]bool, nClients),
	}
}

// acceptLimit —— 内核实际生效的全连接队列上限：backlog 与 somaxconn 取小
func (l *listener) acceptLimit() int {
	if l.backlog < l.somaxconn {
		return l.backlog
	}
	return l.somaxconn
}

func (l *listener) onSyn(client int) action {
	l.st.synReceived++
	if l.synQ < l.tcpMaxSynBacklog {
		l.synQ++
		if l.synQ > l.st.maxSynQ {
			l.st.maxSynQ = l.synQ
		}
		return actSynackSent
	}
	if l.syncookies {
		l.st.syncookieIssued++
		return actSyncookieSent
	}
	l.st.synDropped++
	return actSynDropped
}

func (l *listener) onFinalAck(client int) action {
	if l.synQ > 0 {
		l.synQ--
	}
	if l.acceptQ < l.acceptLimit() {
		l.acceptQ++
		l.st.established++
		if l.acceptQ > l.st.maxAcceptQ {
			l.st.maxAcceptQ = l.acceptQ
		}
		if l.st.syncookieIssued > 0 {
			l.st.syncookieCompleted++
		}
		if client < len(l.ignored) && l.ignored[client] {
			l.ignored[client] = false
			l.st.recovered++
		}
		return actMovedToAccept
	}
	l.st.overflow++
	if l.abortOnOverflow {
		l.st.rstSent++
		return actRST
	}
	if client < len(l.ignored) {
		l.ignored[client] = true
	}
	return actAckIgnored
}

func (l *listener) accept() {
	if l.acceptQ > 0 {
		l.acceptQ--
		l.st.accepted++
	}
}

type event struct {
	tick, client, attempt int
	isSyn                 bool
}

// retryDelay —— 真实 TCP 的重传是指数退避的（约 1s、2s、4s、8s、16s、32s）。
// 固定间隔重试是常见建模错误：它会让"自愈"几乎不可能发生。
func retryDelay(base, attempt int) int {
	d := base
	for i := 0; i < attempt; i++ {
		d *= 2
	}
	return d
}

// simulate 跑一轮模拟。clientsPerTick > 1 即模拟洪泛/突发。
func simulate(nClients, acceptEvery, ticks, synRetx, rttTicks, clientsPerTick int,
	backlog, somaxconn, synBacklog int, syncookies, abortOnOverflow bool) *listener {
	l := newListener(nClients, backlog, somaxconn, synBacklog, syncookies, abortOnOverflow)
	events := make([]event, 0, nClients*8)
	for i := 0; i < nClients; i++ {
		events = append(events, event{tick: i / clientsPerTick, client: i, isSyn: true})
	}
	nextAccept := acceptEvery

	for t := 0; t < ticks; t++ {
		// 处理本 tick 的所有事件；swap-remove 保证 O(1) 删除
		for i := 0; i < len(events); i++ {
			if events[i].tick != t {
				continue
			}
			ev := events[i]
			events[i] = events[len(events)-1]
			events = events[:len(events)-1]
			i--
			if ev.isSyn {
				switch l.onSyn(ev.client) {
				case actSynDropped:
					if ev.attempt >= synackRetries {
						l.st.gaveUp++
					} else {
						events = append(events, event{
							tick: t + retryDelay(synRetx, ev.attempt),
							client: ev.client, attempt: ev.attempt + 1, isSyn: true})
					}
				default:
					events = append(events, event{tick: t + rttTicks, client: ev.client})
				}
			} else {
				if l.onFinalAck(ev.client) == actAckIgnored {
					// 服务端重传 SYN+ACK，客户端重新 ACK
					if ev.attempt >= synackRetries {
						l.st.gaveUp++
					} else {
						events = append(events, event{
							tick: t + retryDelay(synRetx, ev.attempt),
							client: ev.client, attempt: ev.attempt + 1})
					}
				}
			}
		}
		if t >= nextAccept {
			l.accept()
			nextAccept = t + acceptEvery
		}
		if len(events) == 0 && l.acceptQ == 0 {
			break // 排空才收工
		}
	}
	return l
}

// ---------------------------------------------------------------- 自检

// MySQL 主从复制与 binlog 最小模拟:复制格式 / GTID / 半同步。
// 依据 dev.mysql.com/doc/refman/8.0 归纳:SBR 记语句、RBR 记行事件(默认);
// GTID 形如 source_id:transaction_id,同一 GTID 只应用一次;半同步在
// AFTER_SYNC 点等待 replica 把事件落盘 relay log 后才返回,超时降级异步。
package main

import (
	"fmt"
	"strings"
)

// Event 是一条 binlog 事件。
type Event struct {
	GTID    string
	Kind    string // Query | WriteRows | UpdateRows | DeleteRows
	Payload string
}

// Source 模拟主库。
type Source struct {
	format      string // STATEMENT | ROW
	gtidSeq     int
	binlog      []Event
	relayAcked  int
	degraded    bool // 半同步超时后降级异步
}

func (s *Source) emit(kind, payload string) Event {
	s.gtidSeq++
	ev := Event{
		GTID:    fmt.Sprintf("3E11FA47-71CA-11E1-9E33-C80AA9429562:%d", s.gtidSeq),
		Kind:    kind,
		Payload: payload,
	}
	s.binlog = append(s.binlog, ev)
	return ev
}

// Execute 模拟语句落 binlog。ROW 格式记行变更事件,STATEMENT 记 SQL 文本。
func (s *Source) Execute(sql string, rowChanges []string) []Event {
	if s.format == "ROW" && len(rowChanges) > 0 {
		evs := make([]Event, 0, len(rowChanges))
		for _, c := range rowChanges {
			kind := "WriteRows"
			if strings.HasPrefix(c, "U:") {
				kind = "UpdateRows"
			} else if strings.HasPrefix(c, "D:") {
				kind = "DeleteRows"
			}
			evs = append(evs, s.emit(kind, c))
		}
		return evs
	}
	return []Event{s.emit("Query", sql)}
}

// WaitAck 模拟半同步等待:返回是否在 ACK 到达前完成(此处为纯状态机推演)。
func (s *Source) WaitAck(replicaCaught int) bool {
	if replicaCaught >= len(s.binlog) {
		return true // ACK 已到,commit 返回
	}
	s.degraded = true // 超时 → 降级异步
	return false
}

// Replica 模拟从库:IO 线程拉 relay log,SQL 线程按 GTID 幂等重放。
type Replica struct {
	relayLog     []Event
	executedGTID map[string]bool
}

func NewReplica() *Replica {
	return &Replica{executedGTID: make(map[string]bool)}
}

func (r *Replica) Pull(src *Source) {
	r.relayLog = append(r.relayLog, src.binlog[len(r.relayLog):]...)
	src.relayAcked = len(r.relayLog)
}

func (r *Replica) Replay() int {
	applied := 0
	for _, ev := range r.relayLog {
		if r.executedGTID[ev.GTID] {
			continue // GTID 幂等:已应用的事务忽略
		}
		r.executedGTID[ev.GTID] = true
		applied++
	}
	return applied
}

func main() {
	// 1. 复制格式
	src := &Source{format: "ROW"}
	src.Execute("INSERT INTO t VALUES (1)", []string{"I:1"})
	src.Execute("UPDATE t SET v=v+1", []string{"U:1"})
	kinds := []string{}
	for _, e := range src.binlog {
		kinds = append(kinds, e.Kind)
	}
	fmt.Println("row-binlog kinds:", kinds)

	// 2. GTID 幂等重放
	rep := NewReplica()
	rep.Pull(src)
	fmt.Println("first replay applied:", rep.Replay())
	fmt.Println("second replay applied:", rep.Replay(), "(GTID dedup)")

	// 3. 半同步:ACK 到齐 → 不降级;ACK 缺失 → 降级
	fmt.Println("wait-ack ok:", src.WaitAck(len(src.binlog)))
	fmt.Println("wait-ack degraded:", src.WaitAck(0))

	// 4. 静态自检(人工审查替代编译运行,见 README)
	assert := func(b bool, msg string) {
		if !b {
			panic("assert failed: " + msg)
		}
	}
	assert(kinds[0] == "WriteRows" && kinds[1] == "UpdateRows", "row kinds")
	assert(rep.Replay() == 0, "gtid idempotent")
	fmt.Println("go static checks passed")
}

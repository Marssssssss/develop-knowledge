// PostgreSQL 逻辑复制(logical decoding)与 CDC 最小模拟。
// 依据 postgresql.org/docs/current/logicaldecoding.html 及 Debezium 文档归纳:
// WAL → 逻辑解码 → replication slot 持留 WAL → 事件流(begin/insert/update/
// delete/commit);old 元组依赖 REPLICA IDENTITY;offset 断点续传。
package main

import "fmt"

// WALRecord 是物理 WAL 记录(简化)。
type WALRecord struct {
	LSN     int
	Kind    string // begin | insert | update | delete | commit
	Table   string
	After   map[string]int
	Old     map[string]int // 可能为 nil(replica identity = nothing)
	XID     int
}

// LogicalSlot 复制槽:confirmed_flush_lsn 之前的 WAL 才能回收。
type LogicalSlot struct {
	name          string
	confirmedLSN  int
	streamedLSN   int
}

// Decode 解码 confirmed 之后的 WAL,输出逻辑事件。
func (s *LogicalSlot) Decode(wal []WALRecord) []string {
	events := []string{}
	for _, rec := range wal {
		if rec.LSN <= s.streamedLSN {
			continue
		}
		s.streamedLSN = rec.LSN
		switch rec.Kind {
		case "insert":
			events = append(events, fmt.Sprintf("insert %s after=%v", rec.Table, rec.After))
		case "update":
			events = append(events, fmt.Sprintf("update %s after=%v old=%v", rec.Table, rec.After, rec.Old))
		case "delete":
			events = append(events, fmt.Sprintf("delete %s old=%v", rec.Table, rec.Old))
		default:
			events = append(events, rec.Kind)
		}
	}
	return events
}

// Ack 消费者确认:confirmed_flush_lsn 前移,旧 WAL 可回收。
func (s *LogicalSlot) Ack(lsn int) { s.confirmedLSN = lsn }

// RetentionLSN 槽持留的 WAL 下界。
func (s *LogicalSlot) RetentionLSN() int { return s.confirmedLSN }

func main() {
	wal := []WALRecord{
		{LSN: 1, Kind: "begin", XID: 101},
		{LSN: 2, Kind: "insert", Table: "orders", After: map[string]int{"amount": 100}},
		{LSN: 3, Kind: "commit", XID: 101},
		{LSN: 4, Kind: "update", Table: "orders", After: map[string]int{"amount": 150},
			Old: nil}, // replica identity nothing → old 不可用
	}
	slot := &LogicalSlot{name: "dbz_slot"}

	evs := slot.Decode(wal)
	fmt.Println("decoded:", evs)

	// 未 ACK 前 WAL 持留;ACK 后前移
	fmt.Println("retained before ack:", slot.RetentionLSN(), "< streamed:", slot.streamedLSN)
	slot.Ack(slot.streamedLSN)
	fmt.Println("retained after ack:", slot.RetentionLSN())

	// 静态自检(人工审查替代编译,见 README)
	assert := func(b bool, msg string) {
		if !b {
			panic("assert failed: " + msg)
		}
	}
	assert(len(evs) == 4, "4 events decoded")
	assert(evs[1][:6] == "insert", "insert first")
	assert(slot.RetentionLSN() == slot.streamedLSN, "ack advanced lsn")
	fmt.Println("go static checks passed")
}

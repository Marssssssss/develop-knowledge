// mailqueue.go — 与 python/mailqueue.py 同语义的 Go 复刻(静态审查用)。
package main

import "fmt"

type Entry struct {
	ID      string
	Payload string
}

type Stream struct {
	entries []Entry
	seq     int
}

// XAdd:传 * 时自动生成 时间戳-序号 ID。
func (s *Stream) XAdd(payload string) string {
	s.seq++
	id := fmt.Sprintf("1690000000000-%04d", s.seq)
	s.entries = append(s.entries, Entry{id, payload})
	return id
}

type Group struct {
	stream        *Stream
	lastDelivered int
	pending       map[string][]Entry // consumer -> 未确认条目
}

func NewGroup(s *Stream) *Group { return &Group{stream: s, pending: map[string][]Entry{}} }

// XReadGroup ">":新条目只给本消费者,组内不重复投递。
func (g *Group) XReadGroup(consumer string, count int) []Entry {
	var out []Entry
	for i := 0; i < count && g.lastDelivered < len(g.stream.entries); i++ {
		e := g.stream.entries[g.lastDelivered]
		g.lastDelivered++
		g.pending[consumer] = append(g.pending[consumer], e)
		out = append(out, e)
	}
	return out
}

// XAck:离开 pending(至少一次交付的收口)。
func (g *Group) XAck(consumer, id string) bool {
	list := g.pending[consumer]
	for i, e := range list {
		if e.ID == id {
			g.pending[consumer] = append(list[:i:i], list[i+1:]...)
			return true
		}
	}
	return false
}

func main() {
	s := &Stream{}
	s.XAdd("mail0")
	s.XAdd("mail1")
	g := NewGroup(s)
	a := g.XReadGroup("worker-a", 2)
	fmt.Println("a got:", a)
	fmt.Println("ack:", g.XAck("worker-a", a[0].ID))
	fmt.Println("pending:", g.pending)
}

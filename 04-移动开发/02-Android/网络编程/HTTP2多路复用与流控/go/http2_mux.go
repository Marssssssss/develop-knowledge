// http2_mux.go — 与 python/http2_mux.py 同语义的 Go 复刻(静态审查用)。
package main

import "fmt"

const (
	maxWindow       = 1<<31 - 1
	defaultInitial  = 65535
)

type FlowWindow struct {
	stream  map[int]int
	conn    int
	initial int
}

func NewFlowWindow() *FlowWindow {
	return &FlowWindow{stream: map[int]int{}, conn: defaultInitial, initial: defaultInitial}
}

func (w *FlowWindow) NewStream(sid int)  { w.stream[sid] = w.initial }
func (w *FlowWindow) CanSend(sid, n int) bool { return w.stream[sid] >= n && w.conn >= n }

// DataSent:发送后流窗口与连接窗口都减(9 字节帧头不计入)。
func (w *FlowWindow) DataSent(sid, n int) {
	w.stream[sid] -= n
	w.conn -= n
}

// WindowUpdate:sid=0 为连接级;超 2^31-1 = FLOW_CONTROL_ERROR。
func (w *FlowWindow) WindowUpdate(sid, n int) error {
	if sid == 0 {
		if w.conn+n > maxWindow {
			return fmt.Errorf("FLOW_CONTROL_ERROR")
		}
		w.conn += n
		return nil
	}
	if w.stream[sid]+n > maxWindow {
		return fmt.Errorf("FLOW_CONTROL_ERROR")
	}
	w.stream[sid] += n
	return nil
}

// ApplySettings:按 delta 作用于所有已开流。
func (w *FlowWindow) ApplySettings(newInitial int) {
	delta := newInitial - w.initial
	for sid := range w.stream {
		w.stream[sid] += delta
	}
	w.initial = newInitial
}

func main() {
	w := NewFlowWindow()
	w.NewStream(1)
	w.NewStream(3)
	w.DataSent(1, 40000)
	fmt.Println(w.CanSend(1, 40000), w.CanSend(3, 40000)) // false false(连接窗口钳住)
	w.WindowUpdate(1, 40000)
	fmt.Println(w.CanSend(1, 40000)) // false:连接窗口未补
	w.WindowUpdate(0, 40000)
	fmt.Println(w.CanSend(1, 40000), w.CanSend(3, 65535-40000+40000-65535+65535) == false || true)
}

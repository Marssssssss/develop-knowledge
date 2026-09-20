// Go 侧对照实现（一）：RFC 9113 的帧头、帧大小约束、SETTINGS 初始值与流控。
//
// 官方口径：
//   §4.1  HTTP Frame { Length(24), Type(8), Flags(8), Reserved(1), Stream Identifier(31) }
//         「The 9 octets of the frame header are not included in this value.」
//   §4.2  MAX_FRAME_SIZE 允许区间 [2^14, 2^24-1]；超限 → FRAME_SIZE_ERROR
//   §6.5.2 HEADER_TABLE_SIZE 4096 / ENABLE_PUSH 1 / MAX_CONCURRENT_STREAMS 无限制 /
//          INITIAL_WINDOW_SIZE 65535 / MAX_FRAME_SIZE 16384 / MAX_HEADER_LIST_SIZE 无限制
//   §6.9.2 「A SETTINGS frame cannot alter the connection flow-control window.」
//          初始窗口变化时按「新旧初始值之差」调整所有已存在的流窗口，可以为负
package main

import (
	"encoding/binary"
	"errors"
	"fmt"
)

// SETTINGS 参数标识（§6.5.2）。
const (
	SettingsHeaderTableSize      uint16 = 0x01
	SettingsEnablePush           uint16 = 0x02
	SettingsMaxConcurrentStreams uint16 = 0x03
	SettingsInitialWindowSize    uint16 = 0x04
	SettingsMaxFrameSize         uint16 = 0x05
	SettingsMaxHeaderListSize    uint16 = 0x06
)

// 初始值与边界。
const (
	InitHeaderTableSize   = 4096
	InitEnablePush        = 1
	InitInitialWindowSize = 65535  // 2^16 - 1
	InitMaxFrameSize      = 16384  // 2^14
	MaxFrameSizeMin       = 1 << 14
	MaxFrameSizeMax       = (1 << 24) - 1
	MaxWindowSize         = (1 << 31) - 1
)

// ErrProtocol / ErrFlowControl / ErrFrameSize 对应 §5.4 的三类错误。
var (
	ErrProtocol   = errors.New("PROTOCOL_ERROR")
	ErrFlowControl = errors.New("FLOW_CONTROL_ERROR")
	ErrFrameSize  = errors.New("FRAME_SIZE_ERROR")
)

// Frame 是一个已解析的帧。
type Frame struct {
	Length   uint32
	Type     uint8
	Flags    uint8
	StreamID uint32
	Payload  []byte
}

// PackFrame 生成 9 字节帧头 + payload；Length 只算 payload。
func PackFrame(frtype, flags uint8, streamID uint32, payload []byte) []byte {
	buf := make([]byte, 0, 9+len(payload))
	buf = append(buf, byte(len(payload)>>16), byte(len(payload)>>8), byte(len(payload)))
	buf = append(buf, frtype, flags)
	// Reserved(1) + Stream Identifier(31)：Reserved 发送时必须为 0
	sid := make([]byte, 4)
	binary.BigEndian.PutUint32(sid, streamID&0x7FFFFFFF)
	buf = append(buf, sid...)
	return append(buf, payload...)
}

// UnpackFrame 解析帧；Reserved 位被掩掉（接收时必须忽略）。
func UnpackFrame(raw []byte) (*Frame, error) {
	if len(raw) < 9 {
		return nil, ErrFrameSize
	}
	length := uint32(raw[0])<<16 | uint32(raw[1])<<8 | uint32(raw[2])
	if len(raw) < 9+int(length) {
		return nil, ErrFrameSize
	}
	streamID := binary.BigEndian.Uint32(raw[5:9]) & 0x7FFFFFFF
	return &Frame{
		Length:   length,
		Type:     raw[3],
		Flags:    raw[4],
		StreamID: streamID,
		Payload:  raw[9 : 9+length],
	}, nil
}

// CheckSettingsMaxFrameSize 校验 §4.2 的合法区间。
func CheckSettingsMaxFrameSize(v uint32) error {
	if v < MaxFrameSizeMin || v > MaxFrameSizeMax {
		return ErrProtocol
	}
	return nil
}

// FlowControl 维护连接窗口与各流窗口。
type FlowControl struct {
	InitialWindow    int64
	ConnectionWindow int64
	StreamWindow     map[uint32]int64
}

// NewFlowControl 建立流控状态。
func NewFlowControl(initialWindow int64) *FlowControl {
	return &FlowControl{
		InitialWindow:    initialWindow,
		ConnectionWindow: initialWindow,
		StreamWindow:     map[uint32]int64{},
	}
}

// OpenStream 以当前初始窗口打开一条流。
func (f *FlowControl) OpenStream(sid uint32) { f.StreamWindow[sid] = f.InitialWindow }

// Send 返回实际可发送量（受连接与流两级窗口共同限制，可为 0）。
func (f *FlowControl) Send(sid uint32, size int64) int64 {
	allowed := f.ConnectionWindow
	if w := f.StreamWindow[sid]; w < allowed {
		allowed = w
	}
	if allowed < 0 {
		allowed = 0
	}
	n := allowed
	if size < n {
		n = size
	}
	f.ConnectionWindow -= n
	f.StreamWindow[sid] -= n
	return n
}

// WindowUpdate 作用于连接（sid == nil）或单条流。
func (f *FlowControl) WindowUpdate(sid *uint32, increment int64) error {
	if sid == nil {
		f.ConnectionWindow += increment
		if f.ConnectionWindow > MaxWindowSize {
			return ErrFlowControl
		}
		return nil
	}
	f.StreamWindow[*sid] += increment
	if f.StreamWindow[*sid] > MaxWindowSize {
		return ErrFlowControl
	}
	return nil
}

// ApplyInitialWindowChange 对应 §6.9.2：按差值调整所有已存在的流窗口，
// **不**触碰连接窗口。
func (f *FlowControl) ApplyInitialWindowChange(newValue int64) error {
	if newValue > MaxWindowSize {
		return ErrFlowControl
	}
	delta := newValue - f.InitialWindow
	f.InitialWindow = newValue
	for sid := range f.StreamWindow {
		f.StreamWindow[sid] += delta
	}
	return nil
}

func main() {
	// 帧头往返
	raw := PackFrame(0x0, 0x01, 1, []byte("hello"))
	fr, err := UnpackFrame(raw)
	if err != nil {
		fmt.Println("unpack err:", err)
		return
	}
	fmt.Printf("frame: len=%d type=%d flags=%d sid=%d payload=%q\n",
		fr.Length, fr.Type, fr.Flags, fr.StreamID, fr.Payload)

	// 流控：官方的 −44 KB 例子
	fc := NewFlowControl(InitInitialWindowSize)
	fc.OpenStream(1)
	total := int64(0)
	for total < 60*1024 {
		n := fc.Send(1, 60*1024-total)
		if n == 0 {
			break
		}
		total += n
	}
	fmt.Printf("sent=%d streamWindow=%d\n", total, fc.StreamWindow[1])
	if err := fc.ApplyInitialWindowChange(16 * 1024); err != nil {
		fmt.Println("err:", err)
	}
	fmt.Printf("after SETTINGS 16KB: streamWindow=%d (=%d KB)\n",
		fc.StreamWindow[1], fc.StreamWindow[1]/1024)

	DemoHpack()
}

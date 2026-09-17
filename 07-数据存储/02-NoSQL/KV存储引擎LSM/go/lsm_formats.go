// lsm_formats.go — LSM 引擎的两个磁盘格式:WAL 日志分块 + SSTable 文件布局(Go 实现)
//
// 权威来源(google/leveldb 官方文档原文):
//   doc/log_format.md
//     * "The log file contents are a sequence of 32KB blocks."
//     * record := checksum:uint32(crc32c of type and data) / length:uint16 / type:uint8 / data
//     * "A record never starts within the last six bytes of a block" → 零填充 trailer
//     * 剩余恰好 7 字节时,写一条**零字节用户数据的 FIRST** 把 trailing 7 字节填满
//     * FULL=1 FIRST=2 MIDDLE=3 LAST=4
//   doc/table_format.md
//     * 布局 [data block*][meta block*][metaindex][index][Footer]
//     * BlockHandle = {offset: varint64, size: varint64}
//     * footer 定长:metaindex / index / 零填充到 40 字节 / magic fixed64
//     * magic == 0xdb4775248b80fb57 (little-endian)
//     * filter 元块按 base=2KB 分区间,块尾是 4 字节偏移数组 + 数组起点 + 1 字节 lg(base)
//
// 运行: go run *.go   (或逐一列出:lsm_formats.go lsm_types.go lsm_engine.go lsm_levels.go lsm_compact.go lsm_check.go)
package main

import (
	"encoding/binary"
	"fmt"
)

const (
	BlockSize             = 32768 // 官方:32KB 块
	RecordHeader          = 7     // uint32 校验和 + uint16 长度 + uint8 类型
	Full                  = 1
	First                 = 2
	Middle                = 3
	Last                  = 4
	FooterMagic           = uint64(0xdb4775248b80fb57)
	BlockHandleMaxEncoded = 20 // 官方 40 == 2*BlockHandle::kMaxEncodedLength
	FooterSize            = 2*BlockHandleMaxEncoded + 8
	FilterBase            = 2048 // 官方:"Currently, base is 2KB."
)

var typeNames = map[int]string{Full: "FULL", First: "FIRST", Middle: "MIDDLE", Last: "LAST"}

// ------------------------------------------------ varint

// EncodeVarint base-128 变长整数:每字节 7 位有效位,最高位标记续接。
func EncodeVarint(v uint64) []byte {
	out := []byte{}
	for {
		b := byte(v & 0x7F)
		v >>= 7
		if v != 0 {
			out = append(out, b|0x80)
			continue
		}
		return append(out, b)
	}
}

// DecodeVarint 返回 (值, 新位置)。超过 10 字节视为损坏(varint64 上限)。
func DecodeVarint(data []byte, pos int) (uint64, int, error) {
	start := pos
	var result uint64
	shift := uint(0)
	for {
		if pos >= len(data) || pos-start >= 10 || shift > 63 {
			return 0, pos, fmt.Errorf("bad varint at %d", start)
		}
		b := data[pos]
		pos++
		result |= uint64(b&0x7F) << shift
		if b&0x80 == 0 {
			return result, pos, nil
		}
		shift += 7
	}
}

// ------------------------------------------------ WAL 分块

// Fragment 一条物理记录。用户记录可能被切成 FIRST/MIDDLE/LAST 多段。
type Fragment struct {
	Block   int
	Offset  int
	RType   int
	DataLen int
	PhysLen int
}

// Label 返回类型名。
func (f Fragment) Label() string { return typeNames[f.RType] }

// LogWriter 严格按官方分块规则写;只维护块号/块内偏移与碎片序列,不落盘。
type LogWriter struct {
	Block        int
	Offset       int
	Fragments    []Fragment
	TrailerBytes int
}

// NewLogWriter 新建写入器。
func NewLogWriter() *LogWriter { return &LogWriter{} }

// LeftInBlock 当前块剩余字节数。
func (w *LogWriter) LeftInBlock() int { return BlockSize - w.Offset }

// BlockCount 已用块数。
func (w *LogWriter) BlockCount() int { return w.Block + 1 }

// Append 追加一条用户记录,返回它的物理碎片列表。
func (w *LogWriter) Append(dataLen int) []Fragment {
	if dataLen <= 0 {
		panic("user record must be non-empty")
	}
	out := []Fragment{}
	begin := true
	remaining := dataLen
	for {
		left := w.LeftInBlock()
		if left < RecordHeader {
			// 官方:记录绝不从块最后 6 字节内开始 → 余下字节零填充为 trailer
			w.TrailerBytes += left
			w.Block++
			w.Offset = 0
			left = BlockSize
		}
		avail := left - RecordHeader
		if remaining <= avail {
			rtype := Last
			if begin {
				rtype = Full
			}
			f := Fragment{w.Block, w.Offset, rtype, remaining, RecordHeader + remaining}
			w.Offset += f.PhysLen
			w.Fragments = append(w.Fragments, f)
			return append(out, f)
		}
		rtype := Middle
		if begin {
			rtype = First
		}
		f := Fragment{w.Block, w.Offset, rtype, avail, RecordHeader + avail}
		w.Offset += f.PhysLen
		w.Fragments = append(w.Fragments, f)
		out = append(out, f)
		remaining -= avail
		begin = false
		w.Block++
		w.Offset = 0
	}
}

// ReadLog 从碎片序列还原用户记录长度;不完整序列被丢弃。
func ReadLog(w *LogWriter) []int {
	out := []int{}
	buf := -1
	for _, f := range w.Fragments {
		switch f.RType {
		case Full:
			out = append(out, f.DataLen)
		case First:
			buf = f.DataLen
		case Middle:
			if buf >= 0 {
				buf += f.DataLen
			}
		case Last:
			if buf >= 0 {
				out = append(out, buf+f.DataLen)
				buf = -1
			}
		}
	}
	return out
}

// ------------------------------------------------ SSTable footer

// BlockHandle 指向文件内某一段的 (offset, size)。
type BlockHandle struct {
	Offset uint64
	Size   uint64
}

// Encode 两个 varint64 拼接。
func (h BlockHandle) Encode() []byte {
	return append(EncodeVarint(h.Offset), EncodeVarint(h.Size)...)
}

// EncodeFooter 生成定长 footer。
func EncodeFooter(meta, idx BlockHandle) ([]byte, error) {
	body := append(meta.Encode(), idx.Encode()...)
	if len(body) > 2*BlockHandleMaxEncoded {
		return nil, fmt.Errorf("footer handles too large: %d", len(body))
	}
	out := make([]byte, 0, FooterSize)
	out = append(out, body...)
	out = append(out, make([]byte, 2*BlockHandleMaxEncoded-len(body))...)
	magic := make([]byte, 8)
	binary.LittleEndian.PutUint64(magic, FooterMagic)
	return append(out, magic...), nil
}

// DecodeFooter 解析并校验 magic。
func DecodeFooter(data []byte) (BlockHandle, BlockHandle, error) {
	if len(data) != FooterSize {
		return BlockHandle{}, BlockHandle{}, fmt.Errorf("footer must be %d bytes, got %d", FooterSize, len(data))
	}
	if binary.LittleEndian.Uint64(data[len(data)-8:]) != FooterMagic {
		return BlockHandle{}, BlockHandle{}, fmt.Errorf("bad magic 0x%x", binary.LittleEndian.Uint64(data[len(data)-8:]))
	}
	off, pos, err := DecodeVarint(data, 0)
	if err != nil {
		return BlockHandle{}, BlockHandle{}, err
	}
	size, pos, err := DecodeVarint(data, pos)
	if err != nil {
		return BlockHandle{}, BlockHandle{}, err
	}
	meta := BlockHandle{off, size}
	off, pos, err = DecodeVarint(data, pos)
	if err != nil {
		return BlockHandle{}, BlockHandle{}, err
	}
	size, _, err = DecodeVarint(data, pos)
	if err != nil {
		return BlockHandle{}, BlockHandle{}, err
	}
	return meta, BlockHandle{off, size}, nil
}

// FooterMagicHex 便于断言里比对。
func FooterMagicHex() string { return fmt.Sprintf("0x%016x", FooterMagic) }

// ------------------------------------------------ filter 元块与文件布局

// FilterIndexForOffset 块起始偏移落在 [i*base, (i+1)*base-1] 的 key 归入第 i 个 filter。
func FilterIndexForOffset(blockOffset, base int) int { return blockOffset / base }

// FilterBlockLayout 返回 filter 块各段长度。
func FilterBlockLayout(n, base int) map[string]int {
	lg := 0
	for (1 << lg) < base {
		lg++
	}
	return map[string]int{
		"filter_bytes":      n, // 每个 filter 至少 1 字节
		"offset_array":      4 * n,
		"array_anchor":      4,
		"lg_base":           1,
		"trailer_positions": 4*n + 5,
		"lg_base_value":     lg,
	}
}

// TableLayout 按官方布局算各段偏移。
func TableLayout(dataBlocks, metaBlocks []int, indexSize int) map[string]int {
	sum := func(xs []int) int {
		t := 0
		for _, v := range xs {
			t += v
		}
		return t
	}
	off := 0
	dataOff := off
	off += sum(dataBlocks)
	metaOff := off
	off += sum(metaBlocks)
	metaindexOff := off
	off += 32 // metaindex 条目:名字 + BlockHandle
	indexOff := off
	off += indexSize
	footerOff := off
	off += FooterSize
	return map[string]int{
		"data_offset":      dataOff,
		"n_data_blocks":    len(dataBlocks),
		"meta_offset":      metaOff,
		"metaindex_offset": metaindexOff,
		"index_offset":     indexOff,
		"footer_offset":    footerOff,
		// 官方:"Footer (fixed size; starts at file_size - sizeof(Footer))"
		"footer_starts_at": off - FooterSize,
		"file_size":        off,
	}
}

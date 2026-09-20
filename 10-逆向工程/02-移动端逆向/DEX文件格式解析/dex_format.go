// dex_format.go — DEX 文件格式的 Go 实现(与 dex_leb128.py / dex_format.py 同构)。
// 依据 AOSP《Dalvik 可执行文件格式》官方章节; 本机无 go 工具链: 人工审查 + 括号配平校验。
package main

import (
	"encoding/binary"
	"fmt"
)

// ---------- 常量(官方「位字段、字符串和常量定义」) ----------

var dexFileMagic = [8]byte{0x64, 0x65, 0x78, 0x0a, 0x30, 0x33, 0x39, 0x00} // "dex\n039\0"

const (
	endianConstant        = 0x12345678
	reverseEndianConstant = 0x78563412
	noIndex               = uint32(0xffffffff)
	headerSizeV40         = 0x70
	headerSizeV41         = 0x78
)

// map_list 类型代码(官方「类型代码」表)
const (
	typeHeaderItem             = 0x0000
	typeStringIDItem           = 0x0001
	typeTypeIDItem             = 0x0002
	typeProtoIDItem            = 0x0003
	typeFieldIDItem            = 0x0004
	typeMethodIDItem           = 0x0005
	typeClassDefItem           = 0x0006
	typeMapList                = 0x1000
	typeTypeList               = 0x1001
	typeClassDataItem          = 0x2000
	typeCodeItem               = 0x2001
	typeStringDataItem         = 0x2002
	typeDebugInfoItem          = 0x2003
	typeHiddenAPIDataItem      = 0xF000
)

// ---------- LEB128 ----------

// uleb128Encode: 除最后一字节外最高位均置 1, 未明确表示的位解译为 0
func uleb128Encode(v uint32) []byte {
	out := []byte{}
	for {
		b := byte(v & 0x7f)
		v >>= 7
		if v != 0 {
			out = append(out, b|0x80)
		} else {
			out = append(out, b)
			return out
		}
	}
}

// uleb128Decode 返回 (值, 消耗字节数)
func uleb128Decode(buf []byte) (uint32, int) {
	var result uint32
	var shift uint
	for i := 0; i < len(buf); i++ {
		b := buf[i]
		result |= uint32(b&0x7f) << shift
		shift += 7
		if b&0x80 == 0 {
			return result, i + 1
		}
	}
	return 0, len(buf)
}

// sleb128Encode: 最后一字节的最高载荷位做符号扩展
func sleb128Encode(v int32) []byte {
	out := []byte{}
	more := true
	for more {
		b := byte(uint32(v) & 0x7f)
		// Go 对 int32 的 >> 是算术右移, 天然符号扩展
		v >>= 7
		if (v == 0 && b&0x40 == 0) || (v == -1 && b&0x40 != 0) {
			more = false
		} else {
			b |= 0x80
		}
		out = append(out, b)
	}
	return out
}

func sleb128Decode(buf []byte) (int32, int) {
	var result uint32
	var shift uint
	var last byte
	n := 0
	for i := 0; i < len(buf); i++ {
		last = buf[i]
		result |= uint32(last&0x7f) << shift
		shift += 7
		n = i + 1
		if last&0x80 == 0 {
			break
		}
	}
	if last&0x40 != 0 {
		result -= uint32(1) << shift
	}
	return int32(result), n
}

// uleb128p1 = uleb128(v+1): 让 -1 编码成单字节, 且没有其他负数能编码
func uleb128p1Encode(v int32) []byte { return uleb128Encode(uint32(v) + 1) }

func uleb128p1Decode(buf []byte) (int32, int) {
	raw, n := uleb128Decode(buf)
	return int32(raw - 1), n
}

// ---------- MUTF-8 ----------

// utf16Units: 增补平面字符拆成两个 UTF-16 码元
func utf16Units(cp uint32) []uint16 {
	if cp < 0x10000 {
		return []uint16{uint16(cp)}
	}
	v := cp - 0x10000
	return []uint16{uint16(0xD800 + (v >> 10)), uint16(0xDC00 + (v & 0x3FF))}
}

// utf16Size = 官方 string_data_item.utf16_size: 以 UTF-16 代码单元计, 不是字节数
func utf16Size(s string) int {
	n := 0
	for _, r := range s {
		n += len(utf16Units(uint32(r)))
	}
	return n
}

// mutf8Encode: U+0000 编成 0xc0 0x80, 结尾补一个 0 字节
func mutf8Encode(s string) []byte {
	out := []byte{}
	for _, r := range s {
		for _, u := range utf16Units(uint32(r)) {
			switch {
			case u == 0x0000:
				out = append(out, 0xc0, 0x80)
			case u <= 0x7f:
				out = append(out, byte(u))
			case u <= 0x7ff:
				out = append(out, byte(0xc0|(u>>6)), byte(0x80|(u&0x3f)))
			default:
				out = append(out, byte(0xe0|(u>>12)), byte(0x80|((u>>6)&0x3f)), byte(0x80|(u&0x3f)))
			}
		}
	}
	return append(out, 0x00)
}

// mutf8Decode 还原码元序列(不做排序语义)
func mutf8Decode(buf []byte) []uint16 {
	units := []uint16{}
	for i := 0; i < len(buf) && buf[i] != 0x00; {
		b0 := buf[i]
		switch {
		case b0 < 0x80:
			units = append(units, uint16(b0))
			i++
		case b0&0xe0 == 0xc0:
			units = append(units, uint16(b0&0x1f)<<6|uint16(buf[i+1]&0x3f))
			i += 2
		case b0&0xf0 == 0xe0:
			units = append(units, uint16(b0&0x0f)<<12|uint16(buf[i+1]&0x3f)<<6|uint16(buf[i+2]&0x3f))
			i += 3
		default:
			panic("非法 MUTF-8 起始字节")
		}
	}
	return units
}

// ---------- header_item ----------

type dexHeader struct {
	magic        [8]byte
	checksum     uint32
	signature    [20]byte
	fileSize     uint32
	headerSize   uint32
	endianTag    uint32
	linkSize     uint32
	linkOff      uint32
	mapOff       uint32
	stringIDsSize uint32
	stringIDsOff uint32
	typeIDsSize  uint32
	typeIDsOff   uint32
	protoIDsSize uint32
	protoIDsOff  uint32
	fieldIDsSize uint32
	fieldIDsOff  uint32
	methodIDsSize uint32
	methodIDsOff uint32
	classDefsSize uint32
	classDefsOff uint32
	dataSize     uint32
	dataOff      uint32
}

// parseHeader 按官方 header_item 逐字段解析(小端, v40 版 0x70 字节)
func parseHeader(buf []byte) (*dexHeader, error) {
	if len(buf) < headerSizeV40 {
		return nil, fmt.Errorf("文件短于 header_item 的 0x70 字节")
	}
	h := &dexHeader{}
	copy(h.magic[:], buf[0:8])
	for i, b := range dexFileMagic {
		if h.magic[i] != b {
			return nil, fmt.Errorf("magic 不是 dex\\n039\\0")
		}
	}
	h.checksum = binary.LittleEndian.Uint32(buf[8:12])
	copy(h.signature[:], buf[12:32])
	h.fileSize = binary.LittleEndian.Uint32(buf[32:36])
	h.headerSize = binary.LittleEndian.Uint32(buf[36:40])
	h.endianTag = binary.LittleEndian.Uint32(buf[40:44])
	if h.endianTag != endianConstant && h.endianTag != reverseEndianConstant {
		return nil, fmt.Errorf("endian_tag 0x%08x 既不是 ENDIAN_CONSTANT 也不是 REVERSE", h.endianTag)
	}
	h.linkSize = binary.LittleEndian.Uint32(buf[44:48])
	h.linkOff = binary.LittleEndian.Uint32(buf[48:52])
	h.mapOff = binary.LittleEndian.Uint32(buf[52:56])
	h.stringIDsSize = binary.LittleEndian.Uint32(buf[56:60])
	h.stringIDsOff = binary.LittleEndian.Uint32(buf[60:64])
	h.typeIDsSize = binary.LittleEndian.Uint32(buf[64:68])
	h.typeIDsOff = binary.LittleEndian.Uint32(buf[68:72])
	h.protoIDsSize = binary.LittleEndian.Uint32(buf[72:76])
	h.protoIDsOff = binary.LittleEndian.Uint32(buf[76:80])
	h.fieldIDsSize = binary.LittleEndian.Uint32(buf[80:84])
	h.fieldIDsOff = binary.LittleEndian.Uint32(buf[84:88])
	h.methodIDsSize = binary.LittleEndian.Uint32(buf[88:92])
	h.methodIDsOff = binary.LittleEndian.Uint32(buf[92:96])
	h.classDefsSize = binary.LittleEndian.Uint32(buf[96:100])
	h.classDefsOff = binary.LittleEndian.Uint32(buf[100:104])
	h.dataSize = binary.LittleEndian.Uint32(buf[104:108])
	h.dataOff = binary.LittleEndian.Uint32(buf[108:112])
	return h, nil
}

// codeItemPadding: 官方 —— 只有 tries_size 非零**且** insns_size 是奇数时才存在 2 字节填充
func codeItemPadding(triesSize, insnsSize int) int {
	if triesSize != 0 && insnsSize%2 == 1 {
		return 1
	}
	return 0
}

func codeItemUnits(triesSize, insnsSize int) int {
	return 8 + insnsSize + codeItemPadding(triesSize, insnsSize)
}

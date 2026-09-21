// Package hermes 复刻 facebook/hermes 的 64 位值表示与 HBC 文件头布局：
//   include/hermes/VM/HermesValue.h             —— NaN-boxing 标签
//   include/hermes/BCGen/HBC/BytecodeFileFormat.h —— MAGIC / 文件头 / 字符串表项
// 无本机 Go 工具链，仅人工审查 + 括号配平校验。
package hermes

import "math"

// 位布局常量（HermesValue.h:185-197）
const (
	NumTagExpBits = 16
	NumDataBits   = 64 - NumTagExpBits // 48
	DataMask      = uint64(1)<<NumDataBits - 1
	TagWidth      = 3
	ETagWidth     = 4
)

// Tag 取值：SignExtend32<8>(0xf9) = -7 起，到 -1 止。
const (
	TagFirst         = -7
	TagEmptyInvalid  = TagFirst
	TagUndefinedNull = -6
	TagBoolSymbol    = -5
	TagNativeValue   = -4
	TagFirstPointer  = -3
	TagStr           = TagFirstPointer
	TagBigInt        = -2
	TagObject        = -1
	TagLast          = -1
)

// ETag = 2×Tag 或 2×Tag+1。
const (
	ETagEmpty     = TagEmptyInvalid * 2
	ETagInvalid   = TagEmptyInvalid*2 + 1
	ETagUndefined = TagUndefinedNull * 2
	ETagNull      = TagUndefinedNull*2 + 1
	ETagBool      = TagBoolSymbol * 2
	ETagSymbol    = TagBoolSymbol*2 + 1
	ETagStr1      = TagStr * 2
	ETagObject1   = TagObject * 2
)

// TagFirstRaw / PointerFirstRaw 是 isDouble 与 isPointer 的比较阈值。
const (
	TagFirstRaw     = uint64(0xFFF9) << NumDataBits
	PointerFirstRaw = uint64(0xFFFD) << NumDataBits
)

func u64(x int64) uint64 { return uint64(x) }

// WithTag 对应 HermesValue(uint64_t val, Tag tag)：raw = val | tag<<48。
func WithTag(val uint64, tag int64) uint64 {
	return val | u64(tag)<<NumDataBits
}

// WithETag 对应 HermesValue(uint64_t val, ETag etag)：raw = val | etag<<47。
func WithETag(val uint64, etag int64) uint64 {
	return val | u64(etag)<<(NumDataBits-1)
}

// GetTag / GetETag 走算术右移。
func GetTag(raw uint64) int64  { return int64(raw) >> NumDataBits }
func GetETag(raw uint64) int64 { return int64(raw) >> (NumDataBits - 1) }

// IsDouble 对应 raw_ < ((uint64_t)Tag::First << kNumDataBits)。
func IsDouble(raw uint64) bool { return raw < TagFirstRaw }

// IsPointer 对应 raw_ >= ((uint64_t)Tag::FirstPointer << kNumDataBits)。
func IsPointer(raw uint64) bool { return raw >= PointerFirstRaw }

// GetPointer 掩掉高 16 位。
func GetPointer(raw uint64) uint64 { return raw & DataMask }

// EncodeNumber 直接存放 IEEE754 位模式。
func EncodeNumber(d float64) uint64 { return math.Float64bits(d) }

// EncodeNaN 用 quiet NaN 的位模式。
func EncodeNaN() uint64 { return math.Float64bits(math.NaN()) }

// IsNaN 先掩掉符号位再与 quiet NaN 比较。
func IsNaN(raw uint64) bool {
	const mask = uint64(1)<<63 - 1
	return raw&mask == EncodeNaN()&mask
}

// EncodeNull / EncodeUndefined / EncodeBool / EncodeObject 对应同名工厂方法。
func EncodeNull() uint64      { return WithETag(0, ETagNull) }
func EncodeUndefined() uint64 { return WithETag(0, ETagUndefined) }

// EncodeBool 把布尔值放数据位。
func EncodeBool(v bool) uint64 {
	if v {
		return WithETag(1, ETagBool)
	}
	return WithETag(0, ETagBool)
}

// EncodeObject 编码堆对象指针（要求指针不超过 48 位）。
func EncodeObject(ptr uint64) uint64 { return WithTag(ptr&DataMask, TagObject) }

// ------------------------------------------------------------ HBC 文件格式

// Magic 是「Hermes」的古希腊语 Ἑρμῆ 的 UTF-16BE，截到 8 字节。
const Magic = uint64(0x1F1903C103BC1FC6)

// DeltaMagic 是 ~Magic，表示 delta 形态（不可执行）。
const DeltaMagic = ^Magic

// SHA1NumBytes 见 include/hermes/Support/SHA1.h。
const SHA1NumBytes = 20

// BytecodeAlignment 见 BytecodeFileFormat.h：alignof(uint32_t)。
const BytecodeAlignment = 4

// HeaderSize 由 8 + 4 + 20 + 20×4 + 1 + 19 得到，必须是 32 的整数倍。
const HeaderSize = 8 + 4 + SHA1NumBytes + 20*4 + 1 + 19

// MagicCodeUnits 返回 MAGIC 的四个 UTF-16BE 码元。
func MagicCodeUnits() [4]uint16 {
	return [4]uint16{
		uint16(Magic >> 48), uint16(Magic >> 32),
		uint16(Magic >> 16), uint16(Magic),
	}
}

// 字符串表项的位域哨兵值。
const (
	InvalidOffset = 1 << 23
	InvalidLength = (1 << 8) - 1
)

// SmallStringTableEntry 对应同名结构：isUTF16:1 / offset:23 / length:8。
type SmallStringTableEntry struct {
	IsUTF16 bool
	Offset  uint32
	Length  uint32
}

// IsOverflowed 以 length == INVALID_LENGTH 判断。
func (e SmallStringTableEntry) IsOverflowed() bool { return e.Length == InvalidLength }

// SmallFuncHeader 在 overflowed 时把 32 位偏移拆成两个 16 位位域。
type SmallFuncHeader struct {
	Offset     uint32
	InfoOffset uint32
	Overflowed bool
}

// SetLargeHeaderOffset 写入大函数头偏移。
func (h *SmallFuncHeader) SetLargeHeaderOffset(v uint32) {
	h.Overflowed = true
	h.Offset = v & 0xFFFF
	h.InfoOffset = v >> 16
}

// GetLargeHeaderOffset 还原大函数头偏移。
func (h *SmallFuncHeader) GetLargeHeaderOffset() uint32 {
	return h.InfoOffset<<16 | h.Offset
}

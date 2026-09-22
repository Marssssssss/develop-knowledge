// Package sqfs 对应 Python 侧的 squashfs.py。
//
// 常量取自 torvalds/linux@master 的 fs/squashfs/squashfs_fs.h，
// 目录头的 +1 来自 fs/squashfs/dir.c：dir_count = le32_to_cpu(dirh.count) + 1。
package sqfs

import "encoding/binary"

// Magic 是 "hsqs" 按小端读出的 u32。
const Magic = 0x73717368

// MetadataSize 是元数据块大小。
const MetadataSize = 8192

// DirCount 是目录头 count 字段的上限。
const DirCount = 256

// InvalidFrag 表示该文件没有 fragment。
const InvalidFrag = uint32(0xFFFFFFFF)

// FlagBit 对应 SQUASHFS_BIT(flag, bit)。
const (
	FlagNoI     = 0
	FlagNoD     = 1
	FlagNoF     = 3
	FlagNoFrag  = 4
	FlagCompOpt = 10
)

// 压缩 id。
const (
	Zlib = 1
	Lzma = 2
	Lzo  = 3
	Xz   = 4
	Lz4  = 5
	Zstd = 6
)

// CompressedBit 是 2 字节块头的最高位：置位表示未压缩。
const CompressedBit = 1 << 15

// Superblock 与 struct squashfs_super_block 一一对应（全小端）。
type Superblock struct {
	Magic             uint32
	Inodes            uint32
	MkfsTime          uint32
	BlockSize         uint32
	Fragments         uint32
	Compression       uint16
	BlockLog          uint16
	Flags             uint16
	NoIds             uint16
	Major             uint16
	Minor             uint16
	RootInode         uint64
	BytesUsed         uint64
	IDTableStart      uint64
	XattrIDTableStart uint64
	InodeTableStart   uint64
	DirTableStart     uint64
	FragTableStart    uint64
	LookupTableStart  uint64
}

// Pack 序列化成 96 字节。
func (s *Superblock) Pack() []byte {
	b := make([]byte, 96)
	binary.LittleEndian.PutUint32(b[0:], s.Magic)
	binary.LittleEndian.PutUint32(b[4:], s.Inodes)
	binary.LittleEndian.PutUint32(b[8:], s.MkfsTime)
	binary.LittleEndian.PutUint32(b[12:], s.BlockSize)
	binary.LittleEndian.PutUint32(b[16:], s.Fragments)
	binary.LittleEndian.PutUint16(b[20:], s.Compression)
	binary.LittleEndian.PutUint16(b[22:], s.BlockLog)
	binary.LittleEndian.PutUint16(b[24:], s.Flags)
	binary.LittleEndian.PutUint16(b[26:], s.NoIds)
	binary.LittleEndian.PutUint16(b[28:], s.Major)
	binary.LittleEndian.PutUint16(b[30:], s.Minor)
	binary.LittleEndian.PutUint64(b[32:], s.RootInode)
	binary.LittleEndian.PutUint64(b[40:], s.BytesUsed)
	binary.LittleEndian.PutUint64(b[48:], s.IDTableStart)
	binary.LittleEndian.PutUint64(b[56:], s.XattrIDTableStart)
	binary.LittleEndian.PutUint64(b[64:], s.InodeTableStart)
	binary.LittleEndian.PutUint64(b[72:], s.DirTableStart)
	binary.LittleEndian.PutUint64(b[80:], s.FragTableStart)
	binary.LittleEndian.PutUint64(b[88:], s.LookupTableStart)
	return b
}

// ParseSuperblock 反序列化。
func ParseSuperblock(blob []byte, off int) (*Superblock, bool) {
	if off+96 > len(blob) {
		return nil, false
	}
	b := blob[off : off+96]
	return &Superblock{
		Magic:             binary.LittleEndian.Uint32(b[0:]),
		Inodes:            binary.LittleEndian.Uint32(b[4:]),
		MkfsTime:          binary.LittleEndian.Uint32(b[8:]),
		BlockSize:         binary.LittleEndian.Uint32(b[12:]),
		Fragments:         binary.LittleEndian.Uint32(b[16:]),
		Compression:       binary.LittleEndian.Uint16(b[20:]),
		BlockLog:          binary.LittleEndian.Uint16(b[22:]),
		Flags:             binary.LittleEndian.Uint16(b[24:]),
		NoIds:             binary.LittleEndian.Uint16(b[26:]),
		Major:             binary.LittleEndian.Uint16(b[28:]),
		Minor:             binary.LittleEndian.Uint16(b[30:]),
		RootInode:         binary.LittleEndian.Uint64(b[32:]),
		BytesUsed:         binary.LittleEndian.Uint64(b[40:]),
		IDTableStart:      binary.LittleEndian.Uint64(b[48:]),
		XattrIDTableStart: binary.LittleEndian.Uint64(b[56:]),
		InodeTableStart:   binary.LittleEndian.Uint64(b[64:]),
		DirTableStart:     binary.LittleEndian.Uint64(b[72:]),
		FragTableStart:    binary.LittleEndian.Uint64(b[80:]),
		LookupTableStart:  binary.LittleEndian.Uint64(b[88:]),
	}, true
}

// Flag 取某一位。
func (s *Superblock) Flag(bit uint) uint16 { return (s.Flags >> bit) & 1 }

// Compressed 判断 2 字节块头是否表示"已压缩"。
func Compressed(h uint16) bool { return h&CompressedBit == 0 }

// CompressedSize 复刻 SQUASHFS_COMPRESSED_SIZE：低 15 位为 0 时返回 32768。
func CompressedSize(h uint16) uint16 {
	low := h & ^uint16(CompressedBit)
	if low != 0 {
		return low
	}
	return CompressedBit
}

// InodeBlk / InodeOffset / MKINODE 是 48 位 inode 引用的编解码。
func InodeBlk(a uint64) uint32   { return uint32(a >> 16) }
func InodeOffset(a uint64) uint32 { return uint32(a & 0xFFFF) }
func MKINODE(blk uint32, off uint32) uint64 {
	return (uint64(blk) << 16) + uint64(off)
}

// FragmentBytes 是 fragment 查找表的字节数（每项 16 字节）。
func FragmentBytes(n uint32) uint32 { return n * 16 }

// FragmentIndexes 是它占用的元数据块数。
func FragmentIndexes(n uint32) uint32 {
	return (FragmentBytes(n) + MetadataSize - 1) / MetadataSize
}

// DirCountOf 是 fs/squashfs/dir.c 的 count + 1。
func DirCountOf(raw uint32) uint32 { return raw + 1 }

// DirCountOK 判断换算后的条目数有没有超过上限。
func DirCountOK(raw uint32) bool { return DirCountOf(raw) <= DirCount }

// DataBlockCount 是普通文件的数据块表项数。
func DataBlockCount(fileSize uint32, blockLog uint32, hasFrag bool) uint32 {
	bs := uint32(1) << blockLog
	full := fileSize / bs
	if fileSize%bs != 0 && !hasFrag {
		full++
	}
	return full
}

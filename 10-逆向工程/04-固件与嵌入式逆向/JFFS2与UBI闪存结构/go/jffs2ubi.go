// Package jffs2ubi 对应 Python 侧的 jffs2ubi.py。
//
// JFFS2 侧取自 include/uapi/linux/jffs2.h（全小端、紧排）；
// CRC 覆盖范围取自 fs/jffs2/summary.c 与 readinode.c：
//   hdr_crc  = crc32(0, node, sizeof(unknown_node) - 4)
//   node_crc = crc32(0, rd,   sizeof(*rd) - 8)
// UBI 侧取自 drivers/mtd/ubi/ubi-media.h（全大端）与 io.c（CRC 种子 ~0）。
package jffs2ubi

import "encoding/binary"

// JFFS2 常量。
const (
	MagicBitmask    = 0x1985
	MagicSwapped    = 0x8519
	SumMagic        = 0x02851885
	MaxNameLen      = 254
	UnknownNodeSize = 12
	DirentHdrSize   = 40
	InodeHdrSize    = 68
)

// nodetype：高两位是兼容级别，0x2000 是 NODE_ACCURATE。
const (
	NodetypeDirent      = 0xE001
	NodetypeInode       = 0xE002
	NodetypeCleanmarker = 0x2003
	NodetypePadding     = 0x2004
	NodetypeSummary     = 0x2006
	NodetypeXattr       = 0xE008
	NodetypeXref        = 0xE009
)

// 兼容级别掩码。
const (
	CompatMask    = 0xC000
	FeatureIncomp = 0xC000
	FeatureRoComp = 0x8000
	FeatureCopy   = 0x4000
	FeatureDelete = 0x0000
)

// UBI 常量。
const (
	UBIVersion    = 1
	UBICRC32Init  = uint32(0xFFFFFFFF)
	UBIEcHdrMagic = 0x55424923 // "UBI#"
	UBIVidHdrMagic = 0x55424921 // "UBI!"
	UBIVidDynamic = 1
	UBIVidStatic  = 2
	ECReqSize     = 64
	VIDHdrSize    = 64
	MaxVolumes    = 128
	VolNameMax    = 127
)

// LayoutVolumeID 是内部卷的起始 id：0x7FFFFFFF - 4096。
const LayoutVolumeID = 0x7FFFFFFF - 4096

var crcTable = func() []uint32 {
	t := make([]uint32, 256)
	for i := 0; i < 256; i++ {
		c := uint32(i)
		for b := 0; b < 8; b++ {
			if c&1 != 0 {
				c = (c >> 1) ^ 0xEDB88320
			} else {
				c >>= 1
			}
		}
		t[i] = c
	}
	return t
}()

// CRC32 是 Linux crc32()：seed 传入、首尾不取反。
func CRC32(data []byte, seed uint32) uint32 {
	crc := seed
	for _, b := range data {
		crc = crcTable[(crc^uint32(b))&0xFF] ^ (crc >> 8)
	}
	return crc
}

// RawNode 是 struct jffs2_unknown_node。
type RawNode struct {
	Magic    uint16
	Nodetype uint16
	Totlen   uint32
	HdrCRC   uint32
}

// Common 序列化通用头。
func (n *RawNode) Common() []byte {
	b := make([]byte, UnknownNodeSize)
	binary.LittleEndian.PutUint16(b[0:], n.Magic)
	binary.LittleEndian.PutUint16(b[2:], n.Nodetype)
	binary.LittleEndian.PutUint32(b[4:], n.Totlen)
	binary.LittleEndian.PutUint32(b[8:], n.HdrCRC)
	return b
}

// ParseCommon 读通用头。
func ParseCommon(blob []byte, off int) (*RawNode, bool) {
	if off+UnknownNodeSize > len(blob) {
		return nil, false
	}
	b := blob[off : off+UnknownNodeSize]
	return &RawNode{
		Magic:    binary.LittleEndian.Uint16(b[0:]),
		Nodetype: binary.LittleEndian.Uint16(b[2:]),
		Totlen:   binary.LittleEndian.Uint32(b[4:]),
		HdrCRC:   binary.LittleEndian.Uint32(b[8:]),
	}, true
}

// SealCommon 计算 hdr_crc：覆盖 magic + nodetype + totlen。
func SealCommon(n *RawNode) {
	body := make([]byte, 8)
	binary.LittleEndian.PutUint16(body[0:], n.Magic)
	binary.LittleEndian.PutUint16(body[2:], n.Nodetype)
	binary.LittleEndian.PutUint32(body[4:], n.Totlen)
	n.HdrCRC = CRC32(body, 0)
}

// CheckHdrCRC 校验 hdr_crc。
func (n *RawNode) CheckHdrCRC() bool {
	body := make([]byte, 8)
	binary.LittleEndian.PutUint16(body[0:], n.Magic)
	binary.LittleEndian.PutUint16(body[2:], n.Nodetype)
	binary.LittleEndian.PutUint32(body[4:], n.Totlen)
	return CRC32(body, 0) == n.HdrCRC
}

// NodeCompat 取高两位的兼容级别。
func NodeCompat(t uint16) uint16 { return t & CompatMask }

// IsIncompat 判断未知结点是否该导致挂载失败。
func IsIncompat(t uint16) bool { return NodeCompat(t) == FeatureIncomp }

// SwappedEndian 判断是否读到了反字节序的文件系统。
func SwappedEndian(magic uint16) bool { return magic == MagicSwapped }

// ECHeader 是 struct ubi_ec_hdr（大端）。
type ECHeader struct {
	Magic         uint32
	Version       uint8
	Ec            uint64
	VidHdrOffset  uint32
	DataOffset    uint32
	ImageSeq      uint32
	HdrCRC        uint32
}

// PackEC 序列化 EC 头，并算出覆盖前 60 字节的 CRC。
func PackEC(e *ECHeader) []byte {
	b := make([]byte, ECReqSize)
	binary.BigEndian.PutUint32(b[0:], e.Magic)
	b[4] = e.Version
	binary.BigEndian.PutUint64(b[8:], e.Ec)
	binary.BigEndian.PutUint32(b[16:], e.VidHdrOffset)
	binary.BigEndian.PutUint32(b[20:], e.DataOffset)
	binary.BigEndian.PutUint32(b[24:], e.ImageSeq)
	binary.BigEndian.PutUint32(b[60:], CRC32(b[:60], UBICRC32Init))
	return b
}

// CheckECCRC 校验 EC 头 CRC。
func CheckECCRC(b []byte) bool {
	if len(b) < ECReqSize {
		return false
	}
	want := binary.BigEndian.Uint32(b[60:])
	return CRC32(b[:60], UBICRC32Init) == want
}

// VIDHeader 是 struct ubi_vid_hdr（大端）。
type VIDHeader struct {
	Magic     uint32
	Version   uint8
	VolType   uint8
	CopyFlag  uint8
	Compat    uint8
	VolID     uint32
	Lnum      uint32
	DataSize  uint32
	UsedEbs   uint32
	DataPad   uint32
	DataCRC   uint32
	Sqnum     uint64
	HdrCRC    uint32
}

// PackVID 序列化 VID 头。
func PackVID(v *VIDHeader) []byte {
	b := make([]byte, VIDHdrSize)
	binary.BigEndian.PutUint32(b[0:], v.Magic)
	b[4] = v.Version
	b[5] = v.VolType
	b[6] = v.CopyFlag
	b[7] = v.Compat
	binary.BigEndian.PutUint32(b[8:], v.VolID)
	binary.BigEndian.PutUint32(b[12:], v.Lnum)
	binary.BigEndian.PutUint32(b[20:], v.DataSize)
	binary.BigEndian.PutUint32(b[24:], v.UsedEbs)
	binary.BigEndian.PutUint32(b[28:], v.DataPad)
	binary.BigEndian.PutUint32(b[32:], v.DataCRC)
	binary.BigEndian.PutUint64(b[40:], v.Sqnum)
	binary.BigEndian.PutUint32(b[60:], CRC32(b[:60], UBICRC32Init))
	return b
}

// CheckVIDCRC 校验 VID 头 CRC。
func CheckVIDCRC(b []byte) bool {
	if len(b) < VIDHdrSize {
		return false
	}
	return CRC32(b[:60], UBICRC32Init) == binary.BigEndian.Uint32(b[60:])
}

// PickPEB 在同一个 LEB 有两份 VID 头时决定选谁。
func PickPEB(old, nw *VIDHeader, nwCRCOK bool) *VIDHeader {
	if nw.CopyFlag == 0 {
		return nw
	}
	if nwCRCOK {
		return nw
	}
	return old
}

// IsInternalVol 判断卷 id 是否落在内部卷保留区。
func IsInternalVol(volID uint32) bool { return volID >= LayoutVolumeID }

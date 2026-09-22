// Package fitimg 对应 Python 侧的 uimage.py：U-Boot legacy uImage 头。
//
// 常量取自 u-boot@master 的 include/image.h，字段一律网络序（大端）。
// CRC 是 Linux 的 crc32(seed, ...)：首尾都不取反。
package fitimg

import "encoding/binary"

// IH_MAGIC 是 include/image.h 里的 #define IH_MAGIC 0x27051956。
const IH_MAGIC = 0x27051956

// IH_NMLEN 是镜像名字段的定长。
const IH_NMLEN = 32

// HeaderSize 是 struct legacy_img_hdr 的长度：7 个 u32 + 4 个 u8 + 32 字节名字。
const HeaderSize = 64

// OS / Arch / Type / Comp 与 image.h 的枚举顺序一致。
const (
	IHOsLinux   = 5
	IHArchARM   = 2
	IHTypeKernel = 2
	IHTypeMulti  = 4
	IHCompNone   = 0
	IHCompGzip   = 1
)

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

// CRC32 复刻 Linux crc32()：寄存器初值 = seed，结尾不取反。
func CRC32(data []byte, seed uint32) uint32 {
	crc := seed
	for _, b := range data {
		crc = crcTable[(crc^uint32(b))&0xFF] ^ (crc >> 8)
	}
	return crc
}

// LegacyHeader 是 64 字节的 uImage 头。
type LegacyHeader struct {
	Magic uint32
	Hcrc  uint32
	Time  uint32
	Size  uint32
	Load  uint32
	Ep    uint32
	Dcrc  uint32
	Os    uint8
	Arch  uint8
	Type  uint8
	Comp  uint8
	Name  [IH_NMLEN]byte
}

// Pack 按大端序列化。
func (h *LegacyHeader) Pack() []byte {
	b := make([]byte, HeaderSize)
	binary.BigEndian.PutUint32(b[0:], h.Magic)
	binary.BigEndian.PutUint32(b[4:], h.Hcrc)
	binary.BigEndian.PutUint32(b[8:], h.Time)
	binary.BigEndian.PutUint32(b[12:], h.Size)
	binary.BigEndian.PutUint32(b[16:], h.Load)
	binary.BigEndian.PutUint32(b[20:], h.Ep)
	binary.BigEndian.PutUint32(b[24:], h.Dcrc)
	b[28] = h.Os
	b[29] = h.Arch
	b[30] = h.Type
	b[31] = h.Comp
	copy(b[32:], h.Name[:])
	return b
}

// ParseLegacy 从 blob 的 off 处读一个头。
func ParseLegacy(blob []byte, off int) (*LegacyHeader, bool) {
	if off+HeaderSize > len(blob) {
		return nil, false
	}
	b := blob[off : off+HeaderSize]
	h := &LegacyHeader{
		Magic: binary.BigEndian.Uint32(b[0:]),
		Hcrc:  binary.BigEndian.Uint32(b[4:]),
		Time:  binary.BigEndian.Uint32(b[8:]),
		Size:  binary.BigEndian.Uint32(b[12:]),
		Load:  binary.BigEndian.Uint32(b[16:]),
		Ep:    binary.BigEndian.Uint32(b[20:]),
		Dcrc:  binary.BigEndian.Uint32(b[24:]),
		Os:    b[28],
		Arch:  b[29],
		Type:  b[30],
		Comp:  b[31],
	}
	copy(h.Name[:], b[32:])
	return h, true
}

// Seal 按 mkimage 的顺序填 dcrc 与 hcrc（hcrc 计算时自身字段先清零）。
func (h *LegacyHeader) Seal(payload []byte) {
	h.Size = uint32(len(payload))
	h.Dcrc = CRC32(payload, 0)
	saved := h.Hcrc
	h.Hcrc = 0
	h.Hcrc = CRC32(h.Pack(), 0)
	if saved == 0 {
		return
	}
}

// CheckHcrc 校验头 CRC。
func (h *LegacyHeader) CheckHcrc() bool {
	saved := h.Hcrc
	h.Hcrc = 0
	want := CRC32(h.Pack(), 0)
	h.Hcrc = saved
	return want == saved
}

// CheckDcrc 校验数据 CRC。
func (h *LegacyHeader) CheckDcrc(payload []byte) bool {
	return CRC32(payload, 0) == h.Dcrc
}

// DataOffset 是 image_get_data() 的等价物。
func DataOffset(off int) int { return off + HeaderSize }

// ImageSize 是 image_get_image_size() 的等价物。
func ImageSize(h *LegacyHeader) uint32 { return h.Size + HeaderSize }

// MultiSizes 读多文件镜像开头那串 be32 长度（以 0 结束）。
func MultiSizes(payload []byte) []uint32 {
	out := []uint32{}
	for i := 0; (i+1)*4 <= len(payload); i++ {
		v := binary.BigEndian.Uint32(payload[i*4:])
		if v == 0 {
			break
		}
		out = append(out, v)
	}
	return out
}

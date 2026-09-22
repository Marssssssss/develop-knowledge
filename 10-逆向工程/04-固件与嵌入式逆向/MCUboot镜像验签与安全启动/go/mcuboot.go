// Package mcuboot 对应 Python 侧的 mcuboot.py。
//
// 常量取自 mcu-tools/mcuboot@main 的 boot/bootutil/include/bootutil/image.h
// 与 docs/design.md §Image format；trailer 布局取自
// boot/bootutil/include/bootutil/bootutil.h 的 struct image_trailer。
// 与 U-Boot 相反，MCUboot 的头是**小端**。
package mcuboot

import (
	"crypto/sha256"
	"encoding/binary"
)

// 魔数与尺寸。
const (
	ImageMagic        = 0x96F3B83D
	ImageMagicV1      = 0x96F3B83C
	ImageMagicNone    = 0xFFFFFFFF
	TLVInfoMagic      = 0x6907
	TLVProtInfoMagic  = 0x6908
	ImageHeaderSize   = 32
	ImageHashLen      = 32
	BootMaxAlign      = 8
	BootMagicSz       = 16
	TrailerSize       = BootMaxAlign*3 + BootMagicSz
)

// 镜像头标志位。
const (
	ImageFRAMLoad         = 0x00000020
	ImageFEncryptedAES128 = 0x00000004
	ImageFEncryptedAES256 = 0x00000008
	ImageFNonBootable     = 0x00000010
)

// TLV 类型。
const (
	TLVKeyhash = 0x01
	TLVPubkey  = 0x02
	TLVSha256  = 0x10
	TLVSha384  = 0x11
	TLVSha512  = 0x12
	TLVRSA2048 = 0x20
	TLVECDSA   = 0x22
	TLVED25519 = 0x24
	TLVSecCnt  = 0x50
)

// ImageVersion 是 struct image_version：major/minor 各 1 字节，
// revision 2 字节，build_num 4 字节。
type ImageVersion struct {
	Major    uint8
	Minor    uint8
	Revision uint16
	BuildNum uint32
}

// ImageHeader 是 struct image_header。
type ImageHeader struct {
	Magic           uint32
	LoadAddr        uint32
	HdrSize         uint16
	ProtectTLVSize  uint16
	ImgSize         uint32
	Flags           uint32
	Ver             ImageVersion
	Pad             uint32
}

// Pack 序列化 32 字节头。
func (h *ImageHeader) Pack() []byte {
	b := make([]byte, ImageHeaderSize)
	binary.LittleEndian.PutUint32(b[0:], h.Magic)
	binary.LittleEndian.PutUint32(b[4:], h.LoadAddr)
	binary.LittleEndian.PutUint16(b[8:], h.HdrSize)
	binary.LittleEndian.PutUint16(b[10:], h.ProtectTLVSize)
	binary.LittleEndian.PutUint32(b[12:], h.ImgSize)
	binary.LittleEndian.PutUint32(b[16:], h.Flags)
	b[20] = h.Ver.Major
	b[21] = h.Ver.Minor
	binary.LittleEndian.PutUint16(b[22:], h.Ver.Revision)
	binary.LittleEndian.PutUint32(b[24:], h.Ver.BuildNum)
	binary.LittleEndian.PutUint32(b[28:], h.Pad)
	return b
}

// ParseHeader 反序列化。
func ParseHeader(blob []byte, off int) (*ImageHeader, bool) {
	if off+ImageHeaderSize > len(blob) {
		return nil, false
	}
	b := blob[off : off+ImageHeaderSize]
	return &ImageHeader{
		Magic:          binary.LittleEndian.Uint32(b[0:]),
		LoadAddr:       binary.LittleEndian.Uint32(b[4:]),
		HdrSize:        binary.LittleEndian.Uint16(b[8:]),
		ProtectTLVSize: binary.LittleEndian.Uint16(b[10:]),
		ImgSize:        binary.LittleEndian.Uint32(b[12:]),
		Flags:          binary.LittleEndian.Uint32(b[16:]),
		Ver: ImageVersion{
			Major:    b[20],
			Minor:    b[21],
			Revision: binary.LittleEndian.Uint16(b[22:]),
			BuildNum: binary.LittleEndian.Uint32(b[24:]),
		},
		Pad: binary.LittleEndian.Uint32(b[28:]),
	}, true
}

// ParseTLVInfo 读 4 字节的 TLV info 头。
func ParseTLVInfo(blob []byte, off int) (uint16, uint16, bool) {
	if off+4 > len(blob) {
		return 0, 0, false
	}
	return binary.LittleEndian.Uint16(blob[off:]),
		binary.LittleEndian.Uint16(blob[off+2:]), true
}

// TLV 是一条 TLV 记录。
type TLV struct {
	Type  uint16
	Len   uint16
	Value []byte
}

// IterTLVs 顺序遍历 TLV 区；Len 不含 4 字节头。
func IterTLVs(blob []byte, off int, total uint16) []TLV {
	out := []TLV{}
	end := off + int(total)
	pos := off + 4
	for pos+4 <= end && pos+4 <= len(blob) {
		t := binary.LittleEndian.Uint16(blob[pos:])
		l := binary.LittleEndian.Uint16(blob[pos+2:])
		if pos+4+int(l) > len(blob) {
			break
		}
		out = append(out, TLV{t, l, blob[pos+4 : pos+4+int(l)]})
		pos += 4 + int(l)
	}
	return out
}

// BuildTLVArea 把若干条 TLV 拼成一段（含 4 字节 info 头）。
func BuildTLVArea(entries []TLV, magic uint16) []byte {
	body := []byte{}
	for _, e := range entries {
		h := make([]byte, 4)
		binary.LittleEndian.PutUint16(h[0:], e.Type)
		binary.LittleEndian.PutUint16(h[2:], uint16(len(e.Value)))
		body = append(body, h...)
		body = append(body, e.Value...)
	}
	info := make([]byte, 4)
	binary.LittleEndian.PutUint16(info[0:], magic)
	binary.LittleEndian.PutUint16(info[2:], uint16(len(body)+4))
	return append(info, body...)
}

// ImageHash 是 SHA256(头 + 镜像体 + 受保护 TLV 区)。
func ImageHash(blob []byte, h *ImageHeader) []byte {
	end := int(h.HdrSize) + int(h.ImgSize) + int(h.ProtectTLVSize)
	if end > len(blob) {
		end = len(blob)
	}
	sum := sha256.Sum256(blob[:end])
	return sum[:]
}

// Keyhash 是 SHA256(公钥)：KEYHASH TLV 的内容。
func Keyhash(pub []byte) []byte {
	sum := sha256.Sum256(pub)
	return sum[:]
}

// Validate 按 docs/design.md 的顺序校验。
func Validate(blob []byte) (bool, string) {
	h, ok := ParseHeader(blob, 0)
	if !ok {
		return false, "too short"
	}
	if h.Magic != ImageMagic {
		return false, "bad magic"
	}
	off := int(h.HdrSize) + int(h.ImgSize)
	magic, total, ok := ParseTLVInfo(blob, off)
	if !ok {
		return false, "no tlv info"
	}
	if magic == TLVProtInfoMagic {
		if h.ProtectTLVSize == 0 {
			return false, "PROT_INFO but protect_tlv_size == 0"
		}
		nxt := off + int(h.ProtectTLVSize)
		m2, t2, ok2 := ParseTLVInfo(blob, nxt)
		if !ok2 || m2 != TLVInfoMagic {
			return false, "second tlv info missing or wrong"
		}
		off, magic, total = nxt, m2, t2
	} else if magic != TLVInfoMagic {
		return false, "bad tlv info magic"
	}
	for _, t := range IterTLVs(blob, off, total) {
		if t.Type == TLVSha256 {
			if len(t.Value) != ImageHashLen {
				return false, "sha256 tlv bad length"
			}
			want := ImageHash(blob, h)
			for i := range want {
				if want[i] != t.Value[i] {
					return false, "sha256 mismatch"
				}
			}
			return true, "ok"
		}
	}
	return false, "no sha256 tlv"
}

// Trailer 是 slot 末尾各字段的偏移。
type Trailer struct {
	SwapType int
	CopyDone int
	ImageOK  int
	Magic    int
}

// TrailerOffsets 计算 trailer 四个字段在 slot 里的偏移。
func TrailerOffsets(slotSize, align, magicSz int) Trailer {
	st := slotSize - (align*3 + magicSz)
	return Trailer{st, st + align, st + align*2, st + align*3}
}

package main

import (
	"encoding/binary"
	"fmt"

	"mcuboot"
)

func main() {
	body := make([]byte, 64)
	for i := range body {
		body[i] = byte(i)
	}
	prot := []mcuboot.TLV{{Type: mcuboot.TLVSecCnt, Value: []byte{3, 0, 0, 0}}}
	unprot := []mcuboot.TLV{
		{Type: mcuboot.TLVSha256, Value: make([]byte, 32)},
		{Type: mcuboot.TLVKeyhash, Value: mcuboot.Keyhash([]byte{4, 0x21})},
	}
	protArea := mcuboot.BuildTLVArea(prot, mcuboot.TLVProtInfoMagic)
	unprotArea := mcuboot.BuildTLVArea(unprot, mcuboot.TLVInfoMagic)

	h := &mcuboot.ImageHeader{
		Magic:          mcuboot.ImageMagic,
		HdrSize:        mcuboot.ImageHeaderSize,
		ProtectTLVSize: uint16(len(protArea)),
		ImgSize:        uint32(len(body)),
		Ver:            mcuboot.ImageVersion{Major: 2, Minor: 1, BuildNum: 42},
	}
	blob := append(h.Pack(), body...)
	blob = append(blob, protArea...)
	blob = append(blob, unprotArea...)

	// 用真实摘要回填 SHA256 TLV
	digest := mcuboot.ImageHash(blob, h)
	off := mcuboot.ImageHeaderSize + len(body) + len(protArea)
	copy(blob[off+8:off+40], digest)

	ok, why := mcuboot.Validate(blob)
	fmt.Printf("magic=%#010x hdr=%d img=%d prot=%d\n",
		h.Magic, h.HdrSize, h.ImgSize, h.ProtectTLVSize)
	fmt.Printf("digest=%x\nvalidate=%v %s\n", digest[:8], ok, why)

	m1, t1, _ := mcuboot.ParseTLVInfo(blob, mcuboot.ImageHeaderSize+len(body))
	m2, t2, _ := mcuboot.ParseTLVInfo(blob, mcuboot.ImageHeaderSize+len(body)+len(protArea))
	fmt.Printf("prot info=%#06x/%d  plain info=%#06x/%d\n", m1, t1, m2, t2)
	tr := mcuboot.TrailerOffsets(0x10000, mcuboot.BootMaxAlign, mcuboot.BootMagicSz)
	fmt.Printf("trailer swap=%d copy_done=%d image_ok=%d magic=%d\n",
		tr.SwapType, tr.CopyDone, tr.ImageOK, tr.Magic)
	_ = binary.LittleEndian
}

package main

import (
	"fmt"

	"fitimg"
)

func main() {
	payload := make([]byte, 256)
	for i := range payload {
		payload[i] = byte(i)
	}
	h := &fitimg.LegacyHeader{
		Magic: fitimg.IH_MAGIC,
		Time:  1700000000,
		Load:  0x80008000,
		Ep:    0x80008000,
		Os:    fitimg.IHOsLinux,
		Arch:  fitimg.IHArchARM,
		Type:  fitimg.IHTypeKernel,
		Comp:  fitimg.IHCompGzip,
	}
	copy(h.Name[:], []byte("Linux-6.6.0"))
	h.Seal(payload)

	blob := append(h.Pack(), payload...)
	p, _ := fitimg.ParseLegacy(blob, 0)
	fmt.Printf("magic=%#010x os=%d arch=%d type=%d comp=%d\n",
		p.Magic, p.Os, p.Arch, p.Type, p.Comp)
	fmt.Printf("load=%#010x entry=%#010x size=%d\n", p.Load, p.Ep, p.Size)
	fmt.Printf("hcrc_ok=%v dcrc_ok=%v data_off=%d image_size=%d\n",
		p.CheckHcrc(), p.CheckDcrc(payload), fitimg.DataOffset(0), fitimg.ImageSize(p))
}

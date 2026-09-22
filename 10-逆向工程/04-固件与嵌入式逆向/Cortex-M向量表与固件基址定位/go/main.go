package main

import (
	"encoding/binary"
	"fmt"

	"vecbase"
)

func main() {
	fmt.Printf("TBLOFF pos=%d -> min align=%d ; TBLBASE pos=%d\n",
		vecbase.VTORTbloffPos, vecbase.MinVectorAlign, vecbase.VTORTblbasePos)
	fmt.Printf("relocatable 0x08000000/armv7-m=%v  0x08000004=%v  armv6-m=%v\n",
		vecbase.VTORRelocatable(0x08000000, "armv7-m"),
		vecbase.VTORRelocatable(0x08000004, "armv7-m"),
		vecbase.VTORRelocatable(0x08000000, "armv6-m"))
	fmt.Printf("tblbase bit set=%v  tbloff round-trip=%#x\n",
		vecbase.VTORValue(0x08002000, true)&uint32(vecbase.VTORTblbaseMask) != 0,
		vecbase.VTORTbloff(vecbase.VTORValue(0x08002000, false)))
	fmt.Printf("required_align(16)=%d (64)=%d table_size(8)=%d\n",
		vecbase.RequiredAlign(16), vecbase.RequiredAlign(64), vecbase.VectorTableSize(8))

	// 手工造一份向量表：8 个外部中断 + 4 个桩函数
	const base = 0x08000000
	const ramLo, ramHi = 0x20000000, 0x20010000
	nVec := vecbase.NVICExternalOffset + 8
	table := make([]byte, nVec*4)
	binary.LittleEndian.PutUint32(table[0:], ramLo+0x1000)
	for i := 1; i < nVec; i++ {
		if i-1 < 4 {
			addr := uint32(base+nVec*4) + uint32((i-1)*8)
			binary.LittleEndian.PutUint32(table[i*4:], addr|1)
		}
	}
	img := append(table, make([]byte, 32)...)
	got, score := vecbase.InferBase(img, ramLo, ramHi, 24)
	fmt.Printf("inferred base=%#010x (true %#010x) score=%d\n", got, uint32(base), score)
	fmt.Printf("entry=%#010x\n", vecbase.EntryPoint(vecbase.CandidateVectors(img, 0, 24)))
}

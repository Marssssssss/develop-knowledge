package main

import (
	"fmt"

	"hermes"
)

func main() {
	fmt.Println("== HermesValue 编码 ==")
	fmt.Printf("  undefined      = %#016x\n", hermes.EncodeUndefined())
	fmt.Printf("  null           = %#016x\n", hermes.EncodeNull())
	fmt.Printf("  true           = %#016x\n", hermes.EncodeBool(true))
	fmt.Printf("  object@0x1234  = %#016x\n", hermes.EncodeObject(0x1234))
	fmt.Printf("  number 1.5     = %#016x (isDouble=%v)\n",
		hermes.EncodeNumber(1.5), hermes.IsDouble(hermes.EncodeNumber(1.5)))

	fmt.Println("== 标签读取 ==")
	o := hermes.EncodeObject(0x1234)
	fmt.Printf("  object: Tag=%d ETag=%d pointer=%#x\n",
		hermes.GetTag(o), hermes.GetETag(o), hermes.GetPointer(o))
	u := hermes.EncodeUndefined()
	fmt.Printf("  undefined: Tag=%d ETag=%d isPointer=%v\n",
		hermes.GetTag(u), hermes.GetETag(u), hermes.IsPointer(u))

	fmt.Println("== 边界 ==")
	fmt.Printf("  isDouble(TagFirstRaw)=%v  isDouble(TagFirstRaw-1)=%v\n",
		hermes.IsDouble(hermes.TagFirstRaw), hermes.IsDouble(hermes.TagFirstRaw-1))
	fmt.Printf("  isNaN(-quietNaN)=%v（符号位被掩掉）\n", hermes.IsNaN(0xFFF8000000000000))

	fmt.Println("== HBC ==")
	fmt.Printf("  MAGIC=%#016x codeUnits=%v delta=%#016x\n",
		hermes.Magic, hermes.MagicCodeUnits(), hermes.DeltaMagic)
	fmt.Printf("  header=%d bytes, %%32=%d\n", hermes.HeaderSize, hermes.HeaderSize%32)

	h := &hermes.SmallFuncHeader{}
	h.SetLargeHeaderOffset(0xDEADBEEF)
	fmt.Printf("  large header offset -> %#x\n", h.GetLargeHeaderOffset())
}

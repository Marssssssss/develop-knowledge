package main

import "fmt"

const prefAddr = 0x1000000
const pageSize = 0x1000

func build() (Segment, SegmentStarts) {
	f, _ := MakeFormat(ptr64)
	seg := Segment{Index: 0, Content: map[uint64]uint64{}, PageSize: pageSize}

	// 链 A:0x100 -> 0x108 -> 0x110(末项 next=0 终止)
	seg.Content[0x100], _ = f.Write(Fixup{Target: 0x2100}, 8, prefAddr)
	seg.Content[0x108], _ = f.Write(Fixup{Target: 0x3100}, 8, prefAddr)
	seg.Content[0x110], _ = f.Write(Fixup{IsBind: true, Ordinal: 7, Addend: 16}, 0, prefAddr)

	// 链 B:0x900 -> 0x904
	seg.Content[0x900], _ = f.Write(Fixup{Target: 0x2900}, 4, prefAddr)
	seg.Content[0x904], _ = f.Write(Fixup{IsBind: true, Ordinal: 3}, 0, prefAddr)

	// page_start 前 PageCount 项是每页一项,之后才是 MULTI 的 overflow 区
	st := SegmentStarts{
		PageSize:      pageSize,
		PointerFormat: ptr64,
		PageStart:     []uint16{0x8002, startNone, 0x0100, 0x8900},
		PageCount:     2,
	}
	return seg, st
}

func main() {
	seg, st := build()
	f, _ := MakeFormat(st.PointerFormat)
	fmt.Printf("page_size=0x%x pointer_format=%d page_count=%d stride=%d maxNext=%d\n",
		st.PageSize, st.PointerFormat, st.PageCount, f.Stride, f.maxNext())

	total := 0
	for page := 0; page < st.PageCount; page++ {
		starts := ChainStartsOnPage(st, page)
		fmt.Printf("  page[%d] chain starts =", page)
		for _, s := range starts {
			fmt.Printf(" 0x%x", s)
		}
		fmt.Println()
		for _, off := range starts {
			abs := uint64(page)*pageSize + uint64(off)
			locs, fxs, err := ForEachFixupInChain(f, seg, int(abs), page, prefAddr)
			if err != nil {
				fmt.Println("   error:", err)
				continue
			}
			fmt.Printf("\n  chain at page[%d]+0x%x (abs 0x%x)\n", page, off, abs)
			for i, loc := range locs {
				fx := fxs[i]
				if fx.IsBind {
					fmt.Printf("    0x%04x bind   ordinal=%d addend=%d\n", loc, fx.Ordinal, fx.Addend)
				} else {
					fmt.Printf("    0x%04x rebase target=0x%x\n", loc, fx.Target)
				}
				total++
			}
		}
	}
	fmt.Printf("\ntotal fixups = %d\n", total)

	// 同一段二进制按错误口径(PTR_64 当 OFFSET)读会差一个基址
	fOff, _ := MakeFormat(ptr64Offset)
	raw := seg.Content[0x100]
	fmt.Printf("0x100 按 PTR_64 读=0x%x,按 PTR_64_OFFSET 读=0x%x\n",
		f.Parse(raw, prefAddr).Target, fOff.Parse(raw, prefAddr).Target)

	// 超宽 addend / 链距会被读回校验拦住
	if _, err := f.Write(Fixup{IsBind: true, Ordinal: 1, Addend: 300}, 8, prefAddr); err != nil {
		fmt.Println("addend=300 ->", err)
	}
	if _, err := f.Write(Fixup{Target: 0x1000}, 6, prefAddr); err != nil {
		fmt.Println("delta=6 ->", err)
	}

	e := ParseImportAddend(uint32((0x20<<9)|(1<<8)|0xF6), uint32(0xFFFFFFF0))
	fmt.Printf("import: lib_ordinal=%d weak=%d name_offset=0x%x addend=%d\n",
		e.LibOrdinal, e.WeakImport, e.NameOffset, e.Addend)
}

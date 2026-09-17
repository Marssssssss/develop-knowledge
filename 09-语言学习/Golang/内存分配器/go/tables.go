// Go 1.24 内存分配器：常量表与纯函数（数据层，对照 python/go_sizeclasses.py）。
//
// 来源：go1.24.0 src/runtime/sizeclasses.go（尺寸类表）、src/runtime/msize.go（roundupsize）。
// 表数据由 python/inject_tables.py 从官方源码注入，避免手工抄录 68×5 项常量出错；
// 本机无 Go 工具链，编译期正确性依赖人工审查 + _docs/tools/bracket_check.py。
package main

import (
	"fmt"
)

const (
	pageSize       = 8192
	smallSizeDiv   = 8     // sizeclasses.go: smallSizeDiv
	smallSizeMax   = 1024  // sizeclasses.go: smallSizeMax
	largeSizeDiv   = 128   // sizeclasses.go: largeSizeDiv
	maxSmallSize   = 32768 // gc.MaxSmallSize
	tinySize       = 16    // gc.TinySize（malloc.go: maxTinySize）
	tinySizeClass  = 2     // gc.TinySizeClass
	mallocHeader   = 8     // msize.go 判据用到的 gc.MallocHeaderSize（amd64 上为指针宽度）
	maxSmallReq    = maxSmallSize - mallocHeader
	spanClassCount = 68 * 2 // spanClass = size class × 2（scan / noscan）
)

var classToSize = [68]int{0, 8, 16, 24, 32, 48, 64, 80, 96, 112, 128, 144, 160, 176, 192, 208, 224, 240, 256, 288, 320, 352, 384, 416, 448, 480, 512, 576, 640, 704, 768, 896, 1024, 1152, 1280, 1408, 1536, 1792, 2048, 2304, 2688, 3072, 3200, 3456, 4096, 4864, 5376, 6144, 6528, 6784, 6912, 8192, 9472, 9728, 10240, 10880, 12288, 13568, 14336, 16384, 18432, 19072, 20480, 21760, 24576, 27264, 28672, 32768}

var classToAllocnpages = [68]int{0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2, 1, 2, 1, 2, 1, 3, 2, 3, 1, 3, 2, 3, 4, 5, 6, 1, 7, 6, 5, 4, 3, 5, 7, 2, 9, 7, 5, 8, 3, 10, 7, 4}

var officialObjects = [67]int{1024, 512, 341, 256, 170, 128, 102, 85, 73, 64, 56, 51, 46, 42, 39, 36, 34, 32, 28, 25, 23, 21, 19, 18, 17, 16, 14, 12, 11, 10, 9, 8, 7, 6, 11, 5, 9, 4, 7, 3, 8, 5, 7, 2, 5, 3, 4, 5, 6, 7, 1, 6, 5, 4, 3, 2, 3, 4, 1, 4, 3, 2, 3, 1, 3, 2, 1}

var officialTailWaste = [67]int{0, 0, 8, 0, 32, 0, 32, 32, 16, 0, 128, 32, 96, 128, 80, 128, 32, 0, 128, 192, 96, 128, 288, 128, 32, 0, 128, 512, 448, 512, 128, 0, 128, 512, 896, 512, 256, 0, 256, 128, 0, 384, 384, 0, 256, 256, 0, 128, 256, 768, 0, 512, 512, 0, 128, 0, 256, 0, 0, 0, 128, 0, 256, 0, 128, 0, 0}

var officialMaxWasteBP = [67]int{8750, 4375, 2924, 2188, 3152, 2344, 1907, 1595, 1356, 1172, 1182, 973, 959, 925, 812, 815, 662, 586, 1216, 1180, 988, 951, 1071, 837, 682, 605, 1233, 1548, 1393, 1394, 1552, 1240, 1241, 1555, 1400, 1400, 1557, 1245, 1246, 1559, 1247, 622, 883, 1560, 1665, 1092, 1248, 623, 436, 337, 1561, 1428, 364, 499, 624, 1145, 999, 535, 1249, 1111, 357, 687, 625, 1145, 1000, 491, 1250}

var officialSizeToClass128 = []int{32, 33, 34, 35, 36, 37, 37, 38, 38, 39, 39, 40, 40, 40, 41, 41, 41, 42, 43, 43, 44, 44, 44, 44, 44, 45, 45, 45, 45, 45, 45, 46, 46, 46, 46, 47, 47, 47, 47, 47, 47, 48, 48, 48, 49, 49, 50, 51, 51, 51, 51, 51, 51, 51, 51, 51, 51, 52, 52, 52, 52, 52, 52, 52, 52, 52, 52, 53, 53, 54, 54, 54, 54, 55, 55, 55, 55, 55, 56, 56, 56, 56, 56, 56, 56, 56, 56, 56, 56, 57, 57, 57, 57, 57, 57, 57, 57, 57, 57, 58, 58, 58, 58, 58, 58, 59, 59, 59, 59, 59, 59, 59, 59, 59, 59, 59, 59, 59, 59, 59, 59, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60, 61, 61, 61, 61, 61, 62, 62, 62, 62, 62, 62, 62, 62, 62, 62, 62, 63, 63, 63, 63, 63, 63, 63, 63, 63, 63, 64, 64, 64, 64, 64, 64, 64, 64, 64, 64, 64, 64, 64, 64, 64, 64, 64, 64, 64, 64, 64, 64, 65, 65, 65, 65, 65, 65, 65, 65, 65, 65, 65, 65, 65, 65, 65, 65, 65, 65, 65, 65, 65, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67, 67}


var (
	statN    int
	statFail []string
)

// check 与 Python 侧 check() 同名同语义：失败不中止，最后统一汇总。
func check(label string, cond bool, detail ...interface{}) {
	statN++
	if cond {
		fmt.Println("PASS  " + label)
		return
	}
	statFail = append(statFail, label)
	if len(detail) > 0 {
		fmt.Printf("FAIL  %s  | %v
", label, detail[0])
		return
	}
	fmt.Println("FAIL  " + label)
}

func divRoundUp(a, b int) int { return (a + b - 1) / b }

// buildSizeToClass 由 classToSize 反推「字节数 → class」表（与 mksizeclasses.go 同逻辑）。
func buildSizeToClass(tableSize, div, base int) []int {
	out := make([]int, tableSize/div+1)
	for i := range out {
		want := base + i*div
		cls := 1
		for classToSize[cls] < want {
			cls++
		}
		out[i] = cls
	}
	return out
}

var (
	sizeToClass8   = buildSizeToClass(smallSizeMax, smallSizeDiv, 0)
	sizeToClass128 = buildSizeToClass(maxSmallSize-smallSizeMax, largeSizeDiv, smallSizeMax)
)

// sizeClassOf 返回 blk 字节落在哪个 size class（blk 必是表内元素）。
func sizeClassOf(blk int) int {
	for c, s := range classToSize {
		if s == blk {
			return c
		}
	}
	return -1
}

// roundupsize 复刻 runtime/msize.go 的 noscan 小对象路径；大对象按页取整。
func roundupsize(size int) int {
	if size <= maxSmallReq {
		if size <= smallSizeMax-smallSizeDiv {
			return classToSize[sizeToClass8[divRoundUp(size, smallSizeDiv)]]
		}
		return classToSize[sizeToClass128[divRoundUp(size-smallSizeMax, largeSizeDiv)]]
	}
	return divRoundUp(size, pageSize) * pageSize
}

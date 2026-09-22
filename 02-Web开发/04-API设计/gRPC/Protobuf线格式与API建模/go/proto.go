// 609 字段号治理 + gRPC 状态判定 —— Go 版（仅标准库），配套 protobuf.go。
//
// 口径来源（与 Python 版同一批实读资料）：
//   - protobuf.dev/programming-guides/proto3/：字段号 1..536870911，
//     19000~19999 保留给实现；1~15 的 tag 占 1 字节、16~2047 占 2 字节；
//     reserved 区间是闭区间且字段名与字段号不得混写。
//   - grpc.io/docs/guides/status-codes/：17 个码；其中 7 个从不由库生成；
//     UNAVAILABLE 只重试本调用、ABORTED 在更高层重试、FAILED_PRECONDITION 不重试。
//
// 运行：go run protobuf.go proto.go
package main

import "fmt"

const fieldMin = 1
const fieldMax = 536870911
const reservedLow = 19000
const reservedHigh = 19999

// ValidateFieldNumber proto3 指南的字段号约束。
func ValidateFieldNumber(number int) []string {
	out := []string{}
	if number < fieldMin {
		out = append(out, fmt.Sprintf("字段号 %d 小于下限 %d", number, fieldMin))
	}
	if number > fieldMax {
		out = append(out, fmt.Sprintf("字段号 %d 大于上限 %d", number, fieldMax))
	}
	if number >= reservedLow && number <= reservedHigh {
		out = append(out, fmt.Sprintf("字段号 %d 落在实现保留区间", number))
	}
	return out
}

// TagSizeBytes tag 的 varint 字节数。
func TagSizeBytes(field int) int { return len(TagBytes(field, WireVarint)) }

// ExpandReservedNumbers reserved 区间是**闭区间**。
func ExpandReservedNumbers(singles []int, ranges [][2]int) map[int]bool {
	out := map[int]bool{}
	for _, v := range singles {
		out[v] = true
	}
	for _, r := range ranges {
		for v := r[0]; v <= r[1]; v++ {
			out[v] = true
		}
	}
	return out
}

// RetryHint gRPC 的 (a)(b)(c) 三条判定准则。
func RetryHint(code string) string {
	switch code {
	case "UNAVAILABLE":
		return "retry-call"
	case "ABORTED":
		return "retry-higher-level"
	case "FAILED_PRECONDITION":
		return "no-retry"
	}
	return ""
}

// AppOnlyCode 库从不生成、只由用户代码产生的 7 个码。
func AppOnlyCode(code string) bool {
	switch code {
	case "INVALID_ARGUMENT", "NOT_FOUND", "ALREADY_EXISTS", "FAILED_PRECONDITION",
		"ABORTED", "OUT_OF_RANGE", "DATA_LOSS":
		return true
	}
	return false
}

func must4(cond bool, label string) {
	if !cond {
		panic("断言失败: " + label)
	}
}

func main() {
	must4(hexOf(VarintEncode(150)) == "96 01", "150 的 varint")
	must4(hexOf(VarintEncode(300)) == "ac 02", "300 的 varint")
	must4(len(VarintEncode(maxUint64)) == 10, "uint64 上限 10 字节")
	v, n, _ := VarintDecode([]byte{0x96, 0x01})
	must4(v == 150 && n == 2, "varint 解码")

	must4(ZigZagEncode(0, 32) == 0 && ZigZagEncode(-1, 32) == 1 &&
		ZigZagEncode(1, 32) == 2 && ZigZagEncode(-2, 32) == 3, "ZigZag 表")
	must4(ZigZagEncode(-500, 32) == 999, "sint32 -500 → 999")
	must4(ZigZagDecode(999) == -500, "ZigZag 还原")

	must4(hexOf(TagBytes(1, WireVarint)) == "08", "tag(1,VARINT)")
	must4(hexOf(TagBytes(2, WireLen)) == "12", "tag(2,LEN)")
	f, w := SplitTag(0x12)
	must4(f == 2 && w == WireLen, "拆 tag")

	must4(hexOf(EncodeVarintField(1, 150)) == "08 96 01", "Test1")
	must4(hexOf(EncodeStringField(2, "testing")) == "12 07 74 65 73 74 69 6e 67", "Test2")
	must4(hexOf(EncodeLenField(3, EncodeVarintField(1, 150))) == "1a 03 08 96 01", "Test3")
	t4 := append(EncodeStringField(4, "hello"), EncodePacked(5, []uint64{1, 2, 3})...)
	must4(hexOf(t4) == "22 05 68 65 6c 6c 6f 2a 03 01 02 03", "Test4")

	must4(len(EncodeVarintField(1, maxUint64-1)) == 11, "int64 -2 的补码占满 10 字节")
	must4(hexOf(EncodeFixed32Field(1, 0x01020304)) == "0d 04 03 02 01", "I32 小端")
	must4(hexOf(EncodeDoubleField(2, 1.5)) == "11 00 00 00 00 00 00 f8 3f", "double IEEE 754")

	must4(hexOf(EncodePacked(5, []uint64{1, 2, 3})) == "2a 03 01 02 03", "packed")
	concat := ConcatPacked([][]byte{{1, 2}, {3}})
	must4(len(concat) == 3 && concat[2] == 3, "多个 packed 键值对必须拼接")

	must4(len(GroupIssues(DecodeRecords(EncodeGroup(8, EncodeVarintField(1, 2))))) == 0,
		"配对 group 合法")
	bad := append(TagBytes(7, WireSGroup), EncodeVarintField(1, 2)...)
	bad = append(bad, TagBytes(8, WireEGroup)...)
	must4(len(GroupIssues(DecodeRecords(bad))) == 1, "字段号不配对应报错")

	must4(len(ValidateFieldNumber(1)) == 0, "字段号下界")
	must4(len(ValidateFieldNumber(fieldMax)) == 0, "字段号上界")
	must4(len(ValidateFieldNumber(reservedLow)) == 1, "19000 保留")
	must4(len(ValidateFieldNumber(0)) == 1, "0 非法")
	must4(TagSizeBytes(15) == 1 && TagSizeBytes(16) == 2, "tag 字节数分界")
	reserved := ExpandReservedNumbers([]int{2, 15}, [][2]int{{9, 11}})
	must4(reserved[9] && reserved[10] && reserved[11] && !reserved[12], "reserved 闭区间")

	must4(RetryHint("UNAVAILABLE") == "retry-call", "(a)")
	must4(RetryHint("ABORTED") == "retry-higher-level", "(b)")
	must4(RetryHint("FAILED_PRECONDITION") == "no-retry", "(c)")
	must4(AppOnlyCode("NOT_FOUND") && !AppOnlyCode("UNAVAILABLE"), "库从不生成的码")

	fmt.Println("Test4:", hexOf(t4))
	fmt.Println("packed concat:", concat)
	fmt.Println("all go assertions passed")
}

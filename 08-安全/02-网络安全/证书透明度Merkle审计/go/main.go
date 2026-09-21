package main

import (
	"bytes"
	"fmt"
)

func entries(prefix string, n int) [][]byte {
	out := make([][]byte, 0, n)
	for i := 0; i < n; i++ {
		out = append(out, []byte(fmt.Sprintf("%s%d", prefix, i)))
	}
	return out
}

func main() {
	D := entries("d", 7)
	a, b := leafHash(D[0]), leafHash(D[1])
	c, d := leafHash(D[2]), leafHash(D[3])
	e, f := leafHash(D[4]), leafHash(D[5])
	g := nodeHash(a, b)
	h := nodeHash(c, d)
	i := nodeHash(e, f)
	j := leafHash(D[6])
	k := nodeHash(g, h)
	l := nodeHash(i, j)
	root := nodeHash(k, l)

	fmt.Println("== 1. §2.1.5 的 7 叶树 ==")
	fmt.Printf("   MTH(D[0:4])==k : %v ; MTH(D[4:7])==l : %v\n",
		bytes.Equal(MTH(D[:4]), k), bytes.Equal(MTH(D[4:]), l))

	fmt.Println("\n== 2. 四个包含性证明（§2.1.5）==")
	for _, tc := range []struct {
		m      int
		expect [][]byte
		name   string
	}{
		{0, [][]byte{b, h, l}, "[b h l]"},
		{3, [][]byte{c, g, l}, "[c g l]"},
		{4, [][]byte{f, j, k}, "[f j k]"},
		{6, [][]byte{i, k}, "[i k]"},
	} {
		got := Path(tc.m, D)
		fmt.Printf("   PATH(%d, D7) 与文档 %s 一致: %v\n", tc.m, tc.name,
			len(got) == len(tc.expect) && bytes.Equal(bytes.Join(got, nil),
				bytes.Join(tc.expect, nil)))
	}

	fmt.Println("\n== 3. 包含性验证（§2.1.3.2）==")
	for m := 0; m < 7; m++ {
		okk, why := VerifyInclusion(m, 7, Path(m, D), D[m], root)
		fmt.Printf("   leaf %d -> %v %s\n", m, okk, why)
	}
	okk, why := VerifyInclusion(0, 7, [][]byte{b, l, h}, D[0], root)
	fmt.Printf("   负例 顺序颠倒 -> %v (%s)\n", okk, why)
	okk, why = VerifyInclusion(0, 7, [][]byte{b, h}, D[0], root)
	fmt.Printf("   负例 少一个节点 -> %v (%s)\n", okk, why)

	fmt.Println("\n== 4. 三个一致性证明（§2.1.5）==")
	t3, t4, t6, t7 := MTH(D[:3]), MTH(D[:4]), MTH(D[:6]), MTH(D)
	for _, tc := range []struct {
		m      int
		th     []byte
		expect int
	}{
		{3, t3, 4}, {4, t4, 1}, {6, t6, 3},
	} {
		pr := Proof(tc.m, D)
		okk, why := VerifyConsistency(tc.m, 7, pr, tc.th, t7)
		fmt.Printf("   PROOF(%d, D7) 节点数=%d 验证=%v %s\n", tc.m, len(pr), okk, why)
	}
	fmt.Printf("   节点数上界 ceil(log2(7))+1 = %d\n", ProofLengthBound(7))

	fmt.Println("\n== 5. 栈算法（§2.1.2）与递归定义对拍 ==")
	all := true
	for n := 0; n <= 20; n++ {
		ents := entries("s", n)
		if !bytes.Equal(MTHFromEntries(ents), MTH(ents)) {
			all = false
		}
	}
	fmt.Printf("   n=0..20 全部一致: %v\n", all)

	fmt.Println("\n== 6. 结构编码长度 ==")
	fmt.Printf("   TreeHeadDataV2       = %d 字节\n", len(EncodeTreeHead(0, 7, root)))
	fmt.Printf("   InclusionProofDataV2 = %d 字节\n",
		len(EncodeInclusionProof(bytes.Repeat([]byte{1}, 32), 7, 6, [][]byte{i, k})))
}

package main

import (
	"encoding/binary"
	"fmt"
)

// 本节演示 RFC 6962 §3.2 / §3.5 的定长字段布局（与 python/ct_struct.py 同题）。
const (
	versionV1        = 0
	sigTypeCertTS    = 0
	sigTypeTreeHash  = 1
	entryTypeX509    = 0
	entryTypePreCert = 1
)

func vector24(d []byte) []byte {
	n := len(d)
	return append([]byte{byte(n >> 16), byte(n >> 8), byte(n)}, d...)
}

func vector16(d []byte) []byte {
	return append([]byte{byte(len(d) >> 8), byte(len(d))}, d...)
}

func sthSigningInput(timestamp, treeSize uint64, root []byte) []byte {
	out := []byte{versionV1, sigTypeTreeHash}
	out = binary.BigEndian.AppendUint64(out, timestamp)
	out = binary.BigEndian.AppendUint64(out, treeSize)
	return append(out, root...)
}

func main() {
	fmt.Println("1. 7 叶 Merkle 树（RFC 6962 §2.1.3 示例结构）")
	ds := make([][]byte, 7)
	for i := range ds {
		ds[i] = []byte(fmt.Sprintf("cert-%d", i))
	}
	root := MTH(ds)
	fmt.Printf("   根: %x…\n", root[:16])
	for _, m := range []int{0, 3, 4, 6} {
		p := AuditPath(m, ds)
		r, err := RootFromAuditPath(leafHash(ds[m]), m, 7, p)
		same := err == nil && string(r) == string(root)
		fmt.Printf("   d%d 的审计路径 %d 个节点，复算根一致=%v\n", m, len(p), same)
	}

	fmt.Println()
	fmt.Println("2. 一致性证明")
	for _, old := range []int{3, 5, 7} {
		proof, err := ConsistencyProof(old, ds)
		if err != nil {
			fmt.Println("   err:", err)
			continue
		}
		ok, err := VerifyConsistency(old, 7, proof, MTH(ds[:old]), MTH(ds))
		fmt.Printf("   %d -> 7 叶：%d 个节点，验证=%v err=%v\n", old, len(proof), ok, err)
	}

	fmt.Println()
	fmt.Println("3. STH 签名输入（50 字节定长）")
	in := sthSigningInput(1700000000000, 7, root)
	fmt.Printf("   长度=%d  version=%d sig_type=%d tree_size=%d\n",
		len(in), in[0], in[1], binary.BigEndian.Uint64(in[10:18]))
	fmt.Printf("   SCT 的 entry 用 3 字节长度前缀: %d 字节；extensions 用 2 字节: %d 字节\n",
		len(vector24([]byte{0x30, 0x82, 0x01, 0x02})), len(vector16(nil)))
	_ = entryTypeX509
	_ = entryTypePreCert
	_ = sigTypeCertTS
}

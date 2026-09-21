// Package ctmerkle 实现 RFC 9162 §2.1 的 Merkle 树：MTH、包含性证明、
// 一致性证明，以及 §4.9/§4.12 的结构编码。
//
// 与 Python 版的差异显式落地：
//   - Python 的 bytes 拼接 -> Go 的 []byte append；
//   - Python 的整数右移在大整数上无符号 -> Go 用 uint64/int 显式位移；
//   - Python 返回 (bool, string) -> Go 返回 (bool, string) 多值。
package main

import (
	"bytes"
	"crypto/sha256"
)

var (
	leafPrefix = []byte{0x00}
	nodePrefix = []byte{0x01}
)

func hashIt(data []byte) []byte {
	s := sha256.Sum256(data)
	return s[:]
}

func hashSize() int { return sha256.Size }

// LargestPowerOfTwoBelow 返回严格小于 n 的最大 2 的幂：k < n <= 2k。
func LargestPowerOfTwoBelow(n int) int {
	k := 1
	for k*2 < n {
		k *= 2
	}
	return k
}

func leafHash(d []byte) []byte { return hashIt(append(append([]byte{}, leafPrefix...), d...)) }

func nodeHash(left, right []byte) []byte {
	b := append([]byte{}, nodePrefix...)
	b = append(b, left...)
	b = append(b, right...)
	return hashIt(b)
}

// MTH 是 §2.1.1 的递归定义。
func MTH(entries [][]byte) []byte {
	if len(entries) == 0 {
		return hashIt([]byte{})
	}
	if len(entries) == 1 {
		return leafHash(entries[0])
	}
	k := LargestPowerOfTwoBelow(len(entries))
	return nodeHash(MTH(entries[:k]), MTH(entries[k:]))
}

// MTHFromEntries 是 §2.1.2 的栈算法。
func MTHFromEntries(entries [][]byte) []byte {
	stack := [][]byte{}
	for i, e := range entries {
		stack = append(stack, leafHash(e))
		mergeCount := 0
		for (i>>mergeCount)&1 == 1 {
			mergeCount++
		}
		for j := 0; j < mergeCount; j++ {
			n := len(stack)
			right := stack[n-1]
			left := stack[n-2]
			stack = stack[:n-2]
			stack = append(stack, nodeHash(left, right))
		}
	}
	for len(stack) > 1 {
		n := len(stack)
		right := stack[n-1]
		left := stack[n-2]
		stack = stack[:n-2]
		stack = append(stack, nodeHash(left, right))
	}
	if len(stack) == 0 {
		return hashIt([]byte{})
	}
	return stack[0]
}

// Path 是 §2.1.3.1 的 PATH(m, D_n)。
func Path(m int, entries [][]byte) [][]byte {
	n := len(entries)
	if n == 1 {
		return [][]byte{}
	}
	k := LargestPowerOfTwoBelow(n)
	if m < k {
		return append(Path(m, entries[:k]), MTH(entries[k:]))
	}
	return append(Path(m-k, entries[k:]), MTH(entries[:k]))
}

// VerifyInclusion 是 §2.1.3.2 的六步验证。
func VerifyInclusion(leafIndex, treeSize int, pr [][]byte, leafData, rootHash []byte) (bool, string) {
	if leafIndex >= treeSize {
		return false, "leaf_index >= tree_size"
	}
	fn, sn := leafIndex, treeSize-1
	r := leafHash(leafData)
	for _, p := range pr {
		if sn == 0 {
			return false, "sn == 0 但证明还没走完"
		}
		if fn&1 == 1 || fn == sn {
			r = nodeHash(p, r)
			if fn&1 == 0 {
				for fn != 0 && fn&1 == 0 {
					fn >>= 1
					sn >>= 1
				}
			}
		} else {
			r = nodeHash(r, p)
		}
		fn >>= 1
		sn >>= 1
	}
	if sn != 0 {
		return false, "走完证明后 sn != 0"
	}
	if !bytes.Equal(r, rootHash) {
		return false, "根哈希不匹配"
	}
	return true, ""
}

// Subproof 是 §2.1.4.1 的 SUBPROOF(m, D_n, b)。
func Subproof(m int, entries [][]byte, b bool) [][]byte {
	n := len(entries)
	if m == n {
		if b {
			return [][]byte{}
		}
		return [][]byte{MTH(entries)}
	}
	k := LargestPowerOfTwoBelow(n)
	if m <= k {
		return append(Subproof(m, entries[:k], b), MTH(entries[k:]))
	}
	return append(Subproof(m-k, entries[k:], false), MTH(entries[:k]))
}

// Proof 是 §2.1.4.1 的 PROOF(m, D_n) = SUBPROOF(m, D_n, true)。
func Proof(m int, entries [][]byte) [][]byte { return Subproof(m, entries, true) }

// VerifyConsistency 是 §2.1.4.2 的七步验证。
func VerifyConsistency(first, second int, cp [][]byte, firstHash, secondHash []byte) (bool, string) {
	if len(cp) == 0 {
		return false, "consistency_path 为空"
	}
	pathList := append([][]byte{}, cp...)
	if first != 0 && first&(first-1) == 0 {
		pathList = append([][]byte{firstHash}, pathList...)
	}
	fn, sn := first-1, second-1
	for fn&1 == 1 {
		fn >>= 1
		sn >>= 1
	}
	fr, sr := pathList[0], pathList[0]
	for _, c := range pathList[1:] {
		if sn == 0 {
			return false, "sn == 0 但证明还没走完"
		}
		if fn&1 == 1 || fn == sn {
			fr = nodeHash(c, fr)
			sr = nodeHash(c, sr)
			if fn&1 == 0 {
				for fn != 0 && fn&1 == 0 {
					fn >>= 1
					sn >>= 1
				}
			}
		} else {
			sr = nodeHash(sr, c)
		}
		fn >>= 1
		sn >>= 1
	}
	if sn != 0 {
		return false, "走完证明后 sn != 0"
	}
	if !bytes.Equal(fr, firstHash) {
		return false, "fr != first_hash"
	}
	if !bytes.Equal(sr, secondHash) {
		return false, "sr != second_hash"
	}
	return true, ""
}

// EncodeTreeHead 是 §4.9 的 TreeHeadDataV2。
func EncodeTreeHead(timestamp, treeSize uint64, rootHash []byte) []byte {
	out := make([]byte, 0, 51)
	var b8 [8]byte
	for i := 0; i < 8; i++ {
		b8[i] = byte(timestamp >> (56 - 8*i))
	}
	out = append(out, b8[:]...)
	for i := 0; i < 8; i++ {
		b8[i] = byte(treeSize >> (56 - 8*i))
	}
	out = append(out, b8[:]...)
	out = append(out, byte(len(rootHash)))
	out = append(out, rootHash...)
	out = append(out, 0x00, 0x00) // sth_extensions 长度 0
	return out
}

// EncodeInclusionProof 是 §4.12 的 InclusionProofDataV2。
func EncodeInclusionProof(logID []byte, treeSize, leafIndex uint64, inc [][]byte) []byte {
	out := []byte{byte(len(logID))}
	out = append(out, logID...)
	var b8 [8]byte
	for i := 0; i < 8; i++ {
		b8[i] = byte(treeSize >> (56 - 8*i))
	}
	out = append(out, b8[:]...)
	for i := 0; i < 8; i++ {
		b8[i] = byte(leafIndex >> (56 - 8*i))
	}
	out = append(out, b8[:]...)
	out = append(out, byte(len(inc)))
	for _, n := range inc {
		out = append(out, n...)
	}
	return out
}

// ProofLengthBound 是 §2.1.4.1 末句的上界 ceil(log2(n)) + 1。
func ProofLengthBound(n int) int {
	if n <= 1 {
		return 1
	}
	k := 0
	for (1 << k) < n {
		k++
	}
	return k + 1
}

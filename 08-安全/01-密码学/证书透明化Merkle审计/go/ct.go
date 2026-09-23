// RFC 6962 §2.1 的 Merkle 树：MTH、审计路径、一致性证明（与 python/ct_merkle.py 同题）。

package main

import (
	"crypto/sha256"
	"errors"
)

const (
	leafPrefix = 0x00
	nodePrefix = 0x01
)

func sha(b []byte) []byte {
	s := sha256.Sum256(b)
	return s[:]
}

func leafHash(d []byte) []byte { return sha(append([]byte{leafPrefix}, d...)) }

func nodeHash(l, r []byte) []byte {
	return sha(append(append([]byte{nodePrefix}, l...), r...))
}

func emptyRoot() []byte { return sha(nil) }

// largestPowerOfTwoBelow 返回满足 k < n <= 2k 的最大 2 的幂。
func largestPowerOfTwoBelow(n int) int {
	k := 1
	for k*2 < n {
		k *= 2
	}
	return k
}

// MTH 是 Merkle Tree Hash 的递归定义（空树根 = SHA-256("")）。
func MTH(entries [][]byte) []byte {
	n := len(entries)
	switch {
	case n == 0:
		return emptyRoot()
	case n == 1:
		return leafHash(entries[0])
	}
	k := largestPowerOfTwoBelow(n)
	return nodeHash(MTH(entries[:k]), MTH(entries[k:]))
}

// AuditPath 是 PATH(m, D[n])。
func AuditPath(m int, entries [][]byte) [][]byte {
	n := len(entries)
	if n == 1 {
		return nil
	}
	k := largestPowerOfTwoBelow(n)
	if m < k {
		return append(AuditPath(m, entries[:k]), MTH(entries[k:]))
	}
	return append(AuditPath(m-k, entries[k:]), MTH(entries[:k]))
}

// RootFromAuditPath 按 PATH 的递归定义反向拼回根。
// 注意不能套用「看 m 的第 i 位决定左右」的口诀 —— 它在非满树时会拼反。
func RootFromAuditPath(leaf []byte, m, n int, path [][]byte) ([]byte, error) {
	if n == 1 {
		if len(path) != 0 {
			return nil, errors.New("单叶树的审计路径必须为空")
		}
		return leaf, nil
	}
	if len(path) == 0 {
		return nil, errors.New("审计路径长度不足")
	}
	k := largestPowerOfTwoBelow(n)
	if m < k {
		left, err := RootFromAuditPath(leaf, m, k, path[:len(path)-1])
		if err != nil {
			return nil, err
		}
		return nodeHash(left, path[len(path)-1]), nil
	}
	right, err := RootFromAuditPath(leaf, m-k, n-k, path[:len(path)-1])
	if err != nil {
		return nil, err
	}
	return nodeHash(path[len(path)-1], right), nil
}

// Subproof 是 SUBPROOF(m, D[n], b)。
func Subproof(m int, entries [][]byte, b bool) [][]byte {
	n := len(entries)
	if m == n {
		if b {
			return nil
		}
		return [][]byte{MTH(entries)}
	}
	k := largestPowerOfTwoBelow(n)
	if m <= k {
		return append(Subproof(m, entries[:k], b), MTH(entries[k:]))
	}
	return append(Subproof(m-k, entries[k:], false), MTH(entries[:k]))
}

// ConsistencyProof 是 PROOF(m, D[n]) = SUBPROOF(m, D[n], true)。
func ConsistencyProof(m int, entries [][]byte) ([][]byte, error) {
	if m <= 0 || m > len(entries) {
		return nil, errors.New("要求 0 < m <= n")
	}
	return Subproof(m, entries, true), nil
}

// VerifyConsistency 由证明同时复算旧根与新根（RFC 6962 只定义构造，验证算法留给实现）。
func VerifyConsistency(oldSize, newSize int, proof, oldRoot, newRoot []byte) (bool, error) {
	type pair struct {
		old []byte
		new []byte
	}
	var rec func(m, n int, proof [][]byte, b bool) (pair, error)
	rec = func(m, n int, proof [][]byte, b bool) (pair, error) {
		if m == n {
			if b {
				if len(proof) != 0 {
					return pair{}, errors.New("旧根已知时不应再有多余节点")
				}
				return pair{oldRoot, oldRoot}, nil
			}
			if len(proof) != 1 {
				return pair{}, errors.New("b=false 的边界情形应当只含一个节点")
			}
			return pair{proof[0], proof[0]}, nil
		}
		if len(proof) == 0 {
			return pair{}, errors.New("证明长度不足")
		}
		k := largestPowerOfTwoBelow(n)
		last, rest := proof[len(proof)-1], proof[:len(proof)-1]
		if m <= k {
			sub, err := rec(m, k, rest, b)
			if err != nil {
				return pair{}, err
			}
			return pair{sub.old, nodeHash(sub.new, last)}, nil
		}
		sub, err := rec(m-k, n-k, rest, false)
		if err != nil {
			return pair{}, err
		}
		return pair{nodeHash(last, sub.old), nodeHash(last, sub.new)}, nil
	}
	got, err := rec(oldSize, newSize, proof, true)
	if err != nil {
		return false, err
	}
	return string(got.old) == string(oldRoot) && string(got.new) == string(newRoot), nil
}

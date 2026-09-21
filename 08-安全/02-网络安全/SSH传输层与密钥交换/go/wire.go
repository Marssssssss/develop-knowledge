package main

import (
	"encoding/binary"
	"math/big"
)

// Mpint 是 RFC 4251 §5 的 mpint：二进制补码大端，去掉多余前导字节；
// 正数最高位为 1 时前置 0x00；0 编码为空串。
func Mpint(v *big.Int) []byte {
	if v.Sign() == 0 {
		return []byte{}
	}
	neg := v.Sign() < 0
	// 求能容纳 v 的最小字节数 n：-2^(8n-1) <= v < 2^(8n-1)
	n := 1
	for {
		lo := new(big.Int).Lsh(big.NewInt(1), uint(8*n-1))
		lo.Neg(lo)
		hi := new(big.Int).Lsh(big.NewInt(1), uint(8*n-1))
		if v.Cmp(lo) >= 0 && v.Cmp(hi) < 0 {
			break
		}
		n++
	}
	var buf []byte
	if neg {
		mod := new(big.Int).Lsh(big.NewInt(1), uint(8*n))
		buf = new(big.Int).Add(v, mod).Bytes()
	} else {
		buf = v.Bytes()
	}
	if len(buf) < n {
		pad := make([]byte, n-len(buf))
		if neg {
			for i := range pad {
				pad[i] = 0xFF
			}
		}
		buf = append(pad, buf...)
	}
	if !neg && buf[0]&0x80 != 0 {
		buf = append([]byte{0x00}, buf...)
	}
	return buf
}

func sshString(b []byte) []byte {
	out := make([]byte, 4)
	binary.BigEndian.PutUint32(out, uint32(len(b)))
	return append(out, b...)
}

func nameList(names []string) []byte {
	s := ""
	for i, n := range names {
		if i > 0 {
			s += ","
		}
		s += n
	}
	return sshString([]byte(s))
}

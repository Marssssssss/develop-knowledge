package main

// RFC 3394 AES Key Wrap + RFC 5649 AES Key Wrap with Padding。
// 与 Python 版 kw.py 逐行对应。

import (
	"encoding/binary"
	"errors"
)

const (
	defaultIV = uint64(0xA6A6A6A6A6A6A6A6) // RFC 3394 §2.2.3.1
	aivConst  = uint64(0xA65959A6)         // RFC 5649 §3
	maxMLI    = uint64(0xFFFFFFFF)
)

// ErrKeyWrap 表示解包时的完整性检查失败。
var ErrKeyWrap = errors.New("key wrap: integrity check failed")

func splitBlocks(data []byte) ([]uint64, error) {
	if len(data)%8 != 0 {
		return nil, errors.New("key wrap: input must be a multiple of 8 octets")
	}
	out := make([]uint64, len(data)/8)
	for i := range out {
		out[i] = binary.BigEndian.Uint64(data[8*i : 8*i+8])
	}
	return out, nil
}

func joinBlocks(v []uint64) []byte {
	out := make([]byte, 8*len(v))
	for i, x := range v {
		binary.BigEndian.PutUint64(out[8*i:8*i+8], x)
	}
	return out
}

// aesWrap RFC 3394 §2.2.1 索引式包装；明文至少 16 字节（n >= 2）。
func aesWrap(kek, plaintext []byte, iv uint64) ([]byte, error) {
	n := len(plaintext) / 8
	if len(plaintext)%8 != 0 || n < 2 {
		return nil, errors.New("key wrap: RFC 3394 requires n >= 2")
	}
	r, _ := splitBlocks(plaintext)
	a := iv
	buf := make([]byte, 16)
	for j := 0; j < 6; j++ {
		for i := 1; i <= n; i++ {
			binary.BigEndian.PutUint64(buf[:8], a)
			binary.BigEndian.PutUint64(buf[8:], r[i-1])
			b := encryptBlock(kek, buf)
			a = binary.BigEndian.Uint64(b[:8]) ^ uint64(n*j+i)
			r[i-1] = binary.BigEndian.Uint64(b[8:])
		}
	}
	return joinBlocks(append([]uint64{a}, r...)), nil
}

// aesUnwrap RFC 3394 §2.2.2 索引式解包。
func aesUnwrap(kek, ciphertext []byte, iv uint64) ([]byte, error) {
	blocks, err := splitBlocks(ciphertext)
	if err != nil {
		return nil, err
	}
	if len(blocks) < 3 {
		return nil, errors.New("key wrap: RFC 3394 ciphertext needs n+1 >= 3 blocks")
	}
	n := len(blocks) - 1
	a := blocks[0]
	r := blocks[1:]
	buf := make([]byte, 16)
	for j := 5; j >= 0; j-- {
		for i := n; i >= 1; i-- {
			binary.BigEndian.PutUint64(buf[:8], a^uint64(n*j+i))
			binary.BigEndian.PutUint64(buf[8:], r[i-1])
			b := decryptBlock(kek, buf)
			a = binary.BigEndian.Uint64(b[:8])
			r[i-1] = binary.BigEndian.Uint64(b[8:])
		}
	}
	if a != iv {
		return nil, ErrKeyWrap
	}
	return joinBlocks(r), nil
}

func aiv(m uint64) uint64 { return aivConst<<32 | m }

// kwpWrap RFC 5649 §4.1 扩展包装：1 .. 2^32-1 字节。
func kwpWrap(kek, plaintext []byte) ([]byte, error) {
	m := uint64(len(plaintext))
	if m == 0 || m > maxMLI {
		return nil, errors.New("key wrap: plaintext length out of range")
	}
	padded := make([]byte, (int(m)+7)/8*8)
	copy(padded, plaintext)
	n := len(padded) / 8
	if n == 1 {
		buf := make([]byte, 16)
		binary.BigEndian.PutUint64(buf[:8], aiv(m))
		copy(buf[8:], padded)
		return encryptBlock(kek, buf), nil
	}
	return aesWrap(kek, padded, aiv(m))
}

// unwrapCore 解包但不校验 A，返回 (A, padded)；RFC 5649 §4.2 第 1 步。
func unwrapCore(kek, ciphertext []byte) (uint64, []byte, error) {
	if len(ciphertext) < 16 || len(ciphertext)%8 != 0 {
		return 0, nil, ErrKeyWrap
	}
	n := len(ciphertext)/8 - 1
	if n == 1 {
		b := decryptBlock(kek, ciphertext)
		return binary.BigEndian.Uint64(b[:8]), b[8:], nil
	}
	blocks, err := splitBlocks(ciphertext)
	if err != nil {
		return 0, nil, err
	}
	a := blocks[0]
	r := blocks[1:]
	buf := make([]byte, 16)
	for j := 5; j >= 0; j-- {
		for i := n; i >= 1; i-- {
			binary.BigEndian.PutUint64(buf[:8], a^uint64(n*j+i))
			binary.BigEndian.PutUint64(buf[8:], r[i-1])
			b := decryptBlock(kek, buf)
			a = binary.BigEndian.Uint64(b[:8])
			r[i-1] = binary.BigEndian.Uint64(b[8:])
		}
	}
	return a, joinBlocks(r), nil
}

// kwpUnwrap RFC 5649 §4.2：先解包，再对 A 做三条 AIV 检查，最后去补齐。
func kwpUnwrap(kek, ciphertext []byte) ([]byte, error) {
	a, padded, err := unwrapCore(kek, ciphertext)
	if err != nil {
		return nil, err
	}
	n := uint64(len(padded) / 8)
	if a>>32 != aivConst {
		return nil, errors.New("key wrap: AIV check 1 failed: MSB(32,A) != A65959A6")
	}
	mli := a & 0xFFFFFFFF
	if !(8*(n-1) < mli && mli <= 8*n) {
		return nil, errors.New("key wrap: AIV check 2 failed: MLI out of range for n")
	}
	b := int(8*n - mli)
	for _, x := range padded[len(padded)-b:] {
		if x != 0 {
			return nil, errors.New("key wrap: AIV check 3 failed: padding octets not zero")
		}
	}
	return padded[:mli], nil
}

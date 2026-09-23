// JWE 用到的底层构件：RFC 3394 密钥包装 + A128CBC-HS256（RFC 7518 §5.2.2）。

package main

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/binary"
	"errors"
)

var defaultIV = []byte{0xa6, 0xa6, 0xa6, 0xa6, 0xa6, 0xa6, 0xa6, 0xa6}

// AESKeyWrap 是 RFC 3394 §2.2.1：6*n 轮，A 的初值是默认 IV A6A6A6A6A6A6A6A6。
func AESKeyWrap(kek, plain []byte) ([]byte, error) {
	block, err := aes.NewCipher(kek)
	if err != nil {
		return nil, err
	}
	n := len(plain) / 8
	a := binary.BigEndian.Uint64(defaultIV)
	r := make([]uint64, n)
	for i := 0; i < n; i++ {
		r[i] = binary.BigEndian.Uint64(plain[i*8 : i*8+8])
	}
	buf := make([]byte, 16)
	for j := 0; j < 6; j++ {
		for i := 1; i <= n; i++ {
			binary.BigEndian.PutUint64(buf[:8], a)
			binary.BigEndian.PutUint64(buf[8:], r[i-1])
			block.Encrypt(buf, buf)
			a = binary.BigEndian.Uint64(buf[:8]) ^ uint64(n*j+i)
			r[i-1] = binary.BigEndian.Uint64(buf[8:])
		}
	}
	out := make([]byte, 8*(n+1))
	binary.BigEndian.PutUint64(out[:8], a)
	for i := 0; i < n; i++ {
		binary.BigEndian.PutUint64(out[8+i*8:], r[i])
	}
	return out, nil
}

// AESKeyUnwrap 是 §2.2.2：倒着跑，最后必须回收到默认 IV，否则是完整性校验失败。
func AESKeyUnwrap(kek, wrapped []byte) ([]byte, error) {
	block, err := aes.NewCipher(kek)
	if err != nil {
		return nil, err
	}
	n := len(wrapped)/8 - 1
	a := binary.BigEndian.Uint64(wrapped[:8])
	r := make([]uint64, n)
	for i := 0; i < n; i++ {
		r[i] = binary.BigEndian.Uint64(wrapped[8+i*8 : 16+i*8])
	}
	buf := make([]byte, 16)
	for j := 5; j >= 0; j-- {
		for i := n; i >= 1; i-- {
			binary.BigEndian.PutUint64(buf[:8], a^uint64(n*j+i))
			binary.BigEndian.PutUint64(buf[8:], r[i-1])
			block.Decrypt(buf, buf)
			a = binary.BigEndian.Uint64(buf[:8])
			r[i-1] = binary.BigEndian.Uint64(buf[8:])
		}
	}
	if a != binary.BigEndian.Uint64(defaultIV) {
		return nil, errors.New("integrity check failed")
	}
	out := make([]byte, 8*n)
	for i := 0; i < n; i++ {
		binary.BigEndian.PutUint64(out[i*8:], r[i])
	}
	return out, nil
}

func bytesRepeat(n int) []byte {
	out := make([]byte, n)
	for i := range out {
		out[i] = byte(n)
	}
	return out
}

func pkcs7Pad(data []byte) []byte {
	n := 16 - len(data)%16
	return append(append([]byte(nil), data...), bytesRepeat(n)...)
}

func pkcs7Unpad(data []byte) ([]byte, error) {
	if len(data) == 0 || len(data)%16 != 0 {
		return nil, errors.New("bad padded length")
	}
	n := int(data[len(data)-1])
	if n == 0 || n > 16 {
		return nil, errors.New("bad padding")
	}
	for _, b := range data[len(data)-n:] {
		if int(b) != n {
			return nil, errors.New("bad padding")
		}
	}
	return data[:len(data)-n], nil
}

// A128CBCHS256Encrypt 对应 RFC 7518 §5.2.2.1：
// MAC_KEY = CEK 前 16 字节、ENC_KEY = 后 16 字节；
// AL 是 AAD 的**位**长大端 8 字节；HMAC 输入是 AAD‖IV‖CT‖AL。
func A128CBCHS256Encrypt(cek, iv, aad, plain []byte) ([]byte, []byte, error) {
	if len(cek) != 32 {
		return nil, nil, errors.New("A128CBC-HS256 needs a 256-bit CEK")
	}
	block, err := aes.NewCipher(cek[16:])
	if err != nil {
		return nil, nil, err
	}
	padded := pkcs7Pad(plain)
	ct := make([]byte, len(padded))
	cipher.NewCBCEncrypter(block, iv).CryptBlocks(ct, padded)
	al := make([]byte, 8)
	binary.BigEndian.PutUint64(al, uint64(len(aad)*8))
	m := hmac.New(sha256.New, cek[:16])
	m.Write(aad)
	m.Write(iv)
	m.Write(ct)
	m.Write(al)
	return ct, m.Sum(nil)[:16], nil
}

func A128CBCHS256Decrypt(cek, iv, aad, ct, tag []byte) ([]byte, error) {
	block, err := aes.NewCipher(cek[16:])
	if err != nil {
		return nil, err
	}
	al := make([]byte, 8)
	binary.BigEndian.PutUint64(al, uint64(len(aad)*8))
	m := hmac.New(sha256.New, cek[:16])
	m.Write(aad)
	m.Write(iv)
	m.Write(ct)
	m.Write(al)
	if !hmac.Equal(m.Sum(nil)[:16], tag) {
		return nil, errors.New("authentication tag mismatch")
	}
	out := make([]byte, len(ct))
	cipher.NewCBCDecrypter(block, iv).CryptBlocks(out, ct)
	return pkcs7Unpad(out)
}

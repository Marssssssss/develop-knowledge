// sae_group.go —— Dragonfly（WPA3-SAE）的群参数、KDF 与密码元素（RFC 7664 §2.2 / §3.2）
//
// 群：RFC 3526 §3 的 2048 位 MODP Group（id 14），p 是安全素数、G = 2、q = (p-1)/2。
// RFC 7664 把单向函数 H 与 KDF 留给上层协议，本仓库三个实现统一约定：
//
//	H   = SHA-256
//	KDF = HKDF-SHA256（Extract 用全零盐，Expand 用标签作为 info）
//
// 这两处「规范没规定」的地方正是跨语言实现最容易对不上的位置，所以三边都跑同一组向量。
package main

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"math/big"
)

// RFC 3526 §3（2048-bit MODP Group, id 14）
const pHex = "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74" +
	"020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B302B0A6DF25F1437" +
	"4FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED" +
	"EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF05" +
	"98DA48361C55D39A69163FA8FD24CF5F83655D23DCA3AD961C62F356208552BB" +
	"9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B" +
	"E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF695581718" +
	"3995497CEA956AE515D2261898FA051015728E5A8AACAA68FFFFFFFFFFFFFFFF"

const (
	pweLabel = "Dragonfly Hunting And Pecking"
	keyLabel = "Dragonfly Key Derivation"
	pBytes   = 256 // len(p) = 2048 位
)

var (
	p       = mustBig(pHex)
	one     = big.NewInt(1)
	two     = big.NewInt(2)
	pMinus1 = new(big.Int).Sub(p, one)
	q       = new(big.Int).Rsh(pMinus1, 1) // 子群阶 (p-1)/2
	g       = big.NewInt(2)
	expPeck = big.NewInt(2) // (p-1)/q，安全素数下恒为 2
)

func mustBig(hexStr string) *big.Int {
	v, ok := new(big.Int).SetString(hexStr, 16)
	if !ok {
		panic("非法的 16 进制大整数: " + hexStr)
	}
	return v
}

func qBytes() int { return (q.BitLen() + 7) / 8 }

func sha256Sum(data []byte) []byte {
	sum := sha256.Sum256(data)
	return sum[:]
}

func hmacSum(key, data []byte) []byte {
	mac := hmac.New(sha256.New, key)
	mac.Write(data)
	return mac.Sum(nil)
}

// hkdfExpand：T(i) = HMAC(PRK, T(i-1) | info | i)，与 Python/C 版逐位一致
func hkdfExpand(prk, info []byte, n int) []byte {
	out := make([]byte, 0, n)
	var t []byte
	for ctr := byte(1); len(out) < n; ctr++ {
		mac := hmac.New(sha256.New, prk)
		mac.Write(t)
		mac.Write(info)
		mac.Write([]byte{ctr})
		t = mac.Sum(nil)
		out = append(out, t...)
	}
	return out[:n]
}

// kdf：HKDF-SHA256，Extract 盐为全零，Expand 用标签作 info
func kdf(label string, ikm []byte, nBytes int) *big.Int {
	prk := hmacSum(make([]byte, sha256.Size), ikm)
	return new(big.Int).SetBytes(hkdfExpand(prk, []byte(label), nBytes))
}

// ------------------------------------------------------------ 群运算（FFC）

// scalarOp：Z = Y^x mod p
func scalarOp(x, y *big.Int) *big.Int { return new(big.Int).Exp(y, x, p) }

// elementOp：Z = X·Y mod p
func elementOp(x, y *big.Int) *big.Int { return new(big.Int).Mod(new(big.Int).Mul(x, y), p) }

// inverse：R · R^-1 mod p = 1（p 是素数，故必存在）
func inverse(r *big.Int) *big.Int { return new(big.Int).ModInverse(r, p) }

// isValidElement：1 < e < p-1 且 e^q mod p == 1（在阶为 q 的子群内）
func isValidElement(e *big.Int) bool {
	if e == nil || e.Cmp(one) <= 0 || e.Cmp(pMinus1) >= 0 {
		return false
	}
	return new(big.Int).Exp(e, q, p).Cmp(one) == 0
}

// inRange：1 < x < q（规范对 private / mask / 对端 scalar 的共同要求）
func inRange(x *big.Int) bool {
	return x != nil && x.Cmp(one) > 0 && x.Cmp(q) < 0
}

// ------------------------------------------------------------ 密码元素

// huntingAndPecking：base = H(max(A,B)|min(A,B)|password|counter) →
//
//	seed = KDF-(len(p)+64)(base) mod (p-1) + 1 → temp = seed^((p-1)/q) mod p
//
// 找到 PE 之后仍然继续跑到第 k 轮，掩盖真实迭代数（RFC 7664 §3.2 的抗侧信道要求）。
func huntingAndPecking(password, idA, idB []byte, k int) (*big.Int, int) {
	hi, lo := idA, idB
	if bytes.Compare(idA, idB) < 0 { // 身份用 max/min 排序，双方视角才能算出同一个 PE
		hi, lo = idB, idA
	}
	nBytes := (p.BitLen() + 64 + 7) / 8 // 多取 64 位，降低模约减的偏置
	buf := make([]byte, 0, len(hi)+len(lo)+len(password)+1)
	buf = append(buf, hi...)
	buf = append(buf, lo...)
	buf = append(buf, password...)
	var pe *big.Int
	for counter := 1; ; counter++ {
		base := append(buf, byte(counter)) // len(buf) 固定，append 不会越过 cap
		seed := new(big.Int).Mod(kdf(pweLabel, sha256Sum(base), nBytes), pMinus1)
		seed.Add(seed, one)
		temp := scalarOp(expPeck, seed)
		if temp.Cmp(one) > 0 && pe == nil {
			pe = temp
		}
		if pe != nil && counter >= k {
			return pe, counter
		}
	}
}

func derivePWE(password, idA, idB []byte, k int) *big.Int {
	pe, _ := huntingAndPecking(password, idA, idB, k)
	return pe
}

// ------------------------------------------------------------ 序列化与比较

func bytesToHex(b []byte) string { return hex.EncodeToString(b) }

// fillBytes 定宽大端序列化。变长拼接会让不同的 (标量, 元素) 组合撞出同一输入串，
// 所以这里是硬要求：值超出 width 时 FillBytes 会 panic，而不是悄悄截断。
func fillBytes(v *big.Int, width int) []byte {
	buf := make([]byte, width)
	v.FillBytes(buf)
	return buf
}

// bigDigest：定宽序列化后的 sha256 摘要 —— 跨语言比 2048 位常量太笨重
func bigDigest(v *big.Int, width int) string {
	return bytesToHex(sha256Sum(fillBytes(v, width)))
}

// bigEqHex：与 16 进制串比较（Text(16) 不带前导零）
func bigEqHex(v *big.Int, hexStr string) bool { return v.Text(16) == hexStr }

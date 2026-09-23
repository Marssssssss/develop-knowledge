package main

// ML-DSA 环运算，逐行对应 ref/reduce.c / ref/ntt.c / ref/rounding.c。

// MontgomeryReduce 返回 a*2^-32 mod Q，落在 (-Q, Q)。
// Go 的 int32 天然回绕，与 C 的 int32_t 语义一致（Python 版需要手写 _i32）。
func MontgomeryReduce(a int64) int32 {
	t := int32(int32(a) * QInv)
	return int32((a - int64(t)*Q) >> 32)
}

// Reduce32 折回 [-6283008, 6283008]。
func Reduce32(a int32) int32 {
	t := (a + (1 << 22)) >> 23
	return a - t*Q
}

// CAddQ 负数时加 Q。
func CAddQ(a int32) int32 {
	return a + ((a >> 31) & Q)
}

// Freeze 返回 [0, Q) 的标准代表元。
func Freeze(a int32) int32 {
	return CAddQ(Reduce32(a))
}

// NTT 前向变换，输出 bit-reversed 序；加减后不做归约。
func NTT(a [N]int32) [N]int32 {
	k := 0
	for length := 128; length > 0; length >>= 1 {
		for start := 0; start < N; start += 2 * length {
			k++
			zeta := Zetas[k]
			for j := start; j < start+length; j++ {
				t := MontgomeryReduce(int64(zeta) * int64(a[j+length]))
				a[j+length] = a[j] - t
				a[j] = a[j] + t
			}
		}
	}
	return a
}

// InvNTTToMont 逆变换并乘上 Montgomery 因子 2^32。
func InvNTTToMont(a [N]int32) [N]int32 {
	k := N
	for length := 1; length < N; length <<= 1 {
		for start := 0; start < N; start += 2 * length {
			k--
			zeta := -Zetas[k]
			for j := start; j < start+length; j++ {
				t := a[j]
				a[j] = t + a[j+length]
				a[j+length] = t - a[j+length]
				a[j+length] = MontgomeryReduce(int64(zeta) * int64(a[j+length]))
			}
		}
	}
	for j := 0; j < N; j++ {
		a[j] = MontgomeryReduce(int64(FInvNTT) * int64(a[j]))
	}
	return a
}

// PointwiseMontgomery 是 NTT 域上的逐点乘。
func PointwiseMontgomery(a, b [N]int32) [N]int32 {
	var out [N]int32
	for i := 0; i < N; i++ {
		out[i] = MontgomeryReduce(int64(a[i]) * int64(b[i]))
	}
	return out
}

// Power2Round 拆成 a = a1*2^D + a0。
func Power2Round(a int32) (int32, int32) {
	a1 := (a + (1 << (D - 1)) - 1) >> D
	return a1, a - (a1 << D)
}

// Decompose 拆成 a = a1*(2*gamma2) + a0 (mod Q)。
func Decompose(a, gamma2 int32) (int32, int32) {
	a1 := (a + 127) >> 7
	if gamma2 == (Q-1)/32 {
		a1 = (a1*1025 + (1 << 21)) >> 22
		a1 &= 15
	} else {
		a1 = (a1*11275 + (1 << 23)) >> 24
		// C: a1 ^= ((43 - a1) >> 31) & a1 —— a1 > 43 时清零
		a1 ^= ((43 - a1) >> 31) & a1
	}
	a0 := a - a1*2*gamma2
	a0 -= (((Q - 1) / 2 - a0) >> 31) & Q
	return a1, a0
}

// HighBits 是 FIPS 204 Algorithm 37。
func HighBits(a, gamma2 int32) int32 {
	a1, _ := Decompose(a, gamma2)
	return a1
}

// LowBits 是 FIPS 204 Algorithm 38。
func LowBits(a, gamma2 int32) int32 {
	_, a0 := Decompose(a, gamma2)
	return a0
}

// MakeHint 是 FIPS 204 Algorithm 39。
func MakeHint(a0, a1, gamma2 int32) int {
	if a0 > gamma2 || a0 < -gamma2 || (a0 == -gamma2 && a1 != 0) {
		return 1
	}
	return 0
}

// UseHint 是 FIPS 204 Algorithm 40。
func UseHint(a int32, hint int, gamma2 int32) int32 {
	a1, a0 := Decompose(a, gamma2)
	if hint == 0 {
		return a1
	}
	if gamma2 == (Q-1)/32 {
		if a0 > 0 {
			return (a1 + 1) & 15
		}
		return (a1 - 1) & 15
	}
	if a0 > 0 {
		if a1 == 43 {
			return 0
		}
		return a1 + 1
	}
	if a1 == 0 {
		return 43
	}
	return a1 - 1
}

// ChkNorm 是 ref/poly.c poly_chknorm：有任意 |c| >= bound 即返回 true。
func ChkNorm(a [N]int32, bound int32) bool {
	if bound > (Q-1)/8 {
		return true
	}
	for i := 0; i < N; i++ {
		t := a[i] >> 31
		t = a[i] - (t & 2 * a[i])
		if t >= bound {
			return true
		}
	}
	return false
}

// PackBits 把 256 个 bits 位宽的系数打成小端位流。
func PackBits(coeffs [N]int32, bits int) []byte {
	nbytes := (N * bits) / 8
	out := make([]byte, nbytes)
	for i := 0; i < N; i++ {
		v := uint64(coeffs[i]) & uint64((1<<bits)-1)
		bitpos := i * bits
		for b := 0; b < bits; b++ {
			if (v>>b)&1 == 1 {
				out[(bitpos+b)/8] |= 1 << uint((bitpos+b)%8)
			}
		}
	}
	return out
}

// UnpackBits 是 PackBits 的逆。
func UnpackBits(buf []byte, bits int) [N]int32 {
	var out [N]int32
	for i := 0; i < N; i++ {
		var v int64
		for b := 0; b < bits; b++ {
			pos := i*bits + b
			if (buf[pos/8]>>uint(pos%8))&1 == 1 {
				v |= 1 << b
			}
		}
		out[i] = int32(v)
	}
	return out
}

// PackHint 只记录 hint 为 1 的下标；末 k 字节记录每行用掉的槽数。
func PackHint(hints [][N]int32, omega, k int) []byte {
	out := make([]byte, omega+k)
	idx := 0
	for i, row := range hints {
		for pos, h := range row {
			if h != 0 && idx < omega {
				out[idx] = byte(pos)
				idx++
			}
		}
		out[omega+i] = byte(idx)
	}
	return out
}

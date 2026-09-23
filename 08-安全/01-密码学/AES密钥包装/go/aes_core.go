package main

// AES 分组密码（FIPS 197），支持 128/192/256 位密钥。
// 与 Python 版 aes_core.py 逐行对应：状态矩阵按列填充 state[r][c] = block[4*c+r]。

var rcon = [10]byte{0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36}

func xtime(a byte) byte {
	x := uint16(a) << 1
	if x&0x100 != 0 {
		x ^= 0x11b
	}
	return byte(x & 0xff)
}

func gmul(a, b byte) byte {
	var r byte
	for i := 0; i < 8; i++ {
		if b&1 != 0 {
			r ^= a
		}
		b >>= 1
		a = xtime(a)
	}
	return r
}

var sbox, invSbox = buildTables()

func buildTables() ([256]byte, [256]byte) {
	var s, inv [256]byte
	for x := 0; x < 256; x++ {
		if x == 0 {
			continue
		}
		for y := 1; y < 256; y++ {
			if gmul(byte(x), byte(y)) == 1 {
				inv[x] = byte(y)
				break
			}
		}
	}
	for x := 0; x < 256; x++ {
		b := inv[x]
		v := b
		for k := uint(1); k <= 4; k++ {
			v ^= (b << k) | (b >> (8 - k))
		}
		s[x] = v ^ 0x63
	}
	var is [256]byte
	for x := 0; x < 256; x++ {
		is[s[x]] = byte(x)
	}
	return s, is
}

// keyExpansion 返回轮密钥（每个元素 4 字节）与轮数 Nr。
func keyExpansion(key []byte) ([][4]byte, int) {
	nk := len(key) / 4
	if nk != 4 && nk != 6 && nk != 8 {
		panic("aes: key must be 16/24/32 bytes")
	}
	nr := nk + 6
	w := make([][4]byte, 4*(nr+1))
	for i := 0; i < nk; i++ {
		copy(w[i][:], key[4*i:4*i+4])
	}
	for i := nk; i < len(w); i++ {
		t := w[i-1]
		if i%nk == 0 {
			t = [4]byte{t[1], t[2], t[3], t[0]} // RotWord
			for j := 0; j < 4; j++ {
				t[j] = sbox[t[j]] // SubWord
			}
			t[0] ^= rcon[i/nk-1]
		} else if nk > 6 && i%nk == 4 {
			for j := 0; j < 4; j++ {
				t[j] = sbox[t[j]]
			}
		}
		for j := 0; j < 4; j++ {
			w[i][j] = w[i-nk][j] ^ t[j]
		}
	}
	return w, nr
}

type state [4][4]byte

func fromBlock(b []byte) *state {
	var s state
	for c := 0; c < 4; c++ {
		for r := 0; r < 4; r++ {
			s[r][c] = b[4*c+r]
		}
	}
	return &s
}

func (s *state) bytes() []byte {
	out := make([]byte, 16)
	for c := 0; c < 4; c++ {
		for r := 0; r < 4; r++ {
			out[4*c+r] = s[r][c]
		}
	}
	return out
}

func (s *state) addRoundKey(w [][4]byte, rnd int) {
	for c := 0; c < 4; c++ {
		for r := 0; r < 4; r++ {
			s[r][c] ^= w[rnd*4+c][r]
		}
	}
}

func (s *state) subBytes() {
	for r := 0; r < 4; r++ {
		for c := 0; c < 4; c++ {
			s[r][c] = sbox[s[r][c]]
		}
	}
}

func (s *state) invSubBytes() {
	for r := 0; r < 4; r++ {
		for c := 0; c < 4; c++ {
			s[r][c] = invSbox[s[r][c]]
		}
	}
}

func (s *state) shiftRows() {
	for r := 1; r < 4; r++ {
		var row [4]byte
		for c := 0; c < 4; c++ {
			row[c] = s[r][(c+r)%4]
		}
		s[r] = row
	}
}

func (s *state) invShiftRows() {
	for r := 1; r < 4; r++ {
		var row [4]byte
		for c := 0; c < 4; c++ {
			row[(c+r)%4] = s[r][c]
		}
		s[r] = row
	}
}

func (s *state) mixColumns() {
	for c := 0; c < 4; c++ {
		a0, a1, a2, a3 := s[0][c], s[1][c], s[2][c], s[3][c]
		s[0][c] = gmul(a0, 2) ^ gmul(a1, 3) ^ a2 ^ a3
		s[1][c] = a0 ^ gmul(a1, 2) ^ gmul(a2, 3) ^ a3
		s[2][c] = a0 ^ a1 ^ gmul(a2, 2) ^ gmul(a3, 3)
		s[3][c] = gmul(a0, 3) ^ a1 ^ a2 ^ gmul(a3, 2)
	}
}

func (s *state) invMixColumns() {
	for c := 0; c < 4; c++ {
		a0, a1, a2, a3 := s[0][c], s[1][c], s[2][c], s[3][c]
		s[0][c] = gmul(a0, 14) ^ gmul(a1, 11) ^ gmul(a2, 13) ^ gmul(a3, 9)
		s[1][c] = gmul(a0, 9) ^ gmul(a1, 14) ^ gmul(a2, 11) ^ gmul(a3, 13)
		s[2][c] = gmul(a0, 13) ^ gmul(a1, 9) ^ gmul(a2, 14) ^ gmul(a3, 11)
		s[3][c] = gmul(a0, 11) ^ gmul(a1, 13) ^ gmul(a2, 9) ^ gmul(a3, 14)
	}
}

func encryptBlock(key, block []byte) []byte {
	w, nr := keyExpansion(key)
	s := fromBlock(block)
	s.addRoundKey(w, 0)
	for rnd := 1; rnd < nr; rnd++ {
		s.subBytes()
		s.shiftRows()
		s.mixColumns()
		s.addRoundKey(w, rnd)
	}
	s.subBytes()
	s.shiftRows()
	s.addRoundKey(w, nr)
	return s.bytes()
}

func decryptBlock(key, block []byte) []byte {
	w, nr := keyExpansion(key)
	s := fromBlock(block)
	s.addRoundKey(w, nr)
	for rnd := nr - 1; rnd >= 1; rnd-- {
		s.invShiftRows()
		s.invSubBytes()
		s.addRoundKey(w, rnd)
		s.invMixColumns()
	}
	s.invShiftRows()
	s.invSubBytes()
	s.addRoundKey(w, 0)
	return s.bytes()
}

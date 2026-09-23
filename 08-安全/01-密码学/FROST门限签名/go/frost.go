package main

// FROST 两轮门限签名，逐行对应 RFC 9591 §4、§5 与附录 C。

import (
	"errors"
	"math/big"
)

var (
	errDup     = errors.New("invalid parameters: duplicate x-coordinate")
	errMissing = errors.New("invalid parameters: x_i not in L")
	errTooFew  = errors.New("invalid parameters: fewer than MIN_PARTICIPANTS shares")
)

// PolynomialEvaluate 用 Horner 法求值；coeffs[0] 是常数项（秘密）。
func PolynomialEvaluate(x *big.Int, coeffs []*big.Int) *big.Int {
	v := big.NewInt(0)
	q := Order()
	for i := len(coeffs) - 1; i >= 0; i-- {
		v.Mul(v, x)
		v.Add(v, coeffs[i])
		v.Mod(v, q)
	}
	return v
}

// SecretShareShard 把 s 分成 maxParticipants 份。
func SecretShareShard(s *big.Int, coefficients []*big.Int, maxParticipants int) ([][2]*big.Int, []*big.Int) {
	coeffs := append([]*big.Int{s}, coefficients...)
	out := make([][2]*big.Int, 0, maxParticipants)
	for i := 1; i <= maxParticipants; i++ {
		x := big.NewInt(int64(i))
		out = append(out, [2]*big.Int{x, PolynomialEvaluate(x, coeffs)})
	}
	return out, coeffs
}

// DeriveInterpolatingValue 是 §4.2 的拉格朗日系数。
func DeriveInterpolatingValue(L []*big.Int, xi *big.Int) (*big.Int, error) {
	found := false
	for _, x := range L {
		if x.Cmp(xi) == 0 {
			if found {
				return nil, errDup
			}
			found = true
		}
	}
	if !found {
		return nil, errMissing
	}
	num := big.NewInt(1)
	den := big.NewInt(1)
	q := Order()
	for _, xj := range L {
		if xj.Cmp(xi) == 0 {
			continue
		}
		num.Mul(num, xj)
		num.Mod(num, q)
		den.Mul(den, ScalarSub(xj, xi))
		den.Mod(den, q)
	}
	inv := new(big.Int).ModInverse(den, q)
	return new(big.Int).Mod(new(big.Int).Mul(num, inv), q), nil
}

// SecretShareCombine 用至少 minParticipants 份还原常数项。
func SecretShareCombine(shares [][2]*big.Int, minParticipants int) (*big.Int, error) {
	if len(shares) < minParticipants {
		return nil, errTooFew
	}
	xs := make([]*big.Int, 0, len(shares))
	for _, sh := range shares {
		xs = append(xs, sh[0])
	}
	total := big.NewInt(0)
	q := Order()
	for _, sh := range shares {
		lam, err := DeriveInterpolatingValue(xs, sh[0])
		if err != nil {
			return nil, err
		}
		total.Add(total, ScalarMul(sh[1], lam))
		total.Mod(total, q)
	}
	return total, nil
}

// Commitment 是 (identifier, hiding, binding) 三元组。
type Commitment struct {
	ID      *big.Int
	Hiding  *big.Int
	Binding *big.Int
}

// EncodeGroupCommitmentList 是 §4.3 的序列化。
func EncodeGroupCommitmentList(cl []Commitment) []byte {
	out := []byte{}
	for _, c := range cl {
		out = append(out, SerializeScalar(c.ID)...)
		out = append(out, SerializeElement(c.Hiding)...)
		out = append(out, SerializeElement(c.Binding)...)
	}
	return out
}

// ComputeBindingFactors 是 §4.4。
func ComputeBindingFactors(pk *big.Int, cl []Commitment, msg []byte) []*big.Int {
	prefix := append([]byte{}, SerializeElement(pk)...)
	prefix = append(prefix, H4(msg)...)
	prefix = append(prefix, H5(EncodeGroupCommitmentList(cl))...)
	out := make([]*big.Int, 0, len(cl))
	for _, c := range cl {
		rho := H1(append(append([]byte{}, prefix...), SerializeScalar(c.ID)...))
		out = append(out, rho)
	}
	return out
}

// ComputeGroupCommitment 是 §4.5：R = prod(D_i * E_i^rho_i)。
func ComputeGroupCommitment(cl []Commitment, bfl []*big.Int) *big.Int {
	r := big.NewInt(1)
	for i, c := range cl {
		r = ElementAdd(r, ScalarMult(c.Binding, bfl[i]))
		r = ElementAdd(r, c.Hiding)
	}
	return r
}

// ComputeChallenge 是 §4.6。
func ComputeChallenge(r, pk *big.Int, msg []byte) *big.Int {
	in := append([]byte{}, SerializeElement(r)...)
	in = append(in, SerializeElement(pk)...)
	in = append(in, msg...)
	return H2(in)
}

// Sign 是 §5.2 的份额生成：z_i = d_i + e_i*rho_i + lambda_i*sk_i*c。
func Sign(id, sk, pk, hiding, binding *big.Int, msg []byte, cl []Commitment) *big.Int {
	bfl := ComputeBindingFactors(pk, cl, msg)
	rho := bfl[0]
	lam := big.NewInt(1)
	for i, c := range cl {
		if c.ID.Cmp(id) == 0 {
			rho = bfl[i]
		}
	}
	xs := make([]*big.Int, 0, len(cl))
	for _, c := range cl {
		xs = append(xs, c.ID)
	}
	if l, err := DeriveInterpolatingValue(xs, id); err == nil {
		lam = l
	}
	r := ComputeGroupCommitment(cl, bfl)
	c := ComputeChallenge(r, pk, msg)
	z := new(big.Int).Mul(binding, rho)
	z.Add(z, hiding)
	z.Add(z, ScalarMul(ScalarMul(lam, sk), c))
	return z.Mod(z, Order())
}

// Aggregate 是 §5.3：只把 z 相加，R 重算。
func Aggregate(pk *big.Int, cl []Commitment, msg []byte, zs []*big.Int) (*big.Int, *big.Int) {
	bfl := ComputeBindingFactors(pk, cl, msg)
	r := ComputeGroupCommitment(cl, bfl)
	z := big.NewInt(0)
	for _, zi := range zs {
		z.Add(z, zi)
		z.Mod(z, Order())
	}
	return r, z
}

// SchnorrVerify 是附录 B：g^z == R * PK^c。
func SchnorrVerify(r, z, pk *big.Int, msg []byte) bool {
	c := ComputeChallenge(r, pk, msg)
	lhs := ScalarBaseMult(z)
	rhs := ElementAdd(r, ScalarMult(pk, c))
	return lhs.Cmp(rhs) == 0
}

// NaiveGroupCommitment 是不含绑定项的朴素 R = prod(D_i)。
func NaiveGroupCommitment(ds []*big.Int) *big.Int {
	r := big.NewInt(1)
	for _, d := range ds {
		r = ElementAdd(r, d)
	}
	return r
}

// RecoverSkFromReusedNonce 演示朴素方案 nonce 复用时的私钥恢复。
func RecoverSkFromReusedNonce(z1, z2, c1, c2 *big.Int) *big.Int {
	num := ScalarSub(z1, z2)
	den := ScalarSub(c1, c2)
	inv := new(big.Int).ModInverse(den, Order())
	return new(big.Int).Mod(new(big.Int).Mul(num, inv), Order())
}

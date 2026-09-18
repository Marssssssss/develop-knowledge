// sae_peer.go —— SAE 一端的完整状态机（提交 → 吸收 → 确认），对应 Python 的 SaePeer
//
// 三条规范 MUST：
//  1. scalar < 2、private/mask 不落在 (1, q) 内 → 必须重来
//  2. mask 用完立即销毁（它是唯一能把 Element 反推回 PE 的值）
//  3. 对端回送与自己完全相同的 scalar 与 Element 属于反射攻击，必须中止
package main

import (
	"crypto/subtle"
	"errors"
	"math/big"
)

var (
	errNoCommit   = errors.New("必须先 commit 再吸收对端提交")
	errReflection = errors.New("对端回送了相同的标量与元素：反射攻击")
	errBadScalar  = errors.New("标量不在 (1, q) 内")
	errBadElement = errors.New("元素不是合法的子群元素")
)

type saePeer struct {
	name, peerName         string
	pe                     *big.Int // 双方共享的密码元素
	priv, mask             *big.Int // private 全程保密；mask 在 commit 内销毁
	scalar, element        *big.Int // 发出去的两个值
	peerScalar, peerElement *big.Int
	ss, kck, mk            *big.Int
	iterations             int
	committed, haveKeys    bool
}

func newPeer(password, name, peerName string, k int) *saePeer {
	pe, iters := huntingAndPecking([]byte(password), []byte(name), []byte(peerName), k)
	return &saePeer{name: name, peerName: peerName, pe: pe, iterations: iters}
}

// commitWith 用**固定**标量提交。真实实现必须用密码学随机数取 (1, q) 内的 private/mask，
// 这里写死是为了让 Python / C / Go 三边输出可以逐位对照。
func (pr *saePeer) commitWith(privHex, maskHex string) error {
	priv, ok1 := new(big.Int).SetString(privHex, 16)
	mask, ok2 := new(big.Int).SetString(maskHex, 16)
	if !ok1 || !ok2 || !inRange(priv) || !inRange(mask) {
		return errBadScalar
	}
	pr.priv, pr.mask = priv, mask
	pr.scalar = new(big.Int).Mod(new(big.Int).Add(priv, mask), q) // (private + mask) mod q
	if pr.scalar.Cmp(two) < 0 {
		return errBadScalar
	}
	pr.element = inverse(scalarOp(mask, pr.pe)) // = PE^(-mask) mod p
	pr.mask = nil                               // MUST：mask 用完立即销毁
	pr.committed = true
	return nil
}

// absorb 校验并吸收对端提交，随后推导 ss / kck / mk
func (pr *saePeer) absorb(peer *saePeer) error {
	if !pr.committed {
		return errNoCommit
	}
	if peer.scalar == nil || peer.element == nil {
		return errBadScalar
	}
	if peer.scalar.Cmp(pr.scalar) == 0 && peer.element.Cmp(pr.element) == 0 {
		return errReflection
	}
	if !inRange(peer.scalar) {
		return errBadScalar
	}
	if !isValidElement(peer.element) {
		return errBadElement
	}
	pr.peerScalar, pr.peerElement = peer.scalar, peer.element
	// ss = (peer-Element · PE^peer-scalar)^private mod p
	inner := elementOp(peer.element, scalarOp(peer.scalar, pr.pe))
	pr.ss = scalarOp(pr.priv, inner)
	// kck | mk = KDF(ss, "Dragonfly Key Derivation")，各 len(p) 位
	material := kdf(keyLabel, fillBytes(pr.ss, pBytes), 2*pBytes)
	shift := uint(p.BitLen())
	pr.kck = new(big.Int).Rsh(material, shift)
	pr.mk = new(big.Int).And(material, new(big.Int).Sub(new(big.Int).Lsh(one, shift), one))
	pr.haveKeys = true
	return nil
}

// confirmValue：H(kck | 发送方标量 | 接收方标量 | 发送方元素 | 接收方元素 | 发送方标识)
//
// 四个数值一律定宽大端 —— 变长拼接会让不同组合撞出同一个输入串。
func (pr *saePeer) confirmValue(scalar, peerScalar, element, peerElement *big.Int,
	sender string) []byte {
	buf := make([]byte, 0, 3*pBytes+2*qBytes()+len(sender))
	buf = append(buf, fillBytes(pr.kck, pBytes)...)
	buf = append(buf, fillBytes(scalar, qBytes())...)
	buf = append(buf, fillBytes(peerScalar, qBytes())...)
	buf = append(buf, fillBytes(element, pBytes)...)
	buf = append(buf, fillBytes(peerElement, pBytes)...)
	buf = append(buf, sender...)
	return sha256Sum(buf)
}

// selfConfirm 自己要发出的确认值（发送方视角：自己的标量/元素在前）
func (pr *saePeer) selfConfirm() []byte {
	if !pr.haveKeys {
		return nil
	}
	return pr.confirmValue(pr.scalar, pr.peerScalar, pr.element, pr.peerElement, pr.name)
}

// expectedPeerConfirm 对端**应该**发出的确认值。
//
// 顺序是「发送方在前、接收方在后」，而这里的发送方是**对端**，所以标量/元素的先后
// 与自己 confirm 时正好相反 —— 把它写成「与自己 selfConfirm 比较」是最容易犯的错
// （Python 版就是这么被自检抓出来的，见 README「注意事项」）。
func (pr *saePeer) expectedPeerConfirm() []byte {
	if !pr.haveKeys {
		return nil
	}
	return pr.confirmValue(pr.peerScalar, pr.scalar, pr.peerElement, pr.element,
		pr.peerName)
}

// verify 用常数时间比较：确认值比对不能按字节提前返回，否则泄漏前缀匹配长度
func (pr *saePeer) verify(peerConfirm []byte) bool {
	want := pr.expectedPeerConfirm()
	return want != nil && subtle.ConstantTimeCompare(want, peerConfirm) == 1
}

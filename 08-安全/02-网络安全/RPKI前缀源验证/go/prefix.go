// prefix.go —— ROA / VRP / RFC 6811 源验证（基于标准库 net/netip）
//
// 这里刻意把前缀运算交给 net/netip：它已经实现了地址族区分、位比较与主机位清零
// （Masked()），正好是 ROA 里 BIT STRING 需要的形态。要小心的只有两点：
//   - Prefix.Overlaps() 对「互相覆盖」两个方向都返回 true，判断 Covered 时必须
//     自己再加上「VRP 前缀不更长」的条件；
//   - 不同地址族的前缀 Overlaps 恒为 false，所以跨族路由自然落到 NotFound。
package main

import (
	"fmt"
	"net/netip"
)

// RFC 6811 §2 的三个验证状态
type pfState string

const (
	stateNotFound pfState = "NotFound"
	stateValid    pfState = "Valid"
	stateInvalid  pfState = "Invalid"
)

type roaAddress struct {
	prefix    netip.Prefix
	maxLength int
	hasMax    bool
}

type roa struct {
	version   int
	asn       uint32
	addresses []roaAddress
}

type vrp struct {
	prefix    netip.Prefix
	maxLength int
	asn       uint32
}

// problems 检查 RFC 6482 对字段取值的硬约束
func (r roa) problems() []string {
	var issues []string
	if r.version != 0 {
		issues = append(issues, fmt.Sprintf("version 必须为 0，实际 %d", r.version))
	}
	if len(r.addresses) == 0 {
		issues = append(issues, "ipAddrBlocks 不能为空")
	}
	for _, a := range r.addresses {
		bits := a.prefix.Addr().BitLen()
		if !a.hasMax {
			continue
		}
		if a.maxLength < a.prefix.Bits() {
			issues = append(issues, fmt.Sprintf("%s 的 maxLength 小于前缀长度", a.prefix))
		}
		if a.maxLength > bits {
			issues = append(issues, fmt.Sprintf("%s 的 maxLength 超过 AFI 位宽 %d",
				a.prefix, bits))
		}
	}
	return issues
}

func (a roaAddress) effectiveMaxLength() int {
	if a.hasMax {
		return a.maxLength
	}
	return a.prefix.Bits() // 缺省只授权精确前缀（RFC 6482 §3.3）
}

// toVRPs 一个 ROAIPAddress 展开成恰好一条 VRP
func (r roa) toVRPs() ([]vrp, error) {
	if bad := r.problems(); len(bad) > 0 {
		return nil, fmt.Errorf("非法 ROA：%v", bad)
	}
	out := make([]vrp, 0, len(r.addresses))
	for _, a := range r.addresses {
		out = append(out, vrp{a.prefix, a.effectiveMaxLength(), r.asn})
	}
	return out, nil
}

// covered 实现 RFC 6811 §2 的 Covered
func covered(v vrp, route netip.Prefix) bool {
	return v.prefix.Bits() <= route.Bits() && v.prefix.Overlaps(route)
}

// validate 实现 RFC 6811 §2.1：hasOrigin=false 表示规范里的 NONE
func validate(vrps []vrp, route netip.Prefix, origin uint32, hasOrigin bool) pfState {
	coveredBy := false
	for _, v := range vrps {
		if !covered(v, route) {
			continue
		}
		coveredBy = true
		if route.Bits() <= v.maxLength && hasOrigin && origin != 0 && v.asn != 0 &&
			origin == v.asn {
			return stateValid
		}
	}
	if coveredBy {
		return stateInvalid
	}
	return stateNotFound
}

// ------------------------------------------------------------ 源 AS 推导

const (
	asSet            = 1
	asSequence       = 2
	asConfedSequence = 3
	asConfedSet      = 4
)

type asPathSegment struct {
	segType int
	asns    []uint32
}

// originASN 按 RFC 6811 §2：由**最后一个段的类型**决定，ok=false 表示 NONE
func originASN(segments []asPathSegment, local uint32) (uint32, bool) {
	if len(segments) == 0 {
		return local, true
	}
	last := segments[len(segments)-1]
	switch last.segType {
	case asConfedSequence, asConfedSet:
		return local, true
	case asSequence:
		if len(last.asns) == 0 {
			return 0, false
		}
		return last.asns[len(last.asns)-1], true
	default:
		return 0, false
	}
}

// ------------------------------------------------------------ DER 辅助

// bitStringContent 给出 ROA 里 IPAddress(BIT STRING) 的内容：
// 第 0 字节是未使用位数，其后是左对齐的前缀字节。
// netip 的 Masked() 会把主机位清零，所以 As4()/As16() 正好是可用的左对齐字节。
func bitStringContent(p netip.Prefix) []byte {
	masked := p.Masked()
	width := masked.Addr().BitLen()
	var raw []byte
	if width == 32 {
		b := masked.Addr().As4()
		raw = b[:]
	} else {
		b := masked.Addr().As16()
		raw = b[:]
	}
	nbytes := (p.Bits() + 7) / 8
	out := make([]byte, 0, nbytes+1)
	out = append(out, byte(8*nbytes-p.Bits()))
	return append(out, raw[:nbytes]...)
}

// encodeOID 编码 OID 的分量序列（前两个分量合并成 40a+b），各分量 base-128
func encodeOID(arcs []uint32) []byte {
	if len(arcs) < 2 || arcs[0] > 2 {
		return nil
	}
	var out []byte
	for i := 0; i < len(arcs); i++ {
		if i == 1 {
			continue
		}
		value := uint64(arcs[i])
		if i == 0 {
			value = uint64(arcs[0])*40 + uint64(arcs[1])
		}
		var tmp []byte
		tmp = append(tmp, byte(value&0x7f))
		value >>= 7
		for value > 0 {
			tmp = append(tmp, byte(0x80|(value&0x7f)))
			value >>= 7
		}
		for j := len(tmp) - 1; j >= 0; j-- {
			out = append(out, tmp[j])
		}
	}
	return out
}

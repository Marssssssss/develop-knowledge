// CNI host-local IPAM：Range / RangeSet 的规范化与包含判定（Python 模型的 Go 复刻）。
//
// 语义对照 containernetworking/plugins：
//
//	plugins/ipam/host-local/backend/allocator/range.go
//	plugins/ipam/host-local/backend/allocator/range_set.go
package main

import (
	"bytes"
	"fmt"
	"strings"
)

// ipToInt 把字节序列当作大端整数。
func ipToInt(ip []byte) uint64 {
	var n uint64
	for _, b := range ip {
		n = (n << 8) | uint64(b)
	}
	return n
}

// intToIP 生成 length 字节的大端表示（小整数落在末字节）。
func intToIP(n uint64, length int) []byte {
	out := make([]byte, length)
	for i := 0; i < length; i++ {
		out[i] = byte(n >> (8 * uint(length-1-i)))
	}
	return out
}

func ipStr(ip []byte) string {
	if len(ip) == 4 {
		return fmt.Sprintf("%d.%d.%d.%d", ip[0], ip[1], ip[2], ip[3])
	}
	parts := make([]string, 0, len(ip))
	for _, b := range ip {
		parts = append(parts, fmt.Sprintf("%02x", b))
	}
	return strings.Join(parts, ":")
}

func parseV4(s string) []byte {
	out := make([]byte, 4)
	fmt.Sscanf(s, "%d.%d.%d.%d", &out[0], &out[1], &out[2], &out[3])
	return out
}

func maskOf(prefix, length int) []byte {
	total := uint(length * 8)
	mask := uint64(0)
	if prefix > 0 {
		mask = ^uint64(0) << (total - uint(prefix))
	}
	return intToIP(mask, length)
}

func canonicalizeIP(ip []byte) ([]byte, error) {
	if len(ip) == 4 || len(ip) == 16 {
		return ip, nil
	}
	return nil, fmt.Errorf("IP not v4 nor v6")
}

func applyMask(ip, mask []byte) []byte {
	out := make([]byte, len(ip))
	for i := range ip {
		out[i] = ip[i] & mask[i]
	}
	return out
}

func nextIP(ip []byte) []byte {
	return intToIP(ipToInt(ip)+1, len(ip))
}

// lastIP 取 ip|^mask（广播地址），v4 再末字节减 1 排除广播。
func lastIP(subnetIP, subnetMask []byte) []byte {
	end := make([]byte, len(subnetIP))
	for i := range subnetIP {
		end[i] = subnetIP[i] | (subnetMask[i] ^ 0xFF)
	}
	if len(end) == 4 {
		end[3]--
	}
	return end
}

func cmpIP(a, b []byte) int {
	ia, ib := ipToInt(a), ipToInt(b)
	switch {
	case ia < ib:
		return -1
	case ia > ib:
		return 1
	}
	return 0
}

func ipEq(a, b []byte) bool { return bytes.Equal(a, b) }

// Range 对应 Go 的 allocator.Range。
type Range struct {
	SubnetIP   []byte
	SubnetMask []byte
	RangeStart []byte
	RangeEnd   []byte
	Gateway    []byte
}

func NewRange(subnet string, gateway, start, end string) *Range {
	parts := strings.SplitN(subnet, "/", 2)
	prefix := 0
	fmt.Sscanf(parts[1], "%d", &prefix)
	mask := maskOf(prefix, 4)
	r := &Range{SubnetIP: parseV4(parts[0]), SubnetMask: mask}
	if gateway != "" {
		r.Gateway = parseV4(gateway)
	}
	if start != "" {
		r.RangeStart = parseV4(start)
	}
	if end != "" {
		r.RangeEnd = parseV4(end)
	}
	return r
}

func (r *Range) PrefixLen() int {
	n := 0
	for _, b := range r.SubnetMask {
		for i := 7; i >= 0; i-- {
			if b&(1<<uint(i)) != 0 {
				n++
			}
		}
	}
	return n
}

func (r *Range) SubnetStr() string {
	return fmt.Sprintf("%s/%d", ipStr(r.SubnetIP), r.PrefixLen())
}

// Canonicalize 对应 Range.Canonicalize()。
func (r *Range) Canonicalize() error {
	ip, err := canonicalizeIP(r.SubnetIP)
	if err != nil {
		return err
	}
	r.SubnetIP = ip
	ones, masklen := r.PrefixLen(), len(r.SubnetIP)*8
	if ones > masklen-2 {
		return fmt.Errorf("Network %s too small to allocate from", r.SubnetStr())
	}
	if len(r.SubnetIP) != len(r.SubnetMask) {
		return fmt.Errorf("IPNet IP and Mask version mismatch")
	}
	networkIP := applyMask(r.SubnetIP, r.SubnetMask)
	if !ipEq(r.SubnetIP, networkIP) {
		return fmt.Errorf("Network has host bits set. For a subnet mask of length %d the network address is %s", ones, ipStr(networkIP))
	}
	if r.Gateway == nil {
		r.Gateway = nextIP(r.SubnetIP)
	} else if _, err := canonicalizeIP(r.Gateway); err != nil {
		return err
	}
	if r.RangeStart != nil {
		if _, err := canonicalizeIP(r.RangeStart); err != nil {
			return err
		}
		if !r.Contains(r.RangeStart) {
			return fmt.Errorf("RangeStart %s not in network %s", ipStr(r.RangeStart), r.SubnetStr())
		}
	} else {
		r.RangeStart = nextIP(r.SubnetIP)
	}
	if r.RangeEnd != nil {
		if _, err := canonicalizeIP(r.RangeEnd); err != nil {
			return err
		}
		if !r.Contains(r.RangeEnd) {
			return fmt.Errorf("RangeEnd %s not in network %s", ipStr(r.RangeEnd), r.SubnetStr())
		}
	} else {
		r.RangeEnd = lastIP(r.SubnetIP, r.SubnetMask)
	}
	return nil
}

// Contains 对应 Range.Contains()：nil 的边界直接忽略。
func (r *Range) Contains(addr []byte) bool {
	if _, err := canonicalizeIP(addr); err != nil {
		return false
	}
	if len(addr) != len(r.SubnetIP) {
		return false
	}
	if !ipEq(applyMask(addr, r.SubnetMask), r.SubnetIP) {
		return false
	}
	if r.RangeStart != nil && cmpIP(addr, r.RangeStart) < 0 {
		return false
	}
	if r.RangeEnd != nil && cmpIP(addr, r.RangeEnd) > 0 {
		return false
	}
	return true
}

func (r *Range) Overlaps(o *Range) bool {
	if len(r.RangeStart) != len(o.RangeStart) {
		return false
	}
	return r.Contains(o.RangeStart) || r.Contains(o.RangeEnd) ||
		o.Contains(r.RangeStart) || o.Contains(r.RangeEnd)
}

func (r *Range) String() string {
	return fmt.Sprintf("%s-%s", ipStr(r.RangeStart), ipStr(r.RangeEnd))
}

// RangeSet 对应 Go 的 allocator.RangeSet。
type RangeSet struct{ Ranges []*Range }

func (s *RangeSet) Len() int            { return len(s.Ranges) }
func (s *RangeSet) Get(i int) *Range    { return s.Ranges[i] }

func (s *RangeSet) RangeFor(addr []byte) (*Range, error) {
	if _, err := canonicalizeIP(addr); err != nil {
		return nil, err
	}
	for _, r := range s.Ranges {
		if r.Contains(addr) {
			return r, nil
		}
	}
	return nil, fmt.Errorf("%s not in range set %s", ipStr(addr), s)
}

func (s *RangeSet) Contains(addr []byte) bool {
	r, _ := s.RangeFor(addr)
	return r != nil
}

func (s *RangeSet) Canonicalize() error {
	if len(s.Ranges) == 0 {
		return fmt.Errorf("empty range set")
	}
	fam := 0
	for i := range s.Ranges {
		if err := s.Ranges[i].Canonicalize(); err != nil {
			return err
		}
		if i == 0 {
			fam = len(s.Ranges[i].RangeStart)
		} else if fam != len(s.Ranges[i].RangeStart) {
			return fmt.Errorf("mixed address families")
		}
	}
	n := len(s.Ranges)
	for i, r1 := range s.Ranges[:n-1] {
		for _, r2 := range s.Ranges[i+1:] {
			if r1.Overlaps(r2) {
				return fmt.Errorf("subnets %s and %s overlap", r1, r2)
			}
		}
	}
	return nil
}

func (s *RangeSet) String() string {
	out := make([]string, 0, len(s.Ranges))
	for _, r := range s.Ranges {
		out = append(out, r.String())
	}
	return strings.Join(out, ",")
}

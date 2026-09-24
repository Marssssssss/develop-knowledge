// CNI host-local 轮询分配器与运行时链式执行（Python 模型的 Go 复刻）。
//
// 语义对照：
//
//	plugins/ipam/host-local/backend/allocator/allocator.go （Get / GetIter / Next）
//	containernetworking/cni SPEC.md §2 执行协议 与 §3 生命周期与顺序
package main

import "fmt"

// Store 是 backend.Store 的最小模型。
type Store struct {
	Reserved     map[string]string // ip -> containerID
	LastReserved map[string]string // rangeID -> ip
}

func NewStore() *Store {
	return &Store{Reserved: map[string]string{}, LastReserved: map[string]string{}}
}

func (s *Store) Reserve(cid, ip, rangeID string) bool {
	if _, taken := s.Reserved[ip]; taken {
		return false
	}
	s.Reserved[ip] = cid
	s.LastReserved[rangeID] = ip
	return true
}

func (s *Store) GetByID(cid string) []string {
	var out []string
	for ip, who := range s.Reserved {
		if who == cid {
			out = append(out, ip)
		}
	}
	return out
}

func (s *Store) ReleaseByID(cid string) {
	for ip, who := range s.Reserved {
		if who == cid {
			delete(s.Reserved, ip)
		}
	}
}

func (s *Store) LastReservedIP(rangeID string) string {
	return s.LastReserved[rangeID]
}

// RangeIter 对应 Go 的 RangeIter：跨 range 轮询，起点是 lastReservedIP。
type RangeIter struct {
	Rangeset *RangeSet
	RangeIdx int
	Cur      []byte
	StartIP  []byte
}

func (i *RangeIter) Next() ([]byte, []byte) {
	r := i.Rangeset.Get(i.RangeIdx)
	if i.Cur == nil {
		i.Cur = r.RangeStart
		i.StartIP = i.Cur
		if ipEq(i.Cur, r.Gateway) {
			return i.Next()
		}
		return i.Cur, r.Gateway
	}
	if ipEq(i.Cur, r.RangeEnd) {
		i.RangeIdx = (i.RangeIdx + 1) % i.Rangeset.Len()
		r = i.Rangeset.Get(i.RangeIdx)
		i.Cur = r.RangeStart
	} else {
		i.Cur = nextIP(i.Cur)
	}
	if i.StartIP == nil {
		i.StartIP = i.Cur
	} else if ipEq(i.Cur, i.StartIP) {
		return nil, nil
	}
	if ipEq(i.Cur, r.Gateway) {
		return i.Next()
	}
	return i.Cur, r.Gateway
}

// IPAllocator 对应 Go 的 IPAllocator。
type IPAllocator struct {
	Rangeset *RangeSet
	Store    *Store
	RangeID  string
}

func (a *IPAllocator) GetIter() *RangeIter {
	last := a.Store.LastReservedIP(a.RangeID)
	if last != "" && a.Rangeset.Contains(parseV4(last)) {
		lip := parseV4(last)
		for i, r := range a.Rangeset.Ranges {
			if r.Contains(lip) {
				return &RangeIter{Rangeset: a.Rangeset, RangeIdx: i, Cur: lip}
			}
		}
	}
	return &RangeIter{Rangeset: a.Rangeset, RangeIdx: 0,
		StartIP: a.Rangeset.Get(0).RangeStart}
}

func (a *IPAllocator) Get(cid string, requested []byte) (string, string, error) {
	if requested != nil {
		r, err := a.Rangeset.RangeFor(requested)
		if err != nil {
			return "", "", err
		}
		if ipEq(requested, r.Gateway) {
			return "", "", fmt.Errorf("requested ip %s is subnet's gateway", ipStr(requested))
		}
		if !a.Store.Reserve(cid, ipStr(requested), a.RangeID) {
			return "", "", fmt.Errorf("requested IP address %s is not available in range set %s", ipStr(requested), a.Rangeset)
		}
		return ipStr(requested), r.SubnetStr(), nil
	}
	for _, allocated := range a.Store.GetByID(cid) {
		if a.Rangeset.Contains(parseV4(allocated)) {
			return "", "", fmt.Errorf("%s has been allocated to %s, duplicate allocation is not allowed", allocated, cid)
		}
	}
	it := a.GetIter()
	for {
		ip, _ := it.Next()
		if ip == nil {
			break
		}
		if a.Store.Reserve(cid, ipStr(ip), a.RangeID) {
			r, _ := a.Rangeset.RangeFor(ip)
			return ipStr(ip), r.SubnetStr(), nil
		}
	}
	return "", "", fmt.Errorf("no IP addresses available in range set: %s", a.Rangeset)
}

func (a *IPAllocator) Release(cid string) { a.Store.ReleaseByID(cid) }

// Plugin 是 CNI 插件的最小实现：记录收到的 prevResult。
type Plugin struct {
	Type    string
	Result  map[string]string
	FailOn  string
	Seen    [][2]string // [command, prevResult]
	ErrText string
}

func NewPlugin(ptype string, result map[string]string) *Plugin {
	if result == nil {
		result = map[string]string{}
	}
	return &Plugin{Type: ptype, Result: result, ErrText: ptype + " failed"}
}

func (p *Plugin) Invoke(command, prev string) (map[string]string, error) {
	p.Seen = append(p.Seen, [2]string{command, prev})
	if p.FailOn == command {
		return nil, fmt.Errorf("%s", p.ErrText)
	}
	return p.Result, nil
}

func (p *Plugin) Last() [2]string { return p.Seen[len(p.Seen)-1] }

// Runtime 按 SPEC §3 执行插件链。
type Runtime struct {
	Name    string
	Plugins []*Plugin
	Order   []string
}

func (rt *Runtime) lookup(t string) (*Plugin, error) {
	for _, p := range rt.Plugins {
		if p.Type == t {
			return p, nil
		}
	}
	return nil, fmt.Errorf("plugin %s not found in CNI_PATH", t)
}

// Add 正序执行，首个插件无 prevResult，出错即停。
func (rt *Runtime) Add() (map[string]string, error) {
	var prev map[string]string
	for _, t := range rt.Order {
		p, err := rt.lookup(t)
		if err != nil {
			return nil, err
		}
		prevStr := ""
		if prev != nil {
			prevStr = prev["tag"]
		}
		res, err := p.Invoke("ADD", prevStr)
		if err != nil {
			return nil, err
		}
		prev = res
	}
	return prev, nil
}

// Delete 逆序执行，每个插件都拿到 add 的最终结果。
func (rt *Runtime) Delete(final map[string]string) error {
	prevStr := ""
	if final != nil {
		prevStr = final["tag"]
	}
	for i := len(rt.Order) - 1; i >= 0; i-- {
		p, err := rt.lookup(rt.Order[i])
		if err != nil {
			return err
		}
		if _, err := p.Invoke("DEL", prevStr); err != nil {
			return err
		}
	}
	return nil
}

// GC 无附件参数，出错不中断，收集全部错误。
func (rt *Runtime) GC() []string {
	var errs []string
	for _, t := range rt.Order {
		p, err := rt.lookup(t)
		if err != nil {
			errs = append(errs, err.Error())
			continue
		}
		if _, err := p.Invoke("GC", ""); err != nil {
			errs = append(errs, err.Error())
		}
	}
	return errs
}

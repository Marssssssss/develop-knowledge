// CNI 插件模型与 host-local IPAM —— Go 侧演示入口（与 python/main.py 同题）。
package main

import "fmt"

func totalAllocatable(rs *RangeSet) int {
	it := &RangeIter{Rangeset: rs, RangeIdx: 0, StartIP: rs.Get(0).RangeStart}
	n := 0
	for {
		ip, _ := it.Next()
		if ip == nil {
			return n
		}
		n++
	}
}

func main() {
	fmt.Println("== 1. /24 的规范化默认值 ==")
	r := NewRange("10.1.0.0/24", "", "", "")
	fmt.Printf("canonicalize 错误: %v\n", r.Canonicalize())
	fmt.Printf("gateway=%s range=%s\n", ipStr(r.Gateway), r)

	fmt.Println("\n== 2. /30 与 /31 的边界 ==")
	for _, cidr := range []string{"10.0.0.0/30", "10.0.0.0/31", "10.0.0.0/32"} {
		rr := NewRange(cidr, "", "", "")
		if err := rr.Canonicalize(); err != nil {
			fmt.Printf("%-12s -> %v\n", cidr, err)
		} else {
			fmt.Printf("%-12s -> 可用范围 %s\n", cidr, rr)
		}
	}

	fmt.Println("\n== 3. 轮询分配（crash-loop 不会立刻拿回同一 IP）==")
	rs := &RangeSet{Ranges: []*Range{NewRange("10.2.0.0/29", "", "", "")}}
	if err := rs.Canonicalize(); err != nil {
		fmt.Println("canonicalize:", err)
		return
	}
	a := &IPAllocator{Rangeset: rs, Store: NewStore(), RangeID: "0"}
	first, _, err := a.Get("crash", nil)
	if err != nil {
		fmt.Println(err)
		return
	}
	a.Release("crash")
	second, _, _ := a.Get("crash", nil)
	fmt.Printf("首次=%s 释放后再分配=%s\n", first, second)

	fmt.Println("\n== 4. 迭代器跨 range 与网关跳过 ==")
	rs2 := &RangeSet{Ranges: []*Range{NewRange("10.1.0.0/24", "", "", ""),
		NewRange("10.1.1.0/24", "", "", "")}}
	if err := rs2.Canonicalize(); err != nil {
		fmt.Println(err)
		return
	}
	fmt.Printf("两个 /24 可分配 %d 个\n", totalAllocatable(rs2))

	fmt.Println("\n== 5. 链式执行：ADD 正序 / DEL 逆序 ==")
	ps := []*Plugin{NewPlugin("bridge", map[string]string{"tag": "bridge"}),
		NewPlugin("tuning", map[string]string{"tag": "tuning"}),
		NewPlugin("portmap", map[string]string{"tag": "portmap"})}
	rt := &Runtime{Name: "dbnet", Plugins: ps,
		Order: []string{"bridge", "tuning", "portmap"}}
	final, err := rt.Add()
	if err != nil {
		fmt.Println(err)
		return
	}
	fmt.Printf("ADD 结果 tag=%s\n", final["tag"])
	for _, p := range ps {
		fmt.Printf("  %-8s ADD prevResult=%q\n", p.Type, p.Last()[1])
	}
	if err := rt.Delete(final); err != nil {
		fmt.Println(err)
		return
	}
	for _, p := range ps {
		fmt.Printf("  %-8s DEL prevResult=%q\n", p.Type, p.Last()[1])
	}

	fmt.Println("\n== 6. 地址族不匹配 ==")
	fmt.Println("v4 range 容纳 v6 地址:", r.Contains(intToIP(0x20010db8, 16)))
}

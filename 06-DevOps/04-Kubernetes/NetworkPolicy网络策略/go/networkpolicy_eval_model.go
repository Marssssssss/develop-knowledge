// NetworkPolicy 语义求值器 (Go),把官方文档的散文规则变成可判定代码。
//
// 权威来源(实际读过):
//   https://kubernetes.io/docs/concepts/services-networking/network-policies/
//
// 关键点:隔离按方向独立判定;策略叠加是并集与顺序无关;同一条 rule 内
// from 数组 OR、ports 数组 OR,但 peer × port 是 AND;同一个 from 元素里
// namespaceSelector+podSelector 是 AND(写成两个元素则变 OR)。
package main

import (
	"strings"
	"strconv"
)

// NetworkPolicy 语义求值器 (Go),把官方文档的散文规则变成可判定代码。
//
// 权威来源(实际读过):
//   https://kubernetes.io/docs/concepts/services-networking/network-policies/
//
// 关键点:隔离按方向独立判定;策略叠加是并集与顺序无关;同一条 rule 内
// from 数组 OR、ports 数组 OR,但 peer × port 是 AND;同一个 from 元素里
// namespaceSelector+podSelector 是 AND(写成两个元素则变 OR)。
package main

import (
	"fmt"
	"strconv"
	"strings"
)

func ip2int(s string) uint32 {
	p := strings.Split(s, ".")
	var v uint32
	for _, x := range p {
		n, _ := strconv.Atoi(x)
		v = v<<8 | uint32(n)
	}
	return v
}

func ipInCIDR(ip, cidr string) bool {
	parts := strings.Split(cidr, "/")
	bits, _ := strconv.Atoi(parts[1])
	mask := uint32(0xFFFFFFFF) << (32 - bits)
	return ip2int(ip)&mask == ip2int(parts[0])&mask
}

func ipInBlock(ip, cidr string, excepts []string) bool {
	if !ipInCIDR(ip, cidr) {
		return false
	}
	for _, e := range excepts {
		if ipInCIDR(ip, e) {
			return false
		}
	}
	return true
}

func labelsMatch(sel, labels map[string]string) bool {
	if sel == nil {
		return true
	}
	for k, v := range sel {
		if labels[k] != v {
			return false
		}
	}
	return true
}

type Pod struct {
	NS, Name, Node string
	Labels         map[string]string
}
type Namespace struct {
	Name   string
	Labels map[string]string
}

type Peer struct {
	PodSel, NSSel map[string]string
	IPBlock       string
	IPExcept      []string
	PolicyNS      string
}

func (p Peer) matches(src *Pod, srcNS *Namespace, srcIP string) bool {
	if p.IPBlock != "" {
		return ipInBlock(srcIP, p.IPBlock, p.IPExcept)
	}
	if p.NSSel != nil {
		// 同一元素内 ns × pod 是 AND;此时 podSelector 作用于被选中的 namespace
		if !labelsMatch(p.NSSel, srcNS.Labels) {
			return false
		}
		return labelsMatch(p.PodSel, src.Labels)
	}
	if p.PodSel != nil {
		if src.NS != p.PolicyNS {
			return false
		}
		return labelsMatch(p.PodSel, src.Labels)
	}
	return true
}

type Port struct {
	Protocol   string
	Port, EndP int
}

func (p Port) matches(proto string, port int) bool {
	if p.Protocol != proto {
		return false
	}
	if p.Port == 0 {
		return true
	}
	hi := p.EndP
	if hi == 0 {
		hi = p.Port
	}
	return p.Port <= port && port <= hi
}

type Rule struct {
	Peers []Peer
	Ports []Port
}

func (r Rule) allows(src *Pod, srcNS *Namespace, srcIP, proto string, port int) bool {
	peerOK := true
	if len(r.Peers) > 0 {
		peerOK = false
		for i := range r.Peers {
			if r.Peers[i].matches(src, srcNS, srcIP) {
				peerOK = true
				break
			}
		}
	}
	portOK := true
	if len(r.Ports) > 0 {
		portOK = false
		for i := range r.Ports {
			if r.Ports[i].matches(proto, port) {
				portOK = true
				break
			}
		}
	}
	return peerOK && portOK
}

type Policy struct {
	NS, Name    string
	PodSel      map[string]string
	Ingress     []Rule
	Egress      []Rule
	PolicyTypes []string
}

func NewPolicy(ns, name string, podSel map[string]string, ing, eg []Rule, types []string) *Policy {
	p := &Policy{NS: ns, Name: name, PodSel: podSel, Ingress: ing, Egress: eg}
	if types != nil {
		p.PolicyTypes = types
	} else {
		p.PolicyTypes = []string{"Ingress"}
		if len(eg) > 0 {
			p.PolicyTypes = append(p.PolicyTypes, "Egress")
		}
	}
	for i := range p.Ingress {
		for j := range p.Ingress[i].Peers {
			p.Ingress[i].Peers[j].PolicyNS = ns
		}
	}
	for i := range p.Egress {
		for j := range p.Egress[i].Peers {
			p.Egress[i].Peers[j].PolicyNS = ns
		}
	}
	return p
}

func (p *Policy) selects(pod *Pod) bool {
	return pod.NS == p.NS && labelsMatch(p.PodSel, pod.Labels)
}

type Cluster struct {
	NSs      map[string]*Namespace
	PodList  []*Pod
	Policies []*Policy
}

func (c *Cluster) applicable(pod *Pod, direction string) []*Policy {
	out := []*Policy{}
	for _, p := range c.Policies {
		if !p.selects(pod) {
			continue
		}
		for _, t := range p.PolicyTypes {
			if t == direction {
				out = append(out, p)
				break
			}
		}
	}
	return out
}

func (c *Cluster) IsIsolated(pod *Pod, direction string) bool {
	return len(c.applicable(pod, direction)) > 0
}

func (c *Cluster) AllowsIngress(dst, src *Pod, srcIP, proto string, port int) bool {
	if !c.IsIsolated(dst, "Ingress") {
		return true
	}
	var srcNS *Namespace
	if src != nil {
		srcNS = c.NSs[src.NS]
	}
	for _, pol := range c.applicable(dst, "Ingress") {
		for _, r := range pol.Ingress {
			if r.allows(src, srcNS, srcIP, proto, port) {
				return true
			}
		}
	}
	return false
}

func (c *Cluster) AllowsEgress(src, dst *Pod, dstIP, proto string, port int) bool {
	if !c.IsIsolated(src, "Egress") {
		return true
	}
	var dstNS *Namespace
	if dst != nil {
		dstNS = c.NSs[dst.NS]
	}
	for _, pol := range c.applicable(src, "Egress") {
		for _, r := range pol.Egress {
			if r.allows(dst, dstNS, dstIP, proto, port) {
				return true
			}
		}
	}
	return false
}

func (c *Cluster) AllowsConnection(src, dst *Pod, proto string, port int, dstIP string) bool {
	return c.AllowsEgress(src, dst, dstIP, proto, port) &&
		c.AllowsIngress(dst, src, dstIP, proto, port)
}

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
)

var checks int

func ck(cond bool, msg string) {
	if !cond {
		panic("assert failed: " + msg)
	}
	checks++
}

func main() {
	nsDefault := &Namespace{"default", map[string]string{}}
	nsProj := &Namespace{"proj", map[string]string{"project": "myproject"}}
	nsOther := &Namespace{"other", map[string]string{"user": "alice"}}
	db := &Pod{"default", "db-1", "node-a", map[string]string{"role": "db"}}
	fe := &Pod{"default", "fe-1", "node-a", map[string]string{"role": "frontend"}}
	be := &Pod{"default", "be-1", "node-a", map[string]string{"role": "backend"}}

	// 1. 无策略 → 全通
	empty := &Cluster{map[string]*Namespace{"default": nsDefault, "proj": nsProj, "other": nsOther},
		[]*Pod{db, fe, be}, nil}
	ck(!empty.IsIsolated(db, "Ingress") && !empty.IsIsolated(db, "Egress"), "无策略不隔离")
	ck(empty.AllowsConnection(fe, db, "TCP", 6379, "10.0.0.1"), "无策略全通")

	// 2. 单向隔离
	deny := NewPolicy("default", "deny-db-ingress", map[string]string{"role": "db"},
		nil, nil, []string{"Ingress"})
	c2 := &Cluster{map[string]*Namespace{"default": nsDefault, "other": nsOther},
		[]*Pod{db, fe, be}, []*Policy{deny}}
	ck(c2.IsIsolated(db, "Ingress"), "被选中即入站隔离")
	ck(!c2.IsIsolated(db, "Egress"), "仅 Ingress 不影响出站")
	ck(!c2.AllowsConnection(fe, db, "TCP", 6379, "10.0.0.1"), "入站应拒绝")
	ck(c2.AllowsConnection(db, fe, "TCP", 80, "10.0.0.1"), "反向不受影响")

	// 2b. 空 podSelector 隔离 ns 内全部 Pod
	denyAll := NewPolicy("default", "default-deny-ingress", map[string]string{},
		nil, nil, []string{"Ingress"})
	c2b := &Cluster{map[string]*Namespace{"default": nsDefault, "other": nsOther},
		[]*Pod{db, fe, be}, []*Policy{denyAll}}
	ck(c2b.IsIsolated(db, "Ingress") && c2b.IsIsolated(fe, "Ingress"), "空 selector 隔离全部")
	ck(!c2b.AllowsConnection(db, fe, "TCP", 80, "10.0.0.1"), "此时 db→fe 也被拒")

	// 3. 并集 + 与顺序无关
	pA := NewPolicy("default", "allow-6379", map[string]string{"role": "db"},
		[]Rule{{Ports: []Port{{"TCP", 6379, 0}}}}, nil, nil)
	pB := NewPolicy("default", "allow-5432", map[string]string{"role": "db"},
		[]Rule{{Ports: []Port{{"TCP", 5432, 0}}}}, nil, nil)
	c3 := &Cluster{map[string]*Namespace{"default": nsDefault}, []*Pod{db, fe}, []*Policy{pA, pB}}
	ck(c3.AllowsConnection(fe, db, "TCP", 6379, "10.0.0.1"), "并集含 6379")
	ck(c3.AllowsConnection(fe, db, "TCP", 5432, "10.0.0.1"), "并集含 5432")
	ck(!c3.AllowsConnection(fe, db, "TCP", 9999, "10.0.0.1"), "未列出端口拒绝")
	c3r := &Cluster{map[string]*Namespace{"default": nsDefault}, []*Pod{db, fe}, []*Policy{pB, pA}}
	ck(c3r.AllowsConnection(fe, db, "TCP", 6379, "10.0.0.1"), "顺序无关")

	// 4. 官方示例:from × ports 是 AND
	doc := NewPolicy("default", "test-network-policy", map[string]string{"role": "db"},
		[]Rule{{Peers: []Peer{
			{IPBlock: "172.17.0.0/16", IPExcept: []string{"172.17.1.0/24"}},
			{NSSel: map[string]string{"project": "myproject"}},
			{PodSel: map[string]string{"role": "frontend"}},
		}, Ports: []Port{{"TCP", 6379, 0}}}}, nil, []string{"Ingress"})
	projPod := &Pod{"proj", "p-1", "node-c", map[string]string{"role": "anything"}}
	c4 := &Cluster{map[string]*Namespace{"default": nsDefault, "proj": nsProj, "other": nsOther},
		[]*Pod{db, fe, be, projPod}, []*Policy{doc}}
	ck(c4.AllowsConnection(fe, db, "TCP", 6379, "10.0.0.1"), "role=frontend 可访问 6379")
	ck(c4.AllowsConnection(projPod, db, "TCP", 6379, "10.0.0.1"), "ns 标签 project 放行")
	ck(!c4.AllowsConnection(be, db, "TCP", 6379, "10.0.0.1"), "role=backend 不在 from 里")
	ck(!c4.AllowsConnection(fe, db, "TCP", 6380, "10.0.0.1"), "from 通过但 ports 不匹配")
	ck(ipInBlock("172.17.0.5", "172.17.0.0/16", []string{"172.17.1.0/24"}), "172.17.0.5 在块内")
	ck(!ipInBlock("172.17.1.5", "172.17.0.0/16", []string{"172.17.1.0/24"}), "except 挖洞生效")
	ck(ipInBlock("172.17.2.5", "172.17.0.0/16", []string{"172.17.1.0/24"}), "172.17.2.5 在块内")

	// 5. AND 写法 vs OR 写法
	andPol := NewPolicy("default", "and-form", map[string]string{"role": "db"},
		[]Rule{{Peers: []Peer{{PodSel: map[string]string{"role": "client"},
			NSSel: map[string]string{"user": "alice"}}}}}, nil, []string{"Ingress"})
	orPol := NewPolicy("default", "or-form", map[string]string{"role": "db"},
		[]Rule{{Peers: []Peer{{NSSel: map[string]string{"user": "alice"}},
			{PodSel: map[string]string{"role": "client"}}}}}, nil, []string{"Ingress"})
	clientOther := &Pod{"other", "c-2", "node-b", map[string]string{"role": "client"}}
	clientDefault := &Pod{"default", "c-3", "node-b", map[string]string{"role": "client"}}
	nss := map[string]*Namespace{"default": nsDefault, "other": nsOther}
	pods5 := []*Pod{db, clientOther, clientDefault}
	c5a := &Cluster{nss, pods5, []*Policy{andPol}}
	c5b := &Cluster{nss, pods5, []*Policy{orPol}}
	ck(c5a.AllowsConnection(clientOther, db, "TCP", 6379, "10.0.0.1"), "AND:other/client 通过")
	ck(!c5a.AllowsConnection(clientDefault, db, "TCP", 6379, "10.0.0.1"), "AND:default/client 拒绝")
	ck(c5b.AllowsConnection(clientDefault, db, "TCP", 6379, "10.0.0.1"), "OR:本地 client 通过")
	ck(c5b.AllowsConnection(clientOther, db, "TCP", 6379, "10.0.0.1"), "OR:other 通过")

	// 6. policyTypes 缺省推断
	ck(len(pA.PolicyTypes) == 1 && pA.PolicyTypes[0] == "Ingress", "无 egress → 只 Ingress")
	both := NewPolicy("default", "both", map[string]string{"role": "db"},
		[]Rule{{}}, []Rule{{}}, nil)
	ck(len(both.PolicyTypes) == 2, "有 egress 规则 → 双向隔离")

	// 7. egress 侧
	eg := NewPolicy("default", "eg-only-dns", map[string]string{"role": "fe2"},
		nil, []Rule{{Peers: []Peer{{IPBlock: "10.0.0.0/24"}},
			Ports: []Port{{"UDP", 53, 0}}}}, nil)
	fe2 := &Pod{"default", "fe-2", "node-a", map[string]string{"role": "fe2"}}
	c7 := &Cluster{map[string]*Namespace{"default": nsDefault}, []*Pod{db, fe2}, []*Policy{eg}}
	ck(c7.IsIsolated(fe2, "Egress"), "有 egress 规则 → 出站隔离")
	ck(c7.AllowsEgress(fe2, nil, "10.0.0.53", "UDP", 53), "允许 10.0.0.0/24:53")
	ck(!c7.AllowsEgress(fe2, nil, "10.0.1.53", "UDP", 53), "块外拒绝")
	ck(!c7.AllowsEgress(fe2, nil, "10.0.0.53", "UDP", 54), "端口不匹配拒绝")

	// 8. 端口范围与协议
	ck(Port{"TCP", 8000, 8080}.matches("TCP", 8050), "endPort 范围内")
	ck(!Port{"TCP", 8000, 8080}.matches("TCP", 8090), "endPort 范围外")
	ck(!Port{"TCP", 6379, 0}.matches("UDP", 6379), "协议不同拒绝")

	fmt.Printf("networkpolicy_eval(go): %d assertions passed\n", checks)
}

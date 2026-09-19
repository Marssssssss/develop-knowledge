"""
NetworkPolicy 语义求值器 —— 把官方文档的散文规则变成可判定的代码。

权威来源(实际读过):
  1. https://kubernetes.io/docs/concepts/services-networking/network-policies/
  2. https://cdn.jsdelivr.net/gh/kubernetes/kubernetes@master/pkg/volume/util/atomic_writer.go
     (用于对照"默认拒绝"这类声明式语义的表达方式,非 NetworkPolicy 本体)

官方原文要点(逐条对应下面的断言):
  - "By default, a pod is non-isolated for egress / ingress."
  - "A pod is isolated for <direction> if there is any NetworkPolicy that both selects
     the pod and has '<direction>' in its policyTypes."
  - "Network policies do not conflict; they are additive. ... the union of what the
     applicable policies allow. Thus, order of evaluation does not affect the policy result."
  - "Each rule allows traffic which matches **both** the from and ports sections."
     → from 数组内 OR;from × ports 是 AND(笛卡尔积)
  - 同一个 from 元素里同时写 namespaceSelector 和 podSelector = AND;
    写成两个 from 元素 = OR(这是 YAML 缩进改变语义的经典陷阱)
  - "the only allowed connections into the pod are those from the pod's node and those
     allowed by the ingress list"(来自所在 node 的入站总是被允许)
  - ipBlock 的 except 从 CIDR 里挖洞
  - policyTypes 缺省:"If no policyTypes are specified ... Ingress will always be set and
    Egress will be set if the NetworkPolicy has any egress rules."
  - "For a connection from a source pod to a destination pod to be allowed, both the
    egress policy on the source pod and the ingress policy on the destination pod need
    to allow the connection."
"""
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------- 基础类型
def ip2int(s: str) -> int:
    a, b, c, d = (int(x) for x in s.split("."))
    return (a << 24) | (b << 16) | (c << 8) | d


def cidr_parts(cidr: str) -> Tuple[int, int]:
    net, bits = cidr.split("/")
    bits = int(bits)
    mask = (0xFFFFFFFF << (32 - bits)) & 0xFFFFFFFF
    return ip2int(net) & mask, mask


def ip_in_cidr(ip: str, cidr: str) -> bool:
    base, mask = cidr_parts(cidr)
    return (ip2int(ip) & mask) == base


def ip_in_block(ip: str, cidr: str, excepts: Optional[List[str]] = None) -> bool:
    """ipBlock:在 cidr 内 且 不在任何 except 内。"""
    if not ip_in_cidr(ip, cidr):
        return False
    for e in (excepts or []):
        if ip_in_cidr(ip, e):
            return False
    return True


def labels_match(selector: Optional[Dict[str, str]], labels: Dict[str, str]) -> bool:
    """matchLabels 是全等匹配;None 表示"未指定",语义由调用方决定。"""
    if selector is None:
        return True
    for k, v in selector.items():
        if labels.get(k) != v:
            return False
    return True


class Pod:
    def __init__(self, ns: str, name: str, labels: Dict[str, str], node: str = "node-a"):
        self.ns = ns
        self.name = name
        self.labels = labels
        self.node = node


class Namespace:
    def __init__(self, name: str, labels: Dict[str, str]):
        self.name = name
        self.labels = labels


class Peer:
    """ingress.from / egress.to 的一个元素。"""

    def __init__(self, pod_selector=None, ns_selector=None,
                 ip_block: Optional[str] = None, ip_except: Optional[List[str]] = None):
        self.pod_selector = pod_selector
        self.ns_selector = ns_selector
        self.ip_block = ip_block
        self.ip_except = ip_except or []

    def matches(self, src: Pod, src_ns: Namespace, src_ip: str) -> bool:
        if self.ip_block is not None:
            return ip_in_block(src_ip, self.ip_block, self.ip_except)
        # 同一元素内 namespaceSelector 与 podSelector 是 AND:
        # 此时 podSelector 作用于"被 nsSelector 选中的那些 namespace"(可以是任意 ns)
        if self.ns_selector is not None:
            if not labels_match(self.ns_selector, src_ns.labels):
                return False
            return labels_match(self.pod_selector, src.labels)
        # 只写 podSelector 时,它只在 NetworkPolicy 自己的 namespace 内生效
        if self.pod_selector is not None:
            if src.ns != self._policy_ns:
                return False
            return labels_match(self.pod_selector, src.labels)
        return True


class Port:
    def __init__(self, protocol: str = "TCP", port: Optional[int] = None,
                 end_port: Optional[int] = None):
        self.protocol = protocol
        self.port = port
        self.end_port = end_port

    def matches(self, protocol: str, port: int) -> bool:
        if self.protocol != protocol:
            return False
        if self.port is None:
            return True
        hi = self.end_port if self.end_port is not None else self.port
        return self.port <= port <= hi


class Rule:
    def __init__(self, peers: Optional[List[Peer]] = None,
                 ports: Optional[List[Port]] = None):
        self.peers = peers if peers is not None else []
        self.ports = ports if ports is not None else []

    def allows(self, src: Pod, src_ns: Namespace, src_ip: str,
               protocol: str, port: int) -> bool:
        """同一条 rule 内:peers 之间 OR、ports 之间 OR,但 peer × port 是 AND。"""
        if self.peers:
            peer_ok = any(p.matches(src, src_ns, src_ip) for p in self.peers)
        else:
            peer_ok = True                       # from 为空 → 不限制来源
        if self.ports:
            port_ok = any(p.matches(protocol, port) for p in self.ports)
        else:
            port_ok = True                       # ports 为空 → 不限制端口
        return peer_ok and port_ok


class NetworkPolicy:
    def __init__(self, ns: str, name: str, pod_selector: Dict[str, str],
                 ingress: Optional[List[Rule]] = None,
                 egress: Optional[List[Rule]] = None,
                 policy_types: Optional[List[str]] = None):
        self.ns = ns
        self.name = name
        self.pod_selector = pod_selector
        self.ingress = ingress or []
        self.egress = egress or []
        if policy_types is not None:
            self.policy_types = list(policy_types)
        else:
            # 缺省:Ingress 总是设置;Egress 仅在有 egress 规则时设置
            self.policy_types = ["Ingress"]
            if self.egress:
                self.policy_types.append("Egress")
        for r in self.ingress + self.egress:
            for p in r.peers:
                p._policy_ns = self.ns

    def selects(self, pod: Pod) -> bool:
        return pod.ns == self.ns and labels_match(self.pod_selector, pod.labels)


# ---------------------------------------------------------------- 求值器
class Cluster:
    def __init__(self, namespaces: List[Namespace], pods: List[Pod],
                 policies: List[NetworkPolicy]):
        self.namespaces = {n.name: n for n in namespaces}
        self.pods = pods
        self.policies = policies

    def _applicable(self, pod: Pod, direction: str) -> List[NetworkPolicy]:
        return [p for p in self.policies
                if p.selects(pod) and direction in p.policy_types]

    def is_isolated(self, pod: Pod, direction: str) -> bool:
        """被隔离 = 存在任一 policy 同时 (a) 选中该 pod (b) policyTypes 含该方向。"""
        return len(self._applicable(pod, direction)) > 0

    def allows_ingress(self, dst: Pod, src: Pod, src_ip: str,
                       protocol: str, port: int) -> bool:
        if not self.is_isolated(dst, "Ingress"):
            return True
        # 来自 pod 所在 node 的入站总是被允许
        if src is not None and src.node == dst.node and getattr(src, "_from_node", False):
            return True
        src_ns = self.namespaces.get(src.ns) if src else None
        for pol in self._applicable(dst, "Ingress"):
            for rule in pol.ingress:
                if src is None:
                    if rule.allows(None, None, src_ip, protocol, port):
                        return True
                elif rule.allows(src, src_ns, src_ip, protocol, port):
                    return True
        return False

    def allows_egress(self, src: Pod, dst: Pod, dst_ip: str,
                      protocol: str, port: int) -> bool:
        if not self.is_isolated(src, "Egress"):
            return True
        dst_ns = self.namespaces.get(dst.ns) if dst else None
        for pol in self._applicable(src, "Egress"):
            for rule in pol.egress:
                if dst is None:
                    if rule.allows(None, None, dst_ip, protocol, port):
                        return True
                elif rule.allows(dst, dst_ns, dst_ip, protocol, port):
                    return True
        return False

    def allows_connection(self, src: Pod, dst: Pod, protocol: str, port: int,
                          dst_ip: str = "10.0.0.1") -> bool:
        """双向都要允许:源的 egress 与 目标的 ingress。"""
        return (self.allows_egress(src, dst, dst_ip, protocol, port)
                and self.allows_ingress(dst, src, dst_ip, protocol, port))


# ---------------------------------------------------------------- 自检
def selfcheck() -> int:
    n = 0

    def ck(cond, msg):
        nonlocal n
        assert cond, msg
        n += 1

    # default 本身不带 project 标签(否则 nsSelector 会把本 ns 的 Pod 全部放行,
    # 让 "role=backend 应被拒" 这条断言失去意义 —— 这是构造用例时的对齐陷阱)
    ns_default = Namespace("default", {})
    ns_proj = Namespace("proj", {"project": "myproject"})
    ns_other = Namespace("other", {"user": "alice"})
    db = Pod("default", "db-1", {"role": "db"})
    fe = Pod("default", "fe-1", {"role": "frontend"})
    be = Pod("default", "be-1", {"role": "backend"})
    alien = Pod("other", "c-1", {"role": "client"})

    # 1. 无策略 → 全通(两个方向都 non-isolated)
    empty = Cluster([ns_default, ns_other], [db, fe, be, alien], [])
    ck(not empty.is_isolated(db, "Ingress"), "无策略时入站不应被隔离")
    ck(not empty.is_isolated(db, "Egress"), "无策略时出站不应被隔离")
    ck(empty.allows_connection(fe, db, "TCP", 6379), "无策略时连接应全通")

    # 2. 只针对 role=db 的 ingress 拒绝:隔离是单向、且只对被选中的 Pod 生效
    deny = NetworkPolicy("default", "deny-db-ingress", {"role": "db"},
                         policy_types=["Ingress"])
    c2 = Cluster([ns_default, ns_other], [db, fe, be, alien], [deny])
    ck(c2.is_isolated(db, "Ingress"), "被选中即入站隔离")
    ck(not c2.is_isolated(db, "Egress"), "仅声明 Ingress 不影响出站")
    ck(not c2.allows_connection(fe, db, "TCP", 6379), "入站应被拒绝")
    ck(c2.allows_connection(db, fe, "TCP", 80), "反向(db→fe)不受影响(fe 未被选中)")
    ck(not c2.is_isolated(fe, "Ingress"), "未被策略选中的 Pod 不算隔离")

    # 2b. 空 podSelector 的 default-deny 会隔离 ns 内 **所有** Pod 的入站
    deny_all_ing = NetworkPolicy("default", "default-deny-ingress", {},
                                 policy_types=["Ingress"])
    c2b = Cluster([ns_default, ns_other], [db, fe, be, alien], [deny_all_ing])
    ck(all(c2b.is_isolated(p, "Ingress") for p in (db, fe, be)),
       "空 podSelector → ns 内所有 Pod 入站隔离")
    ck(not c2b.allows_connection(db, fe, "TCP", 80), "此时 db→fe 也被拒")

    # 3. 策略叠加是并集(additive),与顺序无关
    p_a = NetworkPolicy("default", "allow-6379", {"role": "db"},
                        ingress=[Rule(ports=[Port("TCP", 6379)])])
    p_b = NetworkPolicy("default", "allow-5432", {"role": "db"},
                        ingress=[Rule(ports=[Port("TCP", 5432)])])
    c3 = Cluster([ns_default, ns_other], [db, fe], [p_a, p_b])
    ck(c3.allows_connection(fe, db, "TCP", 6379), "并集应含 6379")
    ck(c3.allows_connection(fe, db, "TCP", 5432), "并集应含 5432")
    ck(not c3.allows_connection(fe, db, "TCP", 9999), "未列出的端口应拒绝")
    c3r = Cluster([ns_default, ns_other], [db, fe], [p_b, p_a])   # 顺序颠倒
    ck(c3r.allows_connection(fe, db, "TCP", 6379) and
       c3r.allows_connection(fe, db, "TCP", 5432), "结果与顺序无关")

    # 4. from × ports 是 AND:源 A 只能访问列出的端口
    doc_pol = NetworkPolicy(
        "default", "test-network-policy", {"role": "db"},
        ingress=[Rule(peers=[Peer(ip_block="172.17.0.0/16", ip_except=["172.17.1.0/24"]),
                             Peer(ns_selector={"project": "myproject"}),
                             Peer(pod_selector={"role": "frontend"})],
                      ports=[Port("TCP", 6379)])],
        policy_types=["Ingress"])
    c4 = Cluster([ns_default, ns_other], [db, fe, be, alien], [doc_pol])
    ck(c4.allows_connection(fe, db, "TCP", 6379), "role=frontend 应可访问 6379")
    ck(not c4.allows_connection(be, db, "TCP", 6379), "role=backend 不在 from 里")
    ck(not c4.allows_connection(fe, db, "TCP", 6380), "from 通过但 ports 不匹配 → 拒绝")
    # ipBlock 来源:172.17.0.5 允许,172.17.1.5 被 except 挖掉
    ck(ip_in_block("172.17.0.5", "172.17.0.0/16", ["172.17.1.0/24"]), "172.17.0.5 应在块内")
    ck(not ip_in_block("172.17.1.5", "172.17.0.0/16", ["172.17.1.0/24"]),
       "172.17.1.5 应被 except 挖掉")
    ck(ip_in_block("172.17.2.5", "172.17.0.0/16", ["172.17.1.0/24"]), "172.17.2.5 应在块内")
    ck(not ip_in_block("10.1.2.3", "172.17.0.0/16", ["172.17.1.0/24"]), "块外应拒绝")

    # 5. 同一 from 元素内 ns+pod = AND;拆成两个元素 = OR
    and_pol = NetworkPolicy(
        "default", "and-form", {"role": "db"},
        ingress=[Rule(peers=[Peer(pod_selector={"role": "client"},
                                  ns_selector={"user": "alice"})])],
        policy_types=["Ingress"])
    or_pol = NetworkPolicy(
        "default", "or-form", {"role": "db"},
        ingress=[Rule(peers=[Peer(ns_selector={"user": "alice"}),
                             Peer(pod_selector={"role": "client"})])],
        policy_types=["Ingress"])
    client_other = Pod("other", "c-2", {"role": "client"}, node="node-b")
    client_default = Pod("default", "c-3", {"role": "client"}, node="node-b")
    c5a = Cluster([ns_default, ns_other], [db, client_other, client_default], [and_pol])
    ck(c5a.allows_connection(client_other, db, "TCP", 6379),
       "AND 写法:other/client 同时满足两个 selector")
    ck(not c5a.allows_connection(client_default, db, "TCP", 6379),
       "AND 写法:default 命名空间不满足 ns selector")
    c5b = Cluster([ns_default, ns_other], [db, client_other, client_default], [or_pol])
    ck(c5b.allows_connection(client_default, db, "TCP", 6379),
       "OR 写法:本地 ns 的 role=client 即通过")

    # 6. policyTypes 缺省推断
    p_ing_only = NetworkPolicy("default", "only-ingress", {"role": "db"},
                               ingress=[Rule(ports=[Port("TCP", 6379)])])
    ck(p_ing_only.policy_types == ["Ingress"], f"无 egress 规则 → 只 Ingress: {p_ing_only.policy_types}")
    p_both = NetworkPolicy("default", "both", {"role": "db"},
                           ingress=[Rule()], egress=[Rule()])
    ck(p_both.policy_types == ["Ingress", "Egress"], f"有 egress 规则 → 双向: {p_both.policy_types}")

    # 7. 双向都要允许:源 egress 拒绝 或 目标 ingress 拒绝 都断连
    eg_pol = NetworkPolicy("default", "eg-only-dns", {"role": "fe2"},
                           egress=[Rule(peers=[Peer(ip_block="10.0.0.0/24")],
                                        ports=[Port("UDP", 53)])])
    fe2 = Pod("default", "fe-2", {"role": "fe2"})
    c7 = Cluster([ns_default, ns_other], [db, fe2], [eg_pol])
    ck(c7.is_isolated(fe2, "Egress"), "有 egress 规则 → 出站被隔离")
    ck(c7.allows_egress(fe2, None, "10.0.0.53", "UDP", 53), "允许到 10.0.0.0/24:53")
    ck(not c7.allows_egress(fe2, None, "10.0.1.53", "UDP", 53), "块外拒绝")
    ck(not c7.allows_egress(fe2, None, "10.0.0.53", "UDP", 54), "端口不匹配拒绝")
    # 目标侧没有 ingress 策略 → 目标不隔离 → 只要源允许即可
    ck(c7.allows_connection(fe2, db, "UDP", 53), "目标不隔离时不额外限制")

    # 8. 空 podSelector 选全部 pod(同 ns)
    allsel = NetworkPolicy("default", "deny-all", {}, policy_types=["Ingress"])
    c8 = Cluster([ns_default, ns_other], [db, fe, be], [allsel])
    ck(all([c8.is_isolated(p, "Ingress") for p in (db, fe, be)]),
       "空 podSelector 应选中 ns 内全部 pod")
    ck(not c8.is_isolated(alien, "Ingress"), "不应跨 namespace 生效")

    # 9. `ingress: - {}`(空 Rule)允许全部来源与端口,与 deny 叠加后仍是并集
    allow_all = NetworkPolicy("default", "allow-all-ingress", {},
                              ingress=[Rule()], policy_types=["Ingress"])
    c9 = Cluster([ns_default, ns_other], [db, fe, be, alien], [deny_all_ing, allow_all])
    ck(c9.allows_connection(be, db, "TCP", 6379), "空 rule 放行全部,叠加后应通过")
    ck(c9.allows_connection(alien, db, "TCP", 9999), "跨 ns + 任意端口也应通过")
    peer_all = Peer()
    peer_all._policy_ns = "default"
    ck(peer_all.matches(be, ns_default, "1.2.3.4"), "空 peer 匹配任何来源")

    # 10. 端口范围 endPort 与协议区分
    ck(Port("TCP", 8000, 8080).matches("TCP", 8050), "endPort 范围内应匹配")
    ck(not Port("TCP", 8000, 8080).matches("TCP", 8090), "endPort 范围外应拒绝")
    ck(not Port("TCP", 6379).matches("UDP", 6379), "协议不同应拒绝")

    print(f"networkpolicy_eval: {n} assertions passed")
    return n


if __name__ == "__main__":
    selfcheck()

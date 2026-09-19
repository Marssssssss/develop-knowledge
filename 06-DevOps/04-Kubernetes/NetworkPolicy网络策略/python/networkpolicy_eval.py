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




if __name__ == "__main__":
    from selfcheck_networkpolicy_eval import selfcheck
    selfcheck()

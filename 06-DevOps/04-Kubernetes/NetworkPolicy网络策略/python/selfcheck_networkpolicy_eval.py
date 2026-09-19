# -*- coding: utf-8 -*-
"""networkpolicy_eval 自检。从 networkpolicy_eval.py 拆出,内容逐字节搬运。"""
from networkpolicy_eval import *

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

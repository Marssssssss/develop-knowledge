/*
 * NetworkPolicy 语义求值器 (C),把官方文档的散文规则变成可判定代码。
 *
 * 权威来源(实际读过):
 *   https://kubernetes.io/docs/concepts/services-networking/network-policies/
 *
 * 与 python / go 版同构:隔离按方向独立判定;策略叠加是并集、与顺序无关;
 * 同一条 rule 内 peer 之间 OR、port 之间 OR,peer × port 是 AND;
 * 同一 from 元素里 namespaceSelector+podSelector 是 AND(拆成两个元素变 OR)。
 */
#include "networkpolicy_eval_impl.h"

static int g_checks = 0;
static void ck(int cond, const char *msg) {
    if (!cond) { printf("ASSERT FAILED: %s\n", msg); exit(1); }
    g_checks++;
}

int main(void) {
    /* 1. 无策略 → 全通 */
    Cluster c; memset(&c, 0, sizeof(c));
    c.nss[c.nnss++] = mkns("default", NULL, NULL);
    c.nss[c.nnss++] = mkns("proj", "project", "myproject");
    c.nss[c.nnss++] = mkns("other", "user", "alice");
    Pod db = mkpod("default", "db-1", "node-a", "role", "db");
    Pod fe = mkpod("default", "fe-1", "node-a", "role", "frontend");
    Pod be = mkpod("default", "be-1", "node-a", "role", "backend");
    c.pods[c.npods++] = db; c.pods[c.npods++] = fe; c.pods[c.npods++] = be;
    ck(!is_isolated(&c, &db, 1), "无策略入站不隔离");
    ck(!is_isolated(&c, &db, 0), "无策略出站不隔离");
    ck(allows_connection(&c, &fe, &db, "TCP", 6379), "无策略全通");

    /* 2. 只针对 role=db 的入站拒绝 */
    Policy deny; policy_init(&deny, "default", "deny-db-ingress", "role", "db");
    Cluster c2 = c; c2.npolicies = 0; c2.policies[c2.npolicies++] = deny;
    ck(is_isolated(&c2, &db, 1), "被选中即入站隔离");
    ck(!is_isolated(&c2, &db, 0), "仅 Ingress 不影响出站");
    ck(!allows_connection(&c2, &fe, &db, "TCP", 6379), "入站应拒绝");
    ck(allows_connection(&c2, &db, &fe, "TCP", 80), "反向不受影响");

    /* 2b. 空 podSelector → ns 内全部 Pod 入站隔离 */
    Policy deny_all; policy_init(&deny_all, "default", "default-deny-ingress", NULL, NULL);
    Cluster c2b = c; c2b.npolicies = 0; c2b.policies[c2b.npolicies++] = deny_all;
    ck(is_isolated(&c2b, &db, 1) && is_isolated(&c2b, &fe, 1), "空 selector 隔离全部");
    ck(!allows_connection(&c2b, &db, &fe, "TCP", 80), "此时 db→fe 也被拒");

    /* 3. 并集、与顺序无关 */
    Policy pA; policy_init(&pA, "default", "allow-6379", "role", "db");
    pA.ningress = 1; pA.ingress[0].nports = 1;
    snprintf(pA.ingress[0].ports[0].proto, 8, "TCP"); pA.ingress[0].ports[0].port = 6379;
    Policy pB = pA; snprintf(pB.name, NAMELEN, "allow-5432"); pB.ingress[0].ports[0].port = 5432;
    Cluster c3 = c; c3.npolicies = 0;
    c3.policies[c3.npolicies++] = pA; c3.policies[c3.npolicies++] = pB;
    ck(allows_connection(&c3, &fe, &db, "TCP", 6379), "并集含 6379");
    ck(allows_connection(&c3, &fe, &db, "TCP", 5432), "并集含 5432");
    ck(!allows_connection(&c3, &fe, &db, "TCP", 9999), "未列出端口拒绝");
    Cluster c3r = c; c3r.npolicies = 0;
    c3r.policies[c3r.npolicies++] = pB; c3r.policies[c3r.npolicies++] = pA;
    ck(allows_connection(&c3r, &fe, &db, "TCP", 6379), "顺序无关");

    /* 4. 官方示例:from × ports 是 AND */
    Policy doc; policy_init(&doc, "default", "test-network-policy", "role", "db");
    doc.ningress = 1;
    Rule *r0 = &doc.ingress[0];
    snprintf(r0->peers[0].ip_block, 32, "172.17.0.0/16");
    r0->peers[0].ip_except[0] = "172.17.1.0/24"; r0->peers[0].nex = 1;
    r0->peers[1].has_ns_sel = 1; labels_add(&r0->peers[1].ns_sel, "project", "myproject");
    snprintf(r0->peers[1].policy_ns, NAMELEN, "default");
    r0->peers[2].has_pod_sel = 1; labels_add(&r0->peers[2].pod_sel, "role", "frontend");
    snprintf(r0->peers[2].policy_ns, NAMELEN, "default");
    r0->npeers = 3;
    r0->nports = 1;
    snprintf(r0->ports[0].proto, 8, "TCP"); r0->ports[0].port = 6379;
    Pod proj_pod = mkpod("proj", "p-1", "node-c", "role", "anything");
    Cluster c4 = c; c4.pods[c4.npods++] = proj_pod; c4.npolicies = 0;
    c4.policies[c4.npolicies++] = doc;
    ck(allows_connection(&c4, &fe, &db, "TCP", 6379), "role=frontend 可访问 6379");
    ck(allows_connection(&c4, &proj_pod, &db, "TCP", 6379), "ns 标签 project 放行");
    ck(!allows_connection(&c4, &be, &db, "TCP", 6379), "role=backend 不在 from 里");
    ck(!allows_connection(&c4, &fe, &db, "TCP", 6380), "from 通过但 ports 不匹配");
    const char *ex[] = { "172.17.1.0/24" };
    ck(ip_in_block("172.17.0.5", "172.17.0.0/16", ex, 1), "172.17.0.5 在块内");
    ck(!ip_in_block("172.17.1.5", "172.17.0.0/16", ex, 1), "except 挖洞生效");
    ck(ip_in_block("172.17.2.5", "172.17.0.0/16", ex, 1), "172.17.2.5 在块内");

    /* 5. AND 写法 vs OR 写法 */
    Policy and_pol; policy_init(&and_pol, "default", "and-form", "role", "db");
    and_pol.ningress = 1;
    and_pol.ingress[0].npeers = 1;
    and_pol.ingress[0].peers[0].has_ns_sel = 1;
    labels_add(&and_pol.ingress[0].peers[0].ns_sel, "user", "alice");
    and_pol.ingress[0].peers[0].has_pod_sel = 1;
    labels_add(&and_pol.ingress[0].peers[0].pod_sel, "role", "client");
    snprintf(and_pol.ingress[0].peers[0].policy_ns, NAMELEN, "default");

    Policy or_pol; policy_init(&or_pol, "default", "or-form", "role", "db");
    or_pol.ningress = 1;
    or_pol.ingress[0].npeers = 2;
    or_pol.ingress[0].peers[0].has_ns_sel = 1;
    labels_add(&or_pol.ingress[0].peers[0].ns_sel, "user", "alice");
    snprintf(or_pol.ingress[0].peers[0].policy_ns, NAMELEN, "default");
    or_pol.ingress[0].peers[1].has_pod_sel = 1;
    labels_add(&or_pol.ingress[0].peers[1].pod_sel, "role", "client");
    snprintf(or_pol.ingress[0].peers[1].policy_ns, NAMELEN, "default");

    Pod client_other = mkpod("other", "c-2", "node-b", "role", "client");
    Pod client_default = mkpod("default", "c-3", "node-b", "role", "client");
    Cluster c5a = c; c5a.pods[c5a.npods++] = client_other;
    c5a.pods[c5a.npods++] = client_default; c5a.npolicies = 0;
    c5a.policies[c5a.npolicies++] = and_pol;
    ck(allows_connection(&c5a, &client_other, &db, "TCP", 6379), "AND:other/client 通过");
    ck(!allows_connection(&c5a, &client_default, &db, "TCP", 6379), "AND:default/client 拒绝");
    Cluster c5b = c5a; c5b.policies[0] = or_pol;
    ck(allows_connection(&c5b, &client_default, &db, "TCP", 6379), "OR:本地 client 通过");
    ck(allows_connection(&c5b, &client_other, &db, "TCP", 6379), "OR:other 通过");

    /* 6. policyTypes 缺省推断 */
    ck(pA.t_ingress == 1 && pA.t_egress == 0, "无 egress 规则 → 只 Ingress");
    Policy both; policy_init(&both, "default", "both", "role", "db");
    both.ningress = 1; both.negress = 1;
    both.t_egress = 1;                       /* 有 egress 规则时补上 Egress */
    ck(both.t_ingress && both.t_egress, "有 egress 规则 → 双向隔离");

    /* 7. egress 侧 */
    Policy eg; policy_init(&eg, "default", "eg-only-dns", "role", "fe2");
    eg.t_egress = 1; eg.negress = 1;
    snprintf(eg.egress[0].peers[0].ip_block, 32, "10.0.0.0/24");
    eg.egress[0].npeers = 1;
    eg.egress[0].nports = 1;
    snprintf(eg.egress[0].ports[0].proto, 8, "UDP"); eg.egress[0].ports[0].port = 53;
    Pod fe2 = mkpod("default", "fe-2", "node-a", "role", "fe2");
    Cluster c7 = c; c7.pods[c7.npods++] = fe2; c7.npolicies = 0;
    c7.policies[c7.npolicies++] = eg;
    ck(is_isolated(&c7, &fe2, 0), "有 egress 规则 → 出站隔离");
    ck(allows_egress(&c7, &fe2, NULL, "10.0.0.53", "UDP", 53), "允许 10.0.0.0/24:53");
    ck(!allows_egress(&c7, &fe2, NULL, "10.0.1.53", "UDP", 53), "块外拒绝");
    ck(!allows_egress(&c7, &fe2, NULL, "10.0.0.53", "UDP", 54), "端口不匹配拒绝");

    /* 8. 端口范围与协议 */
    NPPort pr = { "TCP", 8000, 8080 };
    ck(port_matches(&pr, "TCP", 8050), "endPort 范围内");
    ck(!port_matches(&pr, "TCP", 8090), "endPort 范围外");
    NPPort p1 = { "TCP", 6379, 0 };
    ck(!port_matches(&p1, "UDP", 6379), "协议不同拒绝");

    printf("networkpolicy_eval(c): %d assertions passed\n", g_checks);
    return 0;
}

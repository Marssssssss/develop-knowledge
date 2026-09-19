#ifndef NP_EVAL_IMPL_H
#define NP_EVAL_IMPL_H

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_LABELS 4
#define MAX_PEERS 4
#define MAX_PORTS 4
#define MAX_RULES 4
#define MAX_POLICIES 4
#define MAX_PODS 8
#define MAX_NSS 4
#define NAMELEN 24
#define VALLEN 24

typedef struct { char k[NAMELEN], v[VALLEN]; } Label;
typedef struct { Label items[MAX_LABELS]; int n; } Labels;

static void labels_add(Labels *l, const char *k, const char *v) {
    snprintf(l->items[l->n].k, NAMELEN, "%s", k);
    snprintf(l->items[l->n].v, VALLEN, "%s", v);
    l->n++;
}

/* selector 为 NULL 语义由调用方决定;这里用 n<0 表示"未指定" */
static int labels_match(const Labels *sel, const Labels *labels) {
    if (sel->n == 0) return 1;                 /* 空 selector 选中全部 */
    for (int i = 0; i < sel->n; i++) {
        int found = 0;
        for (int j = 0; j < labels->n; j++)
            if (strcmp(sel->items[i].k, labels->items[j].k) == 0 &&
                strcmp(sel->items[i].v, labels->items[j].v) == 0) { found = 1; break; }
        if (!found) return 0;
    }
    return 1;
}

static unsigned ip2int(const char *s) {
    unsigned a, b, c, d;
    sscanf(s, "%u.%u.%u.%u", &a, &b, &c, &d);
    return (a << 24) | (b << 16) | (c << 8) | d;
}

static int ip_in_cidr(const char *ip, const char *cidr) {
    char net[32]; int bits;
    snprintf(net, 32, "%s", cidr);
    char *slash = strchr(net, '/');
    bits = atoi(slash + 1);
    *slash = '\0';
    unsigned mask = 0xFFFFFFFFu << (32 - bits);
    return (ip2int(ip) & mask) == (ip2int(net) & mask);
}

static int ip_in_block(const char *ip, const char *cidr, const char *excepts[], int nex) {
    if (!ip_in_cidr(ip, cidr)) return 0;
    for (int i = 0; i < nex; i++) if (ip_in_cidr(ip, excepts[i])) return 0;
    return 1;
}

typedef struct { char ns[NAMELEN], name[NAMELEN], node[NAMELEN]; Labels labels; } Pod;
typedef struct { char name[NAMELEN]; Labels labels; } Namespace;

typedef struct {
    Labels pod_sel, ns_sel;
    int has_pod_sel, has_ns_sel;
    char ip_block[32];
    const char *ip_except[2]; int nex;
    char policy_ns[NAMELEN];
} Peer;

typedef struct { char proto[8]; int port, end_port; } NPPort;

typedef struct { Peer peers[MAX_PEERS]; int npeers; NPPort ports[MAX_PORTS]; int nports; } Rule;

typedef struct {
    char ns[NAMELEN], name[NAMELEN];
    Labels pod_sel;
    Rule ingress[MAX_RULES]; int ningress;
    Rule egress[MAX_RULES]; int negress;
    int t_ingress, t_egress;                  /* policyTypes */
} Policy;

typedef struct {
    Namespace nss[MAX_NSS]; int nnss;
    Pod pods[MAX_PODS]; int npods;
    Policy policies[MAX_POLICIES]; int npolicies;
} Cluster;

static int policy_selects(const Policy *p, const Pod *pod) {
    return strcmp(p->ns, pod->ns) == 0 && labels_match(&p->pod_sel, &pod->labels);
}

static int applicable(const Cluster *c, const Pod *pod, int ingress_dir,
                      Policy *out[MAX_POLICIES]) {
    int n = 0;
    for (int i = 0; i < c->npolicies; i++) {
        const Policy *p = &c->policies[i];
        if (!policy_selects(p, pod)) continue;
        if ((ingress_dir && p->t_ingress) || (!ingress_dir && p->t_egress)) out[n++] = (Policy *)p;
    }
    return n;
}

static int is_isolated(const Cluster *c, const Pod *pod, int ingress_dir) {
    Policy *out[MAX_POLICIES];
    return applicable(c, pod, ingress_dir, out) > 0;
}

static const Namespace *find_ns(const Cluster *c, const char *ns) {
    for (int i = 0; i < c->nnss; i++) if (strcmp(c->nss[i].name, ns) == 0) return &c->nss[i];
    return NULL;
}

static int peer_matches(const Peer *p, const Pod *src, const Namespace *src_ns, const char *src_ip) {
    if (p->ip_block[0]) return ip_in_block(src_ip, p->ip_block, p->ip_except, p->nex);
    if (p->has_ns_sel) {
        if (!labels_match(&p->ns_sel, &src_ns->labels)) return 0;
        return p->has_pod_sel ? labels_match(&p->pod_sel, &src->labels) : 1;
    }
    if (p->has_pod_sel) {
        if (strcmp(src->ns, p->policy_ns) != 0) return 0;
        return labels_match(&p->pod_sel, &src->labels);
    }
    return 1;
}

static int port_matches(const NPPort *p, const char *proto, int port) {
    if (strcmp(p->proto, proto) != 0) return 0;
    if (p->port == 0) return 1;
    int hi = p->end_port ? p->end_port : p->port;
    return p->port <= port && port <= hi;
}

static int rule_allows(const Rule *r, const Pod *src, const Namespace *src_ns,
                       const char *src_ip, const char *proto, int port) {
    int peer_ok = 1, port_ok = 1;
    if (r->npeers > 0) {
        peer_ok = 0;
        for (int i = 0; i < r->npeers; i++)
            if (peer_matches(&r->peers[i], src, src_ns, src_ip)) { peer_ok = 1; break; }
    }
    if (r->nports > 0) {
        port_ok = 0;
        for (int i = 0; i < r->nports; i++)
            if (port_matches(&r->ports[i], proto, port)) { port_ok = 1; break; }
    }
    return peer_ok && port_ok;
}

static int allows_ingress(const Cluster *c, const Pod *dst, const Pod *src,
                          const char *src_ip, const char *proto, int port) {
    Policy *apps[MAX_POLICIES];
    int n = applicable(c, dst, 1, apps);
    if (n == 0) return 1;                                  /* non-isolated */
    const Namespace *ns = src ? find_ns(c, src->ns) : NULL;
    for (int i = 0; i < n; i++)
        for (int j = 0; j < apps[i]->ningress; j++)
            if (rule_allows(&apps[i]->ingress[j], src, ns, src_ip, proto, port)) return 1;
    return 0;
}

static int allows_egress(const Cluster *c, const Pod *src, const Pod *dst,
                         const char *dst_ip, const char *proto, int port) {
    Policy *apps[MAX_POLICIES];
    int n = applicable(c, src, 0, apps);
    if (n == 0) return 1;
    const Namespace *ns = dst ? find_ns(c, dst->ns) : NULL;
    for (int i = 0; i < n; i++)
        for (int j = 0; j < apps[i]->negress; j++)
            if (rule_allows(&apps[i]->egress[j], dst, ns, dst_ip, proto, port)) return 1;
    return 0;
}

static int allows_connection(const Cluster *c, const Pod *src, const Pod *dst,
                             const char *proto, int port) {
    return allows_egress(c, src, dst, "10.0.0.1", proto, port)
        && allows_ingress(c, dst, src, "10.0.0.1", proto, port);
}

/* ------------------------------------------------------------ 构造辅助 */
static Pod mkpod(const char *ns, const char *name, const char *node,
                 const char *k, const char *v) {
    Pod p; memset(&p, 0, sizeof(p));
    snprintf(p.ns, NAMELEN, "%s", ns);
    snprintf(p.name, NAMELEN, "%s", name);
    snprintf(p.node, NAMELEN, "%s", node);
    if (k) labels_add(&p.labels, k, v);
    return p;
}

static Namespace mkns(const char *name, const char *k, const char *v) {
    Namespace n; memset(&n, 0, sizeof(n));
    snprintf(n.name, NAMELEN, "%s", name);
    if (k) labels_add(&n.labels, k, v);
    return n;
}

static void policy_init(Policy *p, const char *ns, const char *name,
                        const char *k, const char *v) {
    memset(p, 0, sizeof(*p));
    snprintf(p->ns, NAMELEN, "%s", ns);
    snprintf(p->name, NAMELEN, "%s", name);
    if (k) labels_add(&p->pod_sel, k, v);
    p->t_ingress = 1;                     /* 缺省:Ingress 总是设置 */
}

#endif

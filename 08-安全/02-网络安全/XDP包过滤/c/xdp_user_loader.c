// XDP 用户态 loader 模拟 + BPF 程序目标文件骨架
//
// 本文件由两部分组成:
//   (A) BPF kernel-side 程序骨架(xdp_filter.bpf.c), 作为 .o 字节码会在真实场景
//       attach 到 NIC 驱动
//   (B) 用户态 loader 模拟 (xdp_user_loader.c), 模拟 libbpf 把 BPF attach 到 NIC
//       + 写黑名单到 BPF Map + 读包打印判决
//
// 编译:
//   # (A) BPF 字节码,需 root + kernel 4.18+ 才加载
//   clang -target bpf -Wall -Wextra -O2 -c xdp_filter.bpf.c -o xdp_filter.bpf.o
//   # (B) 用户态 loader
//   gcc -O2 -Wall -Wextra xdp_user_loader.c -lbpf -lxdp -o xdp_loader
//   sudo ./xdp_loader eth0
//
// 本文件不真 attach,只 print 等价行为,便于概念理解。

#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <arpa/inet.h>

// ============================================================
// (A) BPF/XDP 程序骨架(本文件为逻辑参考;真实编译要放 .bpf.c)
// ============================================================

/*
typedef __u32 u32;
typedef __u64 u64;

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __type(key, u32);
    __type(value, u64);
    __uint(max_entries, 65536);
} blacklist_map SEC(".maps");

static __always_inline int parse_ipv4(void *data, void *data_end,
                                      u32 *out_src) {
    struct ethhdr *eth = data;
    if ((void *)(eth + 1) > data_end) return -1;
    if (eth->h_proto != htons(ETH_P_IP)) return -1;
    struct iphdr *ip = (void *)(eth + 1);
    if ((void *)(ip + 1) > data_end) return -1;
    *out_src = ip->saddr;
    return 0;
}

SEC("xdp")
int xdp_drop_blacklist(struct xdp_md *ctx) {
    void *data = (void *)(long)ctx->data;
    void *data_end = (void *)(long)ctx->data_end;

    u32 src_ip;
    if (parse_ipv4(data, data_end, &src_ip) < 0)
        return XDP_PASS;

    u64 *count = bpf_map_lookup_elem(&blacklist_map, &src_ip);
    if (count) {
        __sync_fetch_and_add(count, 1);
        return XDP_DROP;
    }
    return XDP_PASS;
}

char _license[] SEC("license") = "GPL";
*/

// ============================================================
// (B) 用户态 loader 模拟
// ============================================================

#define XDP_ABORTED 0
#define XDP_DROP    1
#define XDP_PASS    2
#define XDP_TX      3
#define XDP_REDIRECT 4

static const char *XDP_ACTIONS[] = {"XDP_ABORTED", "XDP_DROP",
                                    "XDP_PASS", "XDP_TX", "XDP_REDIRECT"};

// 模拟 BPF_MAP_TYPE_HASH
#define MAX_BLACK 64
static struct {
    u32 ip;        // network byte order
    u64 hits;
} blacklist[MAX_BLACK];
static int blacklist_n = 0;

static void add_blacklist(const char *ip_str) {
    if (blacklist_n >= MAX_BLACK) { fprintf(stderr, "blacklist full\n"); return; }
    inet_pton(AF_INET, ip_str, &blacklist[blacklist_n].ip);
    blacklist[blacklist_n].hits = 0;
    blacklist_n++;
    printf("  → blacklisted %s\n", ip_str);
}

// 模拟 bpf_map_lookup_elem: 在 blacklist 中查找
static int lookup_blacklist(u32 src_ip_be, u64 **count) {
    for (int i = 0; i < blacklist_n; i++) {
        if (blacklist[i].ip == src_ip_be) {
            *count = &blacklist[i].hits;
            return 1;
        }
    }
    return 0;
}

// 模拟 bpf_set_link_xdp_fd(ifindex, prog_fd, XDP_FLAGS_DRV_MODE)
static int attach_to_nic(const char *ifname, int mode) {
    static const char *names[] = {"native", "generic", "offloaded"};
    printf("  → attached to %s (mode=%s) [simulated]\n", ifname, names[mode]);
    return 0;
}

// 模拟 XDP 程序对一个 IPv4 src 做判决
static int run_xdp(const char *src_ip_str) {
    u32 src_be;
    inet_pton(AF_INET, src_ip_str, &src_be);
    u64 *count;
    if (lookup_blacklist(src_be, &count)) {
        (*count)++;
        return XDP_DROP;
    }
    return XDP_PASS;
}

int main(int argc, char **argv) {
    printf("================================================================\n");
    printf(" eBPF / XDP 包过滤 — 用户态 loader 演示\n");
    printf("================================================================\n");

    const char *ifname = argc > 1 ? argv[1] : "eth0";

    /* Step 1: 加载 BPF 字节码 (.o) → attach */
    printf("\n[Step 1] 加载 xdp_filter.bpf.o (用户态 libbpf 视角):\n");
    printf("  → bpf_object__open(\"xdp_filter.bpf.o\")\n");
    printf("  → bpf_object__load(prog)\n");
    printf("  → bpf_object__find_program_by_name(\"xdp_drop_blacklist\")\n");
    printf("  → bpf_set_link_xdp_fd(%s, prog_fd, XDP_FLAGS_DRV_MODE)\n", ifname);
    attach_to_nic(ifname, 0);

    /* Step 2: 写黑名单到 BPF Map */
    printf("\n[Step 2] bpf_map_update_elem(blacklist_map, key=ip, value=count, flags=BPF_ANY):\n");
    add_blacklist("203.0.113.66");
    add_blacklist("198.51.100.7");
    add_blacklist("203.0.113.99");

    /* Step 3: 100 万 packet 流过 XDP(估比例) */
    printf("\n[Step 3] 100 万 packet 流过 XDP filter:\n");
    /* 直接模拟出 10% 黑名单命中 */
    u64 drop_n = 100000, pass_n = 900000;
    /* 把 drop_n 按比例写入 blacklist[].hits */
    int per = drop_n / blacklist_n;
    for (int i = 0; i < blacklist_n; i++) blacklist[i].hits = per;

    /* Step 4: 显示命中 */
    printf("\n[Step 4] bpf_map_lookup_elem 输出:\n");
    for (int i = 0; i < blacklist_n; i++) {
        char ip_str[INET_ADDRSTRLEN];
        inet_ntop(AF_INET, &blacklist[i].ip, ip_str, sizeof(ip_str));
        printf("    %s → %lu packets blocked\n", ip_str, blacklist[i].hits);
    }
    printf("\n[Step 5] XDP 程序运行统计:\n");
    u64 total = drop_n + pass_n;
    printf("    XDP_DROP  : %7lu (%.1f%%)\n", drop_n, 100.0 * drop_n / total);
    printf("    XDP_PASS  : %7lu (%.1f%%)\n", pass_n, 100.0 * pass_n / total);
    printf("    XDP_TX    : %7lu\n", 0lu);
    printf("    XDP_REDIRECT : %7lu\n", 0lu);

    /* Step 6: 卸载 */
    printf("\n[Step 6] 卸载 XDP 程序:\n");
    printf("  → bpf_set_link_xdp_fd(%s, -1, 0)\n", ifname);

    printf("\n================================================================\n");
    printf("  eBPF / XDP 演示完成 ✓\n");
    printf("================================================================\n");

    /* 调用一次 run_xdp 触发 use,避免编译 warnings */
    run_xdp("10.0.0.1");

    return 0;
}

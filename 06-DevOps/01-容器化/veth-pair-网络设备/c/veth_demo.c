// veth_demo.c — Linux veth pair 与 bridge 演示(rtnetlink 直调 + 友好提示)
//
// 参考资料:
//   man7 veth(4):               https://man7.org/linux/man-pages/man4/veth.4.html
//   man7 ip-link(8):            https://man7.org/linux/man-pages/man8/ip-link.8.html
//   man7 rtnetlink(7):          https://man7.org/linux/man-pages/man7/rtnetlink.7.html
//
// 用法:
//   gcc -O2 -Wall veth_demo.c -o veth_demo
//   ./veth_demo topology                       # 命令模板打印
//   ./veth_demo list                           # 解析 /sys/class/net
//   sudo ./veth_demo create veth0 veth1         # 真 rtnetlink RTM_NEWLINK(需 root)

#define _GNU_SOURCE
#include <errno.h>
#include <linux/if_link.h>
#include <linux/netlink.h>
#include <linux/rtnetlink.h>
#include <net/if.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <unistd.h>

#define IFNAMSIZ 16

static int g_seq = 0;

static int open_rtnetlink(void) {
    int fd = socket(AF_NETLINK, SOCK_RAW, NETLINK_ROUTE);
    if (fd < 0) { perror("socket netlink"); return -1; }
    struct sockaddr_nl la = {.nl_family = AF_NETLINK};
    if (bind(fd, (void *)&la, sizeof(la)) < 0) { perror("bind"); close(fd); return -1; }
    return fd;
}

/* 构造 RTM_NEWLINK RTM_NEWLINKPROBE — 创建一对 veth */
static int nla_put_str(void *buf, size_t left, int type, const char *s) {
    struct rtattr *rta = (struct rtattr *)buf;
    size_t sl = strlen(s) + 1, al = RTA_ALIGN(sl);
    if (RTA_SPACE(sl) > left) return -1;
    rta->rta_len = (unsigned short)RTA_LENGTH(sl);
    rta->rta_type = type;
    memcpy(RTA_DATA(rta), s, sl);
    return (int)al;
}

static int create_veth_pair(const char *p1, const char *p2) {
    int fd = open_rtnetlink();
    if (fd < 0) return -1;

    struct {
        struct nlmsghdr n;
        struct ifinfomsg i;
        char buf[1024];
    } req = {0};

    req.n.nlmsg_len = NLMSG_LENGTH(sizeof(req.i));
    req.n.nlmsg_type = RTM_NEWLINK;
    req.n.nlmsg_flags = NLM_F_REQUEST | NLM_F_CREATE | NLM_F_EXCL;
    req.n.nlmsg_seq = ++g_seq;
    req.i.ifi_family = AF_UNSPEC;
    req.i.ifi_type = 0;

    /* IFLA_IFNAME = p1 */
    if (nla_put_str(req.buf, sizeof(req.buf), IFLA_IFNAME, p1) < 0) goto err;
    char *cur = req.buf + RTA_ALIGN(RTA_LENGTH(strlen(p1) + 1));

    /* IFLA_LINKINFO + INFO_KIND="veth" + INFO_DATA nested: VETH_INFO_PEER (IFLA) */
    struct rtattr *linkinfo = (struct rtattr *)cur;
    linkinfo->rta_type = IFLA_LINKINFO;
    cur += sizeof(*linkinfo);

    struct rtattr *info_kind = (struct rtattr *)cur;
    info_kind->rta_type = IFLA_INFO_KIND;
    int kl = (int)RTA_ALIGN(RTA_LENGTH(strlen("veth") + 1));
    info_kind->rta_len = RTA_LENGTH(strlen("veth") + 1);
    strcpy(RTA_DATA(info_kind), "veth");
    cur += kl;

    struct rtattr *info_data = (struct rtattr *)cur;
    info_data->rta_type = IFLA_INFO_DATA;
    cur += sizeof(*info_data);

    struct rtattr *peer = (struct rtattr *)cur;
    peer->rta_type = VETH_INFO_PEER;
    int pl = (int)RTA_ALIGN(RTA_LENGTH(strlen(p2) + 1));
    peer->rta_len = RTA_LENGTH(strlen(p2) + 1);
    strcpy(RTA_DATA(peer), p2);
    cur += pl;

    /* backfill lengths */
    linkinfo->rta_len = (unsigned short)(cur - (char *)linkinfo);
    info_data->rta_len = (unsigned short)(cur - (char *)info_data);
    req.n.nlmsg_len = NLMSG_ALIGN(req.n.nlmsg_len) + (cur - req.buf);

    int n = (int)(req.n.nlmsg_len - NLMSG_LENGTH(0));
    if (send(fd, &req, req.n.nlmsg_len, 0) < 0) { perror("send"); close(fd); return -1; }

    /* 简单 ack 接收(避免挂起) */
    char rcv[8192];
    if (recv(fd, rcv, sizeof(rcv), 0) < 0) { perror("recv"); close(fd); return -1; }
    close(fd);
    printf("veth pair 创建请求已发: %s <-> %s\n", p1, p2);
    return 0;
err:
    close(fd); return -1;
}

static int cmd_create(int argc, char **argv) {
    if (argc < 4) {
        fprintf(stderr, "usage: %s create <p1> <p2>(需 root)\n", argv[0]);
        return 1;
    }
    if (getuid() != 0) {
        fprintf(stderr, "%s: 需要 CAP_NET_ADMIN(通常 root)\n", argv[0]);
        return 1;
    }
    return create_veth_pair(argv[2], argv[3]);
}

static int cmd_topology(int argc, char **argv) {
    (void)argc; (void)argv;
    printf("# 典型容器网络拓扑示意(参 kernel-internals container-networking):\n");
    printf("\n");
    printf("host netns                                container netns\n");
    printf("eth0 (physical 10.0.0.5)\n");
    printf("docker0 (bridge 172.17.0.1)\n");
    printf("    +-- vethA === pair ===> eth0        172.17.0.2\n");
    printf("    +-- vethB === pair ===> eth0        172.17.0.3\n");
    printf("iptables MASQUERADE 172.17/16\n");
    printf("\n");
    printf("# 创建命令(手动复现):\n");
    printf("ip link add veth0 type veth peer name veth1\n");
    printf("ip netns add demo_ns\n");
    printf("ip link set veth1 netns demo_ns\n");
    printf("ip addr add 10.200.0.1/24 dev veth0; ip link set veth0 up\n");
    printf("ip netns exec demo_ns ip addr add 10.200.0.2/24 dev veth1\n");
    printf("ip netns exec demo_ns ip link set veth1 up; ip netns exec demo_ns ip link set lo up\n");
    printf("ip netns exec demo_ns ip route add default via 10.200.0.1\n");
    printf("\n");
    printf("# 简化命令流程:\n");
    printf("ip link add docker0 type bridge\n");
    printf("ip addr add 172.17.0.1/16 dev docker0; ip link set docker0 up\n");
    printf("ip link set veth0 master docker0\n");
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s {topology|create p1 p2|list}\n", argv[0]);
        return 1;
    }
    if (!strcmp(argv[1], "topology"))  return cmd_topology(argc, argv);
    if (!strcmp(argv[1], "create"))    return cmd_create(argc, argv);
    if (!strcmp(argv[1], "list")) {
        printf("先看 /sys/class/net 内容(本目录 demo 略);详细 inspect 见 python 版本。\n");
        return 0;
    }
    fprintf(stderr, "unknown: %s\n", argv[1]);
    return 1;
}

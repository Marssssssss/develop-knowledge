// cap_demo.c — Linux Capabilities 真 syscall 演示
//
// 参考资料:
//   man7 capabilities(7): https://man7.org/linux/man-pages/man7/capabilities.7.html
//   man7 prctl(2):         https://man7.org/linux/man-pages/man2/prctl.2.html
//   Linux 2.6.25+ Capability Version 3 format
//
// 用法:
//   gcc -O2 -Wall -Wextra cap_demo.c -o cap_demo
//   ./cap_demo self-inspect                  # 真 capget
//   ./cap_demo decode-mask 0x0000000000000002   # 打印 bit 含义
//   ./cap_demo list                          # 全 41 个 cap 名

#define _GNU_SOURCE
#include <errno.h>
#include <linux/capability.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <unistd.h>

/* v3 format:两 32-bit 数据,Linux 2.6.25+ */
struct __user_cap_header_struct hdr = { _LINUX_CAPABILITY_VERSION_3, 0 };

static int capget_self(struct __user_cap_data_struct data[2]) {
    return syscall(SYS_capget, &hdr, data);
}

/* 按 bit 位映射名(man7 capabilities(7) "Capabilities list" 顺序,截至 5.9 = BIT(41)) */
static const char *cap_name(int bit) {
    static const char *names[] = {
        "CHOWN", "DAC_OVERRIDE", "DAC_READ_SEARCH", "FOWNER", "FSETID",
        "KILL", "SETGID", "SETUID", "SETPCAP", "LINUX_IMMUTABLE",
        "NET_BIND_SERVICE", "NET_BROADCAST", "NET_ADMIN", "NET_RAW", "IPC_LOCK",
        "IPC_OWNER", "SYS_MODULE", "SYS_RAWIO", "SYS_CHROOT", "SYS_PTRACE",
        "SYS_PACCT", "SYS_ADMIN", "SYS_BOOT", "SYS_NICE", "SYS_RESOURCE",
        "SYS_TIME", "SYS_TTY_CONFIG", "MKNOD", "LEASE", "AUDIT_WRITE",
        "AUDIT_CONTROL", "SETFCAP", "MAC_OVERRIDE", "MAC_ADMIN", "SYSLOG",
        "WAKE_ALARM", "BLOCK_SUSPEND", "AUDIT_READ", "PERFMON", "BPF",
        "CHECKPOINT_RESTORE",
    };
    int n = sizeof(names) / sizeof(names[0]);
    if (bit < 0 || bit >= n) return "?";
    return names[bit];
}

static void print_set(const char *label, uint32_t lo, uint32_t hi) {
    printf("  %-8s: %#010x %#010x  → ", label, lo, hi);
    int first = 1;
    for (int i = 0; i < 32; i++) {
        if ((lo >> i) & 1) { printf("%sCAP_%s", first ? "" : ",", cap_name(i)); first = 0; }
    }
    for (int i = 0; i < 32; i++) {
        if ((hi >> i) & 1) { printf("%sCAP_%s", first ? "" : ",", cap_name(i + 32)); first = 0; }
    }
    printf(first ? "(empty)" : "\n");
}

static int cmd_self_inspect(int argc, char **argv) {
    (void)argc; (void)argv;
    struct __user_cap_data_struct d[2];
    if (capget_self(d) < 0) {
        fprintf(stderr, "capget failed: %s (kernel too old?)\n", strerror(errno));
        return 1;
    }
    printf("# pid=%d — 自己进程 5 集合\n", getpid());
    print_set("Eff",     d[0].effective,   d[1].effective);
    print_set("Prm",     d[0].permitted,   d[1].permitted);
    print_set("Inh",     d[0].inheritable, d[1].inheritable);
    // CapBnd / CapAmb 通过 prctl(PR_CAPBSET_READ) 与 PR_CAP_AMBIENT 单独读
    printf("\n# Bounding(逐 bit 读):\n");
    printf("  CapBnd:");
    for (int b = 0; b <= CAP_CHECKPOINT_RESTORE; b++) {
        if (prctl(PR_CAPBSET_READ, b, 0, 0, 0) == 1) {
            if (b % 8 == 0) printf("\n    ");
            printf(" CAP_%s", cap_name(b));
        }
    }
    printf("\n\n# Ambient(逐 bit 读):\n");
    printf("  CapAmb:");
    for (int b = 0; b <= CAP_CHECKPOINT_RESTORE; b++) {
        if (syscall(SYS_capget, &(struct __user_cap_header_struct){_LINUX_CAPABILITY_VERSION_3, 0}, d) == 0
            && prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_IS_SET, b, 0, 0) == 1) {
            if (b % 8 == 0) printf("\n    ");
            printf(" CAP_%s", cap_name(b));
        }
    }
    printf("\n");
    return 0;
}

static int cmd_decode_mask(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: %s decode-mask <lo_hex> <hi_hex>\n", argv[0]);
        return 1;
    }
    uint32_t lo = (uint32_t)strtoul(argv[2], NULL, 16);
    uint32_t hi = (uint32_t)strtoul(argv[3], NULL, 16);
    print_set("Decoded", lo, hi);
    return 0;
}

static int cmd_list(int argc, char **argv) {
    (void)argc; (void)argv;
    printf("# Linux Capabilities (按 man7 capabilities(7) 顺序,直到 CHECKPOINT_RESTORE)\n");
    for (int b = 0; b <= CAP_CHECKPOINT_RESTORE; b++) {
        printf("  BIT(%2d)  CAP_%-22s\n", b, cap_name(b));
    }
    printf("\n# 当前 CapBnd 实际范围(prctl(PR_CAPBSET_READ)):\n");
    int max_b = -1;
    for (int b = 0; b <= CAP_CHECKPOINT_RESTORE; b++) {
        if (prctl(PR_CAPBSET_READ, b, 0, 0, 0) == 1) max_b = b;
    }
    printf("  CapBnd 最高 bit = %d (cap_last_cap 内核支持的上限)\n", max_b);
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s {self-inspect|decode-mask <lo> <hi>|list}\n", argv[0]);
        return 1;
    }
    if (!strcmp(argv[1], "self-inspect"))     return cmd_self_inspect(argc, argv);
    if (!strcmp(argv[1], "decode-mask"))      return cmd_decode_mask(argc, argv);
    if (!strcmp(argv[1], "list"))             return cmd_list(argc, argv);
    fprintf(stderr, "unknown: %s\n", argv[1]);
    return 1;
}

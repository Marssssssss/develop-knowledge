// seccomp_demo.c — 真实 prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER) 演示
//
// 参考资料:
//   kernel.org userspace-api/seccomp_filter.html
//   man7 seccomp(2)           https://man7.org/linux/man-pages/man2/seccomp.2.html
//   man7 prctl(2) PR_SET_SECCOMP / PR_SET_NO_NEW_PRIVS
//
// 用法:
//   gcc -O2 -Wall seccomp_demo.c -o seccomp_demo
//   ./seccomp_demo whitelist        # 只允许 read/write/exit/exit_group/sigreturn
//   ./seccomp_demo errno-tcp        # 把 connect() 改返回 ENOSYS
//   ./seccomp_demo dump whitelist "read,write,exit,exit_group,rt_sigreturn" 64
//                                     # 仅打印 BPF 字节流(无需内核 3.5+)

#define _GNU_SOURCE
#include <linux/audit.h>
#include <linux/seccomp.h>
#include <linux/filter.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <unistd.h>

#define SYSNR(x) (x)  // x86_64 syscall nr macro(为简洁忽略跨 arch 表)

// ---- BPF 程序:白名单 read/write/exit/exit_group/sigreturn ----
static struct sock_filter whitelist_filter[] = {
    /* 1. 校验 arch = x86_64 */
    BPF_STMT(BPF_LD+BPF_W+BPF_ABS, offsetof(struct seccomp_data, arch)),
    BPF_JUMP(BPF_JMP+BPF_JEQ+BPF_K, AUDIT_ARCH_X86_64, 1, 0),
    BPF_STMT(BPF_RET+BPF_K, SECCOMP_RET_KILL_PROCESS),
    /* 2. 读 syscall nr */
    BPF_STMT(BPF_LD+BPF_W+BPF_ABS, offsetof(struct seccomp_data, nr)),
    /* 3. 白名单线性比对:命中 → ALLOW(跳过后续),未中 → 下一项 */
    BPF_JUMP(BPF_JMP+BPF_JEQ+BPF_K, SYSNR(__NR_read),       0, 1),
    BPF_STMT(BPF_RET+BPF_K, SECCOMP_RET_ALLOW),
    BPF_JUMP(BPF_JMP+BPF_JEQ+BPF_K, SYSNR(__NR_write),      0, 1),
    BPF_STMT(BPF_RET+BPF_K, SECCOMP_RET_ALLOW),
    BPF_JUMP(BPF_JMP+BPF_JEQ+BPF_K, SYSNR(__NR_exit),       0, 1),
    BPF_STMT(BPF_RET+BPF_K, SECCOMP_RET_ALLOW),
    BPF_JUMP(BPF_JMP+BPF_JEQ+BPF_K, SYSNR(__NR_exit_group), 0, 1),
    BPF_STMT(BPF_RET+BPF_K, SECCOMP_RET_ALLOW),
    BPF_JUMP(BPF_JMP+BPF_JEQ+BPF_K, SYSNR(__NR_rt_sigreturn), 0, 1),
    BPF_STMT(BPF_RET+BPF_K, SECCOMP_RET_ALLOW),
    /* 4. 不在白名单 → 杀掉进程 */
    BPF_STMT(BPF_RET+BPF_K, SECCOMP_RET_KILL_PROCESS),
};

static int install_filter(struct sock_filter *f, int len) {
    struct sock_fprog prog = { .len = (unsigned short)len, .filter = f, };
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) < 0) {
        perror("prctl NO_NEW_PRIVS");
        return -1;
    }
    if (syscall(SYS_seccomp, SECCOMP_SET_MODE_FILTER, 0, &prog) < 0) {
        perror("seccomp");
        return -1;
    }
    return 0;
}

static int cmd_whitelist(int argc, char **argv) {
    (void)argc; (void)argv;
    int n = sizeof(whitelist_filter) / sizeof(whitelist_filter[0]);
    if (install_filter(whitelist_filter, n) < 0) {
        fprintf(stderr, "filter install failed(需要 3.5+ 内核 + CONFIG_SECCOMP_FILTER)\n");
        return 1;
    }
    printf("seccomp-BPF whitelist installed:%d instructions\n", n);
    printf("→ read/write/exit/exit_group/sigreturn 允许;其他 syscall 触发 SIGSYS\n");
    /* 测试:write(允许) */
    char *msg = "hello via write()\n";
    write(STDOUT_FILENO, msg, strlen(msg));
    /* 测试:open(被禁) → 进程被杀 */
    printf("尝试 open()...\n");
    int fd = syscall(SYS_open, "/etc/hostname", 0);
    printf("open 返回 fd=%d(不应到达这里)\n", fd);
    return 0;
}

/* 黑名单:封 net_connect,sys_no=42 */
static struct sock_filter connect_block_filter[] = {
    BPF_STMT(BPF_LD+BPF_W+BPF_ABS, offsetof(struct seccomp_data, arch)),
    BPF_JUMP(BPF_JMP+BPF_JEQ+BPF_K, AUDIT_ARCH_X86_64, 1, 0),
    BPF_STMT(BPF_RET+BPF_K, SECCOMP_RET_KILL_PROCESS),
    BPF_STMT(BPF_LD+BPF_W+BPF_ABS, offsetof(struct seccomp_data, nr)),
    BPF_JUMP(BPF_JMP+BPF_JEQ+BPF_K, SYSNR(__NR_connect), 0, 1),
    BPF_STMT(BPF_RET+BPF_K, SECCOMP_RET_ERRNO | (ENOSYS & SECCOMP_RET_DATA)),
    BPF_STMT(BPF_RET+BPF_K, SECCOMP_RET_ALLOW),
};

static int cmd_errno_tcp(int argc, char **argv) {
    (void)argc; (void)argv;
    int n = sizeof(connect_block_filter) / sizeof(connect_block_filter[0]);
    if (install_filter(connect_block_filter, n) < 0) return 1;
    printf("seccomp-BPF connect-block installed:connect() 返回 ENOSYS\n");
    /* 测试 connect(应返回 -1 errno=ENOSYS) */
    int r = syscall(SYS_socket, AF_INET, SOCK_STREAM, 0);
    if (r < 0) {
        printf("socket() errno=%d\n", syscall(SYS_socket, AF_INET, SOCK_STREAM, 0));
        perror("socket");
        return 1;
    }
    /* 此处不真调用 connect,演示足够 */
    printf("(demo 不真调 connect,因 socket 不能连)\n");
    return 0;
}

static void dump_bpf(struct sock_filter *f, int n) {
    printf("/* struct sock_filter filter[] = { */\n");
    for (int i = 0; i < n; i++) {
        printf("    BPF_INSN(0x%04x, %2d, %2d, %d),  /* #%d */\n",
               f[i].code, f[i].jt, f[i].jf, f[i].k, i);
    }
    printf("/* }; */\n");
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s {whitelist|errno-tcp|dump whitelist}\n", argv[0]);
        return 1;
    }
    if (!strcmp(argv[1], "whitelist"))     return cmd_whitelist(argc, argv);
    if (!strcmp(argv[1], "errno-tcp"))     return cmd_errno_tcp(argc, argv);
    if (!strcmp(argv[1], "dump")) {
        int n = sizeof(whitelist_filter)/sizeof(whitelist_filter[0]);
        dump_bpf(whitelist_filter, n);
        return 0;
    }
    fprintf(stderr, "unknown: %s\n", argv[1]);
    return 1;
}

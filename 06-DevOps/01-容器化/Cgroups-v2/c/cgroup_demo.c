// Cgroups v2 资源限制最小演示 —— Linux 内核 cgroup-v2 文档实战
//
// 三个子 demo(./cgroup_demo <org|mem|cpu>,均需 root + cgroup v2):
//   org : 创建子 cgroup、启用控制器、自迁移进程、观察 memory.current
//   mem : 设 memory.max 后子进程超限分配 → OOM kill(memory.events.oom_kill)
//   cpu : 设 cpu.max 配额后忙循环 → 带宽限流(cpu.stat.nr_throttled)
//
// cgroup v2 关键接口(docs.kernel.org admin-guide/cgroup-v2):
//   cgroup.controllers    - 该 cgroup 可用的控制器列表
//   cgroup.subtree_control- 控制启用开关(写 "+cpu +memory")
//   cgroup.procs          - 成员进程 PID 列表(写 PID 即迁移)
//   memory.current/max    - 当前用量 / 硬上限(字节)
//   memory.events         - 事件计数(oom_kill 等)
//   cpu.max               - "$MAX $PERIOD" 带宽配额(微秒)
//   cpu.stat              - usage_usec / nr_periods / nr_throttled ...
//
// 编译: gcc -O2 -Wall -Wextra cgroup_demo.c -o cgroup_demo
#define _GNU_SOURCE
#include <sys/types.h>
#include <sys/wait.h>
#include <sys/stat.h>
#include <unistd.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CGROOT "/sys/fs/cgroup"
#define CGDEMO CGROOT "/cgroup-demo"

static void die(const char *msg) { perror(msg); exit(1); }

/* cgroupfs 是纯文本接口:整文件读 / 整文件写 */
static void read_file(const char *path, char *buf, size_t bufsz) {
    FILE *f = fopen(path, "r");
    if (!f) die(path);
    if (!fgets(buf, (int)bufsz, f)) { buf[0] = '\0'; }
    fclose(f);
    buf[strcspn(buf, "\n")] = '\0';
}

static void write_file(const char *path, const char *content) {
    FILE *f = fopen(path, "w");
    if (!f) die(path);
    if (fwrite(content, 1, strlen(content), f) < strlen(content)) die("fwrite");
    fclose(f);
}

/* 确保 root 的 subtree_control 启用所需控制器(缺才补,不回收)。
 * 注意"内部进程约束":只有 root cgroup 允许既有成员进程又启用控制器。 */
static void ensure_controllers(const char *needed) {
    char cur[256];
    read_file(CGROOT "/cgroup.subtree_control", cur, sizeof(cur));
    char add[64] = "";
    char tok[32];
    const char *p = needed;
    while (sscanf(p, "%31s", tok) == 1) {          /* 逐 token:"+cpu" 等 */
        p += strlen(tok);
        while (*p == ' ') p++;
        if (tok[0] == '+' && !strstr(cur, tok + 1)) {
            strcat(add, tok);                      /* 未启用则追加 */
            strcat(add, " ");
        }
    }
    if (add[0]) {
        write_file(CGROOT "/cgroup.subtree_control", add);
        printf("  已在 root 启用控制器: %s\n", add);
    }
}

/* demo 1: 组织进程 —— 建组 / 自迁移 / 观察内存计量 */
static void demo_org(void) {
    printf("== cgroup 组织与迁移演示 ==\n");
    mkdir(CGDEMO, 0755);
    ensure_controllers("+memory +cpu");

    char buf[256];
    read_file(CGDEMO "/memory.current", buf, sizeof(buf));
    printf("  迁移前 memory.current = %s B(空组)\n", buf);

    /* 自迁移:把本进程 PID 写入子 cgroup 的 cgroup.procs */
    char pidstr[16];
    snprintf(pidstr, sizeof(pidstr), "%d", getpid());
    write_file(CGDEMO "/cgroup.procs", pidstr);

    /* /proc/self/cgroup 应显示 0::/cgroup-demo(v2 单一层级,格式 "0::$PATH") */
    read_file("/proc/self/cgroup", buf, sizeof(buf));
    printf("  /proc/self/cgroup = %s\n", buf);

    /* 分配并触碰 64MB,观察 memory.current 增长(anonymous memory 被记账) */
    size_t sz = 64u << 20;
    volatile unsigned char *p = malloc(sz);
    if (!p) die("malloc");
    for (size_t i = 0; i < sz; i += 4096) p[i] = 1;   /* 逐页触碰才真正分配 */
    read_file(CGDEMO "/memory.current", buf, sizeof(buf));
    printf("  触碰 64MB 后 memory.current = %s B\n", buf);

    /* 清理:先迁回 root,再删组(有进程的组不可 rmdir) */
    write_file(CGROOT "/cgroup.procs", pidstr);
    if (rmdir(CGDEMO) < 0) die("rmdir");
    free((void *)p);
    printf("  已清理 %s\n", CGDEMO);
}

/* demo 2: memory.max 硬上限 + OOM kill */
static void demo_mem(void) {
    printf("== memory.max 资源限制演示 ==\n");
    mkdir(CGDEMO, 0755);
    ensure_controllers("+memory");

    char pidstr[16];
    snprintf(pidstr, sizeof(pidstr), "%d", getpid());
    write_file(CGDEMO "/cgroup.procs", pidstr);   /* 自迁移入组 */

    /* 硬上限 16MiB;超限触发内存回收,回收失败则 OOM kill 组内进程 */
    write_file(CGDEMO "/memory.max", "16777216");

    pid_t pid = fork();
    if (pid < 0) die("fork");
    if (pid == 0) {                               /* 子进程:超限分配 */
        size_t sz = 512u << 20;                   /* 512MB >> 16MiB 上限 */
        volatile unsigned char *p = malloc(sz);
        if (!p) _exit(2);
        for (size_t i = 0; i < sz; i += 4096) p[i] = 1;  /* 触碰直到被杀 */
        _exit(0);                                 /* 正常情况下到不了这里 */
    }
    int st;
    waitpid(pid, &st, 0);
    printf("  子进程被 OOM kill: %s(signal=%d, SIGKILL=9)\n",
           WIFSIGNALED(st) ? "是" : "否", WIFSIGNALED(st) ? WTERMSIG(st) : 0);

    char buf[512];
    FILE *f = fopen(CGDEMO "/memory.events", "r");
    while (f && fgets(buf, sizeof(buf), f))       /* oom_kill 计数应 ≥ 1 */
        printf("  memory.events: %s", buf);
    if (f) fclose(f);

    /* 清理:恢复无上限,迁回,删组 */
    write_file(CGDEMO "/memory.max", "max");
    write_file(CGROOT "/cgroup.procs", pidstr);
    rmdir(CGDEMO);
    printf("  已清理\n");
}

/* demo 3: cpu.max 带宽配额 + 限流统计 */
static void demo_cpu(void) {
    printf("== cpu.max 带宽配额演示 ==\n");
    mkdir(CGDEMO, 0755);
    ensure_controllers("+cpu");

    char pidstr[16];
    snprintf(pidstr, sizeof(pidstr), "%d", getpid());
    write_file(CGDEMO "/cgroup.procs", pidstr);

    /* "50000 100000" = 每 100ms 周期最多用 50ms CPU(50% 单核) */
    write_file(CGDEMO "/cpu.max", "50000 100000");

    pid_t pid = fork();
    if (pid < 0) die("fork");
    if (pid == 0) {                               /* 子进程:满速忙循环 ~2s */
        for (volatile long i = 0; i < 800000000L; i++)
            ;
        _exit(0);
    }
    waitpid(pid, NULL, 0);

    /* cpu.stat:usage_usec 应约为墙钟一半;nr_throttled > 0 表示被限流 */
    char buf[512];
    FILE *f = fopen(CGDEMO "/cpu.stat", "r");
    while (f && fgets(buf, sizeof(buf), f)) {
        buf[strcspn(buf, "\n")] = '\0';
        if (strncmp(buf, "usage_usec", 10) == 0 ||
            strncmp(buf, "nr_periods", 10) == 0 ||
            strncmp(buf, "nr_throttled", 12) == 0 ||
            strncmp(buf, "throttled_usec", 14) == 0)
            printf("  cpu.stat: %s\n", buf);
    }
    if (f) fclose(f);

    write_file(CGDEMO "/cpu.max", "max 100000");
    write_file(CGROOT "/cgroup.procs", pidstr);
    rmdir(CGDEMO);
    printf("  已清理\n");
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr,
            "用法: sudo %s <org|mem|cpu>\n"
            "前置: /sys/fs/cgroup 已挂载 cgroup v2(现代 systemd 发行版默认)\n",
            argv[0]);
        return 1;
    }
    /* cgroup.controllers 存在即 v2 已挂载 */
    struct stat s;
    if (stat(CGROOT "/cgroup.controllers", &s) < 0) {
        fprintf(stderr, "未检测到 cgroup v2(%s/cgroup.controllers 不存在)\n", CGROOT);
        return 1;
    }
    if (geteuid() != 0) {
        fprintf(stderr, "需要 root(写 cgroupfs)\n");
        return 1;
    }
    if (strcmp(argv[1], "org") == 0)      demo_org();
    else if (strcmp(argv[1], "mem") == 0) demo_mem();
    else if (strcmp(argv[1], "cpu") == 0) demo_cpu();
    else { fprintf(stderr, "未知子命令: %s\n", argv[1]); return 1; }
    return 0;
}

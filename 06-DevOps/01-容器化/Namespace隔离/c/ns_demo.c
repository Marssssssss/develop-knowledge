// Namespace 隔离最小演示 —— Linux namespaces(7) / unshare(2) 实战
//
// 三个子 demo(./ns_demo <uts|user|pid>):
//   uts  : fork 先行,子进程 unshare(CLONE_NEWUTS) 后改主机名,
//          父子各自读 hostname —— 互不可见(namespaces(7): "changes are
//          visible to other processes that are members of the namespace,
//          but are invisible to other processes")
//   user : unshare(CLONE_NEWUSER) 无需特权(unshare(2): "no privilege is
//          required"),写 uid_map/gid_map/setgroups 后获得新 ns 内全套
//          capabilities,再以普通用户身份创建 UTS namespace
//   pid  : unshare(CLONE_NEWPID|CLONE_NEWNS) 只影响后续子进程
//          (unshare(2): "only the children"),子进程在新 PID namespace
//          中 PID=1,并挂载新 procfs(pid_namespaces(7): ps(1) 需要)
//
// 编译: gcc -O2 -Wall -Wextra ns_demo.c -o ns_demo
// 运行: sudo ./ns_demo uts && ./ns_demo user && sudo ./ns_demo pid
#define _GNU_SOURCE
#include <sched.h>
#include <signal.h>
#include <sys/mount.h>
#include <sys/utsname.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void die(const char *msg) {
    perror(msg);
    exit(1);
}

/* 打印本进程视角的主机名(UTS namespace 隔离的资源) */
static void print_hostname(const char *who) {
    struct utsname u;
    if (uname(&u) < 0) die("uname");
    printf("  [%s] hostname = %s\n", who, u.nodename);
}

/* 打印 /proc/self/ns 下 namespace 句柄 inode 号,用于观察 ns 是否切换 */
static void print_ns_link(const char *name) {
    char buf[256];
    char path[64];
    snprintf(path, sizeof(path), "/proc/self/ns/%s", name);
    ssize_t n = readlink(path, buf, sizeof(buf) - 1);
    if (n < 0) die("readlink");
    buf[n] = '\0';
    printf("  %-4s ns handle: %s\n", name, buf);
}

/* demo 1: UTS namespace —— 主机名隔离 */
static void demo_uts(void) {
    printf("== UTS namespace 演示 ==\n");
    print_hostname("父(初始)");

    pid_t pid = fork();
    if (pid < 0) die("fork");
    if (pid == 0) {                       /* 子进程 */
        print_ns_link("uts");
        /* 非 root 需要 CAP_SYS_ADMIN;root 下直接成功 */
        if (unshare(CLONE_NEWUTS) < 0) die("unshare(CLONE_NEWUTS)");
        print_ns_link("uts");             /* inode 号已变化 => 新 UTS ns */

        if (sethostname("container-ns", strlen("container-ns")) < 0)
            die("sethostname");
        print_hostname("子(新 UTS ns)");  /* 看到新主机名 */
        _exit(0);
    }
    waitpid(pid, NULL, 0);
    print_hostname("父(旧 UTS ns)");      /* 仍看到旧主机名 */
    printf("  => 父子互不可见对方的修改\n");
}

/* 写 user namespace 的 ID 映射。格式见 user_namespaces(7):
 * uid_map 每行 "新ns内起始ID 父ns内起始ID 映射长度" */
static void write_map(const char *file, const char *content) {
    char path[64];
    snprintf(path, sizeof(path), "/proc/self/%s", file);
    FILE *f = fopen(path, "w");
    if (!f) die(file);
    if (fwrite(content, 1, strlen(content), f) < strlen(content)) die("fwrite map");
    fclose(f);
}

/* demo 2: user namespace —— 无特权获得 capabilities */
static void demo_user(void) {
    printf("== user namespace 演示(无需 root) ==\n");
    uid_t uid = getuid(), gid = getgid();
    printf("  真实身份: uid=%u gid=%u\n", uid, gid);

    /* CLONE_NEWUSER 是唯一不需要 CAP_SYS_ADMIN 的 namespace;
     * 要求调用进程非多线程 */
    if (unshare(CLONE_NEWUSER) < 0) die("unshare(CLONE_NEWUSER)");

    /* Linux 3.19 起无特权进程必须先 setgroups=deny 再写 gid_map
     * (防止用 setgroups(2) 绕过组权限检查) */
    write_map("setgroups", "deny");
    char map[64];
    snprintf(map, sizeof(map), "0 %u 1\n", uid);
    write_map("uid_map", map);            /* 新 ns 的 0 号 uid 映射到真实 uid */
    snprintf(map, sizeof(map), "0 %u 1\n", gid);
    write_map("gid_map", map);

    printf("  映射后身份: uid=%u gid=%u (getuid 在新 ns 内解释为 0)\n",
           getuid(), getgid());

    /* 验证:获得了新 user namespace 内的全套 capabilities。
     * /proc/self/status 的 CapEff 是内核视角的有效能力位图 */
    FILE *f = fopen("/proc/self/status", "r");
    char line[256];
    while (f && fgets(line, sizeof(line), f))
        if (strncmp(line, "CapEff:", 7) == 0) {
            printf("  %s", line);         /* 全 ffffffffffffffff 即全套 */
            break;
        }
    if (f) fclose(f);

    /* 关键推论:新 user ns 内有全套 capabilities,因此可以继续
     * 无特权地创建其他 namespace(unshare(2) NOTES) */
    if (unshare(CLONE_NEWUTS) < 0) die("无特权 unshare(CLONE_NEWUTS)");
    printf("  无特权创建 UTS namespace 成功(依赖新 user ns 的 capabilities)\n");
}

/* demo 3: PID namespace —— 子进程成为 PID 1 */
static void demo_pid(void) {
    printf("== PID namespace 演示 ==\n");
    printf("  [父] unshare 前 getpid() = %d\n", getpid());

    /* 关键语义:CLONE_NEWPID 只让"后续子进程"进入新 PID namespace,
     * 调用者自身不动(否则 getpid() 突变会破坏应用);
     * 同时创建 mount namespace 以便挂载新 procfs */
    if (unshare(CLONE_NEWPID | CLONE_NEWNS) < 0)
        die("unshare(CLONE_NEWPID|CLONE_NEWNS)");

    pid_t pid = fork();
    if (pid < 0) die("fork");
    if (pid == 0) {                       /* 新 PID ns 内的第一个进程 */
        printf("  [子] getpid() = %d (新 ns 内 PID 1)\n", getpid());
        printf("  [子] 父进程在我 ns 视角下 getppid() = %d\n", getppid());

        /* 挂载新 procfs:只显示本 ns 内进程(pid_namespaces(7))。
         * MS_REC|MS_PRIVATE 先断开共享传播,避免泄漏到宿主 */
        if (mount(NULL, "/", NULL, MS_REC | MS_PRIVATE, NULL) < 0)
            die("mount private");
        if (mount("proc", "/proc", "proc", 0, NULL) < 0)
            die("mount proc");
        printf("  [子] 新 /proc 下可见进程数(不含内核线程):\n");
        FILE *f = popen("ls /proc | grep -E '^[0-9]+$'", "r");
        char line[64];
        while (f && fgets(line, sizeof(line), f))
            printf("    pid=%s", line);
        if (f) pclose(f);
        _exit(0);
    }
    int st;
    waitpid(pid, &st, 0);
    printf("  [父] getpid() = %d (仍在旧 ns,值不变)\n", getpid());
    printf("  => PID namespace 只对子进程生效,单向不可回退\n");
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr,
            "用法: %s <uts|user|pid>\n"
            "  uts  需要 root(CAP_SYS_ADMIN)\n"
            "  user 无需 root\n"
            "  pid  需要 root\n", argv[0]);
        return 1;
    }
    if (strcmp(argv[1], "uts") == 0)       demo_uts();
    else if (strcmp(argv[1], "user") == 0) demo_user();
    else if (strcmp(argv[1], "pid") == 0)  demo_pid();
    else { fprintf(stderr, "未知子命令: %s\n", argv[1]); return 1; }
    return 0;
}

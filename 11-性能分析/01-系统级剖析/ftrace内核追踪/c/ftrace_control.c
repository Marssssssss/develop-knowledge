/*
 * ftrace_control.c — 通过 tracefs 控制 ftrace(C 版,教学用)
 *
 * 演示:
 *   1) 找到 tracefs 挂载点(/sys/kernel/tracing,兼容 /sys/kernel/debug/tracing)
 *   2) 按 关旧 tracer -> 选 tracer -> 设 set_graph_function -> tracing_on -> 读 trace -> 恢复 的顺序操作
 *   3) 打印 per-CPU ring buffer 的 stats(overrun 才是"丢了事件"的权威证据)
 *   4) 无权限时降级为"应执行的命令清单"并解释 trace 与 trace_pipe 的语义差异
 *
 * 权威依据:docs.kernel.org/trace/ftrace.html(控制文件、function_graph 格式、
 *           overhead 标记阈值、trace vs trace_pipe、per_cpu stats、error_log)
 *
 * 编译: gcc -O2 -Wall -Wextra -pedantic ftrace_control.c -o ftrace_control
 * 运行: ./ftrace_control ['do_sys_open']
 */

#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define MAXLINE 512
#define MAXPATH 256

static const char *g_base = NULL;

static int exists_writable(const char *path)
{
    return access(path, W_OK) == 0;
}

static const char *find_tracefs(void)
{
    static const char *cands[] = {"/sys/kernel/tracing", "/sys/kernel/debug/tracing"};
    for (size_t i = 0; i < sizeof(cands) / sizeof(cands[0]); i++) {
        char buf[MAXPATH];
        snprintf(buf, sizeof(buf), "%s/tracing_on", cands[i]);
        if (exists_writable(buf))
            return cands[i];
    }
    return NULL;
}

/* 往 tracefs 的某个控制文件写入一行;返回 0 成功、-1 失败 */
static int write_ctrl(const char *file, const char *value)
{
    char path[MAXPATH];
    int fd;
    size_t len;

    snprintf(path, sizeof(path), "%s/%s", g_base, file);
    fd = open(path, O_WRONLY | O_TRUNC);
    if (fd < 0) {
        fprintf(stderr, "  ! 写入 %s 失败: %s\n", path, strerror(errno));
        return -1;
    }
    len = strlen(value);
    if (write(fd, value, len) != (ssize_t)len) {
        fprintf(stderr, "  ! write(%s): %s\n", path, strerror(errno));
        close(fd);
        return -1;
    }
    close(fd);
    return 0;
}

/* 读 tracefs 控制文件/输出文件的前 n 行 */
static int print_head(const char *file, int max_lines)
{
    char path[MAXPATH], line[MAXLINE];
    FILE *f;
    int n = 0;

    snprintf(path, sizeof(path), "%s/%s", g_base, file);
    f = fopen(path, "r");
    if (!f) {
        fprintf(stderr, "  ! 读取 %s 失败: %s\n", path, strerror(errno));
        return -1;
    }
    while (n < max_lines && fgets(line, sizeof(line), f)) {
        fputs("    ", stdout);
        fputs(line, stdout);
        n++;
    }
    fclose(f);
    return n;
}

static void print_overhead_markers(void)
{
    puts("  function_graph 的 overhead 标记(出现在 duration 列):");
    puts("    '$' > 1 s | '@' > 100 ms | '*' > 10 ms | '#' > 1000 us | '!' > 100 us | '+' > 10 us");
    puts("    标记是内核自己打的——它就是\"哪个函数该先看\"的第一手线索");
}

static void print_semantics(void)
{
    puts("  trace      静态文本快照,【不是】消费者;追踪开启时读可能不一致");
    puts("             清空方式:echo > trace(O_TRUNC 打开即清)");
    puts("  trace_pipe 消费者:读完即消费,无新数据时【阻塞】——cat 卡住是设计行为");
    puts("             timeout 3 cat trace_pipe | head");
    puts("  *_raw      二进制(trace_pipe_raw),配合 splice() 高效导出");
}

static void print_fallback(const char *target)
{
    char cmd[MAXLINE];

    puts("=== 无 tracefs 写权限(或非 Linux):应执行的命令 ===");
    printf("  sudo mount -t tracefs nodev /sys/kernel/tracing\n");
    printf("  sudo sh -c 'echo nop > /sys/kernel/tracing/current_tracer'        # 先关干净\n");
    printf("  sudo sh -c 'echo function_graph > /sys/kernel/tracing/current_tracer'\n");
    snprintf(cmd, sizeof(cmd), "  sudo sh -c 'echo %s > /sys/kernel/tracing/set_graph_function'\n", target);
    fputs(cmd, stdout);
    printf("  sudo sh -c 'echo 1 > /sys/kernel/tracing/tracing_on'\n");
    printf("  # 触发一次系统调用(例如 cat /etc/hostname),再关掉 tracing_on:\n");
    printf("  sudo sh -c 'echo 0 > /sys/kernel/tracing/tracing_on'\n");
    printf("  sudo cat /sys/kernel/tracing/trace | head -40\n");
    printf("  sudo cat /sys/kernel/tracing/per_cpu/cpu0/stats        # overrun 非 0 就是丢了事件\n");
    printf("  sudo cat /sys/kernel/tracing/error_log                 # 只留最近 8 条错误\n");
    printf("  sudo sh -c 'echo nop > /sys/kernel/tracing/current_tracer'   # 收尾\n");
    puts("\n提示:改动 current_tracer 会清空 ring buffer 与 snapshot buffer,");
    puts("      排查现场前先读走 trace,或先往 snapshot 里存一份。");
}

static int run_real(const char *target)
{
    char cur[64] = "";

    /* 先记录当前 tracer,便于恢复(写 current_tracer 会清空 buffer) */
    {
        char path[MAXPATH];
        FILE *f;
        snprintf(path, sizeof(path), "%s/current_tracer", g_base);
        f = fopen(path, "r");
        if (f) {
            if (fgets(cur, sizeof(cur), f))
                cur[strcspn(cur, "\n")] = '\0';
            fclose(f);
        }
    }
    printf("=== tracefs = %s(原 current_tracer = '%s')===\n", g_base, cur);

    puts("[1/6] 先关干净:echo nop > current_tracer");
    write_ctrl("current_tracer", "nop");
    puts("[2/6] 选 tracer:echo function_graph > current_tracer");
    write_ctrl("current_tracer", "function_graph");
    printf("[3/6] 收窄范围:echo %s > set_graph_function\n", target);
    write_ctrl("set_graph_function", target);
    puts("[4/6] 打开写缓冲:echo 1 > tracing_on");
    write_ctrl("tracing_on", "1");

    puts("[5/6] 制造一次系统调用(open /etc/hostname)…");
    {
        int fd = open("/etc/hostname", O_RDONLY);
        if (fd >= 0)
            close(fd);
    }
    write_ctrl("tracing_on", "0");

    puts("[6/6] 读结果(trace 前 12 行)与 per-CPU 统计:");
    print_head("trace", 12);
    print_head("per_cpu/cpu0/stats", 8);

    /* 收尾:恢复原 tracer */
    if (cur[0])
        write_ctrl("current_tracer", cur);
    write_ctrl("tracing_on", "1");
    puts("已恢复 current_tracer 并重新 tracing_on=1");
    return 0;
}

int main(int argc, char **argv)
{
    const char *target = (argc > 1) ? argv[1] : "do_sys_open";

    puts("ftrace = 内核内置追踪框架,靠读写 tracefs 文件控制;");
    puts("函数入口默认是 nop,启用时才被 text patching 成跳转 —— 所以未启用时开销≈0。\n");
    print_overhead_markers();
    puts("");
    print_semantics();
    puts("");

    g_base = find_tracefs();
    if (!g_base) {
        print_fallback(target);
        return 0;
    }
    return run_real(target);
}

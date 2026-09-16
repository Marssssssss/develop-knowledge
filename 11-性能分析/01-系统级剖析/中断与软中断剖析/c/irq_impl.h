/* irq_impl.h — irq_check.c 的实现部分(文本级包含,不是独立翻译单元)
 *
 * 包含:头文件、常量、切词工具、/proc/interrupts 与 /proc/net/softnet_stat 解析、
 *       %si 归因判定、IRQ 亲和性位掩码、/proc/stat 的 intr/softirq 两行解析。
 *       自检、fixture 与 main 在 irq_check.c。
 *
 * 为什么用 #include 而不是拆成第二个 .c:本机没有 C 工具链,拆成两个翻译单元就
 * 必须同步改 static/原型,改错也编不出来、发现不了。文本包含让所有 static 定义
 * 仍留在同一个 TU 里,零链接风险,只是让单文件行数落到 300 行以内。
 *
 * 编译入口始终是 irq_check.c:  cc -O2 -o irq_check irq_check.c
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAXTOK 64
#define MAXCPU 64

/* softirq 类型(kernel 预定义,顺序与 /proc/softirqs、/proc/stat 的 softirq 行一致) */
static const char *SOFTIRQ_NAMES[10] = {"HI", "TIMER", "NET_TX", "NET_RX", "BLOCK",
                                        "IRQ_POLL", "TASKLET", "SCHED", "HRTIMER", "RCU"};
#define NETDEV_BUDGET_DEFAULT 300   /* net.core.netdev_budget 的内核默认值 */

/* 归因结论 */
#define REAL_WORK     "REAL_WORK"      /* 高 %si 是真活,不是排队问题 */
#define RAISE_BACKLOG "RAISE_BACKLOG"  /* 队列太浅,包被丢在 backlog 里 */
#define RAISE_BUDGET  "RAISE_BUDGET"   /* 单次预算用完还有活,被 time_squeeze 打断 */

/* ---------------------------------------------------------------- 切词工具 */
static int split_ws(const char *line, char tok[][64], int max) {
    int n = 0;
    const char *p = line;
    while (*p && n < max) {
        while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r') p++;
        if (!*p) break;
        {
            int k = 0;
            while (*p && *p != ' ' && *p != '\t' && *p != '\n' && *p != '\r' && k < 63)
                tok[n][k++] = *p++;
            tok[n][k] = '\0';
            n++;
        }
    }
    return n;
}

static int all_cpu_prefix(char tok[][64], int n) {
    int i;
    if (n == 0) return 0;
    for (i = 0; i < n; i++) if (strncmp(tok[i], "CPU", 3) != 0) return 0;
    return 1;
}

/* ---------------------------------------------------------------- /proc/interrupts */
/* 返回: CPU 列数;rows/labels/rest 由调用方提供。
   关键:紧跟标签的前 ncpu 个整数才是每核计数,后面的 token 是控制器类型与驱动名;
   标签【没有编号】的行(NMI:/LOC:/RES: …)是架构向量,不是 IRQ。 */
typedef struct {
    char  label[64];
    long  counts[MAXCPU];
    int   ncount;
    char  rest[MAXTOK][64];
    int   nrest;
} irq_row;

static int parse_interrupts(char *text, irq_row *rows, int maxrows, char cpu_cols[][64]) {
    int ncols = 0, nrows = 0;
    char tok[MAXTOK][64];
    char *line = strtok(text, "\n");
    while (line && nrows < maxrows) {
        int n = split_ws(line, tok, MAXTOK), i;
        if (n > 0) {
            if (ncols == 0 && all_cpu_prefix(tok, n)) {
                ncols = n;
                for (i = 0; i < n && i < MAXCPU; i++) strcpy(cpu_cols[i], tok[i]);
            } else if (tok[0][strlen(tok[0]) - 1] == ':') {
                irq_row *r = &rows[nrows];
                memset(r, 0, sizeof *r);
                strncpy(r->label, tok[0], strlen(tok[0]) - 1);
                for (i = 0; i < ncols && 1 + i < n; i++) {
                    char *end;
                    long v = strtol(tok[1 + i], &end, 10);
                    if (*end != '\0') break;
                    r->counts[r->ncount++] = v;
                }
                for (; 1 + i < n && r->nrest < MAXTOK; i++)
                    strcpy(r->rest[r->nrest++], tok[1 + i]);
                nrows++;
            }
        }
        line = strtok(NULL, "\n");
    }
    return nrows;   /* ncols 通过 cpu_cols 之外的全局不好传,故下面单独用一个函数取 */
}

/* 标签能否当整数读 —— 判断「是不是 IRQ」的唯一依据,不用白名单 */
static int is_numbered(const char *label) {
    const char *p = label;
    if (!*p) return 0;
    for (; *p; p++) if (*p < '0' || *p > '9') return 0;
    return 1;
}

/* ---------------------------------------------------------------- /proc/net/softnet_stat */
/* 每 CPU 一行,【全部是十六进制】【文件里没有表头】。
   列序必须靠外部知识:1=处理过的包数 2=因 backlog 满而丢的包数 3=time_squeeze。 */
static int parse_softnet(char *text, long rows[][16], int maxrows, int *cols_out) {
    int n = 0;
    char tok[MAXTOK][64];
    char *line = strtok(text, "\n");
    *cols_out = 0;
    while (line && n < maxrows) {
        int k = split_ws(line, tok, MAXTOK), i;
        if (k > 0) {
            if (k > *cols_out) *cols_out = k;
            for (i = 0; i < k && i < 16; i++) rows[n][i] = strtol(tok[i], NULL, 16);
            n++;
        }
        line = strtok(NULL, "\n");
    }
    return n;
}

static void softnet_totals(long rows[][16], int n, int cols, long *p, long *d, long *s) {
    int i;
    *p = *d = *s = 0;
    for (i = 0; i < n; i++) {
        if (cols > 0) *p += rows[i][0];
        if (cols > 1) *d += rows[i][1];
        if (cols > 2) *s += rows[i][2];
    }
}

/* 把「%si 高」拆成三种完全不同的动作。丢包优先于 squeeze:
   丢包是已经发生的损失,squeeze 只是「被打断」,前者更紧急。 */
static const char *attribute(long dropped, long squeeze) {
    if (dropped <= 0 && squeeze <= 0) return REAL_WORK;
    return dropped > squeeze ? RAISE_BACKLOG : RAISE_BUDGET;
}

/* ---------------------------------------------------------------- IRQ 亲和性 */
/* 十六进制位掩码 -> 掩码整数。支持 'f' / '0x3' / 'ffffffff,ffffffff'。
   逗号分组时【低位组在前】:第 1 组代表 CPU 0..31,第 2 组代表 32..63。
   返回 -1 表示解析失败。 */
static long long parse_smp_affinity(const char *text) {
    char buf[128], *p, *g;
    long long mask = 0;
    int idx = 0;
    size_t i, j = 0;
    for (i = 0; text[i] && j < sizeof buf - 1; i++)
        if (text[i] != ' ' && text[i] != '\t' && text[i] != '\n' && text[i] != '\r')
            buf[j++] = text[i];
    buf[j] = '\0';
    if (j == 0) return -1;
    /* 去掉可能存在的 0x / 0X 前缀(每个分组前都可能出现) */
    p = buf;
    while (*p) {
        char grp[32];
        int k = 0;
        g = p;
        while (*g && *g != ',' && k < 31) {
            if (g[0] == '0' && (g[1] == 'x' || g[1] == 'X')) { g += 2; continue; }
            grp[k++] = *g++;
        }
        grp[k] = '\0';
        if (k == 0) return -1;
        {
            char *end;
            long long v = strtoll(grp, &end, 16);
            if (*end != '\0') return -1;
            mask |= v << (32 * idx);
        }
        idx++;
        p = (*g == ',') ? g + 1 : g;
    }
    return mask;
}

static int mask_to_cpus(long long mask, int *out) {
    int i, n = 0;
    for (i = 0; i < 64; i++) if (mask >> i & 1) out[n++] = i;
    return n;
}

static int check_affinity(long long mask) { return mask == 0; }

/* '0-1' / '0,3' / '1024-1031' -> CPU 列表。这是给人看的接口,避免掩码要写 32 个零。 */
static int parse_affinity_list(const char *text, int *out) {
    int n = 0;
    const char *p = text;
    while (*p) {
        while (*p == ' ' || *p == ',') p++;
        if (!*p) break;
        {
            char *end;
            long lo = strtol(p, &end, 10);
            if (end == p) break;
            p = end;
            if (*p == '-') {
                long hi = strtol(p + 1, &end, 10);
                long v;
                for (v = lo; v <= hi; v++) out[n++] = (int)v;
                p = end;
            } else {
                out[n++] = (int)lo;
            }
        }
    }
    return n;
}

/* ---------------------------------------------------------------- /proc/stat */
/* 取出 intr 与 softirq 两行。结构一样:第一个数是总数,后面是分项。
   但口径不同:intr 的总数【大于】分项之和(未编号架构向量只计入总数),
   而 softirq 的总数【严格等于】分项之和。 */
static int parse_proc_stat(const char *text, long *intr, int *nintr,
                           long *sirq, int *nsirq) {
    const char *p = text;
    *nintr = *nsirq = 0;
    while (*p) {
        const char *e = strchr(p, '\n');
        size_t len = e ? (size_t)(e - p) : strlen(p);
        if (len > 5 && strncmp(p, "intr ", 5) == 0) {
            const char *q = p + 5;
            while (q < p + len) {
                char *end;
                long v = strtol(q, &end, 10);
                if (end == q) break;
                if (*nintr < 64) intr[(*nintr)++] = v;
                q = end;
            }
        } else if (len > 8 && strncmp(p, "softirq ", 8) == 0) {
            const char *q = p + 8;
            while (q < p + len) {
                char *end;
                long v = strtol(q, &end, 10);
                if (end == q) break;
                if (*nsirq < 64) sirq[(*nsirq)++] = v;
                q = end;
            }
        }
        if (!e) break;
        p = e + 1;
    }
    return (*nintr > 0) && (*nsirq > 0);
}


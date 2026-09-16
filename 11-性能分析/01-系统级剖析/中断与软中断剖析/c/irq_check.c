/* irq_check.c — 中断与软中断剖析
 *
 * 与 python/irq_check.py、go/irq_check.go 姊妹实现,同一组口径断言。
 *
 * 四份文件口径全都不一样:
 *   /proc/interrupts             每个 IRQ 在每个 CPU 上的累计处理次数
 *   /proc/softirqs               每种 softirq 在每个 CPU 上的累计次数
 *   /proc/net/softnet_stat       每 CPU 一行,【十六进制、没有表头】
 *   /proc/irq/<N>/smp_affinity   十六进制 CPU 位掩码(超过 32 核是逗号分组、低位组在前)
 *
 * 编译: cc -O2 -o irq_check irq_check.c
 * 运行: ./irq_check
 */
#include "irq_impl.h"

/* ---------------------------------------------------------------- 自检 */
static int g_ok = 1, g_pass = 0, g_total = 0;
static void check(const char *label, int cond, const char *detail) {
    g_total++;
    if (cond) g_pass++; else g_ok = 0;
    printf("  [%s] %s%s%s\n", cond ? "PASS" : "FAIL", label,
           (detail && *detail) ? "  <- " : "", (detail && *detail) ? detail : "");
}

/* 注意:INTERRUPTS 与 SOFTNET 必须是【可写数组】—— 下面的解析用 strtok,
   它会就地改写字符串。写成 const char * 再强转,等于往只读段写,直接段错误。
   PROC_STAT 不需要可写:parse_proc_stat 只用 strncmp/strtol,不改字符串。 */
static char INTERRUPTS[] =
    "           CPU0       CPU1       CPU2       CPU3\n"
    "  0:         46          0          0          0   IO-APIC    2-edge      timer\n"
    "  1:          3          0          0          0   IO-APIC    1-edge      i8042\n"
    " 24:      10234       5601       4200       8991   PCI-MSI 524288-edge      eth0\n"
    " 25:      54321       6789       4321       1234   PCI-MSI 524289-edge      nvme0q0\n"
    " 32:          0    1034521          0          0   PCI-MSI-edge      eth0-TxRx-0\n"
    " 33:          0          0    1034522          0   PCI-MSI-edge      eth0-TxRx-1\n"
    "NMI:         12         14         13         12   Non-maskable interrupts\n"
    "LOC:    1234567    1234568    1234569    1234570   Local timer interrupts\n"
    "RES:       4321       2109       3210       4102   Rescheduling interrupts\n";

static char SOFTNET[] =
    "0001e240 0000000c 00000003 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000\n"
    "0001e241 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000\n";

static const char *PROC_STAT =
    "cpu  100 0 50 800 0 0 0 0 0 0\n"
    "intr 9876543 46 3 0 10234 54321 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
    "ctxt 1234567\n"
    "softirq 33967864 1 4938234 2036 16059360 0 0 1235 4938268 0 8028730\n";

int main(void) {
    char d[200], cpu_cols[MAXCPU][64];
    irq_row rows[32];
    int nrows, i, numbered = 0, arch = 0;
    long sn[8][16];
    int sn_n, sn_cols = 0;

    /* 1. /proc/interrupts 的行结构 */
    printf("== 1. /proc/interrupts 的行结构 ==\n");
    nrows = parse_interrupts(INTERRUPTS, rows, 32, cpu_cols);
    {
        irq_row *r24 = NULL;
        for (i = 0; i < nrows; i++) if (strcmp(rows[i].label, "24") == 0) r24 = &rows[i];
        check("解析出 9 条数据行", nrows == 9, NULL);
        check("IRQ 24(eth0)每核计数 = 10234/5601/4200/8991",
              r24 && r24->ncount == 4 && r24->counts[0] == 10234 && r24->counts[1] == 5601 &&
              r24->counts[2] == 4200 && r24->counts[3] == 8991, NULL);
        check("计数只取前 4 个整数,后面的控制器类型不被当成计数",
              r24 && r24->nrest == 3 && strcmp(r24->rest[0], "PCI-MSI") == 0 &&
              strcmp(r24->rest[1], "524288-edge") == 0, NULL);
        check("设备名 = 剩余 token 的最后一个",
              r24 && r24->nrest > 0 && strcmp(r24->rest[r24->nrest - 1], "eth0") == 0, NULL);
    }

    /* 2. 没有编号的架构向量不是 IRQ */
    printf("== 2. 没有编号的架构向量不是 IRQ ==\n");
    for (i = 0; i < nrows; i++) {
        if (is_numbered(rows[i].label)) numbered++;
        else if (strncmp(rows[i].label, "CPU", 3) != 0) arch++;
    }
    snprintf(d, sizeof d, "%d", numbered);
    check("识别出 6 条有编号的 IRQ", numbered == 6, d);
    snprintf(d, sizeof d, "%d", arch);
    check("NMI/LOC/RES 3 条被归为架构向量而非 IRQ", arch == 3, d);
    check("判断依据是「标签能否当整数读」,不是白名单",
          is_numbered("24") && !is_numbered("NMI") && !is_numbered("LOC"), NULL);

    /* 3. 多队列网卡的中断分散 */
    printf("== 3. 多队列网卡的中断分散 ==\n");
    {
        irq_row *q0 = NULL, *q1 = NULL;
        for (i = 0; i < nrows; i++) {
            if (strcmp(rows[i].label, "32") == 0) q0 = &rows[i];
            if (strcmp(rows[i].label, "33") == 0) q1 = &rows[i];
        }
        check("eth0-TxRx-0 只落在 CPU1(1034521)", q0 && q0->counts[1] == 1034521, NULL);
        check("eth0-TxRx-1 只落在 CPU2(1034522)", q1 && q1->counts[2] == 1034522, NULL);
        check("两个队列都长了 -> 分散成功;若都只落 CPU0 就说明没做多队列",
              q0 && q1 && q0->counts[0] == 0 && q1->counts[0] == 0, NULL);
    }

    /* 4. softirq 名字来自内核预定义表 */
    printf("== 4. softirq 名字来自内核预定义表 ==");
    printf(" (第 4 个 = %s)\n", SOFTIRQ_NAMES[3]);
    check("NET_RX 是第 4 个(下标 3),顺序不能靠行内猜", strcmp(SOFTIRQ_NAMES[3], "NET_RX") == 0, NULL);
    check("BLOCK/IRQ_POLL 为 0 是正常的(没有对应负载)",
          strcmp(SOFTIRQ_NAMES[4], "BLOCK") == 0 && strcmp(SOFTIRQ_NAMES[5], "IRQ_POLL") == 0, NULL);

    /* 5. /proc/net/softnet_stat 是十六进制、无表头 */
    printf("== 5. /proc/net/softnet_stat 是十六进制、无表头 ==\n");
    sn_n = parse_softnet(SOFTNET, sn, 8, &sn_cols);
    {
        long p, dr, sq, p2, d2, s2;
        snprintf(d, sizeof d, "行数=%d 列数=%d", sn_n, sn_cols);
        check("每 CPU 一行,共 2 行,每行 13 列", sn_n == 2 && sn_cols == 13, d);
        snprintf(d, sizeof d, "%ld", sn[0][0]);
        check("0001e240 按十六进制 = 123456,不是十进制 124480", sn[0][0] == 123456 && sn[0][0] == 0x1E240, d);
        softnet_totals(sn, sn_n, sn_cols, &p, &dr, &sq);
        snprintf(d, sizeof d, "proc=%ld drop=%ld squeeze=%ld", p, dr, sq);
        check("第 1/2/3 列分别是 处理数 / backlog 丢包 / time_squeeze",
              p == 123456 + 123457 && dr == 12 && sq == 3, d);
        sn[0][0] = 0x1E241; sn[0][1] = 20; sn[0][2] = 5;
        sn[1][0] = 0x1E241; sn[1][1] = 0;  sn[1][2] = 0;
        softnet_totals(sn, sn_n, sn_cols, &p2, &d2, &s2);
        snprintf(d, sizeof d, "+%ld/+%ld/+%ld", p2 - p, d2 - dr, s2 - sq);
        check("差分也要按列分别做:处理数 +1、丢包 +8、squeeze +2",
              p2 - p == 1 && d2 - dr == 8 && s2 - sq == 2, d);
        check("'00000123' 既是合法十进制(123)又是合法十六进制(291),按十进制读是静默错数",
              strtol("00000123", NULL, 16) == 291 && strtol("00000123", NULL, 10) == 123, NULL);
    }

    /* 6. 归因:把「%si 高」拆成三种动作 */
    printf("== 6. 归因:把「%si 高」拆成三种动作 ==\n");
    check("丢包长 + squeeze 不长 -> 抬 netdev_max_backlog",
          strcmp(attribute(500, 0), RAISE_BACKLOG) == 0, NULL);
    check("squeeze 长 + 不丢包 -> 抬 netdev_budget",
          strcmp(attribute(0, 500), RAISE_BUDGET) == 0, NULL);
    check("丢包优先于 squeeze(丢包是已发生的损失,更紧急)",
          strcmp(attribute(900, 100), RAISE_BACKLOG) == 0, NULL);
    check("两个都不长 -> 高 %si 是真活,该分散 IRQ / 合并中断",
          strcmp(attribute(0, 0), REAL_WORK) == 0, NULL);
    check("netdev_budget 内核默认 300", NETDEV_BUDGET_DEFAULT == 300, NULL);

    /* 7. smp_affinity 位掩码 */
    printf("== 7. smp_affinity 位掩码 ==\n");
    {
        int cpus[64], n;
        n = mask_to_cpus(parse_smp_affinity("f"), cpus);
        check("'f' -> CPU 0-3", n == 4 && cpus[0] == 0 && cpus[3] == 3, NULL);
        check("'0000000f' 前导零不影响", parse_smp_affinity("0000000f") == 0xF, NULL);
        n = mask_to_cpus(parse_smp_affinity("0x3"), cpus);
        check("'0x3' 带前缀也能读", n == 2 && cpus[0] == 0 && cpus[1] == 1, NULL);
        n = mask_to_cpus(parse_smp_affinity("9"), cpus);
        check("'9' -> CPU0 和 CPU3(位 0 与位 3)", n == 2 && cpus[0] == 0 && cpus[1] == 3, NULL);
        n = mask_to_cpus(parse_smp_affinity("ffffffff,ffffffff"), cpus);
        snprintf(d, sizeof d, "%d", n);
        check("'ffffffff,ffffffff' -> 64 个核", n == 64, d);
        n = mask_to_cpus(parse_smp_affinity("00000001,00000001"), cpus);
        check("逗号分组【低位组在前】:第 1 组管 CPU0-31,第 2 组管 32-63",
              n == 2 && cpus[0] == 0 && cpus[1] == 32, NULL);
        n = mask_to_cpus(parse_smp_affinity("00000000,ffffffff"), cpus);
        check("'00000000,ffffffff' -> 只用高位 32 核(CPU 32-63)", n == 32 && cpus[0] == 32, NULL);
        check("全 0 必须被拒绝:内核不允许把 IRQ 挂到「没有核」上",
              check_affinity(parse_smp_affinity("0")) == 1 && check_affinity(0xF) == 0, NULL);
        n = parse_affinity_list("1024-1031", cpus);
        check("'1024-1031' -> 8 个核,从 1024 开始(掩码写这个要 32 个零)",
              n == 8 && cpus[0] == 1024 && cpus[7] == 1031, NULL);
        n = parse_affinity_list("0,3", cpus);
        check("'0,3' -> [0,3]", n == 2 && cpus[0] == 0 && cpus[1] == 3, NULL);
    }

    /* 8. /proc/stat 的 intr / softirq 两行 */
    printf("== 8. /proc/stat 的 intr / softirq 两行 ==\n");
    {
        long intr[64], sirq[64], sum_i = 0, sum_s = 0;
        int ni, ns;
        int got = parse_proc_stat(PROC_STAT, intr, &ni, sirq, &ns);
        snprintf(d, sizeof d, "intr 个数=%d softirq 个数=%d", ni, ns);
        check("两个字段都取到(intr 33 个数、softirq 11 个数)", got == 1 && ni == 33 && ns == 11, d);
        check("intr 行:第一个数是总数,后面是分项",
              intr[0] == 9876543 && intr[1] == 46 && intr[2] == 3, NULL);
        check("softirq 行 = 总数 + 10 种类型", ns == 11, NULL);
        check("softirq 分项顺序与 SOFTIRQ_NAMES 对齐:第 1 项 HI=1,第 4 项 NET_RX=16059360",
              sirq[1] == 1 && sirq[4] == 16059360, NULL);
        for (i = 1; i < ns; i++) sum_s += sirq[i];
        for (i = 1; i < ni; i++) sum_i += intr[i];
        snprintf(d, sizeof d, "%ld vs %ld", sum_s, sirq[0]);
        check("softirq 行的总数【严格等于】分项之和(所有类型都被列出)", sum_s == sirq[0], d);
        snprintf(d, sizeof d, "总数 %ld vs 分项和 %ld", intr[0], sum_i);
        check("intr 行相反:总数【大于】分项之和 —— 未编号的架构向量只被计入总数",
              intr[0] > sum_i, d);
        check("这两行的口径差别是同一份文档里两句话,不看清楚就会算错占比",
              intr[0] - sum_i > 0, NULL);
    }

    printf("\n%s(%d/%d)\n", g_ok ? "全部通过" : "存在失败项", g_pass, g_total);
    return g_ok ? 0 : 1;
}

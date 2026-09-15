/* lockstep.c — 确定性帧同步 (deterministic lockstep) 最小实现与自检
 *
 * 编译: gcc -O2 -Wall -Wextra -pedantic lockstep.c -o lockstep
 *
 * 与 python/lockstep.py 一一对应:
 *   A. 通信回合 / 2 回合前瞻 / 丢包重传 的定量模型(AoE GDC 2001 模型);
 *   B. 3 个 peer 逐回合比校验和, 找首次失同步(out-of-sync)回合;
 *   C. 浮点 vs 定点: 求和顺序敏感性 + Q16.16 舍入方向。
 *
 * 核心结论: 帧同步只传指令、不传状态, 所以「同一段代码在任何机器上必须
 * 逐位产生相同结果」是硬约束 —— 整数/定点满足, 浮点不满足。
 */
#include <stdio.h>
#include <string.h>

#define N_UNITS   8
#define LOOKAHEAD 2                 /* 指令预约 2 个通信回合后执行 */
#define SCALE     (1 << 16)         /* Q16.16 */
#define N_TURNS   20

static int failures;

static void check(int cond, const char *what)
{
    if (!cond) { printf("  [FAIL] %s\n", what); failures++; }
}

/* ---------------- A. 通信回合 / 前瞻 / 重传 ---------------- */
typedef struct {
    int latency, drops, executed, comm_turns, stall_events, setup_delay;
} Lockstep;

static Lockstep run_lockstep(int n_peers, int latency, int n_turns,
                             const int *drops, int n_drops, int rto)
{
    /* ready[exec_turn] 是 bitmask: 哪些 peer 的指令已到齐 */
    unsigned char ready[N_TURNS + LOOKAHEAD + 4];
    /* arrive[p][s] = peer p 在通信回合 s 发出的包的到达回合; 0 = 未发出 */
    static int arrive[8][512];
    Lockstep r = {latency, n_drops, 0, 0, 0, 0};
    int first_exec = 1 + LOOKAHEAD;
    int last_exec = n_turns + LOOKAHEAD;
    int next_exec = first_exec, first_success = 0;
    int p, t, s;

    memset(ready, 0, sizeof(ready));
    memset(arrive, 0, sizeof(arrive));

    for (t = 1; t <= last_exec + 64; t++) {
        if (next_exec > last_exec) break;
        /* 1) 各 peer 在通信回合 t 发出本地指令, 预约在 t + LOOKAHEAD 执行 */
        for (p = 0; p < n_peers; p++) {
            int penalty = 0, i;
            for (i = 0; i < n_drops; i++)
                if (drops[i * 2] == p && drops[i * 2 + 1] == t) penalty = rto;
            arrive[p][t] = t + latency + penalty;
        }
        /* 2) 投递: 到达回合 <= t 的包标记到它所预约的执行回合上 */
        for (p = 0; p < n_peers; p++)
            for (s = 1; s <= t && s + LOOKAHEAD <= last_exec; s++)
                if (arrive[p][s] && arrive[p][s] <= t)
                    ready[s + LOOKAHEAD] |= (unsigned char)(1u << p);

        /* 3) 该执行回合的全部指令已到齐 -> 推进; 时隙已到却凑不齐 -> 卡顿 */
        if (ready[next_exec] == (unsigned char)((1u << n_peers) - 1u)) {
            if (!first_success) first_success = t;
            next_exec++;
        } else if (t >= next_exec) {
            r.stall_events++;
        }
    }
    r.executed = next_exec - first_exec;
    r.comm_turns = t - 1;
    r.setup_delay = (first_success ? first_success : t) - first_exec;
    if (r.setup_delay < 0) r.setup_delay = 0;
    return r;
}

/* ---------------- B/C. 世界与校验和 ---------------- */
enum { MODE_FIXED = 0, MODE_FLOAT = 1 };

typedef struct {
    int    mode;
    int    extra_rng;                    /* 故意多取一次随机数 */
    long long pool;                      /* 定点池 (Q16.16) */
    double poolf;                        /* 浮点池 */
    int    px[N_UNITS];
    int    hp[N_UNITS];
    unsigned rng;
} World;

static const double RATE_F[N_UNITS] = {
    1.0, 1.0 / 2, 1.0 / 3, 1.0 / 4, 1.0 / 5, 1.0 / 6, 1.0 / 7, 1.0 / 8
};

static long long rate_q(int i)
{
    double v = RATE_F[i] * (double)SCALE;
    return (long long)(v + 0.5);         /* 四舍五入到最近的 1/65536 */
}

static void world_init(World *w, int mode, int extra_rng)
{
    int i;
    memset(w, 0, sizeof(*w));
    w->mode = mode;
    w->extra_rng = extra_rng;
    for (i = 0; i < N_UNITS; i++) w->hp[i] = 100;
    w->rng = 20260915u;
}

typedef struct { int peer, uid, dx, dy; } Cmd;

static unsigned rng_next(unsigned *s)
{
    *s = ((unsigned long long)(*s) * 1103515245ULL + 12345ULL) & 0x7FFFFFFFULL;
    return *s;
}

static int cmp_cmd(const Cmd *a, const Cmd *b)
{
    if (a->uid != b->uid) return a->uid - b->uid;
    return a->peer - b->peer;
}

static void world_step(World *w, Cmd *cmds, int n, int descending)
{
    int i, j;
    long long acc_i = w->pool;
    double acc_f = w->poolf;
    unsigned r;

    /* 插入排序: 与到达顺序无关, 保证可复现 */
    for (i = 1; i < n; i++) {
        Cmd key = cmds[i];
        for (j = i - 1; j >= 0 && cmp_cmd(&cmds[j], &key) > 0; j--) cmds[j + 1] = cmds[j];
        cmds[j + 1] = key;
    }
    for (i = 0; i < n; i++) w->px[cmds[i].uid] += cmds[i].dx;

    for (i = 0; i < N_UNITS; i++) {
        int k = descending ? (N_UNITS - 1 - i) : i;
        if (w->mode == MODE_FIXED) acc_i += rate_q(k);
        else                       acc_f += RATE_F[k];
    }
    if (w->mode == MODE_FIXED) w->pool = acc_i;
    else                       w->poolf = acc_f;

    r = rng_next(&w->rng);
    if (w->extra_rng) rng_next(&w->rng);
    w->hp[r % N_UNITS] -= 1;
}

static unsigned long long world_checksum(const World *w)
{
    unsigned long long h = 0xCBF29CE484222325ULL;
    unsigned char buf[256];
    int n = 0, i;
    if (w->mode == MODE_FIXED) {
        memcpy(buf + n, &w->pool, sizeof(w->pool)); n += (int)sizeof(w->pool);
    } else {
        memcpy(buf + n, &w->poolf, sizeof(w->poolf)); n += (int)sizeof(w->poolf);
    }
    for (i = 0; i < N_UNITS; i++) { memcpy(buf + n, &w->px[i], 4); n += 4; }
    for (i = 0; i < N_UNITS; i++) { memcpy(buf + n, &w->hp[i], 4); n += 4; }
    memcpy(buf + n, &w->rng, 4); n += 4;
    for (i = 0; i < n; i++) {
        h ^= buf[i];
        h *= 0x100000001B3ULL;
    }
    return h;
}

/* peer 在 turn 回合产生的本地指令 */
static void local_cmd(int peer, int turn, Cmd *out)
{
    out->peer = peer;
    out->uid  = (peer * 3 + turn) % N_UNITS;
    out->dx   = (peer + 1) * SCALE / 64;
    out->dy   = turn % 3 - 1;
}

/* 返回首次失同步回合, 0 = 全程一致 */
static int run_peers(int mode, int bug_peer, int extra_rng, int *out_agree)
{
    World w[3];
    int p, turn, first = 0, agree = 1;
    for (p = 0; p < 3; p++) world_init(&w[p], mode, p == bug_peer ? extra_rng : 0);

    for (turn = 1; turn <= N_TURNS; turn++) {
        Cmd cmds[3];
        for (p = 0; p < 3; p++) local_cmd(p, turn, &cmds[p]);
        for (p = 0; p < 3; p++) world_step(&w[p], cmds, 3, p == bug_peer ? 1 : 0);
        if (world_checksum(&w[0]) != world_checksum(&w[1]) ||
            world_checksum(&w[0]) != world_checksum(&w[2])) {
            agree = 0;
            if (!first) first = turn;
        }
    }
    *out_agree = agree;
    return first;
}

/* ---------------- main ---------------- */
int main(void)
{
    Lockstep a, b, c;
    int no_drops[1] = {0};
    int drops[6] = {0, 3, 1, 3, 2, 3};
    int agree, t;
    int i;
    long long ia = 0, ib = 0;

    printf("== A. 通信回合 / 2 回合前瞻 / 丢包重传 ==\n");
    a = run_lockstep(3, 1, 12, no_drops, 0, 2);
    b = run_lockstep(3, 2, 12, no_drops, 0, 3);
    c = run_lockstep(3, 3, 12, no_drops, 0, 4);
    printf("  链路延迟 1 回合: 执行 %d/12, 起播延迟 %d, 卡顿 %d\n",
           a.executed, a.setup_delay, a.stall_events);
    printf("  链路延迟 2 回合: 执行 %d/12, 起播延迟 %d, 卡顿 %d\n",
           b.executed, b.setup_delay, b.stall_events);
    printf("  链路延迟 3 回合: 执行 %d/12, 起播延迟 %d, 卡顿 %d\n",
           c.executed, c.setup_delay, c.stall_events);
    check(a.setup_delay == 0 && b.setup_delay == 0, "延迟 <= 2 回合应被 2 回合前瞻全额吸收");
    check(c.setup_delay == c.latency - LOOKAHEAD, "延迟 3 回合应产生 1 回合起播延迟");

    {
        Lockstep d = run_lockstep(3, 1, 12, drops, 3, 2);
        Lockstep e = run_lockstep(3, 1, 12, no_drops, 0, 2);
        printf("  通信回合 3 上 3 个包全丢: 卡顿 %d 次 vs 无丢包 %d 次, "
               "通信回合 %d vs %d (+%d)\n",
               d.stall_events, e.stall_events, d.comm_turns, e.comm_turns,
               d.comm_turns - e.comm_turns);
        check(d.stall_events > e.stall_events, "丢包应造成额外卡顿");
        check(d.comm_turns > e.comm_turns, "丢包重传应拉长通信回合");
    }

    printf("\n== B. 校验和跨 peer 对比(找首次失同步回合) ==\n");
    t = run_peers(MODE_FIXED, -1, 0, &agree);
    printf("  定点 + 全部同序            首次失同步回合 = %s\n", t ? "有" : "未失同步");
    check(t == 0 && agree, "定点同序不应失同步");
    t = run_peers(MODE_FIXED, 2, 0, &agree);
    printf("  定点 + peer2 反向遍历      首次失同步回合 = %s\n", t ? "有" : "未失同步");
    check(t == 0, "整数加法与顺序无关, 定点反向遍历不应失同步");
    t = run_peers(MODE_FLOAT, 2, 0, &agree);
    printf("  浮点 + peer2 反向遍历      首次失同步回合 = %d\n", t);
    check(t == 2, "浮点换求和顺序应在第 2 回合分叉");
    t = run_peers(MODE_FIXED, 2, 1, &agree);
    printf("  定点 + peer2 多取一次随机  首次失同步回合 = %d\n", t);
    check(t == 1, "随机次数不一致应在第 1 回合分叉");

    printf("\n== C. 浮点 vs 定点 ==\n");
    /* 浮点: 升序 vs 降序累加 1/(i+1), 找首次分叉回合 */
    {
        double fa = 0.0, fb = 0.0;
        int first = 0, turn;
        for (turn = 1; turn <= 200 && !first; turn++) {
            double x = fa, y = fb;
            for (i = 0; i < N_UNITS; i++) x += RATE_F[i];
            for (i = N_UNITS - 1; i >= 0; i--) y += RATE_F[i];
            fa = x; fb = y;
            if (fa != fb) first = turn;
        }
        printf("  浮点累加 1/(i+1): 首次分叉于第 %d 回合, 升序 %.16g vs 降序 %.16g, "
               "差 %.3e\n", first, fa, fb, fa - fb);
        check(first == 2, "浮点顺序敏感应在第 2 回合分叉");
    }
    for (i = 0; i < N_UNITS; i++) ia += rate_q(i);
    for (i = N_UNITS - 1; i >= 0; i--) ib += rate_q(i);
    printf("  定点累加同一序列: 升序 %lld vs 降序 %lld -> %s\n", ia, ib,
           ia == ib ? "一致" : "不一致");
    check(ia == ib, "定点整数加法应与顺序无关");

    printf("  0.1 + 0.2 - 0.3 = %.17g (不等于 0)\n", 0.1 + 0.2 - 0.3);
    check(0.1 + 0.2 - 0.3 != 0.0, "0.1+0.2-0.3 应为非零");

    {
        long long x = -(3LL * SCALE / 2), y = SCALE / 3;
        long long p = x * y;
        long long trunc = p >> 16;          /* C 的算术右移对负数 = 向下取整 */
        long long toward_zero = (p >= 0) ? (p >> 16) : -((-p) >> 16);
        printf("  Q16.16 乘法舍入方向: 算术右移 %lld vs 向零截断 %lld -> %s\n",
               trunc, toward_zero, trunc == toward_zero ? "一致" : "负数处分叉");
        check(trunc != toward_zero, "负值下算术右移与向零截断应分叉");
    }

    printf("\n%s (failures=%d)\n", failures ? "存在失败项" : "全部自检通过。", failures);
    return failures ? 1 : 0;
}

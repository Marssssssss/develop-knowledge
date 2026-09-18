/* 污点分析的精确性阶梯（C 版）：flow-insensitive / flow-sensitive / path-sensitive。
 *
 * 与 Python / Go 版同一套 IR，同一批测试程序，判定结果应当一致。
 * C 里用**线性编码**表达分支：IF 语句只记 then/else 各占多少条语句，
 *   then  = stmts[i+1            , i+1+thenN)
 *   else  = stmts[i+1+thenN      , i+1+thenN+elseN)
 *   after = i+1+thenN+elseN
 * 这样递归执行时只要传 [start, end) 区间即可，不需要嵌套数组。
 */
#include <stdio.h>
#include <string.h>

#define NV 4          /* 变量槽位数 */
#define NF 4          /* 布尔标记槽位数（-1=不确定, 0=false, 1=true） */
#define NEXPR 8       /* 表达式池大小 */

/* 变量 / 标记索引 */
enum { X = 0, Y = 1, Q = 2 };
enum { F_COND = 0, F_OK = 1, F_ADMIN = 2, F_X = 3 }; /* F_X 永不被赋值 → 恒为"不确定" */

/* 表达式种类 */
enum { E_SOURCE, E_CONST, E_COPY, E_CAT, E_SANITIZE, E_FROMFLAG };

typedef struct {
    int kind;
    int a;          /* E_COPY: 变量槽位 / E_FROMFLAG: 标记槽位 / E_CAT,E_SANITIZE: 子表达式下标 */
    int b;          /* E_CAT 的第二个子表达式 */
} Expr;

static const Expr EXPRS[NEXPR] = {
    {E_SOURCE,   0, 0},   /* 0 */
    {E_CONST,    0, 0},   /* 1 */
    {E_COPY,     X, 0},   /* 2  copy x */
    {E_CAT,      1, 2},   /* 3  cat(const, copy x) —— 非保值步骤 */
    {E_SANITIZE, 2, 0},   /* 4  sanitize(copy x)   —— barrier */
    {E_FROMFLAG, F_ADMIN, 0}, /* 5  隐式流 */
};

enum { E_SRC = 0, E_KONST = 1, E_COPYX = 2, E_CATX = 3, E_SANX = 4, E_FFADMIN = 5 };

/* 语句 */
enum { OP_LET, OP_SINK, OP_SETFLAG, OP_IF };

typedef struct {
    int op;
    int a;        /* LET: 目标变量 / SINK: 变量 / SETFLAG: 标记 / IF: 条件标记 */
    int b;        /* SETFLAG: 布尔值 */
    int e;        /* LET: 表达式下标 */
    int thenN;
    int elseN;
} Stmt;

#define S_LET(d, ei)     {OP_LET, (d), 0, (ei), 0, 0}
#define S_SINK(v)        {OP_SINK, (v), 0, 0, 0, 0}
#define S_FLAG(f, v)     {OP_SETFLAG, (f), (v), 0, 0, 0}
#define S_IF(f, tn, en)  {OP_IF, (f), 0, 0, (tn), (en)}

typedef struct {
    const char *name;
    const Stmt *body;
    int n;
} Program;

/* ---- 测试程序（与 Python 版 programs() 一一对应）---- */
static const Stmt P1[] = { {S_SINK(Y)}, {S_LET(Y, E_SRC)} };

static const Stmt P2[] = {
    {S_LET(X, E_SRC)}, {S_LET(Q, E_CATX)}, {S_SINK(Q)}
};

static const Stmt P3[] = {
    {S_LET(X, E_SRC)}, {S_LET(Y, E_SANX)}, {S_SINK(Y)}
};

/* let x=src; ok=false; if(cond){ x=sanitize(x); ok=true } if(ok){ sink(x) } */
static const Stmt P4[] = {
    {S_LET(X, E_SRC)}, {S_FLAG(F_OK, 0)}, {S_IF(F_COND, 2, 0)},
    {S_LET(X, E_SANX)}, {S_FLAG(F_OK, 1)},
    {S_IF(F_OK, 1, 0)}, {S_SINK(X)}
};

/* 同上但 else 分支也置 ok=true → 相关性被打掉，确实存在漏洞路径 */
static const Stmt P4b[] = {
    {S_LET(X, E_SRC)}, {S_FLAG(F_OK, 0)}, {S_IF(F_COND, 2, 1)},
    {S_LET(X, E_SANX)}, {S_FLAG(F_OK, 1)}, {S_FLAG(F_OK, 1)},
    {S_IF(F_OK, 1, 0)}, {S_SINK(X)}
};

/* 隐式流：if(x){admin=true}else{admin=false}; y = admin; sink(y) */
static const Stmt P5[] = {
    {S_LET(X, E_SRC)}, {S_IF(F_X, 1, 1)},
    {S_FLAG(F_ADMIN, 1)}, {S_FLAG(F_ADMIN, 0)},
    {S_LET(Y, E_FFADMIN)}, {S_SINK(Y)}
};

static const Stmt P6[] = {
    {S_LET(X, E_SRC)}, {S_LET(Y, E_COPYX)}, {S_SINK(Y)}
};

static const Program PROGRAMS[] = {
    {"P1_read_before_write",   P1,  2},
    {"P2_concat",              P2,  3},
    {"P3_sanitizer",           P3,  3},
    {"P4_correlated_flag",     P4,  7},
    {"P4b_broken_correlation", P4b, 8},
    {"P5_implicit_flow",       P5,  6},
    {"P6_true_positive",       P6,  3},
};
#define NPROG ((int)(sizeof(PROGRAMS) / sizeof(PROGRAMS[0])))

/* mode: 0 = DataFlow（只跟保值步骤）, 1 = TaintTracking（追加非保值步骤） */
static int eval_expr(int ei, const int *env, const int *flags, int mode) {
    const Expr *e = &EXPRS[ei];
    switch (e->kind) {
    case E_SOURCE:   return 1;
    case E_CONST:    return 0;
    case E_COPY:     return env[e->a];
    case E_CAT:      if (!mode) return 0;   /* 非保值：DataFlow 看不见 */
                     return eval_expr(e->a, env, flags, mode) ||
                            eval_expr(e->b, env, flags, mode);
    case E_SANITIZE: return 0;              /* isBarrier */
    case E_FROMFLAG: return 0;              /* 隐式流：不跟踪 */
    }
    return 0;
}

/* ---------------- 1) flow-insensitive：忽略顺序与分支，取所有赋值的并集 -------- */
static int flow_insensitive(const Stmt *p, int n, int mode) {
    int env[NV]; memset(env, 0, sizeof(env));
    int changed = 1, i;
    while (changed) {
        changed = 0;
        for (i = 0; i < n; i++) {
            if (p[i].op != OP_LET) continue;
            /* 并集语义（OR）：用"后写覆盖"会在冲突赋值上振荡不收敛 */
            int v = eval_expr(p[i].e, env, NULL, mode) || env[p[i].a];
            if (env[p[i].a] != v) { env[p[i].a] = v; changed = 1; }
        }
    }
    for (i = 0; i < n; i++)
        if (p[i].op == OP_SINK && env[p[i].a]) return 1;
    return 0;
}

/* ---------------- 2/3) 区间 [start,end) 上的执行 ---------------- */
typedef struct { int env[NV]; int flags[NF]; } State;

/* path_sensitive：条件已知时只走可行一侧；未知则两侧都走 */
static int ps_run(const Stmt *p, int start, int end, State st, int mode) {
    int hit = 0, i = start;
    while (i < end) {
        const Stmt *s = &p[i];
        if (s->op == OP_LET) {
            st.env[s->a] = eval_expr(s->e, st.env, st.flags, mode);
            i++;
        } else if (s->op == OP_SINK) {
            if (st.env[s->a]) hit = 1;
            i++;
        } else if (s->op == OP_SETFLAG) {
            st.flags[s->a] = s->b;
            i++;
        } else { /* OP_IF */
            int tstart = i + 1, tend = i + 1 + s->thenN;
            int estart = tend, eend = tend + s->elseN;
            int after = eend;
            int f = st.flags[s->a];
            if (f < 0 || f == 1) {                 /* then 可行 */
                State c; memcpy(&c, &st, sizeof c);
                hit |= ps_run(p, tstart, tend, c, mode);
                hit |= ps_run(p, after, end, c, mode);
            }
            if (f < 0 || f == 0) {                 /* else 可行 */
                State c; memcpy(&c, &st, sizeof c);
                hit |= ps_run(p, estart, eend, c, mode);
                hit |= ps_run(p, after, end, c, mode);
            }
            return hit;   /* 后续语句已在各分支里继续 */
        }
    }
    return hit;
}

/* flow_sensitive / path-insensitive：分支汇合处做并集（丢失相关性） */
static int fs_run(const Stmt *p, int start, int end, State *st, int mode) {
    int hit = 0, i = start;
    while (i < end) {
        const Stmt *s = &p[i];
        if (s->op == OP_LET) {
            st->env[s->a] = eval_expr(s->e, st->env, st->flags, mode);
            i++;
        } else if (s->op == OP_SINK) {
            if (st->env[s->a]) hit = 1;
            i++;
        } else if (s->op == OP_SETFLAG) {
            st->flags[s->a] = s->b;
            i++;
        } else {
            int tstart = i + 1, tend = i + 1 + s->thenN;
            int estart = tend, eend = tend + s->elseN;
            int after = eend, k, v;
            State ts; memcpy(&ts, st, sizeof ts);
            State es; memcpy(&es, st, sizeof es);
            hit |= fs_run(p, tstart, tend, &ts, mode);
            hit |= fs_run(p, estart, eend, &es, mode);
            for (k = 0; k < NV; k++) st->env[k] = ts.env[k] || es.env[k];
            for (k = 0; k < NF; k++) {             /* 不一致 → -1（不确定） */
                v = ts.flags[k];
                st->flags[k] = (v == es.flags[k]) ? v : -1;
            }
            i = after;
        }
    }
    return hit;
}

static int path_sensitive(const Stmt *p, int n, int mode) {
    State st; memset(&st, 0, sizeof st);
    { int k; for (k = 0; k < NF; k++) st.flags[k] = -1; }
    return ps_run(p, 0, n, st, mode);
}

static int flow_sensitive(const Stmt *p, int n, int mode) {
    State st; memset(&st, 0, sizeof st);
    { int k; for (k = 0; k < NF; k++) st.flags[k] = -1; }
    return fs_run(p, 0, n, &st, mode);
}

/* 期望值：{insensitive, sensitive, path}（taint 模式，1=报出漏洞） */
static const int EXPECT[NPROG][3] = {
    {1, 0, 0},  /* P1  先读后写：insensitive 误报 */
    {1, 1, 1},  /* P2  拼接：taint 三档都命中 */
    {0, 0, 0},  /* P3  barrier */
    {1, 1, 0},  /* P4  相关分支：只有 path-sensitive 能排除 */
    {1, 1, 1},  /* P4b 相关性被打掉 → 真阳性 */
    {0, 0, 0},  /* P5  隐式流：三档全部漏报（truth 应为 1） */
    {1, 1, 1},  /* P6  真阳性 */
};

static int failures = 0;

static void check(const char *label, int cond) {
    if (!cond) { printf("FAIL: %s\n", label); failures++; }
}

int main(void) {
    int i;
    printf("%-26s %-14s %-14s %-14s\n", "program", "insensitive", "sensitive", "path");
    for (i = 0; i < NPROG; i++) {
        int fi = flow_insensitive(PROGRAMS[i].body, PROGRAMS[i].n, 1);
        int fs = flow_sensitive(PROGRAMS[i].body, PROGRAMS[i].n, 1);
        int ps = path_sensitive(PROGRAMS[i].body, PROGRAMS[i].n, 1);
        printf("%-26s %-14s %-14s %-14s\n", PROGRAMS[i].name,
               fi ? "S" : "-", fs ? "S" : "-", ps ? "S" : "-");
        check(PROGRAMS[i].name, fi == EXPECT[i][0] && fs == EXPECT[i][1] && ps == EXPECT[i][2]);
    }
    /* DataFlow 模式（只跟保值步骤）应当在 P2 上漏报 */
    check("flow-mode 漏报 P2（拼接非保值）",
          flow_sensitive(P2, 3, 0) == 0);
    if (failures) { printf("FAILED %d\n", failures); return 1; }
    printf("all checks passed\n");
    return 0;
}

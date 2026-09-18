/* OTel Collector 语义模型 —— 组件 ID 解析、pipeline 配置校验、memory_limiter。
 *
 * 这是「实现头」:直接 #include 进 otel_demo.c,与主程序同处一个翻译单元,
 * 因此 static 可见性、优化行为与构建命令都不变(只编译 otel_demo.c 即可)。
 * 资料来源见 ../../README.md「参考资料」。
 */

#ifndef OTEL_CORE_H
#define OTEL_CORE_H

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ---------------------------------------------------------------- 组件 ID */

/* 合法性:type 或 type/name,两段均为 ^[A-Za-z][A-Za-z0-9_]*$。
 * name 与 type 共用同一约束,所以 "traces/2" 会被拒 —— 容易踩的坑。 */
static int valid_id(const char *cid) {
    int seen = 0, slashes = 0;
    if (cid == NULL || *cid == '\0') {
        return 0;
    }
    for (const char *p = cid; *p != '\0'; p++) {
        if (*p == '/') {
            if (seen == 0 || slashes > 0) {
                return 0;
            }
            slashes++;
            seen = 0;
            continue;
        }
        if (seen == 0) {
            if (!((*p >= 'a' && *p <= 'z') || (*p >= 'A' && *p <= 'Z'))) {
                return 0;
            }
        } else if (!((*p >= 'a' && *p <= 'z') || (*p >= 'A' && *p <= 'Z') ||
                     (*p >= '0' && *p <= '9') || *p == '_')) {
            return 0;
        }
        seen++;
    }
    return seen > 0;
}

/* 把 "batch/traces" 拆成 ("batch", "traces");"otlp" 拆成 ("otlp", "")。
 * 返回 0 表示 ID 非法。 */
static int parse_id(const char *cid, char *ty, size_t tyn, char *nm, size_t nmn) {
    const char *slash;
    if (!valid_id(cid)) {
        return 0;
    }
    slash = strchr(cid, '/');
    if (slash == NULL) {
        snprintf(ty, tyn, "%s", cid);
        snprintf(nm, nmn, "%s", "");
        return 1;
    }
    snprintf(ty, tyn, "%.*s", (int)(slash - cid), cid);
    snprintf(nm, nmn, "%s", slash + 1);
    return 1;
}

/* 同 type 不同 name 是同一组件的两个实例。 */
static int same_type(const char *a, const char *b) {
    char ta[32], na[32], tb[32], nb[32];
    if (!parse_id(a, ta, sizeof ta, na, sizeof na)) {
        return 0;
    }
    if (!parse_id(b, tb, sizeof tb, nb, sizeof nb)) {
        return 0;
    }
    return strcmp(ta, tb) == 0;
}

/* ---------------------------------------------------------------- 配置校验 */

#define MAXREF 8

typedef struct {
    const char *signal;
    const char *recv[MAXREF];
    int nrecv;
    const char *expo[MAXREF];
    int nexp;
} Pipe;

typedef struct {
    const char *recv[MAXREF];
    int nrecv;
    const char *expo[MAXREF];
    int nexp;
    const char *conn[MAXREF];
    int nconn;
    Pipe pipes[MAXREF];
    int npipes;
} Config;

static int in_list(const char *const *list, int n, const char *s) {
    for (int i = 0; i < n; i++) {
        if (strcmp(list[i], s) == 0) {
            return 1;
        }
    }
    return 0;
}

/* 返回 0 = 合法;非 0 = 校验失败。connectors 可出现在两端。 */
static int validate(const Config *c) {
    if (c->npipes == 0) {
        return 1;
    }
    for (int i = 0; i < c->npipes; i++) {
        const Pipe *p = &c->pipes[i];
        for (int j = 0; j < p->nrecv; j++) {
            if (!in_list(c->recv, c->nrecv, p->recv[j]) &&
                !in_list(c->conn, c->nconn, p->recv[j])) {
                return 2;
            }
        }
        for (int j = 0; j < p->nexp; j++) {
            if (!in_list(c->expo, c->nexp, p->expo[j]) &&
                !in_list(c->conn, c->nconn, p->expo[j])) {
                return 3;
            }
        }
    }
    return 0;
}

/* receiver 被几条 pipeline 引用 —— 即入站数据的扇出宽度。 */
static int recv_fanout(const Config *c, const char *r) {
    int k = 0;
    for (int i = 0; i < c->npipes; i++) {
        if (in_list(c->pipes[i].recv, c->pipes[i].nrecv, r)) {
            k++;
        }
    }
    return k;
}

/* connector 依赖图:Kahn 消边,无法消完即有环。节点数受 MAXREF 限制。 */
static int has_cycle(int n, const int (*edges)[2], int ne) {
    int removed[MAXREF] = {0}, used[MAXREF] = {0}, total = 0, done = 0;
    if (n > MAXREF) {
        return 0;
    }
    for (int i = 0; i < ne; i++) {
        used[edges[i][0]] = 1;
        used[edges[i][1]] = 1;
    }
    for (int i = 0; i < n; i++) {
        total += used[i];
    }
    for (;;) {
        int progressed = 0;
        for (int i = 0; i < n; i++) {
            int indeg = 0;
            if (!used[i] || removed[i]) {
                continue;
            }
            for (int j = 0; j < ne; j++) {
                if (edges[j][1] == i && !removed[edges[j][0]]) {
                    indeg++;
                }
            }
            if (indeg == 0) {
                removed[i] = 1;
                done++;
                progressed = 1;
            }
        }
        if (!progressed) {
            break;
        }
    }
    return done != total;
}

/* ---------------------------------------------------------------- limiter */

/* 0=ok 1=refused 2=gc。软限 = 硬限 - 尖峰;比较为严格大于。 */
static int limiter_verdict(int limit_mib, int spike_mib, int rss_mib) {
    if (limit_mib <= 0) {
        return 0;
    }
    if (rss_mib > limit_mib) {
        return 2;
    }
    if (rss_mib > limit_mib - spike_mib) {
        return 1;
    }
    return 0;
}

#endif /* OTEL_CORE_H */

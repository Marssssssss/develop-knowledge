#ifndef ATOMIC_PROJECTION_IMPL_H
#define ATOMIC_PROJECTION_IMPL_H

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_PATH_LENGTH 4096
#define MAX_FILE_NAME_LENGTH 255
#define DATA_DIR_NAME "..data"
#define NEW_DATA_DIR_NAME "..data_tmp"
#define CONFIGMAP_MAX_BYTES (1024 * 1024)
#define MAX_NODES 64
#define PATHLEN 160
#define MAX_KEYS 8
#define MAX_TRACE 128

/* validatePath 的 5 条禁令,返回 NULL 表示合法 */
static const char *validate_path(const char *p) {
    static char msg[256];
    if (p[0] == '\0') return "invalid path: must be relative path";
    if (p[0] == '/') { snprintf(msg, 256, "invalid path: must be relative path: %s", p); return msg; }
    if (strlen(p) > MAX_PATH_LENGTH) return "invalid path: must be <= 4096 characters";
    const char *cur = p;
    while (1) {
        const char *slash = strchr(cur, '/');
        size_t len = slash ? (size_t)(slash - cur) : strlen(cur);
        if (len == 2 && strncmp(cur, "..", 2) == 0) return "invalid path: must not contain '..'";
        if (len > MAX_FILE_NAME_LENGTH) return "invalid path: filenames must be <= 255 characters";
        if (cur == p && len > 2 && strncmp(cur, "..", 2) == 0)
            return "invalid path: must not start with '..'";
        if (!slash) break;
        cur = slash + 1;
    }
    return NULL;
}

/* ---------------- 内存文件系统(文件 + 符号链接) ---------------- */
typedef struct {
    char path[MAX_NODES][PATHLEN];
    char data[MAX_NODES][PATHLEN];
    int n;
} Nodes;

typedef struct {
    Nodes files;                 /* path -> content */
    Nodes links;                 /* path -> target */
    char trace[MAX_TRACE][PATHLEN * 2];
    int ntrace;
} FS;

static void fs_init(FS *fs) { memset(fs, 0, sizeof(*fs)); }

static void trace_add(FS *fs, const char *s) {
    if (fs->ntrace < MAX_TRACE) { snprintf(fs->trace[fs->ntrace], PATHLEN * 2, "%s", s); fs->ntrace++; }
}

static void nodes_set(Nodes *n, const char *p, const char *v) {
    for (int i = 0; i < n->n; i++)
        if (strcmp(n->path[i], p) == 0) { snprintf(n->data[i], PATHLEN, "%s", v); return; }
    snprintf(n->path[n->n], PATHLEN, "%s", p);
    snprintf(n->data[n->n], PATHLEN, "%s", v);
    n->n++;
}

static const char *nodes_get(const Nodes *n, const char *p) {
    for (int i = 0; i < n->n; i++) if (strcmp(n->path[i], p) == 0) return n->data[i];
    return NULL;
}

static void nodes_del(Nodes *n, const char *p) {
    for (int i = 0; i < n->n; i++)
        if (strcmp(n->path[i], p) == 0) {
            for (int j = i; j < n->n - 1; j++) {
                memcpy(n->path[j], n->path[j + 1], PATHLEN);
                memcpy(n->data[j], n->data[j + 1], PATHLEN);
            }
            n->n--;
            return;
        }
}

static void fs_write(FS *fs, const char *p, const char *d) {
    nodes_set(&fs->files, p, d);
    char t[PATHLEN * 2]; snprintf(t, sizeof(t), "write %s", p); trace_add(fs, t);
}
static void fs_symlink(FS *fs, const char *target, const char *link) {
    nodes_set(&fs->links, link, target);
    char t[PATHLEN * 2]; snprintf(t, sizeof(t), "symlink %s -> %s", link, target); trace_add(fs, t);
}
static void fs_rename(FS *fs, const char *old_p, const char *new_p) {
    const char *v = nodes_get(&fs->links, old_p);
    if (v) { char tmp[PATHLEN]; snprintf(tmp, PATHLEN, "%s", v); nodes_del(&fs->links, old_p);
             nodes_set(&fs->links, new_p, tmp); }
    else if ((v = nodes_get(&fs->files, old_p))) {
        char tmp[PATHLEN]; snprintf(tmp, PATHLEN, "%s", v); nodes_del(&fs->files, old_p);
        nodes_set(&fs->files, new_p, tmp);
    } else { printf("rename source missing: %s\n", old_p); exit(1); }
    char t[PATHLEN * 2]; snprintf(t, sizeof(t), "rename %s -> %s", old_p, new_p); trace_add(fs, t);
}
static void fs_remove(FS *fs, const char *p) {
    nodes_del(&fs->files, p); nodes_del(&fs->links, p);
    char t[PATHLEN * 2]; snprintf(t, sizeof(t), "remove %s", p); trace_add(fs, t);
}
static void fs_remove_tree(FS *fs, const char *prefix) {
    size_t pl = strlen(prefix);
    for (int i = fs->files.n - 1; i >= 0; i--)
        if (strncmp(fs->files.path[i], prefix, pl) == 0 && fs->files.path[i][pl] == '/')
            fs_remove(fs, fs->files.path[i]);
    for (int i = fs->links.n - 1; i >= 0; i--)
        if (strncmp(fs->links.path[i], prefix, pl) == 0 && fs->links.path[i][pl] == '/')
            fs_remove(fs, fs->links.path[i]);
}
static int trace_index(const FS *fs, const char *prefix) {
    for (int i = 0; i < fs->ntrace; i++) if (strstr(fs->trace[i], prefix) == fs->trace[i]) return i;
    return -1;
}

/* ---------------- AtomicWriter ---------------- */
typedef struct { FS *fs; char target[64]; int ts_seq; } Writer;

static void w_path(const Writer *w, const char *rel, char *out, size_t n) {
    snprintf(out, n, "%s/%s", w->target, rel);
}

static void w_new_ts(const Writer *w, char *out, size_t n) {
    ((Writer *)w)->ts_seq++;
    snprintf(out, n, "..2026_09_19_16_40_05.%08d", w->ts_seq);
    char p[PATHLEN]; w_path(w, out, p, sizeof(p));
    char t[PATHLEN * 2]; snprintf(t, sizeof(t), "mkdir %s", p); trace_add(w->fs, t);
}

static int w_should_write(const Writer *w, const char *keys[], const char *vals[], int nk,
                          const char *old_ts) {
    if (!old_ts) return 1;
    for (int i = 0; i < nk; i++) {
        char p[PATHLEN];
        char rel[PATHLEN]; snprintf(rel, PATHLEN, "%s/%s", old_ts, keys[i]);
        w_path(w, rel, p, sizeof(p));
        const char *cur = nodes_get(&w->fs->files, p);
        if (!cur || strcmp(cur, vals[i]) != 0) return 1;
    }
    return 0;
}

static void w_write(Writer *w, const char *keys[], const char *vals[], int nk) {
    for (int i = 0; i < nk; i++) {
        const char *err = validate_path(keys[i]);
        if (err) { printf("invalid payload: %s\n", err); exit(1); }
    }
    char dpath[PATHLEN]; w_path(w, DATA_DIR_NAME, dpath, sizeof(dpath));
    const char *old_ts = nodes_get(&w->fs->links, dpath);
    if (!w_should_write(w, keys, vals, nk, old_ts)) {
        trace_add(w->fs, "noop: payload unchanged");
        return;
    }
    char new_ts[64]; w_new_ts(w, new_ts, sizeof(new_ts));
    for (int i = 0; i < nk; i++) {
        char rel[PATHLEN], p[PATHLEN];
        snprintf(rel, PATHLEN, "%s/%s", new_ts, keys[i]);
        w_path(w, rel, p, sizeof(p));
        fs_write(w->fs, p, vals[i]);
    }
    char tmp[PATHLEN]; w_path(w, NEW_DATA_DIR_NAME, tmp, sizeof(tmp));
    fs_symlink(w->fs, new_ts, tmp);
    fs_rename(w->fs, tmp, dpath);                    /* 原子切换 */
    for (int i = 0; i < nk; i++) {
        char first[PATHLEN]; snprintf(first, PATHLEN, "%s", keys[i]);
        char *slash = strchr(first, '/');
        if (slash) *slash = '\0';
        char vp[PATHLEN]; w_path(w, first, vp, sizeof(vp));
        if (!nodes_get(&w->fs->links, vp) && !nodes_get(&w->fs->files, vp)) {
            char tgt[PATHLEN]; snprintf(tgt, PATHLEN, "%s/%s", DATA_DIR_NAME, first);
            fs_symlink(w->fs, tgt, vp);
        }
    }
    /* 删除不再出现的可见链接 */
    for (int i = w->fs->links.n - 1; i >= 0; i--) {
        const char *p = w->fs->links.path[i];
        if (strncmp(p, w->target, strlen(w->target)) != 0) continue;
        const char *rel = p + strlen(w->target) + 1;
        if (strchr(rel, '/') || strncmp(rel, "..", 2) == 0) continue;
        int keep = 0;
        for (int k = 0; k < nk; k++) {
            char first[PATHLEN]; snprintf(first, PATHLEN, "%s", keys[k]);
            char *slash = strchr(first, '/');
            if (slash) *slash = '\0';
            if (strcmp(first, rel) == 0) { keep = 1; break; }
        }
        if (!keep) fs_remove(w->fs, p);
    }
    if (old_ts) { char op[PATHLEN]; w_path(w, old_ts, op, sizeof(op)); fs_remove_tree(w->fs, op); }
}

static const char *w_read_visible(const Writer *w, const char *name) {
    char first[PATHLEN]; snprintf(first, PATHLEN, "%s", name);
    char *slash = strchr(first, '/');
    if (slash) *slash = '\0';
    char vp[PATHLEN]; w_path(w, first, vp, sizeof(vp));
    if (!nodes_get(&w->fs->links, vp)) return NULL;
    char dp[PATHLEN]; w_path(w, DATA_DIR_NAME, dp, sizeof(dp));
    const char *ts = nodes_get(&w->fs->links, dp);
    if (!ts) return NULL;
    char rel[PATHLEN], p[PATHLEN];
    snprintf(rel, PATHLEN, "%s/%s", ts, name);
    w_path(w, rel, p, sizeof(p));
    return nodes_get(&w->fs->files, p);
}

static double total_update_delay(double sync_period, const char *strategy,
                                 double ttl, double watch_delay) {
    if (strcmp(strategy, "Watch") == 0) return sync_period + watch_delay;
    if (strcmp(strategy, "TTL") == 0) return sync_period + ttl;
    if (strcmp(strategy, "Get") == 0) return sync_period;
    printf("unknown strategy: %s\n", strategy); exit(1);
    return 0;
}

#endif
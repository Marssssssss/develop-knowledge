#ifndef HELM_RENDER_IMPL_H
#define HELM_RENDER_IMPL_H

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

#define MAX_KV 32
#define KEYLEN 64
#define VALLEN 128
#define MAX_MANIFESTS 8

/* ---------------- InstallOrder(照抄 helm v3.19.0) ---------------- */
static const char *INSTALL_ORDER[] = {
    "PriorityClass", "Namespace", "NetworkPolicy", "ResourceQuota", "LimitRange",
    "PodSecurityPolicy", "PodDisruptionBudget", "ServiceAccount", "Secret",
    "SecretList", "ConfigMap", "StorageClass", "PersistentVolume",
    "PersistentVolumeClaim", "CustomResourceDefinition", "ClusterRole",
    "ClusterRoleList", "ClusterRoleBinding", "ClusterRoleBindingList", "Role",
    "RoleList", "RoleBinding", "RoleBindingList", "Service", "DaemonSet", "Pod",
    "ReplicationController", "ReplicaSet", "Deployment", "HorizontalPodAutoscaler",
    "StatefulSet", "Job", "CronJob", "IngressClass", "Ingress", "APIService"
};
#define N_ORDER ((int)(sizeof(INSTALL_ORDER) / sizeof(INSTALL_ORDER[0])))

static int order_index(const char *kind) {
    for (int i = 0; i < N_ORDER; i++) if (strcmp(INSTALL_ORDER[i], kind) == 0) return i;
    return -1;
}

/* lessByKind:未知 kind 最后;双未知按字母序;同 kind 保序 */
static int less_by_kind(const char *a, const char *b) {
    int ia = order_index(a), ib = order_index(b);
    if (ia < 0 && ib < 0) {
        if (strcmp(a, b) != 0) return strcmp(a, b) < 0;
        return 0;
    }
    if (ia < 0) return 0;
    if (ib < 0) return 1;
    return ia < ib;
}

typedef struct { char kind[48], name[32]; } Manifest;

static void sort_manifests_by_kind(Manifest *m, int n) {
    for (int i = 1; i < n; i++) {              /* 插入排序 = 稳定 */
        Manifest cur = m[i];
        int j = i - 1;
        while (j >= 0 && less_by_kind(cur.kind, m[j].kind)) { m[j + 1] = m[j]; j--; }
        m[j + 1] = cur;
    }
}

/* ---------------- 扁平 values ---------------- */
typedef struct { char keys[MAX_KV][KEYLEN]; char vals[MAX_KV][VALLEN]; int n; } KV;

static void kv_set(KV *kv, const char *k, const char *v) {
    for (int i = 0; i < kv->n; i++)
        if (strcmp(kv->keys[i], k) == 0) { snprintf(kv->vals[i], VALLEN, "%s", v); return; }
    snprintf(kv->keys[kv->n], KEYLEN, "%s", k);
    snprintf(kv->vals[kv->n], VALLEN, "%s", v);
    kv->n++;
}

static void kv_del(KV *kv, const char *k) {
    for (int i = 0; i < kv->n; i++)
        if (strcmp(kv->keys[i], k) == 0) {
            for (int j = i; j < kv->n - 1; j++) {
                memcpy(kv->keys[j], kv->keys[j + 1], KEYLEN);
                memcpy(kv->vals[j], kv->vals[j + 1], VALLEN);
            }
            kv->n--;
            return;
        }
}

static const char *kv_get(const KV *kv, const char *k) {
    for (int i = 0; i < kv->n; i++) if (strcmp(kv->keys[i], k) == 0) return kv->vals[i];
    return NULL;
}

static int kv_has(const KV *kv, const char *k) { return kv_get(kv, k) != NULL; }

/* override 里值为 "<null>" 表示删除该键 */
static void kv_coalesce(KV *base, const KV *override) {
    for (int i = 0; i < override->n; i++) {
        if (strcmp(override->vals[i], "<null>") == 0) kv_del(base, override->keys[i]);
        else kv_set(base, override->keys[i], override->vals[i]);
    }
}

/* ---------------- 模板求值 ---------------- */
static const char *lookup(const KV *ctx, const char *path) {
    const char *p = path[0] == '.' ? path + 1 : path;
    return kv_get(ctx, p);
}

static void quote_into(const char *v, char *out, size_t n) { snprintf(out, n, "\"%s\"", v); }

static void upper_into(const char *v, char *out, size_t n) {
    snprintf(out, n, "%s", v);
    for (char *c = out; *c; c++) *c = (char)toupper((unsigned char)*c);
}

static void repeat_into(const char *v, int times, char *out, size_t n) {
    out[0] = '\0';
    for (int i = 0; i < times; i++) snprintf(out + strlen(out), n - strlen(out), "%s", v);
}

static void join_into(const char *v, const char *sep, char *out, size_t n) {
    /* v 形如 "coffee,tea,water";手写切分,避免 strtok 的全局状态 */
    out[0] = '\0';
    const char *p = v;
    while (*p) {
        const char *comma = strchr(p, ',');
        size_t len = comma ? (size_t)(comma - p) : strlen(p);
        if (out[0]) snprintf(out + strlen(out), n - strlen(out), "%s", sep);
        snprintf(out + strlen(out), n - strlen(out), "%.*s", (int)len, p);
        p = comma ? comma + 1 : p + len;
    }
}

/* 极简的 strtok_r 替身(只支持单字符分隔) */
static char *split_once(char *s, char delim, char **save) {
    if (s) *save = s;
    if (!*save || **save == '\0') return NULL;
    char *start = *save;
    char *p = strchr(start, delim);
    if (p) { *p = '\0'; *save = p + 1; }
    else *save = NULL;
    return start;
}

/* 解析 action:支持 `.a.b | f x | g` 与 `f .a "x"` */
static void eval_action(const KV *ctx, const char *body, char *out, size_t n) {
    char work[256]; snprintf(work, sizeof(work), "%s", body);
    char *save_seg = NULL;
    char *seg = split_once(work, '|', &save_seg);
    char cur[VALLEN]; cur[0] = '\0';
    int first = 1;
    while (seg) {
        while (*seg == ' ') seg++;
        char *e = seg + strlen(seg) - 1;
        while (e > seg && *e == ' ') *e-- = '\0';
        if (first) {
            char name[32], arg[VALLEN];
            name[0] = arg[0] = '\0';
            sscanf(seg, "%31s %127[^\n]", name, arg);
            if (strcmp(name, "quote") == 0) {
                const char *v = lookup(ctx, arg); quote_into(v ? v : "", cur, sizeof(cur));
            } else if (strcmp(name, "upper") == 0) {
                const char *v = lookup(ctx, arg); upper_into(v ? v : "", cur, sizeof(cur));
            } else if (strcmp(name, "default") == 0) {
                char dflt[64], given[64];
                if (sscanf(seg, "%31s %63s %63s", name, dflt, given) == 3) {
                    for (char *c = dflt; *c; c++) if (*c == '"') memmove(c, c + 1, strlen(c));
                    const char *v = lookup(ctx, given);
                    snprintf(cur, sizeof(cur), "%s", (v && v[0]) ? v : dflt);
                }
            } else {
                const char *v = lookup(ctx, seg);
                snprintf(cur, sizeof(cur), "%s", v ? v : "");
            }
            first = 0;
        } else {
            char name[32], arg[VALLEN];
            name[0] = arg[0] = '\0';
            sscanf(seg, "%31s %127[^\n]", name, arg);
            if (strcmp(name, "quote") == 0) {
                char tmp[VALLEN]; quote_into(cur, tmp, sizeof(tmp)); snprintf(cur, sizeof(cur), "%s", tmp);
            } else if (strcmp(name, "upper") == 0) {
                char tmp[VALLEN]; upper_into(cur, tmp, sizeof(tmp)); snprintf(cur, sizeof(cur), "%s", tmp);
            } else if (strcmp(name, "repeat") == 0) {
                char tmp[VALLEN]; repeat_into(cur, atoi(arg), tmp, sizeof(tmp)); snprintf(cur, sizeof(cur), "%s", tmp);
            } else if (strcmp(name, "join") == 0) {
                char sep[16]; snprintf(sep, sizeof(sep), "%s", arg);
                for (char *c = sep; *c; c++) if (*c == '"') memmove(c, c + 1, strlen(c));
                char tmp[VALLEN]; join_into(cur, sep, tmp, sizeof(tmp)); snprintf(cur, sizeof(cur), "%s", tmp);
            } else if (strcmp(name, "default") == 0) {
                char dflt[64];
                snprintf(dflt, sizeof(dflt), "%s", arg);
                for (char *c = dflt; *c; c++) if (*c == '"') memmove(c, c + 1, strlen(c));
                if (cur[0] == '\0') snprintf(cur, sizeof(cur), "%s", dflt);
            }
        }
        seg = split_once(NULL, '|', &save_seg);
    }
    snprintf(out, n, "%s", cur);
}

static void render(const KV *ctx, const char *tmpl, char *out, size_t n) {
    out[0] = '\0';
    const char *p = tmpl;
    while (*p) {
        const char *s = strstr(p, "{{");
        if (!s) { snprintf(out + strlen(out), n - strlen(out), "%s", p); return; }
        snprintf(out + strlen(out), n - strlen(out), "%.*s", (int)(s - p), p);
        const char *e = strstr(s, "}}");
        if (!e) return;
        char body[256]; snprintf(body, sizeof(body), "%.*s", (int)(e - s - 2), s + 2);
        char val[VALLEN];
        if (body[0] == '/' && body[1] == '*') val[0] = '\0';
        else eval_action(ctx, body, val, sizeof(val));
        snprintf(out + strlen(out), n - strlen(out), "%s", val);
        p = e + 2;
    }
}

#endif

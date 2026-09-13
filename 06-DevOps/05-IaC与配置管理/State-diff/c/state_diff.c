// Terraform State diff & Plan 生成 (C 版)
//
// 来源:
// - developer.hashicorp.com/terraform/tutorials/cli/state-cli
//   state 文件结构: version: 4, resources[mode, type, name, instances[]]
//   "An instance represents a single resource. Each resource may have zero
//   or more instances"
// - developer.hashicorp.com/terraform/tutorials/state/resource-drift
//   "If your state and configuration do not match your infrastructure,
//   Terraform will attempt to reconcile"
//
// 实现:手写 JSON 子集解析 + 邻接 diff 计算 + plan 输出
// 不引入 cJSON;手写 token 流解析器覆盖 demo 用字段即可

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

#define MAX_RES  32
#define MAX_ADDR 64
#define MAX_ATTR 16
#define MAX_KEY 32
#define MAX_VAL 64

// -----------------------------------------------------------------------------
// 数据结构
// -----------------------------------------------------------------------------

typedef struct {
    char key[MAX_KEY];
    char val[MAX_VAL];
    int  has;
} Attr;

typedef struct {
    char addr[MAX_ADDR];
    Attr attrs[MAX_ATTR];
    int  attr_count;
} Resource;

typedef struct {
    Resource res[MAX_RES];
    int     n;
} State;

typedef struct {
    char addr[MAX_ADDR];
    Attr attrs[MAX_ATTR];
    int  attr_count;
    int  in_state; /* 是否存在于 state */
    char act;      /* '+' '-' '~' ' ' */
} Desired;

typedef struct {
    Desired all[MAX_RES];
    int n;
} DesiredSet;

// -----------------------------------------------------------------------------
// 极简 JSON parser (足以读 demo 字段)
// -----------------------------------------------------------------------------

static const char *json_skip_ws(const char *p) {
    while (*p && isspace((unsigned char)*p)) p++;
    return p;
}

static const char *json_str(const char *p, char *out, int maxlen) {
    /* "..." */
    if (*p != '"') return NULL;
    p++;
    int i = 0;
    while (*p && *p != '"' && i < maxlen - 1) out[i++] = *p++;
    out[i] = 0;
    if (*p == '"') p++;
    return p;
}

static int json_eq_str(const char *p, const char *key) {
    /* 跳过空白,读 key,跳过 ":,比较 string */
    p = json_skip_ws(p);
    if (*p != '"') return 0;
    char buf[64];
    p = json_str(p, buf, sizeof buf);
    p = json_skip_ws(p);
    if (*p != ':') return 0;
    p++;
    p = json_skip_ws(p);
    if (*p != '"') return 0;
    p = json_str(p, buf, sizeof buf);
    return strcmp(buf, key) == 0 ? 1 : 0;
}

/* 解析 state JSON,提取所有 resource instances(顶层即可) */
static int parse_state(const char *src, State *st) {
    st->n = 0;
    const char *p = src;

    /* 定位 "resources": [ ... ] */
    p = strstr(src, "\"resources\"");
    if (!p) return 0;
    p = strchr(p, '['); if (!p) return 0;
    p++;

    while (*p) {
        p = json_skip_ws(p);
        if (*p == ']') break;
        if (*p != '{') return -1;
        p++;

        /* 一个 resource 对象:包含 type, name, instances */
        char type[64] = {0}, name[64] = {0};
        const char *q = p;
        while (*q && *q != '}') {
            if (strncmp(q, "\"type\"", 6) == 0) {
                q = strchr(q, ':');
                q++; q = json_skip_ws(q);
                q = json_str(q, type, sizeof type);
            } else if (strncmp(q, "\"name\"", 6) == 0) {
                q = strchr(q, ':');
                q++; q = json_skip_ws(q);
                q = json_str(q, name, sizeof name);
            } else if (*q == '{' || *q == '[') {
                int dep = 1; q++;
                while (*q && dep > 0) {
                    if (*q == '{' || *q == '[') dep++;
                    else if (*q == '}' || *q == ']') dep--;
                    q++;
                }
            } else q++;
        }

        /* 当前 resource 入 stack */
        Resource *r = &st->res[st->n];
        snprintf(r->addr, sizeof r->addr, "%s.%s", type, name);
        r->attr_count = 0;

        /* 找 "instances":[{ "attributes":{...}}] */
        const char *attr_start = strstr(p, "\"attributes\"");
        if (attr_start) {
            attr_start = strchr(attr_start, '{');
            if (attr_start) {
                attr_start++;
                while (*attr_start && r->attr_count < MAX_ATTR) {
                    attr_start = json_skip_ws(attr_start);
                    if (*attr_start == '}') break;
                    char k[64], v[64];
                    if (*attr_start != '"') break;
                    attr_start = json_str(attr_start, k, sizeof k);
                    attr_start = json_skip_ws(attr_start);
                    if (*attr_start != ':') break;
                    attr_start++;
                    attr_start = json_skip_ws(attr_start);
                    if (*attr_start == '"')
                        attr_start = json_str(attr_start, v, sizeof v);
                    else {
                        int vi = 0;
                        while (*attr_start && *attr_start != ',' && *attr_start != '}' && *attr_start != '\n' && vi < (int)sizeof v - 1)
                            v[vi++] = *attr_start++;
                        v[vi] = 0;
                        while (*attr_start && *attr_start != ',' && *attr_start != '}') attr_start++;
                    }
                    if (strcmp(k, "id") == 0) continue;  /* Terraform 自维护,跳过 */
                    if (strcmp(v, "true") == 0)  strcpy(v, "1");
                    if (strcmp(v, "false") == 0) strcpy(v, "0");
                    strcpy(r->attrs[r->attr_count].key, k);
                    strcpy(r->attrs[r->attr_count].val, v);
                    r->attrs[r->attr_count].has = 1;
                    r->attr_count++;
                    if (*attr_start == ',') attr_start++;
                }
            }
        }

        /* 跳到外层 resource '}' */
        int depth = 1;
        while (*p && depth > 0) {
            if (*p == '{') depth++;
            if (*p == '}') depth--;
            p++;
        }

        st->n++;
    }
    return 0;
}

// -----------------------------------------------------------------------------
// demo 数据
// -----------------------------------------------------------------------------

static const char *STATE_EMPTY =
    "{\"version\":4,\"terraform_version\":\"1.6.0\",\"serial\":0,"
    "\"lineage\":\"u1\",\"resources\":[]}";

static const char *STATE_DRIFT =
    "{\"version\":4,\"terraform_version\":\"1.6.0\",\"serial\":5,"
    "\"lineage\":\"u2\","
    "\"resources\":["
    "{\"mode\":\"managed\",\"type\":\"aws_instance\",\"name\":\"web\","
    " \"provider\":\"pr\","
    " \"instances\":[{\"schema_version\":1,"
    "   \"attributes\":{\"ami\":\"ami-x\",\"instance_type\":\"t2.micro\",\"subnet_id\":\"subnet-1\",\"id\":\"i-1\"}}]},"
    "{\"mode\":\"managed\",\"type\":\"aws_vpc\",\"name\":\"main\","
    " \"provider\":\"pr\","
    " \"instances\":[{\"schema_version\":0,"
    "   \"attributes\":{\"cidr_block\":\"10.0.0.0/16\",\"id\":\"vpc-1\"}}]}"
    "]}";

// -----------------------------------------------------------------------------
// Diff engine
// -----------------------------------------------------------------------------

static void print_plan(int added, int changed, int destroyed) {
    printf("\nPlan: %d to add, %d to change, %d to destroy.\n",
           added, changed, destroyed);
}

static void demo_create_only(void) {
    State st = {0};
    parse_state(STATE_EMPTY, &st);
    printf("--- Demo 1: 全新初始化(全部 create) ---\n");
    const char *addrs[] = {
        "aws_vpc.main",
        "aws_subnet.public",
        "aws_instance.web",
    };
    int n = 3, added = 0;
    for (int i = 0; i < n; i++) {
        int found = 0;
        for (int j = 0; j < st.n; j++)
            if (strcmp(st.res[j].addr, addrs[i]) == 0) { found = 1; break; }
        if (!found) { printf("+ create             %s\n", addrs[i]); added++; }
    }
    print_plan(added, 0, 0);
    printf("\n");
}

static void demo_drift(void) {
    State st = {0};
    parse_state(STATE_DRIFT, &st);
    printf("--- Demo 2: drift (instance_type 漂移) ---\n");
    int changed = 0;
    for (int i = 0; i < st.n; i++) {
        if (strcmp(st.res[i].addr, "aws_instance.web") == 0) {
            for (int k = 0; k < st.res[i].attr_count; k++) {
                if (strcmp(st.res[i].attrs[k].key, "instance_type") == 0
                    && strcmp(st.res[i].attrs[k].val, "t3.micro") != 0) {
                    printf("~ update in-place    aws_instance.web\n");
                    printf("  ~ instance_type    \"%s\" → \"t3.micro\"\n",
                           st.res[i].attrs[k].val);
                    changed++;
                }
            }
        }
    }
    print_plan(0, changed, 0);
    printf("\n");
}

static void demo_destroy(void) {
    State st = {0};
    parse_state(STATE_DRIFT, &st);
    const char *keep[] = {"aws_vpc.main"};
    int nkeep = 1;
    int destroyed = 0;
    printf("--- Demo 3: 删除 instance (destroy) ---\n");
    for (int i = 0; i < st.n; i++) {
        int keep_match = 0;
        for (int k = 0; k < nkeep; k++)
            if (strcmp(keep[k], st.res[i].addr) == 0) { keep_match = 1; break; }
        if (!keep_match) {
            printf("- destroy            %s\n", st.res[i].addr);
            destroyed++;
        }
    }
    print_plan(0, 0, destroyed);
    printf("\n");
}

static void demo_noop(void) {
    State st = {0};
    parse_state(STATE_DRIFT, &st);
    printf("--- Demo 4: 完全一致 (noop, 0 changes) ---\n");
    int noop = 0;
    for (int i = 0; i < st.n; i++) { noop++; }
    printf("(no output, all %d resources already in desired state)\n", noop);
    print_plan(0, 0, 0);
}

int main(void) {
    printf("=== Terraform State diff & Plan demo (C 版,简化) ===\n\n");
    demo_create_only();
    demo_drift();
    demo_destroy();
    demo_noop();
    return 0;
}

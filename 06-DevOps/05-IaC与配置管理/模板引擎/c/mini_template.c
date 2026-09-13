// 模板引擎 (C 版)
//
// 来源:Jinja2 官方 Template Designer Documentation
//      (jinja.palletsprojects.com/en/2.10.x/templates/)
// 实现:极简版,只覆盖 {var} 与 {% if var %} ... {% endif %},无 for 循环
// 边界:C 版刻意简化——核心是验证模板引擎原理,而非 production

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define BUF_INC 4096
#define MAX_KEY 64

// -----------------------------------------------------------------------------
// Mini string buffer
// -----------------------------------------------------------------------------

typedef struct {
    char *data;
    size_t len, cap;
} Buf;

static void buf_init(Buf *b)  { b->data = NULL; b->len = b->cap = 0; }
static void buf_push(Buf *b, const char *s, size_t n) {
    if (b->len + n + 1 > b->cap) {
        size_t nc = b->cap + BUF_INC;
        char *nd = realloc(b->data, nc);
        if (!nd) { fprintf(stderr, "OOM\n"); exit(1); }
        b->data = nd; b->cap = nc;
    }
    memcpy(b->data + b->len, s, n);
    b->len += n;
    b->data[b->len] = 0;
}
static void buf_str(Buf *b, const char *s) { buf_push(b, s, strlen(s)); }

// -----------------------------------------------------------------------------
// Context lookup — 简单 linear search 数组,key → value
// -----------------------------------------------------------------------------

typedef struct { char key[MAX_KEY]; char val[MAX_KEY]; } KV;

typedef struct {
    KV items[64];
    int n;
} Ctx;

static void ctx_put(Ctx *c, const char *k, const char *v) {
    for (int i = 0; i < c->n; i++) {
        if (strcmp(c->items[i].key, k) == 0) {
            strncpy(c->items[i].val, v, MAX_KEY - 1);
            return;
        }
    }
    strncpy(c->items[c->n].key, k, MAX_KEY - 1);
    strncpy(c->items[c->n].val, v, MAX_KEY - 1);
    c->n++;
}

static const char *ctx_get(Ctx *c, const char *k) {
    for (int i = 0; i < c->n; i++) {
        if (strcmp(c->items[i].key, k) == 0) return c->items[i].val;
    }
    return "";
}

// -----------------------------------------------------------------------------
// 渲染:扫原始模板,替换 {{ key }} 和 {% if key %} ... {% endif %}
// -----------------------------------------------------------------------------

static void render_until(Ctx *ctx, Buf *out, const char *tpl, const char *end_tag, const char **new_pos) {
    /* 处理 text / {{ var }} 段,直到遇到 {% end_tag %} 为止;若 end_tag==NULL 直到 tpl 结尾 */
    const char *p = tpl;
    while (*p) {
        if (end_tag && strncmp(p, "{% ", 3) == 0) {
            const char *e = strstr(p + 3, end_tag);
            if (e) { *new_pos = p; return; }
            /* 不是 end_tag,继续 */
        }
        /* 找下一个 {{ */
        const char *lb = strstr(p, "{{");
        if (!lb) {
            buf_push(out, p, strlen(p));
            *new_pos = p + strlen(p);
            return;
        }
        /* text 段 */
        if (lb > p) buf_push(out, p, lb - p);
        const char *rb = strstr(lb + 2, "}}");
        if (!rb) { buf_push(out, lb, strlen(lb)); *new_pos = lb + strlen(lb); return; }
        /* 解析 {{ name | filter }} 简化版本:只取第一个非空白 token */
        char keybuf[MAX_KEY] = {0};
        int bi = 0;
        for (const char *q = lb + 2; q < rb && bi < MAX_KEY - 1; q++) {
            char c = *q;
            if (c == ' ' || c == '|') break;
            keybuf[bi++] = c;
        }
        keybuf[bi] = 0;
        const char *val = ctx_get(ctx, keybuf);
        if (val[0]) buf_str(out, val);
        p = rb + 2;
    }
    *new_pos = p;
}

static const char *skip_tag(const char *p) {
    /* 跳过一个完整 {% ... %} */
    const char *e = strstr(p, "%}");
    return e ? e + 2 : p + strlen(p);
}

/* 主渲染:扫模板,处理 text/{{var}}/{%if%}/{%endif%} */
static void render(Ctx *ctx, Buf *out, const char *tpl) {
    const char *p = tpl;
    while (*p) {
        /* text + {{var}} 直到遇到 {% if 或 {% endif */
        const char *new_pos;
        render_until(ctx, out, p, "if", &new_pos);
        if (!strncmp(new_pos, "{% if ", 6)) {
            /* 解析变量名 */
            char key[MAX_KEY] = {0};
            int bi = 0;
            const char *q = new_pos + 6;
            for (; *q != ' ' && bi < MAX_KEY - 1; q++) key[bi++] = *q;
            key[bi] = 0;
            const char *body_start = skip_tag(new_pos);
            /* 跳到匹配 {% endif %} */
            int depth = 1;
            const char *m = body_start;
            while (*m && depth > 0) {
                if (strncmp(m, "{% if ", 6) == 0) { depth++; m += 6; continue; }
                if (strncmp(m, "{% endif %}", 10) == 0) { depth--; if (depth == 0) break; m += 10; continue; }
                m++;
            }
            if (depth != 0) { fprintf(stderr, "missing endif\n"); return; }
            const char *endif_end = m + 10;
            /* 条件成立则渲染 [body_start, m) */
            const char *v = ctx_get(ctx, key);
            if (v && v[0] && strcmp(v, "false") != 0 && strcmp(v, "0") != 0) {
                /* 递归:body 内部可能有 text / {{ var }} / 嵌套 if (本 demo 简化不嵌套) */
                Buf temp; buf_init(&temp);
                /* 调用一个 sub-render 只处理 text/{{var}} */
                const char *sub;
                render_until(ctx, &temp, body_start, NULL, &sub);
                /* append temp 到 out 然后 free */
                buf_push(out, temp.data ? temp.data : "", temp.len);
                free(temp.data);
            }
            p = endif_end;
        } else {
            break;  /* 没有更多 if */
        }
    }
}

// -----------------------------------------------------------------------------
// Demo
// -----------------------------------------------------------------------------

int main(void) {
    const char *tpl =
        "# {{ TITLE }}\n"
        "\n"
        "Hi, I'm a {{ ROLE|developer }}.\n"
        "\n"
        "{% if SHOW_SKILLS %}\n"
        "Skills: ...\n"
        "{% endif %}\n"
        "\n"
        "Env: {{ ENV }}.\n";

    Ctx ctx = {{0}};
    ctx_put(&ctx, "TITLE",        "About me");
    ctx_put(&ctx, "ROLE",         "backend engineer");
    ctx_put(&ctx, "ENV",          "prod");
    ctx_put(&ctx, "SHOW_SKILLS",  "true");

    Buf out; buf_init(&out);
    render(&ctx, &out, tpl);
    printf("=== 模板引擎 demo (C 版, 简化:无 for / 无 filter) ===\n\n");
    printf("--- Template ---\n%s\n", tpl);
    printf("--- Rendered ---\n%s\n", out.data);

    /* 演示 if-else 分支:同一模板,SHOW_SKILLS=false */
    ctx_put(&ctx, "SHOW_SKILLS",  "false");
    Buf out2; buf_init(&out2);
    render(&ctx, &out2, tpl);
    printf("--- with SHOW_SKILLS=false (if 块不渲染) ---\n%s\n", out2.data);

    free(out.data);
    free(out2.data);
    return 0;
}

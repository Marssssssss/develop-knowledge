/* Prometheus text exposition format 0.0.4 —— 词法/语法解析层（实现头）。
 *
 * 本文件是**实现头**（implementation header）：里面是 static 定义，由 expose_demo.c 做
 * **文本级包含**，于是解析层与 main() 处在同一个翻译单元 —— 构建命令不变（仍只编译
 * expose_demo.c）、static 可见性零变化，也不必维护第二份 .c 及其头文件。
 * 代价：它只能被包含一次，不要单独编译它。
 *
 * 依赖包含点之前已定义的内容：Label / Sample / Family 三个结构、MAX_* 与 NAME_LEN /
 * VAL_LEN 常量、以及 g_fams / g_nfams 两个全局。
 */

/* 标签值只定义三种转义: \\ \" \n —— 官方规范明文列举 */
static void unescape(const char *src, char *dst, size_t cap)
{
    size_t o = 0;
    for (size_t i = 0; src[i] != '\0' && o + 1 < cap; i++) {
        if (src[i] == '\\' && src[i + 1] != '\0') {
            char c = src[i + 1];
            dst[o++] = (c == 'n') ? '\n' : c;
            i++;
        } else {
            dst[o++] = src[i];
        }
    }
    dst[o] = '\0';
}

/* strtod 原生接受 NaN/INF(大小写不敏感), 与 Go ParseFloat 语义一致 */
static int parse_value(const char *tok, double *out)
{
    char *end = NULL;
    *out = strtod(tok, &end);
    return (end != NULL && *end == '\0');
}

static Family *find_or_create(const char *name)
{
    for (int i = 0; i < g_nfams; i++)
        if (strcmp(g_fams[i].name, name) == 0)
            return &g_fams[i];
    if (g_nfams >= MAX_FAM) return NULL;
    Family *f = &g_fams[g_nfams++];
    snprintf(f->name, sizeof f->name, "%s", name);
    strcpy(f->type, "untyped"); /* 无 TYPE 行 => untyped */
    f->help[0] = '\0';
    f->nsamples = 0;
    return f;
}

/* 解析 'name{k="v",...}' 部分 */
static int parse_metric_part(char *part, char *name, Label *labels, int *nlabels)
{
    char *brace = strchr(part, '{');
    if (brace == NULL) { /* 无标签 */
        snprintf(name, NAME_LEN, "%s", part);
        *nlabels = 0;
        return 0;
    }
    *brace = '\0';
    snprintf(name, NAME_LEN, "%s", part);
    char *body = brace + 1;
    char *close = strrchr(body, '}');
    if (close == NULL) return -1; /* 括号未闭合 */
    *close = '\0';
    int n = 0;
    char *p = body;
    while (*p != '\0') {
        char *eq = strchr(p, '=');
        if (eq == NULL || eq[1] != '"') return -1;
        char *j = eq + 2;
        while (*j != '\0') { /* 找未转义的收尾引号 */
            if (*j == '"' && *(j - 1) != '\\') break;
            j++;
        }
        if (*j != '"') return -1;
        if (n >= MAX_LABELS) return -1;
        size_t klen = (size_t)(eq - p);
        if (klen >= NAME_LEN) klen = NAME_LEN - 1;
        memcpy(labels[n].key, p, klen);
        labels[n].key[klen] = '\0';
        *j = '\0';
        unescape(eq + 2, labels[n].value, VAL_LEN);
        n++;
        p = j + 1;
        while (*p == ',' || *p == ' ') p++;
    }
    *nlabels = n;
    return 0;
}

/* 样本行: metric_name_or_labels value [timestamp] —— 从右往左切最稳 */
static int parse_sample_line(char *line)
{
    char *save = NULL;
    /* 复制 token 列表, 从尾部识别 [ts] 和 value */
    char *toks[16];
    int nt = 0;
    for (char *t = strtok_r(line, " \t", &save); t != NULL && nt < 16; t = strtok_r(NULL, " \t", &save))
        toks[nt++] = t;
    if (nt < 2) return -1;

    char *last = toks[nt - 1];
    int is_int = 1;
    for (char *c = last; *c != '\0'; c++)
        if (!isdigit((unsigned char)*c) && *c != '-' && *c != '+') { is_int = 0; break; }

    char *val_tok, *ts_tok = NULL;
    if (nt >= 3 && is_int) {
        ts_tok = last;
        val_tok = toks[nt - 2];
        toks[nt - 2] = NULL; /* 截断, 剩余为指标部分 */
    } else {
        val_tok = last;
        toks[nt - 1] = NULL;
    }
    /* 重组指标部分(标签值内可含空格, 不能整体按空格切 —— 用原始内存拼接) */
    char part[MAX_LINE] = "";
    size_t off = 0;
    for (int i = 0; toks[i] != NULL; i++) {
        size_t l = strlen(toks[i]);
        if (off + l + 2 >= sizeof part) return -1;
        if (i > 0) part[off++] = ' ';
        memcpy(part + off, toks[i], l);
        off += l;
    }
    part[off] = '\0';

    char name[NAME_LEN];
    Label labels[MAX_LABELS];
    int nlabels = 0;
    if (parse_metric_part(part, name, labels, &nlabels) != 0) return -1;

    double value;
    if (!parse_value(val_tok, &value)) return -1;

    Family *f = find_or_create(name);
    if (f == NULL || f->nsamples >= MAX_SAMP) return -1;
    Sample *s = &f->samples[f->nsamples++];
    snprintf(s->name, sizeof s->name, "%s", name);
    memcpy(s->labels, labels, sizeof labels);
    s->nlabels = nlabels;
    s->value = value;
    s->has_ts = (ts_tok != NULL);
    s->ts = ts_tok ? strtoll(ts_tok, NULL, 10) : 0;
    return 0;
}

static void parse_exposition(const char *text)
{
    char buf[MAX_LINE];
    const char *p = text;
    while (*p != '\0') {
        size_t i = 0;
        while (*p != '\0' && *p != '\n' && i < sizeof buf - 1)
            buf[i++] = *p++;
        buf[i] = '\0';
        if (*p == '\n') p++;

        char *line = buf;
        while (isspace((unsigned char)*line)) line++;
        char *end = line + strlen(line);
        while (end > line && isspace((unsigned char)end[-1])) *--end = '\0';
        if (*line == '\0') continue; /* 空行忽略 */

        if (*line == '#') {
            char *rest = line + 1;
            while (isspace((unsigned char)*rest)) rest++;
            char *save = NULL;
            char *kw = strtok_r(rest, " \t", &save);
            if (kw == NULL) continue; /* 普通注释 */
            if (strcmp(kw, "HELP") == 0) {
                char *name = strtok_r(NULL, " \t", &save);
                char *doc = strtok_r(NULL, "", &save);
                Family *f = find_or_create(name);
                if (f != NULL && doc != NULL) {
                    char tmp[MAX_LINE];
                    unescape(doc, tmp, sizeof tmp); /* HELP 只需转义 \\ 和 \n */
                    snprintf(f->help, sizeof f->help, "%s", tmp);
                }
            } else if (strcmp(kw, "TYPE") == 0) {
                char *name = strtok_r(NULL, " \t", &save);
                char *type = strtok_r(NULL, " \t", &save);
                if (type == NULL ||
                    (strcmp(type, "counter") && strcmp(type, "gauge") &&
                     strcmp(type, "histogram") && strcmp(type, "summary") &&
                     strcmp(type, "untyped"))) {
                    fprintf(stderr, "bad TYPE line: %s\n", line);
                    exit(1);
                }
                Family *f = find_or_create(name);
                if (f != NULL) {
                    if (f->nsamples > 0) { /* TYPE 必须在首个样本之前 */
                        fprintf(stderr, "TYPE after first sample: %s\n", name);
                        exit(1);
                    }
                    snprintf(f->type, sizeof f->type, "%s", type);
                }
            }
            continue;
        }
        if (parse_sample_line(line) != 0) {
            fprintf(stderr, "bad sample line: %s\n", line);
            exit(1);
        }
    }
}

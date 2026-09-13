// HCL (HashiCorp Configuration Language) 最小 C 解析器
//
// 来源:HCL Native Syntax Specification v2.12.0
//      (github.com/hashicorp/hcl/blob/v2.12.0/hclsyntax/spec.md)
// 设计:简化教学实现,只覆盖 attribute/block/字符串字面量/嵌套 object
// 边界:不实现 for/splat/heredoc/function call

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

#define MAX_TOKENS 4096
#define MAX_TOKEN_TEXT 512

typedef enum {
    TOK_EOF, TOK_IDENT, TOK_NUMBER, TOK_STRING, TOK_OP, TOK_OTHER
} TokType;

typedef struct {
    TokType type;
    char text[MAX_TOKEN_TEXT];
} Token;

static Token tokens[MAX_TOKENS];
static int ntok = 0, ipos = 0;

// -----------------------------------------------------------------------------
// Lexer: 单字符流一次扫,识别标识符/数字/字符串字面量/单字符 OP/空白+注释
// -----------------------------------------------------------------------------

static void skip_ws_and_comments(const char **p) {
    while (**p) {
        if (isspace((unsigned char)**p)) { (*p)++; continue; }
        if (**p == '#' || (**p == '/' && *(*p + 1) == '/')) {
            while (**p && **p != '\n') (*p)++;
            continue;
        }
        if (**p == '/' && *(*p + 1) == '*') {
            *p += 2;
            while (**p && !(**p == '*' && *(*p + 1) == '/')) (*p)++;
            if (**p) *p += 2;
            continue;
        }
        break;
    }
}

static void add_token(TokType t, const char *start, int len) {
    if (ntok >= MAX_TOKENS) return;
    tokens[ntok].type = t;
    if (len >= MAX_TOKEN_TEXT) len = MAX_TOKEN_TEXT - 1;
    memcpy(tokens[ntok].text, start, len);
    tokens[ntok].text[len] = 0;
    ntok++;
}

static void lex(const char *src) {
    const char *p = src;
    while (*p) {
        skip_ws_and_comments(&p);
        if (!*p) break;

        if (isalpha((unsigned char)*p) || *p == '_') {
            const char *s = p;
            while (isalnum((unsigned char)*p) || *p == '_' || *p == '-') p++;
            add_token(TOK_IDENT, s, p - s);
        } else if (isdigit((unsigned char)*p) || (*p == '-' && isdigit((unsigned char)*(p + 1)))) {
            const char *s = p;
            if (*p == '-') p++;
            while (isdigit((unsigned char)*p)) p++;
            if (*p == '.') { p++; while (isdigit((unsigned char)*p)) p++; }
            add_token(TOK_NUMBER, s, p - s);
        } else if (*p == '"') {
            const char *s = ++p;
            while (*p && *p != '"') {
                if (*p == '\\') p++;
                p++;
            }
            add_token(TOK_STRING, s, p - s);
            if (*p) p++;
        } else if (strchr("={}[]().,+-*/", *p)) {
            char op[2] = {*p, 0};
            add_token(TOK_OP, op, 1);
            p++;
        } else {
            add_token(TOK_OTHER, p, 1);
            p++;
        }
    }
}

static Token *peek(void) { return &tokens[ipos]; }
static Token *next(void) { return &tokens[ipos++]; }

static int match_op(const char *op) {
    if (tokens[ipos].type == TOK_OP && strcmp(tokens[ipos].text, op) == 0) {
        ipos++; return 1;
    }
    return 0;
}

// -----------------------------------------------------------------------------
// Parser: 递归下降,打印 stdout 而非构造 dict (简洁)
// -----------------------------------------------------------------------------

static void die_at(Token *t, const char *msg) {
    fprintf(stderr, "Parse error at token '%s': %s\n", t->text, msg);
    exit(1);
}

// 表达式:仅支持字符串/数字/标识符(true/false/null)
static void parse_expr(void) {
    Token *t = peek();
    if (t->type == TOK_STRING) { printf("\"%s\"", next()->text); return; }
    if (t->type == TOK_NUMBER) { printf("%s", next()->text); return; }
    if (t->type == TOK_IDENT) {
        const char *id = t->text;
        if (strcmp(id, "true") == 0 || strcmp(id, "false") == 0 || strcmp(id, "null") == 0) {
            printf("%s", next()->text);
            return;
        }
        printf("IDENT(%s)", next()->text);
        return;
    }
    if (match_op("{")) { /* nested object -- 此 demo 简化跳过到匹配 } */
        int depth = 1;
        printf("{ ");
        while (depth > 0) {
            Token *x = next();
            if (strcmp(x->text, "{") == 0) depth++;
            else if (strcmp(x->text, "}") == 0) { depth--; if (depth==0) break; }
            printf("%s ", x->text);
        }
        printf("}");
        return;
    }
    die_at(t, "expected expression");
}

// 一条 item: name = expr  或  BLOCK_TYPE "label" "label2" { ... }
static void parse_item(int indent) {
    for (int i = 0; i < indent; i++) putchar(' ');
    Token *name = peek();
    if (name->type != TOK_IDENT) die_at(name, "expected IDENT for item");
    next();
    Token *next = peek();

    if (next->type == TOK_OP && strcmp(next->text, "=") == 0) {
        // attribute
        printf("%s = ", name->text);
        next(); // consume =
        parse_expr();
        printf("\n");
        return;
    }

    // 否则是 block:收 label 直到 {
    printf("%s ", name->text);
    while (peek()->type == TOK_STRING || (peek()->type == TOK_IDENT && strcmp(peek()->text, "{") != 0)) {
        Token *lbl = next();
        if (lbl->type == TOK_STRING) printf("\"%s\" ", lbl->text);
        else printf("%s ", lbl->text);
    }
    if (!match_op("{")) die_at(peek(), "expected { for block body");
    printf("{\n");
    while (!(peek()->type == TOK_OP && strcmp(peek()->text, "}") == 0)) {
        parse_item(indent + 2);
    }
    match_op("}");
    for (int i = 0; i < indent; i++) putchar(' ');
    printf("}\n");
}

int main(void) {
    const char *sample =
        "# 示例 HCL\n"
        "provider \"aws\" {\n"
        "  region = \"us-east-1\"\n"
        "  alias  = \"primary\"\n"
        "}\n"
        "\n"
        "resource \"aws_instance\" \"web\" {\n"
        "  ami           = \"ami-0c55b159cbfafe1f0\"\n"
        "  instance_type = \"t3.micro\"\n"
        "  count         = 2\n"
        "  tags = { Name = \"web-server\", Env = \"prod\" }\n"
        "}\n"
        "\n"
        "variable \"region\" {\n"
        "  default = \"us-west-2\"\n"
        "}\n";

    printf("=== HCL 解析器 demo (C 版,递归下降) ===\n\n");
    printf("--- Input HCL ---\n%s\n", sample);

    lex(sample);
    printf("--- Lexer: %d tokens ---\n", ntok);
    for (int i = 0; i < ntok && i < 16; i++) {
        const char *type_name[] = {"EOF","IDENT","NUMBER","STRING","OP","OTHER"};
        printf("  %-8s '%s'\n", type_name[tokens[i].type], tokens[i].text);
    }
    printf("  ... (%d total)\n\n", ntok);

    printf("--- Pretty-printed structure ---\n");
    while (ipos < ntok) parse_item(0);
    return 0;
}

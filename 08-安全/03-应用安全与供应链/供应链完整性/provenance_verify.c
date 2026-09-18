/* SLSA v1.0 Provenance 的验证规则（C 版）。
 *
 * 注意：C 版不引入密码学依赖，签名与摘要都用 **FNV-1a 占位实现**
 * （真实系统是 DSSE + 公钥签名 / SHA-256 摘要）。本文件演示的是
 * **验证规则的先后顺序与判定逻辑**，不是密码学本身。
 *
 * 规则来源（slsa.dev/spec/v1.0/provenance）：
 *   1. subject 摘要必须等于手上制品的摘要
 *   2. Consumers MUST accept only specific signer-builder pairs
 *   3. builder.id 是 SLSA Build level 的 sole determiner
 *   4. externalParameters 不可信 → 必须验证，SHOULD 拒绝未预期字段
 *   5. internalParameters 由可信平台设置 → 无需验证
 *   6. Consumers MUST ignore unrecognized fields
 */
#include <stdio.h>
#include <string.h>

#define NPAIR   2
#define NKEYEXP 2
#define NEXT    4
#define MAXSTR  160

typedef struct {
    char subject_digest[32];      /* 占位摘要（FNV-1a hex） */
    char predicate_type[MAXSTR];
    char builder[32];
    char ext_keys[NEXT][32];
    char ext_vals[NEXT][MAXSTR];
    int  next;
    char int_keys[NEXT][32];
    int  nint;
    char extra_pred[2][32];       /* 未识别的 predicate 字段 */
    int  nextra;
    int  claimed_level;           /* x_slsaBuildLevel 扩展；-1 表示没有 */
} Prov;

static const char *EXPECTED_EXT[NKEYEXP] = {"ref", "repository"};
static const char *PAIRS[NPAIR][2] = {
    {"github", "github-hosted"},
    {"google", "cloud-build"},
};

/* ---- 占位摘要 / MAC：FNV-1a 64 位 ---- */
static void fnv_hex(const char *s, const char *key, char out[32]) {
    unsigned long long h = 1469598103934665603ULL;
    const char *p;
    if (key) for (p = key; *p; p++) { h ^= (unsigned char)*p; h *= 1099511628211ULL; }
    for (p = s; *p; p++)            { h ^= (unsigned char)*p; h *= 1099511628211ULL; }
    sprintf(out, "%016llx%016llx", h, h ^ 0x5bf03635ULL);
}

static void canonical(const Prov *p, char out[512]) {
    int i;
    out[0] = 0;
    strcat(out, p->subject_digest);
    strcat(out, "|"); strcat(out, p->builder);
    strcat(out, "|"); strcat(out, p->predicate_type);
    for (i = 0; i < p->next; i++) {
        strcat(out, "|e:"); strcat(out, p->ext_keys[i]);
        strcat(out, "=");   strcat(out, p->ext_vals[i]);
    }
    for (i = 0; i < p->nint; i++) { strcat(out, "|i:"); strcat(out, p->int_keys[i]); }
}

static void sign_prov(const Prov *p, const char *key, char sig[32]) {
    char buf[512];
    canonical(p, buf);
    fnv_hex(buf, key, sig);
}

static int builder_level(const char *b) {
    if (!strcmp(b, "github-hosted")) return 2;
    if (!strcmp(b, "cloud-build"))   return 3;
    return 0;
}

static int pair_accepted(const char *signer, const char *builder) {
    int i;
    for (i = 0; i < NPAIR; i++)
        if (!strcmp(PAIRS[i][0], signer) && !strcmp(PAIRS[i][1], builder)) return 1;
    return 0;
}

static int failures = 0;

static void check(const char *label, int cond) {
    if (!cond) { printf("FAIL: %s\n", label); failures++; }
}

/* 返回 1=ALLOW, 0=DENY */
static int verify(const Prov *p, const char *sig, const char *key,
                  const char *artifact, int min_level, char why[256]) {
    char buf[512], expect[32];
    int i, j;

    canonical(p, buf);
    fnv_hex(buf, key, expect);
    if (strcmp(expect, sig)) { strcpy(why, "签名校验失败"); return 0; }

    if (strcmp(p->predicate_type, "https://slsa.dev/provenance/v1")) {
        strcpy(why, "predicateType 不是 SLSA provenance v1"); return 0;
    }
    /* 1) subject 摘要绑定构建输出 */
    fnv_hex(artifact, NULL, expect);
    if (strcmp(expect, p->subject_digest)) {
        strcpy(why, "subject 摘要与制品不匹配（制品在构建后被替换）"); return 0;
    }
    /* 2) signer-builder 配对 */
    if (!pair_accepted(key, p->builder)) {
        sprintf(why, "不接受该 signer-builder 配对: builder=%s", p->builder);
        return 0;
    }
    /* 3) builder.id 唯一决定级别 */
    {
        int lvl = builder_level(p->builder);
        if (lvl < min_level) {
            sprintf(why, "builder.id 声明的级别 %d 低于要求 %d", lvl, min_level);
            if (p->claimed_level >= 0)
                strcat(why, "；扩展字段 x_slsaBuildLevel 不得用于提升级别");
            return 0;
        }
    }
    /* 4) externalParameters 不可信 → 拒绝未预期字段 */
    for (i = 0; i < p->next; i++) {
        int ok = 0;
        for (j = 0; j < NKEYEXP; j++) if (!strcmp(p->ext_keys[i], EXPECTED_EXT[j])) ok = 1;
        if (!ok) {
            sprintf(why, "externalParameters 出现未预期字段: %s", p->ext_keys[i]);
            return 0;
        }
    }
    /* 5) internalParameters 无需验证（由可信平台设置）*/
    /* 6) 未识别字段必须忽略 */
    if (p->nextra > 0) sprintf(why, "ALLOW：忽略扩展字段 %s", p->extra_pred[0]);
    else strcpy(why, "ALLOW：internalParameters 按规范不校验");
    return 1;
}

static void base_prov(Prov *p, const char *artifact) {
    memset(p, 0, sizeof *p);
    fnv_hex(artifact, NULL, p->subject_digest);
    strcpy(p->predicate_type, "https://slsa.dev/provenance/v1");
    strcpy(p->builder, "github-hosted");
    strcpy(p->ext_keys[0], "repository");
    strcpy(p->ext_vals[0], "https://github.com/octocat/hello-world");
    strcpy(p->ext_keys[1], "ref");
    strcpy(p->ext_vals[1], "refs/heads/main");
    p->next = 2;
    strcpy(p->int_keys[0], "runnerArch");
    p->nint = 1;
    p->claimed_level = -1;
}

int main(void) {
    const char *KEY = "github";
    char sig[32], why[256];
    Prov p;

    /* S1 正常 */
    base_prov(&p, "binary-v1.0.0");
    sign_prov(&p, KEY, sig);
    check("S1 正常", verify(&p, sig, KEY, "binary-v1.0.0", 2, why) == 1);

    /* S2 制品被替换 */
    check("S2 制品被替换",
          verify(&p, sig, KEY, "binary-v1.0.0-with-backdoor", 2, why) == 0);
    check("S2 拒绝理由正确", strstr(why, "摘要") != NULL);

    /* S3 未知 builder */
    base_prov(&p, "binary-v1.0.0");
    strcpy(p.builder, "evil-builder");
    sign_prov(&p, KEY, sig);
    check("S3 未知 builder",
          verify(&p, sig, KEY, "binary-v1.0.0", 2, why) == 0);

    /* S4 signer-builder 不匹配：github 不能给 cloud-build 签名 */
    base_prov(&p, "binary-v1.0.0");
    strcpy(p.builder, "cloud-build");
    sign_prov(&p, KEY, sig);
    check("S4 signer-builder 不匹配",
          verify(&p, sig, KEY, "binary-v1.0.0", 2, why) == 0);

    /* S5 externalParameters 被塞了意外字段 */
    base_prov(&p, "binary-v1.0.0");
    strcpy(p.ext_keys[2], "entryPoint");
    strcpy(p.ext_vals[2], "attacker-supplied.yml");
    p.next = 3;
    sign_prov(&p, KEY, sig);
    check("S5 externalParameters 有意外字段",
          verify(&p, sig, KEY, "binary-v1.0.0", 2, why) == 0);

    /* S6 未知扩展字段被忽略 → 仍 ALLOW */
    base_prov(&p, "binary-v1.0.0");
    strcpy(p.extra_pred[0], "x_customHint");
    p.nextra = 1;
    sign_prov(&p, KEY, sig);
    check("S6 未知扩展字段被忽略",
          verify(&p, sig, KEY, "binary-v1.0.0", 2, why) == 1);

    /* S7 扩展自称 L3，要求 L3 → 仍应拒绝 */
    base_prov(&p, "binary-v1.0.0");
    strcpy(p.extra_pred[0], "x_slsaBuildLevel");
    p.nextra = 1;
    p.claimed_level = 3;
    sign_prov(&p, KEY, sig);
    check("S7 扩展自称 L3 但要求 L3",
          verify(&p, sig, KEY, "binary-v1.0.0", 3, why) == 0);

    /* 同一份证明：要求级别 2 → 通过，3 → 拒绝 */
    base_prov(&p, "binary-v1.0.0");
    sign_prov(&p, KEY, sig);
    check("要求 L2 通过", verify(&p, sig, KEY, "binary-v1.0.0", 2, why) == 1);
    check("要求 L3 拒绝", verify(&p, sig, KEY, "binary-v1.0.0", 3, why) == 0);

    /* 伪造签名 */
    check("伪造签名被拒", verify(&p, "00000000000000000000000000000000",
                                 KEY, "binary-v1.0.0", 2, why) == 0);

    if (failures) { printf("FAILED %d\n", failures); return 1; }
    printf("all checks passed\n");
    return 0;
}

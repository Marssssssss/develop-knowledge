/*
 * Helm Chart 模板渲染三件套 (C):值合并 → 模板求值 → 按 Kind 排序。
 *
 * 权威来源(实际读过):
 *   1. https://helm.sh/docs/chart_template_guide/values_files/
 *   2. https://helm.sh/docs/chart_template_guide/functions_and_pipelines/
 *   3. https://cdn.jsdelivr.net/gh/helm/helm@v3.19.0/pkg/releaseutil/kind_sorter.go
 *
 * 与 python / go 版同构:值优先级 values.yaml < 父 chart < -f < --set;置 null 删除键;
 * 管道值作为函数最后一个参数;InstallOrder 中未知 kind 永远最后、同 kind 保序。
 * C 版用扁平的 "a.b.c" 点号键表达嵌套 values,语义与深合并一致。
 */
#include "helm_render_impl.h"

static int g_checks = 0;
static void ck(int cond, const char *msg) {
    if (!cond) { printf("ASSERT FAILED: %s\n", msg); exit(1); }
    g_checks++;
}

int main(void) {
    /* 1. 值优先级:chart < parent < -f < --set */
    KV chart; memset(&chart, 0, sizeof(chart));
    kv_set(&chart, "image.repo", "nginx");
    kv_set(&chart, "image.tag", "1.0");
    kv_set(&chart, "replicas", "1");
    kv_set(&chart, "extra", "keep");
    KV parent; memset(&parent, 0, sizeof(parent)); kv_set(&parent, "image.tag", "1.1");
    KV file; memset(&file, 0, sizeof(file)); kv_set(&file, "replicas", "3");
    KV setv; memset(&setv, 0, sizeof(setv)); kv_set(&setv, "image.tag", "2.0");
    kv_coalesce(&chart, &parent);
    kv_coalesce(&chart, &file);
    kv_coalesce(&chart, &setv);
    ck(strcmp(kv_get(&chart, "image.repo"), "nginx") == 0, "未覆盖的键保留默认");
    ck(strcmp(kv_get(&chart, "image.tag"), "2.0") == 0, "--set 优先级最高");
    ck(strcmp(kv_get(&chart, "replicas"), "3") == 0, "-f 覆盖 chart 默认值");
    ck(strcmp(kv_get(&chart, "extra"), "keep") == 0, "无关键不受影响");

    /* 2. null 删除键 */
    KV base; memset(&base, 0, sizeof(base));
    kv_set(&base, "livenessProbe.httpGet.path", "/login");
    kv_set(&base, "livenessProbe.httpGet.port", "http");
    kv_set(&base, "livenessProbe.initialDelaySeconds", "120");
    KV ovr; memset(&ovr, 0, sizeof(ovr));
    kv_set(&ovr, "livenessProbe.httpGet.path", "<null>");
    kv_set(&ovr, "livenessProbe.httpGet.port", "<null>");
    kv_coalesce(&base, &ovr);
    ck(!kv_has(&base, "livenessProbe.httpGet.path"), "置 null 应删除该键");
    ck(!kv_has(&base, "livenessProbe.httpGet.port"), "同前缀的键也应删除");
    ck(kv_has(&base, "livenessProbe.initialDelaySeconds"), "同层其他键保留");

    /* 3. 模板求值 */
    KV ctx; memset(&ctx, 0, sizeof(ctx));
    kv_set(&ctx, "Values.favorite.drink", "coffee");
    kv_set(&ctx, "Values.favorite.food", "pizza");
    kv_set(&ctx, "Values.favorite.drinks", "coffee,tea,water");
    kv_set(&ctx, "Release.Name", "trendsetting-p");
    kv_set(&ctx, "Chart.Name", "mychart");
    char out[VALLEN];
    eval_action(&ctx, ".Values.favorite.drink | quote", out, sizeof(out));
    ck(strcmp(out, "\"coffee\"") == 0, "quote");
    eval_action(&ctx, "quote .Values.favorite.drink", out, sizeof(out));
    ck(strcmp(out, "\"coffee\"") == 0, "函数式调用等价");
    eval_action(&ctx, ".Values.favorite.food | upper | quote", out, sizeof(out));
    ck(strcmp(out, "\"PIZZA\"") == 0, "管道链式");
    eval_action(&ctx, ".Values.favorite.drink | repeat 5 | quote", out, sizeof(out));
    ck(strcmp(out, "\"coffeecoffeecoffeecoffeecoffee\"") == 0, "repeat:管道值最后参数");
    eval_action(&ctx, ".Values.favorite.drinks | join \", \" | quote", out, sizeof(out));
    ck(strcmp(out, "\"coffee, tea, water\"") == 0, "join");
    eval_action(&ctx, ".Values.nosuch | default \"tea\" | quote", out, sizeof(out));
    ck(strcmp(out, "\"tea\"") == 0, "default:值为空时取默认");
    eval_action(&ctx, ".Values.favorite.drink | default \"tea\"", out, sizeof(out));
    ck(strcmp(out, "coffee") == 0, "default:值非空不生效");

    /* 4. 整段渲染 */
    char big[512];
    render(&ctx, "name: {{ .Release.Name }}-configmap", big, sizeof(big));
    ck(strcmp(big, "name: trendsetting-p-configmap") == 0, "整段渲染");
    render(&ctx, "a{{/* c */}}b", big, sizeof(big));
    ck(strcmp(big, "ab") == 0, "注释 action 输出空");
    render(&ctx, "plain", big, sizeof(big));
    ck(strcmp(big, "plain") == 0, "无 action 原样输出");

    /* 5. InstallOrder 与 lessByKind */
    ck(strcmp(INSTALL_ORDER[0], "PriorityClass") == 0, "PriorityClass 第一");
    ck(strcmp(INSTALL_ORDER[N_ORDER - 1], "APIService") == 0, "APIService 最后");
    ck(less_by_kind("Namespace", "ConfigMap"), "Namespace 早于 ConfigMap");
    ck(less_by_kind("Service", "Deployment"), "Service 早于 Deployment");
    ck(less_by_kind("Deployment", "StatefulSet"), "Deployment 早于 StatefulSet");
    ck(!less_by_kind("ConfigMap", "ConfigMap"), "相同 kind 保序");
    ck(less_by_kind("Deployment", "Widget"), "已知 kind 在未知之前");
    ck(!less_by_kind("Widget", "Deployment"), "未知 kind 排最后");
    ck(less_by_kind("Alpha", "Zeta"), "双未知按字母序");
    ck(!less_by_kind("Zeta", "Alpha"), "双未知字母序反向");

    /* 6. 稳定排序 */
    Manifest ms[MAX_MANIFESTS] = {
        {"Deployment", "d1"}, {"Widget", "w1"}, {"ConfigMap", "c1"},
        {"Deployment", "d2"}, {"Namespace", "n1"}
    };
    sort_manifests_by_kind(ms, 5);
    ck(strcmp(ms[0].kind, "Namespace") == 0, "Namespace 第一");
    ck(strcmp(ms[4].kind, "Widget") == 0, "未知 kind 最后");
    ck(strcmp(ms[2].name, "d1") == 0 && strcmp(ms[3].name, "d2") == 0, "同 kind 保序");

    printf("helm_render(c): %d assertions passed\n", g_checks);
    return 0;
}

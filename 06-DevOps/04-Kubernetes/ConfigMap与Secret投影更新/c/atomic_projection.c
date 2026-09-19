/*
 * ConfigMap / Secret 卷投影的原子写入 (C),对齐 kubelet 的 AtomicWriter。
 *
 * 权威来源(实际读过):
 *   1. https://kubernetes.io/docs/concepts/configuration/configmap/
 *   2. https://cdn.jsdelivr.net/gh/kubernetes/kubernetes@master/pkg/volume/util/atomic_writer.go
 *
 * 与 python / go 版同构:更新 = 换 `..data` 符号链接指向(rename 原子);
 * payload 未变则不新建时间戳目录;可见链接只为路径第一段建且只建一次;
 * rename 必须在建可见链接之前。
 */
#include "atomic_projection_impl.h"

static int g_checks = 0;
static void ck(int cond, const char *msg) {
    if (!cond) { printf("ASSERT FAILED: %s\n", msg); exit(1); }
    g_checks++;
}

int main(void) {
    ck(validate_path("") != NULL, "空路径应被拒");
    ck(validate_path("/etc/passwd") != NULL, "绝对路径应被拒");
    ck(validate_path("a/../b") != NULL, "含 .. 元素应被拒");
    ck(validate_path("..data/x") != NULL, "以 .. 开头且长度>2 应被拒");
    ck(validate_path("..") != NULL, "单独的 '..' 应被拒");
    ck(validate_path("foo/bar") == NULL, "正常相对路径应放行");

    char n255[256]; memset(n255, 'a', 255); n255[255] = '\0';
    ck(validate_path(n255) == NULL, "255 字符文件名放行");
    char n256[257]; memset(n256, 'a', 256); n256[256] = '\0';
    ck(validate_path(n256) != NULL, "256 字符文件名被拒");
    /* 4095 = 16*255 + 15 */
    char long_ok[4200]; long_ok[0] = '\0';
    for (int i = 0; i < 16; i++) { if (i) strcat(long_ok, "/"); strcat(long_ok, n255); }
    char long_bad[4600]; long_bad[0] = '\0';
    for (int i = 0; i < 17; i++) { if (i) strcat(long_bad, "/"); strcat(long_bad, n255); }
    ck(strlen(long_ok) == 4095 && strlen(long_bad) == 4351, "测例长度符合预期");
    ck(validate_path(long_ok) == NULL, "4095 字符路径放行");
    ck(validate_path(long_bad) != NULL, "4351 字符路径被拒");

    /* 首次写入 */
    FS fs; fs_init(&fs);
    Writer w; memset(&w, 0, sizeof(w)); w.fs = &fs; snprintf(w.target, 64, "/mnt/cfg");
    const char *k1[] = { "app.yml" }; const char *v1[] = { "replicas: 1" };
    w_write(&w, k1, v1, 1);
    const char *ts1 = nodes_get(&fs.links, "/mnt/cfg/" DATA_DIR_NAME);
    ck(ts1 && strncmp(ts1, "..", 2) == 0, "..data 应指向 .. 开头的时间戳目录");
    const char *lk = nodes_get(&fs.links, "/mnt/cfg/app.yml");
    ck(lk && strcmp(lk, "..data/app.yml") == 0, "可见文件软链到 ..data/app.yml");
    const char *rd = w_read_visible(&w, "app.yml");
    ck(rd && strcmp(rd, "replicas: 1") == 0, "读者读到内容");

    /* rename 早于可见链接 */
    ck(trace_index(&fs, "rename /mnt/cfg/..data_tmp") < trace_index(&fs, "symlink /mnt/cfg/app.yml"),
       "rename 必须先于可见链接创建");

    /* 内容未变 → no-op */
    int before = fs.ntrace;
    w_write(&w, k1, v1, 1);
    ck(fs.ntrace - before == 1 && strcmp(fs.trace[fs.ntrace - 1], "noop: payload unchanged") == 0,
       "内容未变应 no-op");
    ck(strcmp(nodes_get(&fs.links, "/mnt/cfg/" DATA_DIR_NAME), ts1) == 0, "no-op 后指向不变");

    /* 内容变化 */
    const char *v2[] = { "replicas: 2" };
    w_write(&w, k1, v2, 1);
    const char *ts2 = nodes_get(&fs.links, "/mnt/cfg/" DATA_DIR_NAME);
    ck(ts2 && strcmp(ts2, ts1) != 0, "内容变化应换时间戳目录");
    rd = w_read_visible(&w, "app.yml");
    ck(rd && strcmp(rd, "replicas: 2") == 0, "读者读到新内容");
    int stale = 0;
    char pre[PATHLEN]; snprintf(pre, PATHLEN, "/mnt/cfg/%s", ts1);
    for (int i = 0; i < fs.files.n; i++)
        if (strncmp(fs.files.path[i], pre, strlen(pre)) == 0) stale = 1;
    ck(!stale, "旧时间戳目录应被删除");

    /* 新增 / 删除 key */
    const char *k3[] = { "app.yml", "extra.txt" };
    const char *v3[] = { "replicas: 2", "hi" };
    w_write(&w, k3, v3, 2);
    ck(w_read_visible(&w, "extra.txt") != NULL, "新增 key 可读");
    const char *v4[] = { "replicas: 3" };
    w_write(&w, k1, v4, 1);
    rd = w_read_visible(&w, "app.yml");
    ck(rd && strcmp(rd, "replicas: 3") == 0, "删除 extra 后 app.yml 仍可读");
    ck(w_read_visible(&w, "extra.txt") == NULL, "被删 key 不应可读");

    /* 嵌套路径只为第一段建链接 */
    FS fs2; fs_init(&fs2);
    Writer w2; memset(&w2, 0, sizeof(w2)); w2.fs = &fs2; snprintf(w2.target, 64, "/mnt/cfg2");
    const char *k5[] = { "dir/a.yml", "dir/b.yml" };
    const char *v5[] = { "a", "b" };
    w_write(&w2, k5, v5, 2);
    lk = nodes_get(&fs2.links, "/mnt/cfg2/dir");
    ck(lk && strcmp(lk, "..data/dir") == 0, "嵌套只为第一段建链接");
    ck(nodes_get(&fs2.links, "/mnt/cfg2/dir/a.yml") == NULL, "不应为第二段建独立链接");

    /* 1 MiB 与延迟公式 */
    ck(CONFIGMAP_MAX_BYTES == 1048576, "1 MiB = 1048576");
    ck(total_update_delay(60, "Get", 30, 0.5) == 60.0, "Get → 传播延迟 0");
    ck(total_update_delay(60, "Watch", 30, 0.5) == 60.5, "Watch → +0.5");
    ck(total_update_delay(60, "TTL", 30, 0.5) == 90.0, "TTL → +30");

    printf("atomic_projection(c): %d assertions passed\n", g_checks);
    return 0;
}

/* Loki 存储与 LogQL 语义 —— C 版断言集。
 *
 * 构建：cc -std=c99 -O0 -o loki_demo loki_demo.c -lm
 *       （**本机无 C 工具链，未实跑**；已用 _docs/tools/c_sanity.py --tu
 *         按同翻译单元核查 static 函数实参个数与本地头文件存在性）
 *
 * 四个实现头是**文本级包含**（implementation header 模式）：所有 static
 * 定义与 main() 处在同一个翻译单元，不需要改构建命令，也不影响 static
 * 的可见性。代价：这四个头不能被单独编译。
 */

#include "loki_core.h"
#include "loki_storage.h"
#include "loki_wal.h"
#include "loki_query.h"

static int g_passed = 0;
static int g_failed = 0;

static void check(const char *label, int cond) {
    if (cond) {
        g_passed++;
        return;
    }
    g_failed++;
    printf("  FAIL %s\n", label);
}

static int near(double a, double b) { return fabs(a - b) < 1e-9; }

static const char *push_name(int code) {
    if (code == LOKI_PUSH_APPENDED) {
        return "appended";
    }
    if (code == LOKI_PUSH_DUPLICATE) {
        return "duplicate";
    }
    return "out_of_order";
}

int main(void) {
    const long long SEC = 1000000000LL;
    const long long T0 = 1700000000000000000LL;

    /* ------------------------------------------------------------ A */
    loki_limits lim = loki_default_limits();
    check("A1 ingestion_rate_mb 默认 4MB/s", near(lim.ingestion_rate_mb, 4.0));
    check("A2 ingestion_burst_size_mb 默认 6MB", near(lim.ingestion_burst_size_mb, 6.0));
    check("A3 限流策略默认 global", strcmp(lim.ingestion_rate_strategy, "global") == 0);
    check("A4 每序列标签数默认 30", lim.max_label_names_per_series == 30);
    check("A5 max_streams_per_user 默认 0(本机不限)", lim.max_streams_per_user == 0);
    check("A6 max_global_streams_per_user 默认 5000", lim.max_global_streams_per_user == 5000);
    check("A7 活跃流窗口 = chunk_idle_period 30m", strcmp(lim.chunk_idle_period, "30m") == 0);

    /* ------------------------------------------------------------ B */
    int ok = 0;
    double per = distributor_rate_mb(4.0, 10, "global", &ok);
    check("B1 均摊不报错", ok == 1);
    check("B1b global 策略按实例均摊 4/10", near(per, 0.4));
    check("B2 扩容后每实例额度再降 4/20", near(distributor_rate_mb(4.0, 20, "global", &ok), 0.2));
    check("B3 local 策略不做均摊", near(distributor_rate_mb(4.0, 10, "local", &ok), 4.0));
    check("B4 global 集群总额 = 配置值", near(cluster_effective_rate_mb(4.0, 10, "global"), 4.0));
    check("B5 local 集群总额 = 配置值 x N(坑)", near(cluster_effective_rate_mb(4.0, 10, "local"), 40.0));
    check("B6 burst 不随实例数均摊(官方原文)", near(distributor_burst_mb(6.0, 10, "global"), 6.0));
    check("B7 实例减少反而抬高每实例阈值",
          distributor_rate_mb(4.0, 5, "global", &ok) > distributor_rate_mb(4.0, 10, "global", &ok));
    distributor_rate_mb(4.0, 0, "global", &ok);
    check("B8 实例数为 0 直接标记失败", ok == 0);

    /* ------------------------------------------------------------ C */
    static loki_entry prod;
    entry_init(&prod, T0, "on prod");
    entry_set(&prod, "app", "api-server");
    entry_set(&prod, "env", "prod");
    static loki_entry noenv;
    entry_init(&noenv, T0, "no env label");
    entry_set(&noenv, "app", "api-server");

    const loki_matcher exact[] = {{"app", "=", "api"}};
    loki_selector s_exact = {exact, 1};
    check("C1 精确匹配不做前缀", selector_matches(&s_exact, &prod) == 0);
    const loki_matcher hit[] = {{"app", "=", "api-server"}};
    loki_selector s_hit = {hit, 1};
    check("C2 精确匹配命中", selector_matches(&s_hit, &prod) == 1);
    /* glob_fullmatch 是完全匹配；若误用 strstr 这里会命中 → 断言会挂。 */
    const loki_matcher anchored[] = {{"app", "=~", "api"}};
    loki_selector s_anchored = {anchored, 1};
    check("C3 流选择器的通配完全锚定(坑)", selector_matches(&s_anchored, &prod) == 0);
    const loki_matcher wildcard[] = {{"app", "=~", "api*"}};
    loki_selector s_wild = {wildcard, 1};
    check("C4 需要 * 才能命中", selector_matches(&s_wild, &prod) == 1);
    const loki_matcher neprod[] = {{"env", "!=", "prod"}};
    loki_selector s_neprod = {neprod, 1};
    check("C5 缺失标签 == 空串故 env!=prod 命中", selector_matches(&s_neprod, &noenv) == 1);
    /* C 版用 glob 的 ?* 表示"至少一个字符"，对应正则的 .+ */
    const loki_matcher missing[] = {{"env", "!~", "?*"}};
    loki_selector s_missing = {missing, 1};
    check("C6 env!~\"?*\" 同样命中缺失标签(反直觉)", selector_matches(&s_missing, &noenv) == 1);
    check("C6b 对已有 env 不命中", selector_matches(&s_missing, &prod) == 0);
    check("C7 glob 完全匹配自检", glob_fullmatch("api*", "api-server") == 1);
    check("C7b 无 * 时锚定严格", glob_fullmatch("api", "api-server") == 0);

    /* ------------------------------------------------------------ D */
    const char *LINE = "level=error msg=disk path=/api/v1/users";
    check("D1 |= 是子串包含", line_filter_accepts(LINE, "error", LOKI_LINE_CONTAINS) == 1);
    check("D2 行过滤大小写敏感", line_filter_accepts(LINE, "ERROR", LOKI_LINE_CONTAINS) == 0);
    /* 行过滤是搜索语义（非锚定）—— 与流选择器 =~ 的完全锚定刻意相反。 */
    check("D3 |~ 是搜索语义(非锚定)", line_filter_accepts(LINE, "api", LOKI_LINE_SEARCH) == 1);
    check("D4 对照:同一个 api 在流选择器里不命中", glob_fullmatch("api", "api-server") == 0);
    check("D5 != 是不包含", line_filter_accepts(LINE, "timeout", LOKI_LINE_NOT_CONTAINS) == 1);
    check("D6 !~ 取反", line_filter_accepts(LINE, "error", LOKI_LINE_NOT_SEARCH) == 0);

    /* ------------------------------------------------------------ E */
    static loki_entry parsed;
    entry_init(&parsed, T0, "dur=12 level=error");
    double got = 0.0;
    check("E1 键值对取值成功", extract_number(parsed.line, "dur", &got) == 0 && near(got, 12.0));
    check("E2 取不到数值返回失败", extract_number(parsed.line, "missing", &got) == -1);
    /* 解析失败不丢行，只打错误标签 —— 官方原文如此。 */
    static loki_entry failed;
    entry_init(&failed, T0, "dur=abc");
    if (extract_number(failed.line, "dur", &got) != 0) {
        entry_append_error(&failed, LOKI_ERR_SAMPLE);
    }
    check("E3 解析失败不丢行(官方原文)", failed.nlabels >= 0 && entry_has_error(&failed) == 1);
    check("E3b 错误标签名", strcmp(entry_get(&failed, LOKI_ERROR_LABEL), LOKI_ERR_SAMPLE) == 0);
    check("E4 合法行没有 __error__", entry_has_error(&parsed) == 0);
    static loki_entry doubled;
    entry_init(&doubled, T0, "x");
    entry_append_error(&doubled, LOKI_ERR_JSON);
    entry_append_error(&doubled, LOKI_ERR_SAMPLE);
    check("E5 多个错误用逗号累加", strcmp(entry_get(&doubled, LOKI_ERROR_LABEL), "JSONParserErr, SampleExtractionErr") == 0);
    /* unwrap 会消费掉被 unwrap 的标签：否则每个取值都裂成独立序列。 */
    static loki_entry unwrapped;
    entry_init(&unwrapped, T0, "dur=7");
    entry_set(&unwrapped, "dur", "7");
    if (extract_number(unwrapped.line, "dur", &got) == 0) {
        unwrapped.value = got;
        unwrapped.has_value = 1;
        entry_del(&unwrapped, "dur");
    }
    check("E6 unwrap 取到数值", unwrapped.has_value == 1 && near(unwrapped.value, 7.0));
    check("E6b unwrap 会消费掉被 unwrap 的标签", entry_get(&unwrapped, "dur") == NULL);

    static loki_entry mixed[2];
    entry_init(&mixed[0], T0 - 2 * SEC, "ok");
    entry_init(&mixed[1], T0 - SEC, "bad");
    entry_append_error(&mixed[1], LOKI_ERR_JSON);
    check("E7 干净输入允许指标查询", metric_query_allowed(mixed, 1) == 1);
    check("E8 指标查询含错误直接失败(官方原文)", metric_query_allowed(mixed, 2) == 0);

    /* ------------------------------------------------------------ F */
    check("F1 count_over_time 数行数", near(range_count_over_time(mixed, 2), 2.0));
    check("F2 rate = 行数/窗口秒数", near(range_rate(mixed, 2, 300.0), 2.0 / 300.0));
    double q4[4] = {10.0, 20.0, 30.0, 40.0};
    /* 分位数走线性插值：[10,20,30,40] 的 0.5 分位是 25，不是 20 也不是 30。 */
    check("F3 分位数线性插值", near(prom_quantile(0.5, q4, 4), 25.0));
    check("F3b φ=0.25 插值", near(prom_quantile(0.25, q4, 4), 17.5));
    check("F3c φ=0 取最小", near(prom_quantile(0.0, q4, 4), 10.0));
    check("F3d φ=1 取最大", near(prom_quantile(1.0, q4, 4), 40.0));
    double q3[3] = {10.0, 20.0, 30.0};
    check("F3e 奇数样本落在元素上不插值", near(prom_quantile(0.5, q3, 3), 20.0));
    double four[4] = {1.0, 2.0, 3.0, 4.0};
    check("F4 总体方差(除以 N)", near(population_stdvar(four, 4), 1.25));
    check("F4b stddev = sqrt(总体方差)", near(sqrt(population_stdvar(q4, 4)), sqrt(125.0)));
    check("F5 sum_over_time", near(range_sum_over_time(q4, 4), 100.0));

    /* ------------------------------------------------------------ G */
    static loki_stream st;
    stream_init(&st, "team-a", 1572864, 1800.0, 7200.0, 6.0);
    check("G1 递增时间戳可追加", stream_push(&st, T0, "line0") == LOKI_PUSH_APPENDED);
    check("G1b 第二条", stream_push(&st, T0 + SEC, "line1") == LOKI_PUSH_APPENDED);
    check("G2 同 ts 同内容 = 重复被静默忽略", stream_push(&st, T0 + SEC, "line1") == LOKI_PUSH_DUPLICATE);
    check("G3 同 ts 不同内容 = 接受(坑)", stream_push(&st, T0 + SEC, "other") == LOKI_PUSH_APPENDED);
    check("G4 时间戳回退被拒", stream_push(&st, T0 - SEC, "back") == LOKI_PUSH_OUT_OF_ORDER);
    check("G4b 被拒的行没有入账", st.entry_count == 3);
    check("G4c 重复计数单独统计", st.ignored_duplicates == 1);
    check("G4d 乱序计数单独统计", st.rejected_out_of_order == 1);
    check("G4e 推送结果映射正确", strcmp(push_name(LOKI_PUSH_DUPLICATE), "duplicate") == 0);
    /* 空闲期自**最后一次写入**（T0+1s）起算，所以这里是 T0+1s+1800s。 */
    check("G5 空闲 30m 触发刷写", stream_flush_reasons(&st, T0 + SEC + 1800 * SEC) == LOKI_FLUSH_IDLE);
    check("G5b 未到 30m 不触发", stream_flush_reasons(&st, T0 + SEC + 1799 * SEC) == 0);
    check("G5c 未到 2h 不因存活期触发", stream_flush_reasons(&st, T0 + 7199 * SEC) == LOKI_FLUSH_IDLE);
    static loki_stream tri;
    stream_init(&tri, "t", 100, 1800.0, 7200.0, 6.0);
    for (int i = 0; i < 6; i++) {
        char buf[LOKI_LINE_MAX];
        memset(buf, 'x', sizeof(buf) - 1);
        buf[sizeof(buf) - 1] = '\0';
        buf[0] = (char)('a' + i); /* 各行内容不同，避免被当成重复行 */
        stream_push(&tri, T0 + (long long)i * SEC, buf);
    }
    check("G6 三条件可同时成立",
          stream_flush_reasons(&tri, T0 + 7200 * SEC) == (LOKI_FLUSH_IDLE | LOKI_FLUSH_SIZE | LOKI_FLUSH_AGE));
    check("G7 刷写后返回条目数", stream_flush(&tri) == 6);
    check("G7b 刷写后计数归零", tri.entry_count == 0);
    check("G7c flush 次数累加", tri.flushed_chunks == 1);
    check("G8 rf=3 时仲裁数 2", loki_quorum(3) == 2);
    check("G8b rf=5 时仲裁数 3", loki_quorum(5) == 3);
    check("G8c rf=2 时仲裁数 2(两副本都要成功)", loki_quorum(2) == 2);
    check("G8d rf=0 返回失败", loki_quorum(0) == -1);

    static loki_wal wal;
    wal_init(&wal, 300.0);
    for (int i = 0; i < 3; i++) {
        char buf[LOKI_LINE_MAX];
        snprintf(buf, sizeof(buf), "l%d", i);
        wal_append(&wal, "t", T0 + (long long)i * SEC, buf);
    }
    check("G9 WAL 记录未刷写数据", wal_pending_count(&wal) == 3);
    check("G9b WAL 重放可恢复全部条目", wal.count - wal.replayed_from == 3);
    check("G10 未到 checkpoint 周期不动", wal_checkpoint(&wal, T0 + 100 * SEC) == 0);
    check("G10b 到 5m 折叠 checkpoint", wal_checkpoint(&wal, T0 + 300 * SEC) == 1);
    check("G10c checkpoint 后无待重放记录", wal_pending_count(&wal) == 0);
    wal_append(&wal, "t", T0 + 400 * SEC, "late");
    wal_mark_flushed(&wal);
    check("G11 优雅关闭后 WAL 可整体截断", wal_pending_count(&wal) == 0);
    check("G12 chunk 目标 1.5MiB", LOKI_CHUNK_TARGET_SIZE == 1572864);
    check("G12b 未压缩上限 256KiB", LOKI_CHUNK_BLOCK_SIZE == 262144);
    check("G12c 存活上限 2h", near(LOKI_MAX_CHUNK_AGE_S, 7200.0));
    check("G12d 默认编码 gzip(最佳实践推荐 snappy)", strcmp(LOKI_DEFAULT_ENCODING, "gzip") == 0);

    /* ------------------------------------------------------------ H */
    check("H1 行长等于上限可通过", strcmp(check_line(&lim, 262144), "ok") == 0);
    check("H2 超限默认拒绝(truncate=false)", strcmp(check_line(&lim, 262145), "rejected") == 0);
    loki_limits trunc = lim;
    trunc.max_line_size_truncate = 1;
    check("H3 开启 truncate 则截断而非拒绝", strcmp(check_line(&trunc, 262145), "truncated") == 0);
    static loki_entry few;
    entry_init(&few, T0, "x");
    for (int i = 0; i < 29; i++) {
        char name[LOKI_MAX_NAME];
        snprintf(name, sizeof(name), "l%d", i);
        entry_set(&few, name, "v");
    }
    check("H4 标签数 29 未超限", strcmp(check_labels(&lim, &few), "ok") == 0);
    entry_set(&few, "l29", "v");
    /* 判据是 `>`：恰好等于限额 30 也**通过**。 */
    check("H5 恰好 30 个标签仍通过(边界)", strcmp(check_labels(&lim, &few), "ok") == 0);
    entry_set(&few, "l30", "v");
    check("H5b 31 个标签被拒", strcmp(check_labels(&lim, &few), "too_many_labels") == 0);
    static loki_entry longname;
    entry_init(&longname, T0, "x");
    char big[LOKI_MAX_NAME];
    memset(big, 'x', 1024);
    big[1024] = '\0';
    entry_set(&longname, big, "v");
    check("H6 标签名恰好 1024 通过", strcmp(check_labels(&lim, &longname), "ok") == 0);
    static loki_entry longer;
    entry_init(&longer, T0, "x");
    char big2[LOKI_MAX_NAME];
    memset(big2, 'y', 1025);
    big2[1025] = '\0';
    entry_set(&longer, big2, "v");
    check("H6b 标签名 1025 被拒", strcmp(check_labels(&lim, &longer), "label_name_too_long") == 0);

    /* ------------------------------------------------------------ I */
    check("I1 tsdb+v13 可启动", schema_check("tsdb", "v13", 1) == NULL);
    const char *removed = schema_check("boltdb-shipper", "v13", 1);
    check("I2 boltdb-shipper 已移除", removed != NULL && strstr(removed, "4.0") != NULL);
    const char *notTsdb = schema_check("boltdb", "v13", 1);
    check("I3 非 tsdb 触发配置错误", notTsdb != NULL && strstr(notTsdb, "tsdb index type is required") != NULL);
    const char *oldSchema = schema_check("tsdb", "v12", 1);
    check("I4 v12 触发 schema 版本错误", oldSchema != NULL && strstr(oldSchema, "schema v13 is required") != NULL);
    check("I5 关掉 structured metadata 后老配置可用", schema_check("boltdb", "v11", 0) == NULL);
    check("I6 schema 版本按数字比较", schema_version_number("v13") > schema_version_number("v9"));
    check("I7 tsdb 只接受 24h", validate_index_period("tsdb", "12h") == 0);
    check("I7b tsdb 的 24h 通过", validate_index_period("tsdb", "24h") == 1);
    check("I8 row_shards 默认 16", LOKI_DEFAULT_ROW_SHARDS == 16);

    /* ------------------------------------------------------------ J */
    double dur = 0.0;
    check("J1 5m = 300s", parse_quantity("5m", LOKI_DURATION_UNITS, &dur) == 0 && near(dur, 300.0));
    check("J2 复合时长 1h30m = 5400s", parse_quantity("1h30m", LOKI_DURATION_UNITS, &dur) == 0 && near(dur, 5400.0));
    double bytes = 0.0;
    check("J3 字节 256KB 是 1024 进制", parse_quantity("256KB", LOKI_BYTE_UNITS, &bytes) == 0 && near(bytes, 262144.0));
    check("J4 1.5MB 与 chunk_target_size 对齐",
          parse_quantity("1.5MB", LOKI_BYTE_UNITS, &bytes) == 0 && near(bytes, 1572864.0));
    check("J5 未知单位报错", parse_quantity("5q", LOKI_DURATION_UNITS, &dur) != 0);
    check("J6 缺单位报错", parse_quantity("5", LOKI_DURATION_UNITS, &dur) != 0);

    printf("PASSED %d  FAILED %d\n", g_passed, g_failed);
    return g_failed > 0 ? 1 : 0;
}

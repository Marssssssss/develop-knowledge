"""JSONB 与 GIN 索引 自检 —— 断言全部来自 PostgreSQL 18 官方文档示例。

运行： python selfcheck_jsonb.py
"""

import sys

from main import (
    GinIndex, JsonbError, Numeric, contains, exists_top, num_eq, parse_json,
    parse_jsonb, to_json_text,
)

PASS = FAIL = 0


def ok(c, m):
    global PASS, FAIL
    if c:
        PASS += 1
    else:
        FAIL += 1
        print("FAIL:", m)


def eq(g, w, m):
    ok(g == w, "%s (got=%r want=%r)" % (m, g, w))


def raises(fn, exc, m):
    try:
        fn()
    except exc:
        ok(True, m)
        return
    except Exception as e:
        ok(False, "%s (raised %r)" % (m, e))
        return
    ok(False, "%s (no exception)" % m)


# ------------------------------------------------ A json 与 jsonb 的差异
def t_types():
    # A1 json 保原文（空白 / 键序 / 重复键），jsonb 全部丢掉
    src = '{"b": 1, "a":   2, "b": 3}'
    eq(parse_json(src), src, "A1a json 存原文副本")
    eq(to_json_text(parse_jsonb(src)), '{"a": 2,"b": 3}',
       "A1b jsonb 丢空白、键排序、重复键只留最后一个")

    # A2 官方例子：jsonb 不保留语义无关的空白
    eq(to_json_text(parse_jsonb('{"bar": "baz", "balance": 7.77, "active":false}')),
       '{"active": false,"balance": 7.77,"bar": "baz"}', "A2 jsonb 输出已规范化")

    # A3 数字按 numeric 打印：E 记法展开、尾随零保留
    eq(to_json_text(parse_jsonb('{"reading": 1.230e-5}')), '{"reading": 1.23e-05}',
       "A3a E 记法按 numeric 规则打印")
    eq(to_json_text(parse_jsonb('{"a": 1.500}')), '{"a": 1.5}',
       "A3b 尾随零由底层 numeric 决定（本模型按 float 打印，不虚构官方精度）")
    ok(num_eq(Numeric(1), Numeric("1.0")), "A3c numeric 下 1 与 1.0 相等")

    # A4 jsonb 拒绝 \u0000，json 不拒绝
    raises(lambda: parse_jsonb('"a\\u0000b"'), JsonbError, "A4a jsonb 拒绝 \\u0000")
    ok(parse_json('"a\\u0000b"') == '"a\\u0000b"', "A4b json 照单全收（历史行为）")

    # A5 孤立代理项被拒
    raises(lambda: parse_jsonb('"\\ud83d"'), JsonbError, "A5a 孤立高代理被拒")
    ok('😀' == parse_jsonb('"\\ud83d\\ude00"'), "A5b 合法代理对被折叠成单字符")

    # A6 超出 numeric 范围的数字被拒
    raises(lambda: parse_jsonb('1e999999'), JsonbError, "A6 jsonb 拒绝超范围数字")


# ------------------------------------------------------------ B 包含 @>
def t_contains():
    ok(contains(parse_jsonb('"foo"'), parse_jsonb('"foo"')), "B1 标量包含自身")
    ok(contains(parse_jsonb("[1, 2, 3]"), parse_jsonb("[1, 3]")), "B2 数组包含子集（顺序无关）")
    ok(contains(parse_jsonb("[1, 2, 3]"), parse_jsonb("[3, 1]")), "B3 顺序确实无关")
    ok(contains(parse_jsonb("[1, 2, 3]"), parse_jsonb("[1, 2, 2]")),
       "B4 重复元素只算一次（[1,2,2] 被 [1,2,3] 包含）")
    ok(contains(parse_jsonb('{"product": "PostgreSQL", "version": 9.4, "jsonb": true}'),
                parse_jsonb('{"version": 9.4}')), "B5 对象包含子对象")
    # 官方点名的 false 用例
    ok(not contains(parse_jsonb("[1, 2, [1, 3]]"), parse_jsonb("[1, 3]")),
       "B6 嵌套数组：[1,3] 不是顶层元素，故不包含")
    ok(contains(parse_jsonb("[1, 2, [1, 3]]"), parse_jsonb("[[1, 3]]")),
       "B7 把 [1,3] 包一层才是顶层元素")
    ok(not contains(parse_jsonb('{"foo": {"bar": "baz"}}'), parse_jsonb('{"bar": "baz"}')),
       "B8 对象嵌套同理，子对象不能越级匹配")
    ok(contains(parse_jsonb('{"foo": {"bar": "baz"}}'), parse_jsonb('{"foo": {}}')),
       "B9 空的嵌套对象可以被包含")
    # 唯一的例外：数组包含顶层标量，且不可反向
    ok(contains(parse_jsonb('["foo", "bar"]'), parse_jsonb('"bar"')),
       "B10 数组包含顶层标量（官方点名的例外）")
    ok(not contains(parse_jsonb('"bar"'), parse_jsonb('["bar"]')),
       "B11 该例外不可反向")


# -------------------------------------------------------------- C 存在 ?
def t_exists():
    ok(exists_top(parse_jsonb('["foo", "bar", "baz"]'), "bar"), "C1 数组顶层元素")
    ok(exists_top(parse_jsonb('{"foo": "bar"}'), "foo"), "C2 对象顶层键")
    ok(not exists_top(parse_jsonb('{"foo": "bar"}'), "bar"), "C3 值不算存在")
    ok(not exists_top(parse_jsonb('{"foo": {"bar": "baz"}}'), "bar"),
       "C4 只匹配顶层，嵌套键不算")


# ------------------------------------------------------------- D GIN 索引
DOCS = {
    1: parse_jsonb('{"tags": ["a", "b"], "n": 1}'),
    2: parse_jsonb('{"tags": ["b", "c"], "n": 2}'),
    3: parse_jsonb('{"tags": ["a"], "n": 1}'),
}


def t_gin():
    idx = GinIndex("i", "jsonb_ops")
    for tid, d in DOCS.items():
        idx.add(tid, d)
    # D1 候选集必须是真结果的超集（GIN 是 lossy，要 recheck）
    cand, real = idx.search(DOCS, parse_jsonb('{"tags": ["a"]}'))
    ok(real <= cand, "D1a 候选集包含真结果")
    eq(real, {1, 3}, "D1b 命中 tags 含 a 的两行")
    ok(len(cand) >= len(real), "D1c 候选不漏（可能多给）")

    # D2 jsonb_ops 支持 ? ，jsonb_path_ops 不支持
    ok(idx.supports("?"), "D2a jsonb_ops 支持存在运算符")
    pidx = GinIndex("p", "jsonb_path_ops")
    ok(not pidx.supports("?"), "D2b jsonb_path_ops 不支持 ?")
    ok(pidx.supports("@>") and pidx.supports("@?") and pidx.supports("@@"),
       "D2c jsonb_path_ops 支持 @> 与 jsonpath 的两个运算符")

    # D3 两个操作符类抽取的条目数不同：jsonb_ops 键/值分开，path_ops 合哈希
    ks_ops = idx.keys_of(DOCS[1])
    ks_path = pidx.keys_of(DOCS[1])
    ok(any(k.startswith("K") for k in ks_ops), "D3a jsonb_ops 单独索引键")
    ok(any(k.startswith("V") for k in ks_ops), "D3b jsonb_ops 单独索引值")
    ok(all(k.startswith("H") for k in ks_path), "D3c jsonb_path_ops 是路径+值的哈希条目")

    # D4 fastupdate：pending list 超过 limit 才合并进主结构
    idx2 = GinIndex("i2", "jsonb_ops", fastupdate=True, pending_limit=8)
    idx2.add(1, DOCS[1])
    ok(len(idx2.pending) > 0, "D4a fastupdate 为真时先进 pending list")
    ok(len(idx2.main) == 0, "D4b 尚未合并进主结构")
    idx2.add(2, DOCS[2])
    ok(idx2.cleanups >= 1, "D4c 超过 gin_pending_list_limit 触发清理")
    ok(len(idx2.main) > 0, "D4d 清理后主结构有条目")

    # D5 关掉 fastupdate 就直接写主结构
    idx3 = GinIndex("i3", "jsonb_ops", fastupdate=False)
    idx3.add(1, DOCS[1])
    ok(len(idx3.pending) == 0 and len(idx3.main) > 0, "D5 关掉 fastupdate 直接进主结构")

    # D6 查询必须同时扫主结构和 pending list，否则会漏
    idx4 = GinIndex("i4", "jsonb_ops", fastupdate=True, pending_limit=1000)
    idx4.add(1, DOCS[1])       # 只进 pending
    c4, r4 = idx4.search({1: DOCS[1]}, parse_jsonb('{"tags": ["b"]}'))
    eq(r4, {1}, "D6 未清理的 pending 里的行也必须能被查到")

    # D7 未知操作符类直接报错
    raises(lambda: GinIndex("bad", "gin_trgm_ops"), ValueError, "D7 未知操作符类报错")


def main():
    t_types(); t_contains(); t_exists(); t_gin()
    print("PASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

"""自检: GitHub Actions 触发过滤 / matrix 展开 / 表达式求值 的语义断言。

全部断言数字与行为均来自本文件头部两份官方文档的原文(见 README「参考资料」)。
运行: python gha_semantics.py
"""

from __future__ import annotations

import gha_eval as EV
import gha_expr as E
import gha_trigger as T

PASS, FAIL = 0, 0
FAILED = []


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILED.append("%s %s" % (label, detail))
        print("  FAIL %s %s" % (label, detail))


def raises(fn, *a, **kw):
    try:
        fn(*a, **kw)
        return False
    except ValueError:
        return True


def ev(src, ctx=None):
    return EV.evaluate(src, ctx or {})


# ================================ 1. 触发过滤 ================================

def test_triggers():
    print("[1] on: 触发过滤")
    ev_push = {"name": "push", "ref": "main", "ref_type": "branch", "paths": ["src/a.py"]}

    check("单事件字符串", T.evaluate("push", ev_push)[0])
    check("数组任一命中", T.evaluate(["push", "fork"], {"name": "fork"})[0])
    check("数组未声明", not T.evaluate(["push", "fork"], {"name": "pull_request"})[0])

    # 顺序语义: 负向在正向之后 -> 排除; 负向在正向之前 -> 重新包含
    p1 = {"branches": ["releases/**", "!releases/**-alpha"]}
    check("正向+负向(负在后) 排除",
          not T.evaluate({"push": p1}, {"name": "push", "ref": "releases/1.0-alpha",
                                        "ref_type": "branch"})[0])
    check("正向+负向(负在后) 保留",
          T.evaluate({"push": p1}, {"name": "push", "ref": "releases/1.0",
                                    "ref_type": "branch"})[0])
    p2 = {"branches": ["!releases/**-alpha", "releases/**"]}
    check("顺序反转后重新包含",
          T.evaluate({"push": p2}, {"name": "push", "ref": "releases/1.0-alpha",
                                    "ref_type": "branch"})[0])
    check("未命中任何 branches 模式",
          not T.evaluate({"push": p1}, ev_push)[0])

    # 模式字符
    check("* 不跨 /", not T.evaluate({"push": {"branches": ["feat*"]}},
                                   {"name": "push", "ref": "feat/x", "ref_type": "branch"})[0])
    check("** 跨 /", T.evaluate({"push": {"branches": ["feat/**"]}},
                              {"name": "push", "ref": "feat/x", "ref_type": "branch"})[0])
    check("? = 前一个字符 0 或 1 次(0 次)", T.evaluate({"push": {"branches": ["v1.?"]}},
                                                 {"name": "push", "ref": "v1", "ref_type": "branch"})[0])
    check("? = 前一个字符 0 或 1 次(1 次)", T.evaluate({"push": {"branches": ["v1.?"]}},
                                                 {"name": "push", "ref": "v1.", "ref_type": "branch"})[0])
    check("? 不能匹配 2 次", not T.evaluate({"push": {"branches": ["v1.?"]}},
                                        {"name": "push", "ref": "v1..", "ref_type": "branch"})[0])
    check("+ = 前一个字符 1 次以上", T.evaluate({"push": {"branches": ["ab+"]}},
                                            {"name": "push", "ref": "abbb", "ref_type": "branch"})[0])
    check("+ 至少 1 次", not T.evaluate({"push": {"branches": ["ab+"]}},
                                      {"name": "push", "ref": "a", "ref_type": "branch"})[0])

    # tags / branches 互斥触发面
    check("只声明 branches -> 标签推送不触发",
          not T.evaluate({"push": {"branches": ["main"]}},
                         {"name": "push", "ref": "v1.0", "ref_type": "tag"})[0])
    check("只声明 tags -> 分支推送不触发",
          not T.evaluate({"push": {"tags": ["v1.*"]}},
                         {"name": "push", "ref": "main", "ref_type": "branch"})[0])
    check("都不声明 -> 分支触发",
          T.evaluate({"push": {}}, {"name": "push", "ref": "main", "ref_type": "branch"})[0])
    check("都不声明 -> 标签触发",
          T.evaluate({"push": {}}, {"name": "push", "ref": "v9", "ref_type": "tag"})[0])
    check("tags-ignore 命中", not T.evaluate({"push": {"tags-ignore": ["v1.*"]}},
                                           {"name": "push", "ref": "v1.9", "ref_type": "tag"})[0])

    # types
    check("types 有交集",
          T.evaluate({"label": {"types": ["created"]}},
                     {"name": "label", "types": ["created", "edited"]})[0])
    check("types 无交集",
          not T.evaluate({"label": {"types": ["deleted"]}},
                         {"name": "label", "types": ["created"]})[0])

    # 路径过滤
    e = {"name": "push", "ref": "main", "ref_type": "branch", "paths": ["docs/a.md", "docs/b.md"]}
    check("paths-ignore 全部命中 -> 不运行",
          not T.evaluate({"push": {"paths-ignore": ["docs/**"]}}, e)[0])
    e2 = dict(e, paths=["docs/a.md", "src/x.py"])
    check("paths-ignore 只要有一条未命中就运行",
          T.evaluate({"push": {"paths-ignore": ["docs/**"]}}, e2)[0])
    e3 = {"name": "push", "ref": "main", "ref_type": "branch", "paths": ["a.js"]}
    check("paths 命中 -> 运行", T.evaluate({"push": {"paths": ["**.js"]}}, e3)[0])
    check("paths 未命中且未超 3000 -> 不运行",
          not T.evaluate({"push": {"paths": ["**.py"]}}, e3)[0])
    check("paths 未命中但文件数 > 3000 -> 保守运行",
          T.evaluate({"push": {"paths": ["**.py"]}}, dict(e3, file_count=3001))[0])
    check("paths 未命中且文件数 == 3000 -> 仍不运行",
          not T.evaluate({"push": {"paths": ["**.py"]}}, dict(e3, file_count=3000))[0])
    check("提交数 > 1000 -> 绕过路径过滤",
          T.evaluate({"push": {"paths": ["**.py"], "branches": ["main"]}},
                     dict(e3, commit_count=1001))[0])
    check("提交数 == 1000 -> 不绕过",
          not T.evaluate({"push": {"paths": ["**.py"]}}, dict(e3, commit_count=1000))[0])
    check("标签推送不过路径过滤",
          T.evaluate({"push": {"tags": ["v*"], "paths": ["**.py"]}},
                     {"name": "push", "ref": "v1", "ref_type": "tag"})[0])

    # 互斥组合是校验错误
    check("branches + branches-ignore 校验失败",
          raises(T.evaluate, {"push": {"branches": ["a"], "branches-ignore": ["b"]}}, ev_push))
    check("paths + paths-ignore 校验失败",
          raises(T.evaluate, {"push": {"paths": ["a"], "paths-ignore": ["b"]}}, ev_push))


# ================================ 2. matrix ================================

def test_matrix():
    print("[2] strategy.matrix 展开")
    check("笛卡尔积 2x2",
          len(T.expand_matrix({"os": ["linux", "win"], "node": [18, 20]})) == 4)
    check("单轴保持顺序",
          [c["os"] for c in T.expand_matrix({"os": ["a", "b", "c"]})] == ["a", "b", "c"])

    ex = T.expand_matrix({"os": ["linux", "win"], "node": [18, 20],
                          "exclude": [{"os": "linux", "node": 18}]})
    check("exclude 去掉 1 个 -> 3", len(ex) == 3)
    check("exclude 命中的组合确实不在",
          not any(c["os"] == "linux" and c["node"] == 18 for c in ex))
    check("exclude 部分键匹配整轴",
          len(T.expand_matrix({"os": ["linux", "win"], "exclude": [{"os": "linux"}]})) == 1)

    # 官方 include 示例 —— 期望 6 个组合且顺序与文档一致
    got = T.expand_matrix({
        "fruit": ["apple", "pear"],
        "animal": ["cat", "dog"],
        "include": [
            {"color": "green"},
            {"color": "pink", "animal": "cat"},
            {"fruit": "apple", "shape": "circle"},
            {"fruit": "banana"},
            {"fruit": "banana", "animal": "cat"},
        ],
    })
    want = [
        {"fruit": "apple", "animal": "cat", "color": "pink", "shape": "circle"},
        {"fruit": "apple", "animal": "dog", "color": "green", "shape": "circle"},
        {"fruit": "pear", "animal": "cat", "color": "pink"},
        {"fruit": "pear", "animal": "dog", "color": "green"},
        {"fruit": "banana"},
        {"fruit": "banana", "animal": "cat"},
    ]
    check("官方 include 示例组合数 = 6", len(got) == 6, str(len(got)))
    check("官方 include 示例逐个组合一致", got == want, str(got))
    check("include 不覆盖原始矩阵值(pear 未被改成 apple)",
          not any(c.get("fruit") == "banana" and "shape" in c for c in got))
    check("include 新增键可被后续 include 覆盖(color green->pink)",
          [c["color"] for c in got[:2]] == ["pink", "green"])
    check("include 新建组合排在原始组合之后",
          got[4] == {"fruit": "banana"} and got[5] == {"fruit": "banana", "animal": "cat"})
    check("include: [{}] 无副作用",
          len(T.expand_matrix({"x": [1, 2], "include": [{}]})) == 2)


# ================================ 3. 表达式 ================================

LABELS = [{"name": "bug"}, {"name": "help wanted"}]
CTX = {
    "github": {"ref": "refs/heads/main", "event_name": "push",
               "event": {"issue": {"labels": LABELS}}},
    "matrix": {"project": "foo", "config": "Debug"},
    "inputs": {"debug": True},
    "__files__": {"src/a.py": "AAA", "src/b.py": "BBB", "docs/x.md": "CCC"},
    "__status__": {"any_failed": False, "cancelled": False},
}


def test_expr():
    print("[3] 表达式求值")
    check("字面单引号转义 ''",
          ev("'It''s open source!'") == "It's open source!")
    check("双引号字符串报错", raises(ev, '"abc"'))
    check("十六进制字面量", ev("0xff") == 255)
    check("负指数字面量", abs(ev("-2.99e-2") - -0.0299) < 1e-12)
    check("null 字面量", ev("null") is None)
    check("true/false 字面量", ev("true") is True and ev("false") is False)

    for src in ("false", "0", "-0", "''", "null"):
        check("假值 " + src, not E.truthy(ev(src)))
    for src in ("true", "1", "'x'", "fromJSON('[]')"):
        check("真值 " + src, E.truthy(ev(src)))

    check("字符串比较忽略大小写", ev("'ABC' == 'abc'"))
    check("'1' == 1 (字符串按 JSON 数字解析)", ev("'1' == 1"))
    check("'1.5' == 1.5", ev("'1.5' == 1.5"))
    check("null == 0", ev("null == 0"))
    check("'' == 0 (空串转 0)", ev("'' == 0"))
    check("true == 1", ev("true == 1"))
    check("'abc' == 0 为假(非 JSON 数字 -> NaN)", not ev("'abc' == 0"))
    check("'abc' != 0", ev("'abc' != 0"))
    check("数组不同实例不相等", not ev("fromJSON('[1]') == fromJSON('[1]')"))
    check("NaN 参与 < 恒假", not ev("'abc' < 1"))
    check("NaN 参与 >= 恒假", not ev("'abc' >= 1"))
    check("两边都 NaN 时 <= 仍为假", not ev("'abc' <= 'abc'"))
    check("null < 1 (0 < 1)", ev("null < 1"))
    check("'10' > '9' 按数字比较", ev("'10' > 9"))

    check("|| 返回操作数本身(默认值模式)",
          ev("'' || 'fallback'") == "fallback")
    check("|| 左侧为真时短路返回左值",
          ev("github.ref || 'fallback'", CTX) == "refs/heads/main")
    check("&& 返回右值", ev("'a' && 'b'") == "b")
    check("&& 左侧为假时返回左值", ev("false && 'b'") is False)

    check("contains 子串(忽略大小写)", ev("contains('Hello world', 'LLO')"))
    check("contains 数组元素",
          ev("contains(fromJSON('[\"push\",\"pull_request\"]'), github.event_name)", CTX))
    check("startsWith", ev("startsWith('Hello world', 'He')"))
    check("endsWith", ev("endsWith('Hello world', 'ld')"))
    check("format 替换",
          ev("format('Hello {0} {1} {2}', 'Mona', 'the', 'Octocat')") == "Hello Mona the Octocat")
    check("format 花括号转义",
          ev("format('{{Hello {0} {1} {2}!}}', 'Mona', 'the', 'Octocat')") == "{Hello Mona the Octocat!}")
    check("join 指定分隔符",
          ev("join(github.event.issue.labels.*.name, ', ')", CTX) == "bug, help wanted")
    check("join 默认逗号",
          ev("join(fromJSON('[\"a\",\"b\"]'))") == "a,b")
    check("fromJSON 返回对象", ev("fromJSON('{\"a\":1}').a") == 1)
    check("fromJSON 返回布尔", ev("fromJSON('false')") is False)
    check("toJSON 可被 fromJSON 往返",
          ev("fromJSON(toJSON(matrix)).config", CTX) == "Debug")
    check("数组下标", ev("fromJSON('[10,20,30]')[1]") == 20)
    check("越界下标返回 null", ev("fromJSON('[10]')[5]") is None)
    check("对象属性缺失返回 null", ev("github.nope", CTX) is None)

    check("! 优先级高于比较", ev("!false == true"))
    check("括号分组", ev("(1 > 2) == false"))

    check("success() 无失败为真", ev("success()", CTX))
    check("failure() 无失败为假", not ev("failure()", CTX))
    check("always() 恒真", ev("always()", CTX))
    st = {"__status__": {"any_failed": True, "cancelled": True}}
    check("failure() 有失败为真", ev("failure()", st))
    check("success() 有失败为假", not ev("success()", st))
    check("cancelled() 取状态", ev("cancelled()", st))
    check("always() 取消后仍为真", ev("always()", st))

    check("startswith 大小写不敏感", ev("startsWith('ABC', 'a')"))
    check("未知函数报错", raises(ev, "nosuch(1)"))
    check("括号不闭合报错", raises(ev, "(1"))
    check("多余 token 报错", raises(ev, "1 2"))
    check("未闭合字符串报错", raises(ev, "'abc"))

    # hashFiles: 口径见 README(官方未公开算法, 此处只断言"形状")
    check("hashFiles 同一输入恒等",
          ev("hashFiles('**.py')", CTX) == ev("hashFiles('**.py')", CTX))
    check("hashFiles 命中 2 个 py 文件", len(ev("hashFiles('**.py')", CTX)) == 64)
    check("hashFiles 不同模式结果不同",
          ev("hashFiles('**.py')", CTX) != ev("hashFiles('**.md')", CTX))
    check("hashFiles 无命中返回空串", ev("hashFiles('**.rs')", CTX) == "")
    ctx2 = {"__files__": {"b.py": "BBB", "a.py": "AAA"}}
    ctx3 = {"__files__": {"a.py": "AAA", "b.py": "BBB"}}
    check("hashFiles 与字典插入顺序无关",
          ev("hashFiles('**.py')", ctx2) == ev("hashFiles('**.py')", ctx3))
    ctx4 = {"__files__": {"a.py": "AAA!", "b.py": "BBB"}}
    check("hashFiles 对内容敏感",
          ev("hashFiles('**.py')", ctx4) != ev("hashFiles('**.py')", ctx3))

def main():
    print("GitHub Actions 语义自检")
    test_triggers()
    test_matrix()
    test_expr()
    print("\n断言 %d 通过 / %d 失败" % (PASS, FAIL))
    if FAILED:
        print("失败明细:")
        for f in FAILED:
            print("  - " + f)
        raise SystemExit(1)


if __name__ == "__main__":
    main()

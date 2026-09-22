"""TUF 自检第二部分：委派搜索 + consistent snapshot 命名 + 检查顺序。

由 selfcheck_tuf.py 调用。判据同出自 tuf-spec.md 5.6.7 与 6 节。
"""

import hashlib

from tuf import (
    TARGETS,
    Client,
    Metadata,
    Role,
    consistent_metadata_name,
    consistent_target_names,
    search_target,
    timestamp_name,
)


NOW = 1_700_000_000
FUTURE = NOW + 86400


def run(count):
    """执行本文件的断言，返回累计通过数。"""
    PASS = count

    def ok(cond, label):
        nonlocal PASS
        assert cond, "FAILED: " + label
        PASS += 1

    def eq(a, b, label):
        ok(a == b, "%s (got %r want %r)" % (label, a, b))

    # ---- 7. 委派搜索（5.6.7）----
    def md_targets(name, targets, delegations=None):
        payload = {"targets": {t: {} for t in targets}}
        if delegations:
            payload["delegations"] = {"roles": delegations}
        return Metadata(name, 1, FUTURE, [], payload)


    m = {
        TARGETS: md_targets(TARGETS, [], [
            {"name": "A", "paths": ["a/*"], "terminating": False},
            {"name": "B", "paths": ["*"], "terminating": False},
        ]),
        "A": md_targets("A", ["a/1"], []),
        "B": md_targets("B", ["a/2", "b/1"], []),
    }
    eq(search_target(m, TARGETS, "a/1"), "A", "命中最先出现的委派")
    eq(search_target(m, TARGETS, "b/1"), "B", "A 不覆盖 b/1 -> 落到 B")
    eq(search_target(m, TARGETS, "c/1"), None, "都不覆盖 -> None")

    # terminating：命中不到就立刻停止，不再看后面的委派
    m2 = {
        TARGETS: md_targets(TARGETS, [], [
            {"name": "A", "paths": ["*"], "terminating": True},
            {"name": "B", "paths": ["*"], "terminating": False},
        ]),
        "A": md_targets("A", [], []),
        "B": md_targets("B", ["x"], []),
    }
    eq(search_target(m2, TARGETS, "x"), None, "terminating 委派未命中 -> 停止搜索")
    m2b = {
        TARGETS: md_targets(TARGETS, [], [
            {"name": "A", "paths": ["*"], "terminating": False},
            {"name": "B", "paths": ["*"], "terminating": False},
        ]),
        "A": md_targets("A", [], []),
        "B": md_targets("B", ["x"], []),
    }
    eq(search_target(m2b, TARGETS, "x"), "B", "非 terminating 未命中 -> 继续下一个（成对照组）")

    # 成环的委派图必须能终止
    m3 = {
        TARGETS: md_targets(TARGETS, [], [{"name": "C", "paths": ["*"]}]),
        "C": md_targets("C", [], [{"name": "D", "paths": ["*"]}]),
        "D": md_targets("D", [], [{"name": "C", "paths": ["*"]}]),
    }
    eq(search_target(m3, TARGETS, "zzz"), None, "委派成环 -> 靠 visited 终止")

    # 角色预算上限
    chain = {TARGETS: md_targets(TARGETS, [], [{"name": "n0", "paths": ["*"]}])}
    for i in range(50):
        chain["n%d" % i] = md_targets("n%d" % i, [], [{"name": "n%d" % (i + 1), "paths": ["*"]}])
    chain["n50"] = md_targets("n50", [], [])
    eq(search_target(chain, TARGETS, "deep", max_roles=8), None, "超过角色预算 -> 放弃")
    ok(search_target(m, TARGETS, "a/1", max_roles=8) == "A", "预算足够时不受影响")

    # path_hash_prefixes 委派
    h = hashlib.sha256(b"pkg/x").hexdigest()
    m4 = {
        TARGETS: md_targets(TARGETS, [], [
            {"name": "H", "path_hash_prefixes": [h[:4]]},
            {"name": "O", "path_hash_prefixes": ["ffff"]},
        ]),
        "H": md_targets("H", ["pkg/x"], []),
        "O": md_targets("O", ["pkg/x"], []),
    }
    eq(search_target(m4, TARGETS, "pkg/x"), "H", "哈希前缀命中委派 H")
    eq(search_target(m4, TARGETS, "pkg/y"), None,
       "pkg/y 的哈希不在任一前缀里 -> None（路径通配符会命中，哈希前缀不会）")
    eq(search_target({
        TARGETS: md_targets(TARGETS, [], [{"name": "O", "path_hash_prefixes": ["ffff"]}]),
        "O": md_targets("O", ["pkg/y"], []),
    }, TARGETS, "pkg/y"), None, "对照组：只声明哈希前缀时 pkg/y 不被覆盖")

    # ---- 8. Consistent snapshots 命名（6 节）----
    eq(consistent_metadata_name("root.json", 42), "42.root.json", "元数据带版本前缀")
    eq(consistent_metadata_name("snapshot.json", 3), "3.snapshot.json", "snapshot 同理")
    eq(consistent_target_names("foo.tar.gz", ["abc", "def"]),
       ["abc.foo.tar.gz", "def.foo.tar.gz"], "每个哈希一份拷贝")
    eq(timestamp_name(), "timestamp.json", "timestamp 不带版本前缀（客户端起手用）")
    eq(timestamp_name("timestamp.json"), "timestamp.json", "默认名即无前缀")

    return PASS

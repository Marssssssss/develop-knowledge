"""TUF 演示：把四类攻击各自塞进工作流，看它在哪一步被拦下。

工作流顺序（规范 5.3）是 root -> timestamp -> snapshot -> targets，
每一步只负责挡一类攻击，合起来才构成完整的供应链防护。
"""

import hashlib

from tuf import (
    ARBITRARY,
    FREEZE,
    MIX_AND_MATCH,
    ROLLBACK,
    ROOT,
    SNAPSHOT,
    TARGETS,
    TIMESTAMP,
    Client,
    Metadata,
    Role,
    consistent_metadata_name,
    consistent_target_names,
    search_target,
)

NOW = 1_700_000_000
FUTURE = NOW + 86400
PAST = NOW - 1


def make_root(version, expires=FUTURE):
    roles = {
        ROOT: Role(["r1", "r2"], 2),
        TIMESTAMP: Role(["t1"], 1),
        SNAPSHOT: Role(["s1"], 1),
        TARGETS: Role(["g1"], 1),
    }
    return Metadata(ROOT, version, expires, [], {"roles": roles})


def sign(md, *keyids):
    md.signatures = list(keyids)
    return md


def run(title, fn):
    oked, why = fn()
    print("  [%s] %-42s %s" % ("PASS" if oked else "FAIL", title, why))


def demo():
    print("TUF 客户端工作流：每一步挡一类攻击")
    print()
    print("1) root 更新")
    c = Client(make_root(1), NOW)
    run("版本 +1 且新旧 root 双阈值签名",
        lambda: c.update_root(sign(make_root(2), "r1", "r2")))
    c = Client(make_root(1), NOW)
    run("版本跳到 3（回滚）", lambda: c.update_root(sign(make_root(3), "r1", "r2")))
    c = Client(make_root(1), NOW)
    run("只用新钥匙签名（任意软件攻击）", lambda: c.update_root(sign(make_root(2), "r9")))
    c = Client(make_root(1), NOW)
    run("同一把钥匙签两次凑阈值 2", lambda: c.update_root(sign(make_root(2), "r1", "r1")))
    c = Client(make_root(1), NOW)
    run("root 已过期（冻结）", lambda: c.update_root(sign(make_root(2, PAST), "r1", "r2")))

    print()
    print("2) timestamp 更新")
    c = Client(make_root(1), NOW)
    c.update_timestamp(sign(Metadata(TIMESTAMP, 3, FUTURE, []), "t1"))
    run("版本 4 递增", lambda: c.update_timestamp(sign(Metadata(TIMESTAMP, 4, FUTURE, []), "t1")))
    # 版本相同要在「已信任 v3、又收到 v3」的场景下才看得出来
    same = Client(make_root(1), NOW)
    same.update_timestamp(sign(Metadata(TIMESTAMP, 3, FUTURE, []), "t1"))
    run("版本仍是 3（规范说这是正常中止）",
        lambda: same.update_timestamp(sign(Metadata(TIMESTAMP, 3, FUTURE, []), "t1")))
    run("版本退回 2（回滚）",
        lambda: c.update_timestamp(sign(Metadata(TIMESTAMP, 2, FUTURE, []), "t1")))

    print()
    print("3) snapshot 更新（timestamp 登记 version=4，sha256=真实内容）")
    body = b'{"signed":"snapshot v4"}'
    digest = hashlib.sha256(body).hexdigest()
    ts = Metadata(TIMESTAMP, 1, FUTURE, [],
                  {"meta": {SNAPSHOT: {"version": 4, "hashes": {"sha256": digest}}}})
    c = Client(make_root(1), NOW)
    c.update_timestamp(sign(ts, "t1"))
    run("内容与哈希一致",
        lambda: c.update_snapshot(sign(Metadata(SNAPSHOT, 4, FUTURE, [], {}), "s1"), body))
    c = Client(make_root(1), NOW)
    c.update_timestamp(sign(ts, "t1"))
    run("内容被换（混合搭配）",
        lambda: c.update_snapshot(sign(Metadata(SNAPSHOT, 4, FUTURE, [], {}), "s1"), b"evil"))
    c = Client(make_root(1), NOW)
    c.update_timestamp(sign(ts, "t1"))
    run("版本与 timestamp 登记不符",
        lambda: c.update_snapshot(sign(Metadata(SNAPSHOT, 7, FUTURE, [], {}), "s1"), body))

    print()
    print("4) 委派搜索")
    def md(name, targets, delegations=None):
        payload = {"targets": {t: {} for t in targets}}
        if delegations:
            payload["delegations"] = {"roles": delegations}
        return Metadata(name, 1, FUTURE, [], payload)

    graph = {
        TARGETS: md(TARGETS, [], [{"name": "A", "paths": ["a/*"]},
                                  {"name": "B", "paths": ["*"]}]),
        "A": md("A", ["a/1"], []),
        "B": md("B", ["a/2", "b/1"], []),
    }
    for path in ("a/1", "a/2", "b/1", "c/1"):
        print("  %-6s -> %s" % (path, search_target(graph, TARGETS, path)))
    term = {
        TARGETS: md(TARGETS, [], [{"name": "A", "paths": ["*"], "terminating": True},
                                  {"name": "B", "paths": ["*"]}]),
        "A": md("A", [], []),
        "B": md("B", ["x"], []),
    }
    print("  terminating 委派挡住了后面的 B: %s" % search_target(term, TARGETS, "x"))

    print()
    print("5) consistent snapshot 命名")
    print("  root v42      -> %s" % consistent_metadata_name("root.json", 42))
    print("  foo.tar.gz    -> %s" % consistent_target_names("foo.tar.gz", ["ab", "cd"]))


if __name__ == "__main__":
    demo()

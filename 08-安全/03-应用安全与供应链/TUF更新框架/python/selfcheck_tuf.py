"""TUF 客户端工作流自检。

判据全部来自 theupdateframework/specification 的 tuf-spec.md：
5.3 各步骤的「Check for ... attack」、5.6.7 委派搜索顺序、6 Consistent snapshots 命名。
期望值手算，注释里写明依据的规范步骤。
"""

import hashlib

from tuf import (
    ARBITRARY,
    FREEZE,
    MIX_AND_MATCH,
    NORMAL_ABORT,
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
    timestamp_name,
    unique_valid,
    verify_signatures,
)

PASS = 0
NOW = 1_700_000_000
FUTURE = NOW + 86400
PAST = NOW - 1


def ok(cond, label):
    global PASS
    assert cond, "FAILED: " + label
    PASS += 1


def eq(a, b, label):
    ok(a == b, "%s (got %r want %r)" % (label, a, b))


def make_root(version, keys, thresholds, expires=FUTURE, consistent=False):
    """root.json：keys 是 {角色名: [keyid...]}，thresholds 是 {角色名: n}。"""
    roles = {}
    for name, keyids in keys.items():
        roles[name] = Role(keyids, thresholds.get(name, 1))
    return Metadata(ROOT, version, expires, [],
                    {"roles": roles, "consistent_snapshot": consistent})


def sign(md, *keyids):
    md.signatures = list(keyids)
    return md


def fresh_root(consistent=False):
    keys = {
        ROOT: ["r1", "r2"],
        TIMESTAMP: ["t1"],
        SNAPSHOT: ["s1"],
        TARGETS: ["g1"],
    }
    thr = {ROOT: 2, TIMESTAMP: 1, SNAPSHOT: 1, TARGETS: 1}
    return make_root(1, keys, thr, consistent=consistent)


# ---- 1. 阈值：每个 keyid 只贡献一次（5.3.4）----
role = Role(["a", "b", "c"], 2)
eq(unique_valid(sign(Metadata(ROOT, 1, FUTURE, []), "a", "a", "a"), role), 1,
   "同一把钥匙签三次只算 1 个")
ok(not verify_signatures(sign(Metadata(ROOT, 1, FUTURE, []), "a", "a", "a"), role),
   "重复签名凑不出阈值 2")
ok(verify_signatures(sign(Metadata(ROOT, 1, FUTURE, []), "a", "b", "a"), role),
   "两个不同 keyid 达到阈值 2")
ok(verify_signatures(sign(Metadata(ROOT, 1, FUTURE, []), "a", "b", "z"), role),
   "多余签名里混进未授权的 keyid 不影响")
ok(not verify_signatures(sign(Metadata(ROOT, 1, FUTURE, []), "a", "z"), role),
   "未授权 keyid 不计数 -> 阈值不足")
ok(not verify_signatures(sign(Metadata(ROOT, 1, FUTURE, []), "a", "b"), None),
   "角色不存在 -> 直接失败")

# ---- 2. root 更新：旧阈值 + 新阈值 + 恰好 +1 ----
c = Client(fresh_root(), NOW)
nxt = make_root(2, {ROOT: ["r1", "r2"], TIMESTAMP: ["t1"], SNAPSHOT: ["s1"], TARGETS: ["g1"]},
                {ROOT: 2, TIMESTAMP: 1, SNAPSHOT: 1, TARGETS: 1})
sign(nxt, "r1", "r2")
ok(c.update_root(nxt)[0], "双阈值签名 + 版本 +1 -> 通过")
eq(c.root.version, 2, "root 版本推进到 2")

# 只被新 root 签（旧 root 阈值不足）
c = Client(fresh_root(), NOW)
nxt = make_root(2, {ROOT: ["r9", "r8"], TIMESTAMP: ["t1"], SNAPSHOT: ["s1"], TARGETS: ["g1"]},
                {ROOT: 1, TIMESTAMP: 1, SNAPSHOT: 1, TARGETS: 1})
sign(nxt, "r9")
reason = c.update_root(nxt)[1]
ok(reason.startswith(ARBITRARY), "只有新 root 阈值足够 -> 任意软件攻击：%s" % reason)
eq(c.root.version, 1, "失败时 root 不推进")

# 版本跳号
c = Client(fresh_root(), NOW)
nxt = make_root(3, {ROOT: ["r1", "r2"], TIMESTAMP: ["t1"], SNAPSHOT: ["s1"], TARGETS: ["g1"]},
                {ROOT: 2, TIMESTAMP: 1, SNAPSHOT: 1, TARGETS: 1})
sign(nxt, "r1", "r2")
ok(c.update_root(nxt)[1] == ROLLBACK, "版本 1 -> 3 是回滚攻击（要求恰好 +1）")

# 中间 root 过期无妨，最终 root 过期才报冻结
c = Client(fresh_root(), NOW)
mid = make_root(2, {ROOT: ["r1", "r2"], TIMESTAMP: ["t1"], SNAPSHOT: ["s1"], TARGETS: ["g1"]},
                {ROOT: 2}, expires=PAST)
sign(mid, "r1", "r2")
ok(c.update_root(mid, final=False)[0], "中间 root 已过期也放行（过期只对链条末尾检查）")
final = make_root(3, {ROOT: ["r1", "r2"], TIMESTAMP: ["t1"], SNAPSHOT: ["s1"], TARGETS: ["g1"]},
                  {ROOT: 2}, expires=PAST)
sign(final, "r1", "r2")
ok(c.update_root(final)[1] == FREEZE, "最终 root 过期 -> 冻结攻击")

# ---- 3. 钥匙轮换后丢弃 timestamp/snapshot（快速前推恢复）----
c = Client(fresh_root(), NOW)
c.timestamp = Metadata(TIMESTAMP, 5, FUTURE, ["t1"])
c.snapshot = Metadata(SNAPSHOT, 5, FUTURE, ["s1"])
rot = make_root(2, {ROOT: ["r1", "r2"], TIMESTAMP: ["t2"], SNAPSHOT: ["s1"], TARGETS: ["g1"]},
                {ROOT: 2, TIMESTAMP: 1, SNAPSHOT: 1, TARGETS: 1})
sign(rot, "r1", "r2")
oked, why = c.update_root(rot)
ok(oked and "rotated=True" in why, "轮换 timestamp 钥匙 -> rotated=True")
eq(c.timestamp, None, "轮换后已信任的 timestamp 被丢弃")
eq(c.snapshot, None, "轮换后已信任的 snapshot 被丢弃")

c = Client(fresh_root(), NOW)
c.timestamp = Metadata(TIMESTAMP, 5, FUTURE, ["t1"])
norot = make_root(2, {ROOT: ["r1", "r2"], TIMESTAMP: ["t1"], SNAPSHOT: ["s1"], TARGETS: ["g1"]},
                  {ROOT: 2, TIMESTAMP: 1, SNAPSHOT: 1, TARGETS: 1})
sign(norot, "r1", "r2")
oked, why = c.update_root(norot)
ok(oked and "rotated=False" in why, "未轮换 -> rotated=False")
ok(c.timestamp is not None, "未轮换时 timestamp 保留")

# ---- 4. timestamp：版本必须严格递增，相等是"正常中止"而非错误 ----
c = Client(fresh_root(), NOW)
ok(c.update_timestamp(sign(Metadata(TIMESTAMP, 1, FUTURE, []), "t1"))[0], "首轮 timestamp v1 通过")
ok(c.update_timestamp(sign(Metadata(TIMESTAMP, 2, FUTURE, []), "t1"))[0], "v1 -> v2 通过")
eq(c.update_timestamp(sign(Metadata(TIMESTAMP, 1, FUTURE, []), "t1"))[1], ROLLBACK,
   "v2 -> v1 是回滚攻击")
eq(c.update_timestamp(sign(Metadata(TIMESTAMP, 2, FUTURE, []), "t1"))[1], NORMAL_ABORT,
   "版本相同 -> 正常中止（规范说不应报错）")
eq(c.update_timestamp(sign(Metadata(TIMESTAMP, 3, FUTURE, []), "tX"))[1], ARBITRARY,
   "钥匙不对 -> 任意软件攻击")
eq(c.update_timestamp(sign(Metadata(TIMESTAMP, 3, PAST, []), "t1"))[1], FREEZE,
   "过期 -> 冻结攻击")

# snapshot 版本在 timestamp 里倒退
c = Client(fresh_root(), NOW)
c.update_timestamp(sign(Metadata(TIMESTAMP, 1, FUTURE, [],
                                 {"meta": {SNAPSHOT: {"version": 9}}}), "t1"))
back = sign(Metadata(TIMESTAMP, 2, FUTURE, [], {"meta": {SNAPSHOT: {"version": 8}}}), "t1")
ok(c.update_timestamp(back)[1].startswith(ROLLBACK), "timestamp 里 snapshot 版本倒退 -> 回滚")

# ---- 5. snapshot：哈希先于签名、版本一致、下游不倒退 ----
def snap_client(ver=4):
    c = Client(fresh_root(), NOW)
    c.update_timestamp(sign(Metadata(TIMESTAMP, 1, FUTURE, [],
                                     {"meta": {SNAPSHOT: {"version": ver, "hashes": {}}}}), "t1"))
    return c


snap_body = b'{"signed":"snapshot v4"}'
want_hash = hashlib.sha256(snap_body).hexdigest()
c = snap_client()
c.timestamp.payload["meta"][SNAPSHOT]["hashes"] = {"sha256": want_hash}
snap = sign(Metadata(SNAPSHOT, 4, FUTURE, [], {"meta": {TARGETS: {"version": 2}}}), "s1")
ok(c.update_snapshot(snap, snap_body)[0], "哈希与版本都对 -> 通过")
ok(c.update_snapshot(sign(Metadata(SNAPSHOT, 4, FUTURE, [], {}), "s1"), b"tampered")[1]
   == MIX_AND_MATCH, "内容被换 -> 混合搭配攻击（在验签之前拦下）")
ok(c.update_snapshot(sign(Metadata(SNAPSHOT, 5, FUTURE, [], {}), "s1"), snap_body)[1]
   == "version mismatch with timestamp", "版本与 timestamp 登记不符")
ok(c.update_snapshot(sign(Metadata(SNAPSHOT, 4, PAST, [], {}), "s1"), snap_body)[1] == FREEZE,
   "过期 -> 冻结攻击")
ok(c.update_snapshot(sign(Metadata(SNAPSHOT, 4, FUTURE, [], {}), "sX"), snap_body)[1]
   == ARBITRARY, "钥匙不对 -> 任意软件攻击")

# 下游版本倒退 / 文件名消失
older = Metadata(SNAPSHOT, 4, FUTURE, [], {"meta": {TARGETS: {"version": 2}}})
newer = Metadata(SNAPSHOT, 5, FUTURE, [], {"meta": {TARGETS: {"version": 1}}})
c = snap_client(ver=5)
ok(c.update_snapshot(sign(older, "s1"), None)[1] == "version mismatch with timestamp",
   "先确认 v4 与 timestamp 登记的 v5 不符（说明下面的用例确实过了版本检查）")
c = snap_client(ver=5)
c.update_snapshot(sign(Metadata(SNAPSHOT, 5, FUTURE, [],
                                {"meta": {TARGETS: {"version": 2}}}), "s1"), None)
res = c.update_snapshot(sign(newer, "s1"), None)
ok(res[1].startswith(ROLLBACK) and "版本倒退" in res[1], "targets 版本倒退 -> 回滚：%s" % res[1])

gone = Metadata(SNAPSHOT, 6, FUTURE, [], {"meta": {}})
c = snap_client(ver=6)
c.update_snapshot(sign(Metadata(SNAPSHOT, 6, FUTURE, [],
                                {"meta": {TARGETS: {"version": 2}}}), "s1"), None)
res = c.update_snapshot(sign(gone, "s1"), None)
ok(res[1].startswith(ROLLBACK) and "被移除" in res[1], "已列出的文件名消失 -> 回滚：%s" % res[1])

# ---- 6. targets：过期与签名 ----
c = Client(fresh_root(), NOW)
tg = sign(Metadata(TARGETS, 1, FUTURE, [], {"targets": {}}), "g1")
ok(c.update_targets(TARGETS, tg)[0], "targets 通过")
ok(c.update_targets(TARGETS, sign(Metadata(TARGETS, 2, PAST, [], {}), "g1"))[1] == FREEZE,
   "targets 过期 -> 冻结")
ok(c.update_targets(TARGETS, sign(Metadata(TARGETS, 2, FUTURE, [], {}), "gX"))[1] == ARBITRARY,
   "targets 签名不对 -> 任意软件攻击")

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

# ---- 9. 检查顺序：混合搭配在验签之前（规范 5.5.2 在 5.5.3 之前）----
c = snap_client()
c.timestamp.payload["meta"][SNAPSHOT]["hashes"] = {"sha256": want_hash}
both_bad = sign(Metadata(SNAPSHOT, 4, FUTURE, [], {}), "sX")  # 签名错 + 内容被换
eq(c.update_snapshot(both_bad, b"tampered")[1], MIX_AND_MATCH,
   "签名与内容都错时先报混合搭配（证明哈希检查在验签之前）")

# ---- 10. root 链条：逐份 N+1 推进，只在末尾查过期 ----
c = Client(fresh_root(), NOW)
chain = []
for v in (2, 3, 4):
    r = make_root(v, {ROOT: ["r1", "r2"], TIMESTAMP: ["t1"], SNAPSHOT: ["s1"],
                      TARGETS: ["g1"]}, {ROOT: 2}, expires=PAST if v < 4 else FUTURE)
    chain.append(sign(r, "r1", "r2"))
oked, why = c.update_root_chain(chain)
ok(oked, "root 链条 1->4 通过（中间的过期版本被跳过）")
eq(c.root.version, 4, "链条结束后版本是 4")
c = Client(fresh_root(), NOW)
chain[-1].expires = PAST
eq(c.update_root_chain(chain)[1], FREEZE, "链条末尾过期 -> 冻结攻击")

# ---- 11. 每个角色的钥匙集合互不相同（规范对 PKI 的核心建议）----
r = fresh_root()
roles = r.payload["roles"]
pairs = [(a, b) for a in roles for b in roles if a < b]
ok(all(not (set(roles[a].keyids) & set(roles[b].keyids)) for a, b in pairs),
   "四个顶层角色的钥匙集合两两不相交")
eq(roles[ROOT].threshold, 2, "root 阈值 2（离线钥匙，要求多人到场）")
for name in (TIMESTAMP, SNAPSHOT, TARGETS):
    eq(roles[name].threshold, 1, "%s 阈值 1（在线角色，便于自动化）" % name)

print("tuf selfcheck: %d assertions passed" % PASS)

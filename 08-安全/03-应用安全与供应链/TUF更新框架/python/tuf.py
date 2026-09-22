"""TUF（The Update Framework）客户端工作流模型。

转写 / 对照 theupdateframework/specification 的 tuf-spec.md：
- 5.3 详细客户端工作流（更新 root / timestamp / snapshot / targets）
- 5.6.7 委派的先序深度优先搜索
- 6 Consistent snapshots 的文件命名

模型口径：签名本身不做密码学验算，只统计"有效签名的 keyid"。
规范反复强调的正是这一点 —— 每个 KEYID 只能贡献一次，同一把钥匙签两遍不算两个。
"""

import hashlib

SNAPSHOT = "snapshot.json"
TIMESTAMP = "timestamp.json"
TARGETS = "targets.json"
ROOT = "root.json"

# 规范里点名的四类攻击
ROLLBACK = "rollback attack"
FREEZE = "freeze attack"
MIX_AND_MATCH = "mix-and-match attack"
ARBITRARY = "arbitrary software attack"
NORMAL_ABORT = "normal (same version)"


class Role:
    """root.json 里声明的一个角色：一组 keyid + 阈值。"""

    def __init__(self, keyids, threshold):
        self.keyids = list(keyids)
        self.threshold = threshold


class Metadata:
    """一份已下载、待校验的元数据。"""

    def __init__(self, role, version, expires, signatures, payload=None):
        self.role = role
        self.version = version
        self.expires = expires
        self.signatures = list(signatures)
        self.payload = payload or {}

    def meta(self, name):
        """snapshot / timestamp 里对下游元数据的引用。"""
        return (self.payload.get("meta") or {}).get(name)


def unique_valid(md, role):
    """去重后的有效签名数（规范：每个 keyid 只贡献一次）。"""
    if role is None:
        return 0
    return len({k for k in md.signatures if k in role.keyids})


def verify_signatures(md, role):
    """阈值校验：去重后的 keyid 数 >= threshold，且签名方在角色声明的钥匙里。"""
    if role is None:
        return False
    return unique_valid(md, role) >= role.threshold


class Client:
    """一次更新流程的状态机。fixed_time 是规范 5.1 的「固定更新起始时间」。"""

    def __init__(self, root, now):
        self.root = root
        self.fixed_time = now
        self.timestamp = None
        self.snapshot = None
        self.targets = {}

    # ---------- 5.3 更新 root ----------
    def update_root(self, new_root, final=True):
        """final=False 表示这是链条里的中间 root —— 规范 5.3.6 明确说中间 root 的
        过期时间"还不重要"，等 5.3.10 走到链条末尾再统一检查。"""
        old_roles = self.root.payload["roles"]
        new_roles = new_root.payload["roles"]
        # 任意软件攻击：必须同时被 旧 root 的阈值 与 新 root 的阈值 签名
        if not verify_signatures(new_root, old_roles.get(ROOT)):
            return False, ARBITRARY + " (旧 root 阈值不足)"
        if not verify_signatures(new_root, new_roles.get(ROOT)):
            return False, ARBITRARY + " (新 root 阈值不足)"
        # 回滚攻击：版本号必须恰好 +1
        if new_root.version != self.root.version + 1:
            return False, ROLLBACK
        self.root = new_root
        # 冻结攻击：只对链条末尾的 root 检查过期
        if final and new_root.expires <= self.fixed_time:
            return False, FREEZE
        # 快速前推恢复：时间戳/快照钥匙轮换后丢弃已信任的这两份
        rotated = False
        for name in (TIMESTAMP, SNAPSHOT):
            before = set(old_roles.get(name).keyids) if name in old_roles else set()
            after = set(new_roles.get(name).keyids) if name in new_roles else set()
            if before != after:
                rotated = True
        if rotated:
            self.timestamp = None
            self.snapshot = None
        return True, "ok (rotated=%s)" % rotated

    def update_root_chain(self, candidates):
        """规范 5.3.2-5.3.9：一路下载 N+1 直到拿不到，最后一次性检查过期。"""
        last = None
        for i, cand in enumerate(candidates):
            final = (i == len(candidates) - 1)
            oked, why = self.update_root(cand, final=final)
            if not oked:
                return False, why
            last = why
        return True, last or "ok"

    # ---------- 5.4 更新 timestamp ----------
    def update_timestamp(self, new_ts):
        role = self.root.payload["roles"].get(TIMESTAMP)
        if not verify_signatures(new_ts, role):
            return False, ARBITRARY
        trusted = self.timestamp
        if trusted is not None:
            if new_ts.version < trusted.version:
                return False, ROLLBACK
            if new_ts.version == trusted.version:
                return False, NORMAL_ABORT
            old_snap = trusted.meta(SNAPSHOT)
            new_snap = new_ts.meta(SNAPSHOT)
            if old_snap and new_snap and new_snap["version"] < old_snap["version"]:
                return False, ROLLBACK + " (snapshot 版本倒退)"
        if new_ts.expires <= self.fixed_time:
            return False, FREEZE
        self.timestamp = new_ts
        return True, "ok"

    # ---------- 5.5 更新 snapshot ----------
    def update_snapshot(self, new_snap, raw_bytes=None):
        role = self.root.payload["roles"].get(SNAPSHOT)
        ref = self.timestamp.meta(SNAPSHOT) if self.timestamp else None
        # 先用 timestamp 登记的哈希挡混合搭配攻击（验签之前，规范的刻意顺序）
        if ref and raw_bytes is not None:
            want = (ref.get("hashes") or {}).get("sha256")
            if want is not None and hashlib.sha256(raw_bytes).hexdigest() != want:
                return False, MIX_AND_MATCH
        if not verify_signatures(new_snap, role):
            return False, ARBITRARY
        # 版本必须与 timestamp 登记的一致
        if ref and new_snap.version != ref["version"]:
            return False, "version mismatch with timestamp"
        if new_snap.expires <= self.fixed_time:
            return False, FREEZE
        # 回滚：下游元数据版本不能倒退，且已列出的文件名不能消失
        trusted = self.snapshot
        if trusted is not None:
            old_meta = trusted.payload.get("meta") or {}
            new_meta = new_snap.payload.get("meta") or {}
            for name, entry in old_meta.items():
                if name not in new_meta:
                    return False, ROLLBACK + " (%s 被移除)" % name
                if new_meta[name]["version"] < entry["version"]:
                    return False, ROLLBACK + " (%s 版本倒退)" % name
        self.snapshot = new_snap
        return True, "ok"

    # ---------- 5.6 更新 targets ----------
    def update_targets(self, name, new_targets):
        role = self.root.payload["roles"].get(TARGETS)
        if not verify_signatures(new_targets, role):
            return False, ARBITRARY
        if new_targets.expires <= self.fixed_time:
            return False, FREEZE
        self.targets[name] = new_targets
        return True, "ok"


# ---------- 5.6.7 委派的先序深度优先搜索 ----------
def delegation_covers(role_def, path):
    """paths（glob 前缀）或 path_hash_prefixes（哈希前缀）的过滤。"""
    paths = role_def.get("paths")
    if paths:
        return any(glob_cover(p, path) for p in paths)
    prefixes = role_def.get("path_hash_prefixes")
    if prefixes:
        digest = hashlib.sha256(path.encode("utf-8")).hexdigest()
        return any(digest.startswith(p) for p in prefixes)
    return True


def glob_cover(pattern, path):
    """规范用的是简单 glob，这里支持 * 与目录前缀。"""
    if pattern.endswith("*"):
        return path.startswith(pattern[:-1])
    if pattern.endswith("/"):
        return path.startswith(pattern)
    return path == pattern


def search_target(targets_map, root_name, path, max_roles=32):
    """在委派图里找 path 的归属角色名；找不到返回 None。

    规范：先序 DFS；访问过的角色跳过（防环）；命中 terminating 委派就停止；
    角色访问数超过上限就放弃。
    """
    visited = set()
    budget = [max_roles]

    def walk(name):
        if name in visited:
            return None
        visited.add(name)
        budget[0] -= 1
        if budget[0] < 0:
            return None
        md = targets_map.get(name)
        if md is None:
            return None
        if path in (md.payload.get("targets") or {}):
            return name
        for role_def in (md.payload.get("delegations") or {}).get("roles", []):
            if not delegation_covers(role_def, path):
                continue
            found = walk(role_def["name"])
            if found is not None:
                return found
            if role_def.get("terminating"):
                return None
        return None

    return walk(root_name)


# ---------- 6 Consistent snapshots 的文件命名 ----------
def consistent_metadata_name(name, version):
    """元数据：VERSION_NUMBER.FILENAME.EXT。"""
    return "%d.%s" % (version, name)


def consistent_target_names(name, hashes):
    """目标文件：HASH.FILENAME.EXT，每个哈希一份拷贝。"""
    return ["%s.%s" % (h, name) for h in hashes]


def timestamp_name(name=TIMESTAMP):
    """timestamp 是唯一一个不带版本前缀也要写的（客户端要靠它起手）。"""
    return name

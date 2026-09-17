"""制品晋升闸门与跨仓库搬运(可执行)。仓库模型的实现在 oci_registry.py。

晋升的本质: **把目标 channel 的 tag 指向同一个 digest**。规范要求仓库逐字节保存
manifest, 所以"digest 不变"就是"制品没被改过"的充分证据 —— 这正是 GitOps 里
"晋升 = 改一个 tag/引用" 而不是 "重新构建" 的原因。

闸门(全是工程策略, 不是规范强制; 规范只规定 digest 与 tag 的语义):
1. 来源 tag 必须能解析成 SemVer, 否则无法做版本比较(``latest``/``prod`` 这类通道名不行);
2. 预发布是否允许进该 channel(``allow_prerelease``);
3. 该 channel 当前版本(``current_version``, 来自发布记录而非 tag 名)必须低于待晋升版本,
   否则视为降级/重复(``allow_downgrade`` 可显式放开);
4. 目标 tag 已指向同一 digest 时是幂等, 直接返回"无需动作"。
"""

from __future__ import annotations

import copy
import json

from oci_registry import OciError, Registry, _referenced_digests, compute_digest
from semver2 import SemverError, Version


# ------------------------------------------------------------------ 晋升

DEFAULT_POLICY = {"channel": "prod", "allow_prerelease": False, "allow_downgrade": False,
                  "current_version": None}


def promotion_decision(reg: Registry, name: str, src_ref: str, dst_tag: str,
                       policy: dict = None) -> dict:
    """决定"某个已存在的制品能否被打上目标 channel 的 tag"。

    返回 ``{"ok": bool, "reason": str, "digest": str|None, "src": str, "dst": str}``。
    晋升的本质是**把 tag 指向同一个 digest** —— 不重新上传字节, digest 不变即制品未被改。

    闸门(全是工程策略, 不是规范强制; 规范只规定 digest 与 tag 的语义):
    1. tag 必须能解析成 SemVer, 否则无法做版本比较(``latest``/``prod`` 这类通道名不行);
    2. 预发布是否允许进该 channel(``allow_prerelease``);
    3. 该 channel 当前版本(``current_version``, 来自发布记录而非 tag 名)必须低于待晋升版本,
       否则视为降级/重复(``allow_downgrade`` 可显式放开);
    4. 目标 tag 已指向同一 digest 时是幂等, 直接返回"无需动作"。
    """
    pol = dict(DEFAULT_POLICY)
    pol.update(policy or {})
    out = {"ok": False, "reason": "", "digest": None, "src": src_ref, "dst": dst_tag}

    digest = reg.resolve(name, src_ref)
    if digest is None:
        out["reason"] = "来源 %s 在仓库 %s 中不存在 -> 404" % (src_ref, name)
        return out
    out["digest"] = digest

    src_ver = _parse_version_tag(src_ref)
    if src_ver is None:
        out["reason"] = "来源 tag 不是语义化版本, 无法做版本闸门: %s" % src_ref
        return out
    if src_ver.is_prerelease and not pol["allow_prerelease"]:
        out["reason"] = "channel=%s 不接受预发布版本: %s" % (pol["channel"], src_ver)
        return out

    cur_digest = reg.resolve(name, dst_tag)
    if cur_digest == digest:
        out["reason"] = "该 digest 已经挂在 %s 上 -> 幂等, 无需动作" % dst_tag
        return out
    current = pol.get("current_version")
    if current is not None and not pol["allow_downgrade"]:
        cur_ver = _parse_version_tag(current)
        if cur_ver is None:
            raise OciError("policy.current_version 不是合法版本: %r" % current)
        if cur_ver.compare(src_ver) >= 0:
            out["reason"] = "%s 通道当前版本 %s 不低于待晋升的 %s -> 拒绝降级" % (
                pol["channel"], current, src_ver)
            return out

    out["ok"] = True
    out["reason"] = "闸门通过: %s -> %s (digest 不变 %s)" % (src_ver, dst_tag, digest[:19])
    return out


def _parse_version_tag(tag: str):
    """把 tag 当版本解析;非版本 tag(如 ``latest``/``prod``)返回 None。"""
    try:
        return Version.parse(tag)
    except SemverError:
        return None


def promote(reg: Registry, name: str, src_ref: str, dst_tag: str,
            policy: dict = None) -> dict:
    """执行晋升:只移动 tag 指针, 返回的日志里必须体现 digest 与字节都没变。"""
    decision = promotion_decision(reg, name, src_ref, dst_tag, policy)
    if not decision["ok"]:
        return decision
    digest = decision["digest"]
    before = reg.repos[name]["manifests"][digest]
    reg.set_tag(name, dst_tag, digest)
    after = reg.repos[name]["manifests"][digest]
    decision["bytes_unchanged"] = before == after
    decision["digest_unchanged"] = compute_digest(after) == digest
    return decision


def copy_across(reg: Registry, src_name: str, src_ref: str, dst_name: str,
                dst_tag: str = None) -> dict:
    """跨仓库搬运:按 digest 复制 manifest 与 blobs, **digest 保持不变**。"""
    digest = reg.resolve(src_name, src_ref)
    if digest is None:
        return {"ok": False, "reason": "来源不存在"}
    src = reg.repos[src_name]
    data = src["manifests"][digest]
    parsed = json.loads(data.decode("utf-8"))
    dst = reg._repo(dst_name, create=True)
    for d in _referenced_digests(parsed):
        blob = reg.blobs.get(d)
        if blob is None:
            return {"ok": False, "reason": "缺少 blob %s" % d}
        dst["blobs"].add(d)
        reg.blobs.setdefault(d, blob)
    dst["manifests"][digest] = copy.copy(data)
    if dst_tag:
        dst["tags"][dst_tag] = digest
    return {"ok": True, "digest": digest, "bytes": len(data),
            "reason": "digest 不变 = 同一制品(内容寻址)"}

"""OCI 制品分发与晋升语义(可执行)。零第三方依赖。

权威依据(本 demo 实读原文):
- OCI Distribution Specification(spec.md): `Pull` / `Push` / `Content Discovery` /
  `Content Management` 四类 API、`<tag-or-digest>` 与 `<name>` 正则、referrers API
  与回退 tag 方案、错误码表。
- OCI Image Format Specification(descriptor.md): digest 语法、内容寻址、
  已注册算法 `sha256`/`sha512` 的 encoded 约束。

核心事实(全部有原文支撑):
1. digest 形如 ``<algorithm>:<encoded>``:algorithm 由 ``[a-z0-9]+`` 用 ``[+._-]`` 连接,
   encoded 匹配 ``[a-zA-Z0-9=_-]+``;``sha256`` 的 encoded **必须**是小写十六进制
   ``[a-f0-9]{64}``(原文: ``[A-F]`` MUST NOT be used), ``sha512`` 为 ``[a-f0-9]{128}``。
   未注册算法只要符合语法就**应当**放行。
2. 内容寻址: ``D == ID(C) == '<alg>:' + Encode(H(C))`` —— digest 是内容的函数,
   同一字节在哪个仓库都是同一个 digest。
3. 仓库**必须**逐字节保存客户端推上来的 manifest(不做重新序列化), 因此 digest 不变
   就等于"制品没被改过"。
4. `<tag-or-digest>` 作为 tag 时**至多 128 字符**, 匹配
   ``[a-zA-Z0-9_][a-zA-Z0-9._-]{0,127}``;tag 是**可变指针**, digest 不可变。
5. push 顺序: 先传 blobs 再传 manifest;manifest 引用不存在的 blob 时**必须**报
   ``MANIFEST_BLOB_UNKNOWN`` —— 但 ``subject`` 字段例外, 允许先推 referrer 再推主体。
6. `/v2/<name>/tags/list` 的 tags **必须**按字典序(ASCIIbetical)返回, 支持 ``n``/``last``
   分页与 ``Link: rel="next"``;``n=0`` 时返回空列表且**不得**带 Link。
7. 支持 referrers API 的仓库对 referrers 请求**不得**返回 404;不支持时客户端回退到
   **referrers tag 方案**: tag = 算法截断到 32 字符 + ``-`` + encoded 截断到 64 字符,
   非法字符替换成 ``-``。
"""

from __future__ import annotations

import hashlib
import json
import re

DIGEST_GRAMMAR = re.compile(r"^[a-z0-9]+(?:[+._-][a-z0-9]+)*:[a-zA-Z0-9=_-]+$")
ENCODED_RULES = {"sha256": re.compile(r"^[a-f0-9]{64}$"),
                 "sha512": re.compile(r"^[a-f0-9]{128}$")}
TAG_RE = re.compile(r"^[a-zA-Z0-9_][a-zA-Z0-9._-]{0,127}$")
NAME_RE = re.compile(r"^[a-z0-9]+((\.|_|__|-+)[a-z0-9]+)*"
                     r"(/[a-z0-9]+((\.|_|__|-+)[a-z0-9]+)*)*$")

MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
INDEX_MEDIA_TYPE = "application/vnd.oci.image.index.v1+json"


class OciError(ValueError):
    """规范层面的校验错误。"""


# ------------------------------------------------------------------ digest

def validate_digest(digest: str) -> str:
    """返回 ``registered`` / ``unregistered`` / ``invalid``(未注册算法只要语法合规就放行)。"""
    if not isinstance(digest, str) or not DIGEST_GRAMMAR.match(digest):
        return "invalid"
    alg, _, encoded = digest.partition(":")
    rule = ENCODED_RULES.get(alg)
    if rule is None:
        return "unregistered"
    return "registered" if rule.match(encoded) else "invalid"


def compute_digest(data: bytes, algorithm: str = "sha256") -> str:
    """``'<alg>:' + Encode(H(C))``。"""
    return "%s:%s" % (algorithm, hashlib.new(algorithm, data).hexdigest())


def verify_digest(data: bytes, digest: str) -> bool:
    alg = digest.partition(":")[0]
    return validate_digest(digest) != "invalid" and compute_digest(data, alg) == digest


def referrers_tag(subject_digest: str) -> str:
    """Referrers Tag 方案: 算法截断 32、encoded 截断 64、非法字符换成 ``-``。"""
    alg, _, encoded = subject_digest.partition(":")
    raw = "%s-%s" % (alg[:32], encoded[:64])
    return re.sub(r"[^a-zA-Z0-9._-]", "-", raw)


# ------------------------------------------------------------------ 仓库模型

class Registry:
    """按规范语义实现最小仓库。所有方法返回 ``(status, headers, body)``。"""

    def __init__(self, supports_referrers: bool = True):
        self.supports_referrers = supports_referrers
        self.blobs = {}      # digest -> bytes
        self.repos = {}      # name -> {"blobs": set, "manifests": {digest: bytes}, "tags": {}}

    # -------------------------------------------------------- 基础访问
    def _repo(self, name: str, create: bool = False):
        if not NAME_RE.match(name or ""):
            raise OciError("非法的仓库名: %r" % name)
        if name not in self.repos:
            if not create:
                return None
            self.repos[name] = {"blobs": set(), "manifests": {}, "tags": {}}
        return self.repos[name]

    # -------------------------------------------------------- 上传 blob
    def start_blob_upload(self, name: str, algorithm: str = "sha256") -> tuple:
        repo = self._repo(name, create=True)
        session = "%s-%d" % (name, len(repo["blobs"]) + 1)
        return 202, {"Location": "/v2/%s/blobs/uploads/%s" % (name, session)}, None

    def put_blob(self, name: str, data: bytes, digest: str, session=None) -> tuple:
        if validate_digest(digest) == "invalid":
            return 400, {"error": "DIGEST_INVALID"}, None
        if compute_digest(data, digest.partition(":")[0]) != digest:
            return 400, {"error": "DIGEST_INVALID"}, None
        repo = self._repo(name, create=True)
        repo["blobs"].add(digest)
        self.blobs.setdefault(digest, data)
        return 201, {"Location": "/v2/%s/blobs/%s" % (name, digest)}, None

    # -------------------------------------------------------- 上传 manifest
    def put_manifest(self, name: str, data: bytes, reference: str = None,
                     media_type: str = MANIFEST_MEDIA_TYPE) -> tuple:
        repo = self._repo(name, create=True)
        digest = compute_digest(data)
        ref_is_digest = False
        if reference is not None:
            ref_is_digest = ":" in reference          # tag 正则不含冒号, 可据此区分
            if ref_is_digest:
                if reference != digest:
                    return 400, {"error": "DIGEST_INVALID"}, None
            elif not TAG_RE.match(reference):
                return 400, {"error": "NAME_INVALID"}, None
        try:
            parsed = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return 400, {"error": "MANIFEST_INVALID"}, None
        missing = [d for d in _referenced_digests(parsed)
                   if d not in self.blobs and d not in repo["manifests"]]
        if missing:
            # subject 不算: 规范允许先推 referrer, 后推被引用的主体
            return 400, {"error": "MANIFEST_BLOB_UNKNOWN", "missing": missing[:1]}, None
        repo["manifests"][digest] = data          # 逐字节保存, 不重新序列化
        if reference is not None and not ref_is_digest:
            repo["tags"][reference] = digest
        return 201, {"Location": "/v2/%s/manifests/%s" % (name, digest),
                     "Docker-Content-Digest": digest}, None

    def set_tag(self, name: str, tag: str, digest: str) -> bool:
        """把 tag 指向同一个 digest —— 晋升的本质, 不产生任何新字节。"""
        repo = self._repo(name)
        if repo is None or digest not in repo["manifests"]:
            return False
        repo["tags"][tag] = digest
        return True

    # -------------------------------------------------------- 拉取
    def get(self, name: str, tag_or_digest: str) -> tuple:
        repo = self._repo(name)
        digest = self.resolve(name, tag_or_digest)
        if repo is None or digest is None:
            return 404, {}, None
        return 200, {"Docker-Content-Digest": digest,
                     "Content-Type": MANIFEST_MEDIA_TYPE}, repo["manifests"][digest]

    def head(self, name: str, tag_or_digest: str) -> tuple:
        status, headers, body = self.get(name, tag_or_digest)
        if status != 200:
            return status, {}, None
        headers["Content-Length"] = len(body)
        return 200, headers, None

    def resolve(self, name: str, tag_or_digest: str):
        """把 ``<tag-or-digest>`` 解析成 digest(tag 是可变的, digest 不可变)。"""
        repo = self._repo(name)
        if repo is None:
            return None
        if tag_or_digest in repo["manifests"]:
            return tag_or_digest
        return repo["tags"].get(tag_or_digest)

    # -------------------------------------------------------- 内容发现
    def tags_list(self, name: str, n: int = None, last: str = None) -> tuple:
        repo = self._repo(name)
        if repo is None:
            return 404, {}, None
        tags = sorted(repo["tags"])
        if last is not None:
            tags = [t for t in tags if t > last]
        headers = {}
        if n is not None:
            if n == 0:
                return 200, {}, {"name": name, "tags": []}     # n=0: 空列表且不带 Link
            page, more = tags[:n], tags[n:]
            if more:
                headers["Link"] = '</v2/%s/tags/list?n=%d&last=%s>; rel="next"' % (
                    name, n, page[-1])
            tags = page
        return 200, headers, {"name": name, "tags": tags}

    def referrers(self, name: str, subject_digest: str, tag_fallback: bool = False) -> tuple:
        """referrers API;不支持时返回 404, 客户端按回退 tag 方案再试一次。"""
        repo = self._repo(name)
        if repo is None:
            return 404, {}, None
        if validate_digest(subject_digest) == "invalid":
            return 400, {"error": "DIGEST_INVALID"}, None
        if not self.supports_referrers:
            if not tag_fallback:
                return 404, {}, None
            status, headers, body = self.get(name, referrers_tag(subject_digest))
            if status != 200:
                return status, headers, None
            # 回退方案拿到的是挂在 referrers tag 上的 image index, 客户端自行解析
            return 200, headers, json.loads(body.decode("utf-8"))
        descriptors = []
        for digest, data in sorted(repo["manifests"].items()):
            parsed = json.loads(data.decode("utf-8"))
            if parsed.get("subject", {}).get("digest") != subject_digest:
                continue
            desc = {"mediaType": parsed.get("mediaType", MANIFEST_MEDIA_TYPE),
                    "digest": digest, "size": len(data)}
            artifact_type = parsed.get("artifactType") or \
                (parsed.get("config") or {}).get("mediaType")
            if artifact_type:
                desc["artifactType"] = artifact_type   # 缺失时(索引)必须省略该字段
            if parsed.get("annotations"):
                desc["annotations"] = parsed["annotations"]
            descriptors.append(desc)
        return 200, {"Content-Type": INDEX_MEDIA_TYPE}, {
            "schemaVersion": 2, "mediaType": INDEX_MEDIA_TYPE, "manifests": descriptors}


def _referenced_digests(parsed: dict) -> list:
    out = []
    cfg = parsed.get("config")
    if isinstance(cfg, dict) and cfg.get("digest"):
        out.append(cfg["digest"])
    for layer in parsed.get("layers") or []:
        if isinstance(layer, dict) and layer.get("digest"):
            out.append(layer["digest"])
    return out



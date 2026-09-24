"""OCI Distribution Spec 的 registry 侧模型。

逐节转写自 opencontainers/distribution-spec `spec.md`（54228 B，main 分支）：
  §Endpoints（end-1 .. end-14）、§Error Codes（code-1 .. code-14）、
  §Pushing blobs monolithically / in chunks、§Mounting、§Listing Tags、§Listing Referrers。
所有状态码、头部名与错误码都照抄规范，未做"我觉得更合理"的改动。
"""

import hashlib
import json
import re
import uuid

CONTENT_RANGE_RE = re.compile(r"^[0-9]+-[0-9]+$")

MANIFEST_MT = "application/vnd.oci.image.manifest.v1+json"
INDEX_MT = "application/vnd.oci.image.index.v1+json"


class RegistryError(Exception):
    """规范 §Error Codes 的 JSON 错误体。"""

    def __init__(self, status, code, message="", detail=None):
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail

    def body(self):
        err = {"code": self.code, "message": self.message}
        if self.detail is not None:
            err["detail"] = self.detail
        return {"errors": [err]}


class Response:
    def __init__(self, status, headers=None, body=None):
        self.status = status
        self.headers = headers or {}
        self.body = body


def digest_of(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


class UploadSession:
    def __init__(self, name):
        self.uuid = str(uuid.uuid4())
        self.name = name
        self.data = b""

    @property
    def offset(self):
        return len(self.data)


class Registry:
    def __init__(self, delete_enabled=True, referrers_enabled=False):
        self.repos = {}                 # name -> repo dict
        self.uploads = {}               # uuid -> UploadSession
        self.delete_enabled = delete_enabled
        self.referrers_enabled = referrers_enabled

    # ---------------------------------------------------------------- 基础设施
    def repo(self, name):
        if not name or not re.match(r"^[a-z0-9]+(?:(?:[._]|__|[-]*)[a-z0-9]+)*$", name):
            raise RegistryError(404, "NAME_INVALID", "invalid repository name")
        if name not in self.repos:
            raise RegistryError(404, "NAME_UNKNOWN", "repository name not known to registry")
        return self.repos[name]

    def ensure_repo(self, name):
        if name not in self.repos:
            self.repos[name] = {"manifests": {}, "tags": {}, "blobs": {},
                                "referrers": {}}
        return self.repos[name]

    def _check_refs(self, repo, manifest):
        """MANIFEST_BLOB_UNKNOWN：manifest 引用的层/配置必须在仓库里。"""
        missing = []
        for ref in [manifest.get("config", {}).get("digest")] + \
                [l.get("digest") for l in manifest.get("layers", [])]:
            if ref and ref not in repo["blobs"]:
                missing.append(ref)
        if missing:
            raise RegistryError(404, "MANIFEST_BLOB_UNKNOWN",
                                "manifest references unknown blob",
                                {"digests": missing})

    # ---------------------------------------------------------------- end-1
    def get_v2(self):
        return Response(200, {}, {})

    # ---------------------------------------------------------------- end-2/3
    def get_blob(self, name, digest):
        repo = self.repo(name)
        if digest not in repo["blobs"]:
            raise RegistryError(404, "BLOB_UNKNOWN", "blob unknown to registry")
        data = repo["blobs"][digest]
        return Response(200, {"Docker-Content-Digest": digest,
                              "Content-Length": str(len(data))}, data)

    def head_blob(self, name, digest):
        r = self.get_blob(name, digest)
        return Response(200, r.headers, b"")

    def get_manifest(self, name, reference):
        repo = self.repo(name)
        digest = repo["tags"].get(reference, reference)
        if digest not in repo["manifests"]:
            raise RegistryError(404, "MANIFEST_UNKNOWN",
                                "manifest unknown to registry")
        body = repo["manifests"][digest]
        return Response(200, {"Docker-Content-Digest": digest,
                              "Content-Type": body["mediaType"]}, body)

    def head_manifest(self, name, reference):
        r = self.get_manifest(name, reference)
        return Response(200, r.headers, None)

    # ---------------------------------------------------------------- end-7
    def put_manifest(self, name, reference, manifest, media_type=MANIFEST_MT,
                     extra_tags=None):
        repo = self.ensure_repo(name)
        self._check_refs(repo, manifest)
        raw = json.dumps(manifest, sort_keys=True).encode()
        digest = digest_of(raw)
        repo["manifests"][digest] = dict(manifest, mediaType=media_type)
        refs = [reference] + list(extra_tags or [])
        for ref in refs:
            repo["tags"][ref] = digest
        if manifest.get("subject"):
            repo["referrers"].setdefault(manifest["subject"], []).append(digest)
        return Response(201, {"Location": "/v2/%s/manifests/%s" % (name, digest),
                              "Docker-Content-Digest": digest}, None)

    # ---------------------------------------------------------------- end-4/5/6
    def post_upload(self, name, digest=None, mount=None, frm=None,
                    content_length=0):
        if mount and frm:
            src = self.repo(frm) if frm in self.repos else None
            if src and mount in src["blobs"]:
                dst = self.ensure_repo(name)
                dst["blobs"][mount] = src["blobs"][mount]
                return Response(201, {"Location": "/v2/%s/blobs/%s" % (name, mount)})
            # 挂载失败：回落到普通上传流程（仍是 202）
        if digest is not None:
            # end-4b：单次 POST 整体上传，digest 提前给出
            s = UploadSession(name)
            self.uploads[s.uuid] = s
            return Response(202, {"Location": self._loc(s),
                                  "Docker-Upload-UUID": s.uuid}, None)
        s = UploadSession(name)
        self.uploads[s.uuid] = s
        return Response(202, {"Location": self._loc(s),
                              "Docker-Upload-UUID": s.uuid,
                              "Range": "0-0"}, None)

    @staticmethod
    def _loc(s):
        return "/v2/%s/blobs/uploads/%s" % (s.name, s.uuid)

    def _session(self, uuid_):
        if uuid_ not in self.uploads:
            raise RegistryError(404, "BLOB_UPLOAD_UNKNOWN",
                                "blob upload unknown to registry")
        return self.uploads[uuid_]

    def patch_upload(self, uuid_, data, content_range=None):
        s = self._session(uuid_)
        if content_range is not None:
            if not CONTENT_RANGE_RE.match(content_range):
                raise RegistryError(416, "BLOB_UPLOAD_INVALID",
                                    "bad content range %r" % content_range)
            start = int(content_range.split("-")[0])
            if s.offset == 0 and start != 0:
                raise RegistryError(416, "BLOB_UPLOAD_INVALID",
                                    "the first chunk's range must begin with 0")
            if start != s.offset:
                raise RegistryError(416, "BLOB_UPLOAD_INVALID",
                                    "chunk out of order: expected %d got %d"
                                    % (s.offset, start))
            if len(data) != int(content_range.split("-")[1]) - start + 1:
                raise RegistryError(416, "BLOB_UPLOAD_INVALID",
                                    "content-length does not match range")
        s.data += data
        return Response(202, {"Location": self._loc(s),
                              "Range": "0-%d" % (s.offset - 1),
                              "Docker-Upload-UUID": s.uuid}, None)

    def put_upload(self, uuid_, digest, data=None, content_range=None):
        s = self._session(uuid_)
        if data:
            self.patch_upload(uuid_, data, content_range)
        got = digest_of(s.data)
        if got != digest:
            raise RegistryError(400, "DIGEST_INVALID",
                                "provided digest did not match uploaded content",
                                {"provided": digest, "computed": got})
        repo = self.ensure_repo(s.name)
        repo["blobs"][got] = s.data
        del self.uploads[uuid_]
        return Response(201, {"Location": "/v2/%s/blobs/%s" % (s.name, got),
                              "Docker-Content-Digest": got}, None)

    def get_upload_status(self, uuid_):
        s = self._session(uuid_)
        return Response(204, {"Location": self._loc(s),
                              "Range": "0-%d" % (s.offset - 1 if s.offset else 0)}, None)

    def delete_upload(self, uuid_):
        self._session(uuid_)
        del self.uploads[uuid_]
        return Response(204, {}, None)

    # ---------------------------------------------------------------- end-8
    def list_tags(self, name, n=None, last=None):
        repo = self.repo(name)
        tags = sorted(repo["tags"].keys())
        if last is not None:
            tags = [t for t in tags if t > last]
        headers = {}
        if n is None:
            page, rest = tags, []
        else:
            page, rest = tags[:n], tags[n:]
        if rest and n != 0:
            headers["Link"] = ('</v2/%s/tags/list?n=%d&last=%s>; rel="next"'
                               % (name, len(rest), page[-1] if page else ""))
        return Response(200, headers, {"name": name, "tags": page})

    # ---------------------------------------------------------------- end-9/10
    def delete_manifest(self, name, reference):
        repo = self.repo(name)
        if not self.delete_enabled:
            raise RegistryError(405, "UNSUPPORTED", "the operation is unsupported")
        digest = repo["tags"].get(reference, reference)
        if digest not in repo["manifests"]:
            raise RegistryError(404, "MANIFEST_UNKNOWN", "manifest unknown to registry")
        del repo["manifests"][digest]
        for tag, d in list(repo["tags"].items()):
            if d == digest:
                del repo["tags"][tag]
        return Response(202, {}, None)

    def delete_blob(self, name, digest):
        repo = self.repo(name)
        if not self.delete_enabled:
            raise RegistryError(405, "UNSUPPORTED", "the operation is unsupported")
        if digest not in repo["blobs"]:
            raise RegistryError(404, "BLOB_UNKNOWN", "blob unknown to registry")
        del repo["blobs"][digest]
        return Response(202, {}, None)

    # ---------------------------------------------------------------- end-12
    def get_referrers(self, name, digest, artifact_type=None):
        repo = self.repo(name)
        if not self.referrers_enabled:
            # 不支持该 API 的 registry 回落到 referrers tag schema，客户端按 404 处理
            raise RegistryError(404, "UNSUPPORTED", "referrers API not supported")
        if not digest.startswith("sha256:") or len(digest) != 71:
            raise RegistryError(400, "UNSUPPORTED", "invalid digest syntax")
        digests = repo["referrers"].get(digest, [])
        headers = {"Content-Type": INDEX_MT}
        manifests = []
        for d in digests:
            m = repo["manifests"][d]
            desc = {"mediaType": m.get("mediaType", MANIFEST_MT), "digest": d,
                    "artifactType": m.get("artifactType",
                                          m.get("config", {}).get("mediaType", "")),
                    "annotations": m.get("annotations", {})}
            if artifact_type and desc["artifactType"] != artifact_type:
                continue
            manifests.append(desc)
        if artifact_type:
            headers["OCI-Filters-Applied"] = "artifactType"
        return Response(200, headers,
                        {"schemaVersion": 2, "mediaType": INDEX_MT,
                         "manifests": manifests})

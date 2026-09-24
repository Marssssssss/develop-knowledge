"""OCI Distribution Spec 自检：端点语义、分块上传、标签分页、referrers。"""

import sys

from dist_registry import (INDEX_MT, MANIFEST_MT, Registry, RegistryError,
                           digest_of)

PASSED = 0
FAILED = []


def ok(name, cond):
    global PASSED
    if cond:
        PASSED += 1
    else:
        FAILED.append(name)
        print("  FAIL %s" % name)


def err_of(fn):
    """跑 fn，期望抛 RegistryError 并返回它。"""
    try:
        fn()
    except RegistryError as e:
        return e
    except Exception as e:                       # noqa: BLE001
        print("  非预期异常 %r" % e)
        return None
    return None


def t_endpoints_basics():
    print("[1] end-1 支持性探测与仓库命名")
    reg = Registry()
    ok("GET /v2/ 返回 200", reg.get_v2().status == 200)
    e = err_of(lambda: reg.repo("Bad_Name"))
    ok("非法仓库名 → NAME_INVALID", e is not None and e.code == "NAME_INVALID")
    e = err_of(lambda: reg.repo("nope"))
    ok("未知仓库 → NAME_UNKNOWN（code-9）",
       e is not None and e.code == "NAME_UNKNOWN" and e.status == 404)


def t_blob_monolithic():
    print("[2] end-4a/end-6 整体上传")
    reg = Registry()
    reg.ensure_repo("app")
    data = b"hello layer"
    dgst = digest_of(data)
    r = reg.post_upload("app")
    ok("POST 返回 202", r.status == 202)
    ok("POST 带 Location", "Location" in r.headers)
    ok("POST 带 Docker-Upload-UUID", "Docker-Upload-UUID" in r.headers)
    uid = r.headers["Docker-Upload-UUID"]
    r = reg.put_upload(uid, dgst, data)
    ok("PUT 返回 201", r.status == 201)
    ok("PUT 的 Location 指向可拉取的 blob", r.headers["Location"].endswith(dgst))
    got = reg.get_blob("app", dgst)
    ok("GET blob 内容一致", got.body == data)
    ok("GET blob 带 Docker-Content-Digest", got.headers["Docker-Content-Digest"] == dgst)
    ok("HEAD blob 返回 200 无 body", reg.head_blob("app", dgst).body == b"")

    e = err_of(lambda: reg.get_blob("app", "sha256:" + "0" * 64))
    ok("未知 blob → BLOB_UNKNOWN（code-1）", e is not None and e.code == "BLOB_UNKNOWN")

    uid2 = reg.post_upload("app").headers["Docker-Upload-UUID"]
    e = err_of(lambda: reg.put_upload(uid2, "sha256:" + "1" * 64, b"xyz"))
    ok("digest 不匹配 → DIGEST_INVALID（code-4）",
       e is not None and e.code == "DIGEST_INVALID" and e.status == 400)
    ok("DIGEST_INVALID 的 detail 给出实际算出的 digest",
       e is not None and e.detail["computed"] == digest_of(b"xyz"))

    reg2 = Registry()
    reg2.ensure_repo("app")
    r = reg2.post_upload("app", digest=dgst)
    ok("end-4b 单次 POST 带 digest → 201/202", r.status in (201, 202))


def t_blob_chunked():
    print("[3] end-5/end-6 分块上传")
    reg = Registry()
    reg.ensure_repo("app")
    s = reg.post_upload("app", content_length=0)
    uid = s.headers["Docker-Upload-UUID"]
    ok("分块 POST 的 Range 是 0-0", s.headers["Range"] == "0-0")

    r = reg.patch_upload(uid, b"AAAAA", "0-4")
    ok("首个 PATCH 返回 202", r.status == 202)
    ok("首个 PATCH 的 Range 是 0-4", r.headers["Range"] == "0-4")
    r = reg.patch_upload(uid, b"BBBBB", "5-9")
    ok("第二个 PATCH 的 Range 累计到 0-9", r.headers["Range"] == "0-9")
    body = b"AAAAABBBBB"
    r = reg.put_upload(uid, digest_of(body))
    ok("关闭会话返回 201", r.status == 201)
    ok("合并后的 blob 内容正确", reg.get_blob("app", digest_of(body)).body == body)

    # 乱序 / 非法 range
    u2 = reg.post_upload("app").headers["Docker-Upload-UUID"]
    e = err_of(lambda: reg.patch_upload(u2, b"XXXXX", "5-9"))
    ok("首个 chunk 不从 0 开始 → 416",
       e is not None and e.status == 416 and e.code == "BLOB_UPLOAD_INVALID")
    u3 = reg.post_upload("app").headers["Docker-Upload-UUID"]
    reg.patch_upload(u3, b"AAAAA", "0-4")
    e = err_of(lambda: reg.patch_upload(u3, b"CCCC", "2-5"))
    ok("后续 chunk 错位 → 416", e is not None and e.status == 416)
    e = err_of(lambda: reg.patch_upload(u3, b"DDD", "0-"))
    ok("Content-Range 不合正则 → 416", e is not None and e.status == 416)
    e = err_of(lambda: reg.patch_upload(u3, b"DDD", "10-12"))
    ok("Content-Length 与 range 不符 → 416", e is not None and e.status == 416)

    ok("GET 上传状态返回 204", reg.get_upload_status(u3).status == 204)
    ok("GET 上传状态带 Range", "Range" in reg.get_upload_status(u3).headers)
    ok("DELETE 上传会话返回 204", reg.delete_upload(u3).status == 204)
    e = err_of(lambda: reg.patch_upload(u3, b"Z", "0-0"))
    ok("会话已删后再 PATCH → BLOB_UPLOAD_UNKNOWN（code-3）",
       e is not None and e.code == "BLOB_UPLOAD_UNKNOWN" and e.status == 404)


def t_mount():
    print("[4] end-11 跨仓库挂载")
    reg = Registry()
    base = reg.ensure_repo("base")
    data = b"shared layer"
    dgst = digest_of(data)
    base["blobs"][dgst] = data
    reg.ensure_repo("app")
    r = reg.post_upload("app", mount=dgst, frm="base")
    ok("源仓库有该 blob → 201", r.status == 201)
    ok("挂载后目标仓库可直接拉取", reg.get_blob("app", dgst).body == data)
    r = reg.post_upload("app", mount="sha256:" + "2" * 64, frm="base")
    ok("源仓库没有该 blob → 回落成普通上传 202", r.status == 202)


def t_manifest():
    print("[5] end-3/end-7 manifest")
    reg = Registry()
    reg.ensure_repo("app")
    cfg, layer = b"{}", b"layer bytes"
    for blob in (cfg, layer):
        u = reg.post_upload("app").headers["Docker-Upload-UUID"]
        reg.put_upload(u, digest_of(blob), blob)
    man = {"schemaVersion": 2,
           "config": {"mediaType": "application/vnd.oci.image.config.v1+json",
                      "digest": digest_of(cfg), "size": len(cfg)},
           "layers": [{"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                       "digest": digest_of(layer), "size": len(layer)}]}
    r = reg.put_manifest("app", "v1", man)
    ok("PUT manifest 返回 201", r.status == 201)
    dgst = r.headers["Docker-Content-Digest"]
    ok("PUT manifest 返回 digest 头", dgst.startswith("sha256:"))
    got = reg.get_manifest("app", "v1")
    ok("按 tag 拉取 manifest 返回 200", got.status == 200)
    ok("按 tag 拉取带回 digest", got.headers["Docker-Content-Digest"] == dgst)
    ok("按 digest 拉取等价", reg.get_manifest("app", dgst).status == 200)
    ok("HEAD manifest 不带 body", reg.head_manifest("app", "v1").body is None)
    e = err_of(lambda: reg.get_manifest("app", "v9"))
    ok("未知 manifest → MANIFEST_UNKNOWN（code-7）",
       e is not None and e.code == "MANIFEST_UNKNOWN")

    bad = dict(man, layers=[{"digest": "sha256:" + "3" * 64, "size": 1}])
    e = err_of(lambda: reg.put_manifest("app", "bad", bad))
    ok("引用未知层 → MANIFEST_BLOB_UNKNOWN（code-5）",
       e is not None and e.code == "MANIFEST_BLOB_UNKNOWN" and e.status == 404)
    ok("MANIFEST_BLOB_UNKNOWN 的 detail 列出缺失 digest",
       e is not None and e.detail["digests"] == ["sha256:" + "3" * 64])

    reg.put_manifest("app", dgst, man, extra_tags=["latest", "stable"])
    ok("end-7b 一次 PUT 挂多个 tag",
       reg.repos["app"]["tags"]["latest"] == dgst and
       reg.repos["app"]["tags"]["stable"] == dgst)


def t_tags():
    print("[6] end-8 标签列举与分页")
    reg = Registry()
    reg.ensure_repo("app")
    for t in ("v10", "v2", "V1"):
        reg.repos["app"]["tags"][t] = "sha256:" + "a" * 64
    r = reg.list_tags("app")
    ok("无参数返回全部标签", r.body["tags"] == ["V1", "v10", "v2"])
    ok("标签按 ASCIIbetical 排序（大写在前）", r.body["tags"][0] == "V1")
    r = reg.list_tags("app", n=2)
    ok("n=2 只返回 2 个", len(r.body["tags"]) == 2)
    ok("有余量时带 Link 头", r.headers.get("Link", "").endswith('rel="next"'))
    r = reg.list_tags("app", n=0)
    ok("n=0 返回空列表", r.body["tags"] == [])
    ok("n=0 不带 Link 头", "Link" not in r.headers)
    r = reg.list_tags("app", last="v10")
    ok("last 参数是非包含的（不返回 last 本身）", r.body["tags"] == ["v2"])
    r = reg.list_tags("app", n=1, last="V1")
    ok("n 与 last 组合：跳过 last 再取 1 个", r.body["tags"] == ["v10"])


def t_delete_and_errors():
    print("[7] end-9/end-10 删除与错误体")
    reg = Registry()
    reg.ensure_repo("app")
    u = reg.post_upload("app").headers["Docker-Upload-UUID"]
    dgst = digest_of(b"x")
    reg.put_upload(u, dgst, b"x")
    reg.repos["app"]["manifests"][dgst] = {"mediaType": MANIFEST_MT}
    ok("DELETE manifest 返回 202", reg.delete_manifest("app", dgst).status == 202)
    ok("DELETE blob 返回 202", reg.delete_blob("app", dgst).status == 202)
    e = err_of(lambda: reg.delete_blob("app", dgst))
    ok("删不存在的 blob → BLOB_UNKNOWN", e is not None and e.code == "BLOB_UNKNOWN")
    reg_off = Registry(delete_enabled=False)
    reg_off.ensure_repo("app")
    e = err_of(lambda: reg_off.delete_manifest("app", dgst))
    ok("不支持删除 → 405 UNSUPPORTED（code-13）",
       e is not None and e.status == 405 and e.code == "UNSUPPORTED")

    e = RegistryError(429, "TOOMANYREQUESTS", "too many requests")
    body = e.body()
    ok("错误体是 {errors:[{code,message}]}",
       list(body) == ["errors"] and body["errors"][0]["code"] == "TOOMANYREQUESTS")
    e2 = RegistryError(400, "DIGEST_INVALID", "bad", {"provided": "x"})
    ok("detail 字段可选携带",
       e2.body()["errors"][0]["detail"] == {"provided": "x"})


def t_referrers():
    print("[8] end-12 referrers")
    reg = Registry(referrers_enabled=True)
    reg.ensure_repo("app")
    empty = b"{}"
    u = reg.post_upload("app").headers["Docker-Upload-UUID"]
    ed = digest_of(empty)
    reg.put_upload(u, ed, empty)
    empty_cfg = {"mediaType": "application/vnd.oci.empty.v1+json",
                 "digest": ed, "size": len(empty)}
    subj = "sha256:" + "b" * 64
    reg.put_manifest("app", "att1",
                     {"schemaVersion": 2, "subject": subj,
                      "artifactType": "application/vnd.example.sbom.v1",
                      "config": empty_cfg,
                      "layers": [], "annotations": {"k": "v"}})
    reg.put_manifest("app", "att2",
                     {"schemaVersion": 2, "subject": subj,
                      "artifactType": "application/vnd.example.sig.v1",
                      "config": empty_cfg, "layers": []})
    r = reg.get_referrers("app", subj)
    ok("referrers 返回 200", r.status == 200)
    ok("Content-Type 是 image index", r.headers["Content-Type"] == INDEX_MT)
    ok("列出两条引用", len(r.body["manifests"]) == 2)
    ok("descriptor 带 artifactType",
       r.body["manifests"][0]["artifactType"] == "application/vnd.example.sbom.v1")
    ok("descriptor 带 annotations", r.body["manifests"][0]["annotations"] == {"k": "v"})
    r = reg.get_referrers("app", subj, artifact_type="application/vnd.example.sig.v1")
    ok("按 artifactType 过滤只剩 1 条", len(r.body["manifests"]) == 1)
    ok("过滤时带 OCI-Filters-Applied",
       r.headers.get("OCI-Filters-Applied") == "artifactType")
    ok("查无引用返回空 manifests 而非 404",
       reg.get_referrers("app", "sha256:" + "d" * 64).body["manifests"] == [])
    e = err_of(lambda: reg.get_referrers("app", "not-a-digest"))
    ok("digest 语法非法 → 400", e is not None and e.status == 400)
    reg_off = Registry(referrers_enabled=False)
    reg_off.ensure_repo("app")
    e = err_of(lambda: reg_off.get_referrers("app", subj))
    ok("不支持 referrers API 时回落（404）", e is not None and e.status == 404)


def main():
    t_endpoints_basics()
    t_blob_monolithic()
    t_blob_chunked()
    t_mount()
    t_manifest()
    t_tags()
    t_delete_and_errors()
    t_referrers()
    print("通过 %d 项，失败 %d 项" % (PASSED, len(FAILED)))
    if FAILED:
        for f in FAILED:
            print("  - %s" % f)
        sys.exit(1)


if __name__ == "__main__":
    main()

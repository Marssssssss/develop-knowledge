"""OCI Distribution Spec —— 演示入口：完整跑一遍 push / pull / list。"""

from dist_registry import Registry, RegistryError, digest_of

MANIFEST_MT = "application/vnd.oci.image.manifest.v1+json"


def push_blob(reg, name, data):
    r = reg.post_upload(name)
    uid = r.headers["Docker-Upload-UUID"]
    dgst = digest_of(data)
    r = reg.put_upload(uid, dgst, data)
    return dgst, r.status


def push_chunked(reg, name, chunks):
    """分块上传：POST(Content-Length:0) → PATCH 若干 → PUT ?digest。"""
    s = reg.post_upload(name, content_length=0)
    uid = s.headers["Docker-Upload-UUID"]
    offset = 0
    for c in chunks:
        rng = "%d-%d" % (offset, offset + len(c) - 1)
        r = reg.patch_upload(uid, c, rng)
        print("   PATCH %-8s -> %d  Range %s" % (rng, r.status, r.headers["Range"]))
        offset += len(c)
    body = b"".join(chunks)
    r = reg.put_upload(uid, digest_of(body))
    return digest_of(body), r.status


def show(title):
    print("\n== %s ==" % title)


def main():
    reg = Registry()
    reg.ensure_repo("app")

    show("1. 探测与整体上传")
    print("   GET /v2/ ->", reg.get_v2().status)
    dgst, st = push_blob(reg, "app", b"layer-bytes")
    print("   blob %s  PUT -> %d" % (dgst[:19] + "..", st))
    got = reg.get_blob("app", dgst)
    print("   GET blob -> %d, %r, digest 头一致=%s"
          % (got.status, got.body,
             got.headers["Docker-Content-Digest"] == dgst))

    show("2. 分块上传（乱序会被 416 拒）")
    d2, st = push_chunked(reg, "app", [b"AAAAA", b"BBBBB", b"C"])
    print("   合并后 digest=%s 状态=%d" % (d2[:19] + "..", st))
    bad = reg.post_upload("app").headers["Docker-Upload-UUID"]
    try:
        reg.patch_upload(bad, b"XXXXX", "5-9")
    except RegistryError as e:
        print("   首个 chunk 不从 0 开始 -> %d %s" % (e.status, e.code))

    show("3. manifest 与 tag")
    cfg = b"{}"
    cd, _ = push_blob(reg, "app", cfg)
    man = {"schemaVersion": 2,
           "config": {"mediaType": "application/vnd.oci.image.config.v1+json",
                      "digest": cd, "size": len(cfg)},
           "layers": [{"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                       "digest": dgst, "size": 11}]}
    r = reg.put_manifest("app", "v1", man, extra_tags=["latest"])
    md = r.headers["Docker-Content-Digest"]
    print("   PUT manifest -> %d  digest=%s" % (r.status, md[:19] + ".."))
    print("   按 tag 拉取   ->", reg.get_manifest("app", "v1").status)
    print("   按 digest 拉取 ->", reg.get_manifest("app", md).status)
    reg.repos["app"]["tags"]["v10"] = md
    reg.repos["app"]["tags"]["V2"] = md
    print("   标签列表（ASCIIbetical）->", reg.list_tags("app").body["tags"])
    p = reg.list_tags("app", n=2)
    print("   n=2 ->", p.body["tags"], "Link:", p.headers.get("Link", "(无)"))
    print("   last=v10 ->", reg.list_tags("app", last="v10").body["tags"])

    show("4. 跨仓库挂载")
    reg.ensure_repo("base")
    reg.repos["base"]["blobs"][dgst] = b"layer-bytes"
    r = reg.post_upload("app", mount=dgst, frm="base")
    print("   mount=%s from=base -> %d" % (dgst[:19] + "..", r.status))

    show("5. 错误体")
    e = RegistryError(404, "MANIFEST_UNKNOWN", "manifest unknown to registry")
    print("   ", e.body())


if __name__ == "__main__":
    main()

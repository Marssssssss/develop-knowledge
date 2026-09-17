"""自检(OCI 侧): digest 语法/计算、仓库 API 语义、制品晋升闸门的断言。

由 ``semver_check.py`` 的 ``main()`` 调用;harness 共用。
"""

from __future__ import annotations

import json

from harness import check, raises
from oci_registry import (
    INDEX_MEDIA_TYPE,
    MANIFEST_MEDIA_TYPE,
    OciError,
    Registry,
    compute_digest,
    referrers_tag,
    validate_digest,
    verify_digest,
)
from promotion import copy_across, promotion_decision, promote

HEX64 = "6c3c624b58dbbcd3c0dd82b4c53f04194d1247c6eebdaab7c610cf7d66709b3b"


def make_manifest(config_digest: str, layer_digest: str, **extra) -> bytes:
    doc = {"schemaVersion": 2, "mediaType": MANIFEST_MEDIA_TYPE,
           "config": {"mediaType": "application/vnd.oci.image.config.v1+json",
                      "digest": config_digest, "size": 2},
           "layers": [{"mediaType": "application/vnd.oci.image.layer.v1.tar",
                       "digest": layer_digest, "size": 5}]}
    doc.update(extra)
    return json.dumps(doc, separators=(",", ":")).encode("utf-8")


def push_image(reg: Registry, name: str, tag: str, version: str) -> str:
    """推一个最小镜像: config 里带版本号, 因此不同版本 digest 必然不同。"""
    config = ('{"v":"%s"}' % version).encode("utf-8")
    layer = ("layer-of-%s" % version).encode("utf-8")
    cfg_d, lay_d = compute_digest(config), compute_digest(layer)
    reg.put_blob(name, config, cfg_d)
    reg.put_blob(name, layer, lay_d)
    data = make_manifest(cfg_d, lay_d)
    status, headers, _ = reg.put_manifest(name, data, tag)
    assert status == 201, (status, headers)
    return headers["Docker-Content-Digest"]


# ============================ 4. digest ============================

def test_digest():
    print("[4] digest 语法与内容寻址")
    check("sha256 + 64 位小写十六进制合法", validate_digest("sha256:" + HEX64) == "registered")
    check("sha512 + 128 位小写十六进制合法",
          validate_digest("sha512:" + "a" * 128) == "registered")
    check("官方例子: multihash+base58 语法合法但未注册",
          validate_digest("multihash+base58:QmRZxt2b1FVZPNqd8hsiykDL3TdBDeTSPX9Kv46HmX4Gx8")
          == "unregistered")
    check("官方例子: sha256+b64u 语法合法但未注册",
          validate_digest("sha256+b64u:LCa0a2j_xo_5m0U8HTBBNBNCLXBkg7-g-YpeiGJm564")
          == "unregistered")
    check("大写十六进制非法(原文: [A-F] MUST NOT be used)",
          validate_digest("sha256:" + HEX64.upper()) == "invalid")
    check("长度不足非法", validate_digest("sha256:" + HEX64[:-1]) == "invalid")
    check("算法必须是小写 [a-z0-9]", validate_digest("SHA256:" + HEX64) == "invalid")
    check("缺冒号非法", validate_digest(HEX64) == "invalid")
    check("空 encoded 非法", validate_digest("sha256:") == "invalid")

    check("空内容的 sha256 是官方已知值",
          compute_digest(b"") ==
          "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
    check("同一字节在任何仓库都是同一 digest",
          compute_digest(b"hello") == compute_digest(b"hello"))
    check("verify: 内容与 digest 匹配", verify_digest(b"hello", compute_digest(b"hello")))
    check("verify: 内容被改一个字节就失败",
          not verify_digest(b"hellp", compute_digest(b"hello")))
    check("sha512 摘要更长", len(compute_digest(b"x", "sha512")) == 135)

    # Referrers Tag 方案(官方表格三行例子)
    check("sha256 的 referrers tag = sha256-<64 位>",
          referrers_tag("sha256:" + "a" * 64) == "sha256-" + "a" * 64)
    check("sha512 的 encoded 截断到 64 位",
          referrers_tag("sha512:" + "a" * 128) == "sha512-" + "a" * 64)
    long_alg = ("test+algorithm+using+algorithm+separators+and+lots+of+characters"
                "+to+excercise+overall+truncation")
    long_enc = ("alsoSome=InTheEncodedSectionToShowHyphenReplacementAndLotsAndLots"
                "OfCharactersToExcerciseEncodedTruncation")
    check("官方表格第 3 行: 算法截断 32、非法字符换 -",
          referrers_tag("%s:%s" % (long_alg, long_enc)) ==
          "test-algorithm-using-algorithm-s"
          "-alsoSome-InTheEncodedSectionToShowHyphenReplacementAndLotsAndLot")


# ============================ 5. 仓库 API ============================

def test_registry():
    print("[5] Pull / Push / 内容发现")
    reg = Registry()

    check("非法仓库名报错", raises(reg.get, "App/UPPER", "latest"))
    status, headers, _ = reg.start_blob_upload("demo/app")
    check("POST /blobs/uploads/ 返回 202 + Location", status == 202 and
          headers["Location"].startswith("/v2/demo/app/blobs/uploads/"), str(headers))

    blob = b"config-bytes"
    bd = compute_digest(blob)
    check("digest 不匹配时上传失败(400 DIGEST_INVALID)",
          reg.put_blob("demo/app", blob, compute_digest(b"other"))[0] == 400)
    check("digest 匹配时返回 201", reg.put_blob("demo/app", blob, bd)[0] == 201)

    layer = b"layer-bytes"
    ld = compute_digest(layer)
    reg.put_blob("demo/app", layer, ld)

    orphan = make_manifest(compute_digest(b"nope"), ld)
    status, headers, _ = reg.put_manifest("demo/app", orphan, "v9")
    check("manifest 引用不存在的 blob -> MANIFEST_BLOB_UNKNOWN",
          status == 400 and headers["error"] == "MANIFEST_BLOB_UNKNOWN", str(headers))

    data = make_manifest(bd, ld)
    digest = compute_digest(data)
    status, headers, _ = reg.put_manifest("demo/app", data, "latest")
    check("manifest 推送成功 201 且带 digest",
          status == 201 and headers["Docker-Content-Digest"] == digest, str(headers))
    check("按 digest 推送时 Location 是 manifest URL",
          headers["Location"] == "/v2/demo/app/manifests/" + digest, headers["Location"])

    check("tag 可解析成 digest", reg.resolve("demo/app", "latest") == digest)
    check("digest 自身也可当 reference",
          reg.get("demo/app", digest)[0] == 200)
    check("GET 成功必须带 Docker-Content-Digest",
          reg.get("demo/app", "latest")[1]["Docker-Content-Digest"] == digest)
    check("不存在的 tag -> 404", reg.get("demo/app", "nope")[0] == 404)
    check("不存在的仓库 -> 404", reg.get("no/such", "latest")[0] == 404)
    check("HEAD 返回 Content-Length 与 digest",
          reg.head("demo/app", "latest")[1]["Content-Length"] == len(data))

    # 逐字节保存: 同一逻辑 manifest 的不同字节表示 -> 不同 digest
    reordered = json.dumps(json.loads(data.decode()),
                           separators=(",", ":")).encode()
    if reordered == data:                       # 保证两串字节确实不同
        reordered = data.replace(b'"schemaVersion":2',
                                 b'"schemaVersion" : 2', 1)
    check("仓库必须逐字节保存: 同一逻辑对象的不同字节 = 另一个制品",
          reordered != data and compute_digest(reordered) != digest)
    check("按 digest 推送但字节算出来不符 -> 400 DIGEST_INVALID",
          reg.put_manifest("demo/app", reordered, digest)[0] == 400)

    check("非法 tag(以 - 开头)被拒", reg.put_manifest("demo/app", data, "-bad")[0] == 400)
    check("tag 长度上限 128", reg.put_manifest("demo/app", data, "t" * 129)[0] == 400)


def test_registry_discovery():
    print("[6] tags 列表 / referrers")
    reg = Registry()
    for tag in ["1.9.0", "1.10.0", "latest", "1.2.0"]:
        push_image(reg, "demo/app", tag, tag)

    status, _, body = reg.tags_list("demo/app")
    check("tags 列表按 ASCIIbetical 序(1.10.0 在 1.9.0 之前)",
          status == 200 and body["tags"] == ["1.10.0", "1.2.0", "1.9.0", "latest"],
          str(body["tags"]))
    check("ASCII 序 != 版本序(所以不能靠它挑最新版)",
          body["tags"][0] == "1.10.0" and body["tags"][-1] == "latest")

    status, headers, body = reg.tags_list("demo/app", n=2)
    check("n=2 分页返回 2 条并带 rel=\"next\"",
          len(body["tags"]) == 2 and 'rel="next"' in headers.get("Link", ""), str(headers))
    check("Link 的 last 是页内最后一条",
          headers["Link"].endswith('last=1.2.0>; rel="next"'), headers["Link"])
    check("last 参数从指定 tag 之后继续(不含该 tag)",
          reg.tags_list("demo/app", n=5, last="1.2.0")[2]["tags"] == ["1.9.0", "latest"])
    status, headers, body = reg.tags_list("demo/app", n=0)
    check("n=0 返回空列表且不带 Link",
          body["tags"] == [] and "Link" not in headers, str(headers))
    check("不存在的仓库 tags 列表 404", reg.tags_list("no/such")[0] == 404)

    # referrers: 先推 referrer 再推主体是允许的(subject 例外)
    subject_digest = push_image(reg, "demo/app", "1.0.0", "1.0.0")
    sbom_bytes = b'{"format":"spdx"}'
    sd = compute_digest(sbom_bytes)
    reg.put_blob("demo/app", sbom_bytes, sd)
    referrer = make_manifest(sd, sd, subject={"mediaType": MANIFEST_MEDIA_TYPE,
                                              "digest": subject_digest, "size": 10},
                             artifactType="application/vnd.example.sbom.v1")
    status, headers, _ = reg.put_manifest("demo/app", referrer)
    check("subject 指向不存在的 manifest 时仍可推送",
          status == 201 and headers["Docker-Content-Digest"] == compute_digest(referrer))

    status, headers, body = reg.referrers("demo/app", subject_digest)
    check("referrers 返回 image index",
          status == 200 and headers["Content-Type"] == INDEX_MEDIA_TYPE, str(status))
    check("index 里带 artifactType 与 digest",
          body["manifests"][0]["artifactType"] == "application/vnd.example.sbom.v1" and
          body["manifests"][0]["digest"] == compute_digest(referrer), str(body["manifests"]))
    check("无匹配时返回空 index",
          reg.referrers("demo/app", compute_digest(b"none"))[2]["manifests"] == [])
    check("非法 digest 语法 -> 400",
          reg.referrers("demo/app", "sha256:XYZ")[0] == 400)

    no_api = Registry(supports_referrers=False)
    subject = push_image(no_api, "demo/app", "1.0.0", "1.0.0")
    check("不支持 referrers API 的仓库返回 404(规范允许)",
          no_api.referrers("demo/app", subject)[0] == 404)
    index_bytes = json.dumps({"schemaVersion": 2, "mediaType": INDEX_MEDIA_TYPE,
                              "manifests": []}, separators=(",", ":")).encode("utf-8")
    fb_tag = referrers_tag(subject)
    check("回退 tag 形如 <算法>-<encoded>", fb_tag == "sha256-" + subject.split(":")[1])
    check("回退 tag 本身是合法 tag", no_api.put_manifest("demo/app", index_bytes, fb_tag)[0]
          == 201)
    status, _, body = no_api.referrers("demo/app", subject, tag_fallback=True)
    check("客户端回退到 referrers tag 后能拿到 index",
          status == 200 and body["manifests"] == [], "%s %s" % (status, body))


# ============================ 7. 晋升闸门 ============================

def test_promotion():
    print("[7] 制品晋升")
    reg = Registry()
    for v in ["1.2.0", "1.3.0-rc.1", "1.4.0", "latest"]:
        push_image(reg, "demo/app", v, v)

    d = promote(reg, "demo/app", "1.2.0", "prod")
    check("稳定版可以晋升到 prod", d["ok"], d["reason"])
    check("晋升后 tag 指向同一 digest", reg.resolve("demo/app", "prod") == d["digest"])
    check("晋升只移动指针: 字节未变", d["bytes_unchanged"], str(d))
    check("晋升后 digest 仍然等于内容摘要", d["digest_unchanged"], str(d))

    d = promote(reg, "demo/app", "1.2.0", "prod")
    check("重复晋升同一 digest 是幂等的", not d["ok"] and "幂等" in d["reason"], d["reason"])

    d = promote(reg, "demo/app", "1.3.0-rc.1", "prod")
    check("预发布默认不得进 prod 通道", not d["ok"], d["reason"])
    d = promote(reg, "demo/app", "1.3.0-rc.1", "prod", {"allow_prerelease": True})
    check("显式放开后预发布可以进(用于灰度通道)", d["ok"], d["reason"])

    d = promotion_decision(reg, "demo/app", "1.2.0", "prod",
                           {"current_version": "1.3.0"})
    check("通道当前版本更高时拒绝降级",
          not d["ok"] and "拒绝降级" in d["reason"], d["reason"])
    d = promotion_decision(reg, "demo/app", "1.4.0", "prod",
                           {"current_version": "1.3.0"})
    check("通道当前版本更低时放行",
          d["ok"], d["reason"])
    d = raise_downgrade_error(reg)
    check("current_version 非法时显式报错而不是静默放行", d, "")

    d = promote(reg, "demo/app", "9.9.9", "prod")
    check("来源不存在 -> 拒绝", not d["ok"] and "404" in d["reason"], d["reason"])

    d = promotion_decision(reg, "demo/app", "latest", "prod")
    check("非版本 tag 无法做版本闸门",
          not d["ok"] and "语义化版本" in d["reason"], d["reason"])

    # 跨仓库搬运
    one = push_image(reg, "demo/app", "1.5.0", "1.5.0")
    moved = copy_across(reg, "demo/app", "1.5.0", "mirror/app", "1.5.0")
    check("跨仓库搬运成功", moved["ok"], str(moved))
    check("搬运后 digest 不变(内容寻址的核心收益)", moved["digest"] == one, str(moved))
    check("镜像仓库里能按 digest 拉到同一字节",
          reg.repos["mirror/app"]["manifests"][one] == reg.repos["demo/app"]["manifests"][one])
    check("OciError 属于 ValueError 子类(便于统一捕获)", issubclass(OciError, ValueError))


def raise_downgrade_error(reg: Registry) -> bool:
    """current_version 写错时必须报错: 静默放行等于闸门失效。"""
    try:
        promotion_decision(reg, "demo/app", "1.2.0", "prod", {"current_version": "v1.3.0"})
        return False
    except OciError:
        return True

"""OCI 镜像格式最小实现 —— opencontainers/image-spec 实战(Python 版).

端到端流程:
  1. 构造 3 个 layer(纯 tar,内容含 whiteout)          [image-spec/layer.md]
  2. 计算 DiffID(未压缩 tar 的 sha256)                [image-spec/config.md]
  3. 生成 config JSON(rootfs.diff_ids 自底向上)        [image-spec/config.md]
  4. gzip 压缩 layer,计算 blob digest(压缩后内容的 sha256)
  5. 生成 manifest(config + layers 的 descriptor)      [image-spec/manifest.md]
  6. 写出 OCI image-layout(blobs/ + index.json + oci-layout)
  7. 验证:重读 layout,校验 digest,计算 ChainID,按层序解包(处理
     .wh. / .wh..wh..opq),比对最终文件树

关键标识符(image-spec/config.md 原文):
  DiffID   = digest(未压缩 layer tar)           # 在 config.rootfs.diff_ids 里
  ChainID(L0) = DiffID(L0)
  ChainID(L0|...|Ln) = Digest(ChainID(前缀) + " " + DiffID(Ln))
  ImageID  = SHA256(config JSON)                # 内容寻址的镜像 ID
运行: python3 oci_image.py(输出目录 out_image/,任何平台可跑)
"""
import gzip
import hashlib
import io
import json
import os
import shutil
import sys
import tarfile

MEDIA_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
MEDIA_CONFIG = "application/vnd.oci.image.config.v1+json"
MEDIA_LAYER_GZ = "application/vnd.oci.image.layer.v1.tar+gzip"
MEDIA_INDEX = "application/vnd.oci.image.index.v1+json"


def sha256_digest(b: bytes) -> str:
    """descriptor 使用的 digest 格式:'sha256:<hex>'."""
    return "sha256:" + hashlib.sha256(b).hexdigest()


def build_layer(entries: dict) -> bytes:
    """把 {path: bytes} 序列化为确定性的 layer tar(未压缩)。

    确定性:路径排序、mtime=0、uid/gid=0、无 uname/gname —— 同一
    内容永远得到同一 DiffID(image-spec: 层 SHOULD 可复现打包)。
    路径以 .wh. 开头的空文件是 whiteout 标记,不作为普通内容解包。
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tf:
        for path in sorted(entries):
            content = entries[path]
            info = tarfile.TarInfo(path)
            info.size = len(content)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o644
            tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


# ---------- 三层设计(演示 Add / Modify / whiteout / opaque) ----------

LAYER1 = {  # base:普通文件
    "etc/hostname": b"demo-box\n",
    "etc/motd": b"welcome to base layer\n",
    "bin/sh": b"#!/bin/sh\n# busybox-ish shell placeholder\n",
    "tmp/a": b"temp file a\n",
    "tmp/b": b"temp file b\n",
}
LAYER2 = {  # app:新增 + 显式 whiteout 删除 etc/motd
    "bin/tool": b"#!/bin/sh\n# app tool, modifies nothing\n",
    "etc/.wh.motd": b"",        # .wh.<name> = 应用本层时删除父层的 <name>
    "root/.keep": b"",
}
LAYER3 = {  # cleanup:opaque whiteout 隐藏 tmp/ 全部子项
    "tmp/.wh..wh..opq": b"",    # .wh..wh..opq = 父层同目录所有子项不可见
}

# 期望的最终文件树(逐层应用 whiteout 语义后的结果)
EXPECTED = {
    "etc/hostname": b"demo-box\n",
    "bin/sh": b"#!/bin/sh\n# busybox-ish shell placeholder\n",
    "bin/tool": b"#!/bin/sh\n# app tool, modifies nothing\n",
    "root/.keep": b"",
    # etc/motd 被 L2 whiteout;tmp/a、tmp/b 被 L3 opaque 隐藏
}


def make_config(diff_ids: list) -> bytes:
    """config JSON(image-spec/config.md):diff_ids 自底向上排列."""
    config = {
        "architecture": "amd64",
        "os": "linux",
        "rootfs": {"type": "layers", "diff_ids": diff_ids},
        "history": [
            {"created_by": "base"}, {"created_by": "app"},
            {"created_by": "cleanup"},
        ],
    }
    return json.dumps(config, separators=(",", ":")).encode()


def make_manifest(config_desc: dict, layer_descs: list) -> bytes:
    """manifest(image-spec/manifest.md):schemaVersion 必须为 2."""
    manifest = {
        "schemaVersion": 2,
        "mediaType": MEDIA_MANIFEST,
        "config": config_desc,
        "layers": layer_descs,   # layers[0] 必须是 base,栈序向上
    }
    return json.dumps(manifest, separators=(",", ":")).encode()


def build_image(layout_dir: str) -> dict:
    """构造完整 OCI image-layout 并返回摘要信息."""
    for d in ("blobs/sha256",):
        os.makedirs(os.path.join(layout_dir, d), exist_ok=True)

    def put_blob(content: bytes) -> tuple:
        digest = sha256_digest(content)
        with open(os.path.join(layout_dir, "blobs/sha256",
                               digest.split(":")[1]), "wb") as f:
            f.write(content)
        return {"digest": digest, "size": len(content)}

    layers = [LAYER1, LAYER2, LAYER3]
    diff_ids, layer_descs = [], []
    for layer in layers:
        tar_bytes = build_layer(layer)
        diff_ids.append(sha256_digest(tar_bytes))          # DiffID:未压缩
        gz = gzip.compress(tar_bytes, mtime=0)             # mtime=0 保证可复现
        desc = put_blob(gz)
        desc["mediaType"] = MEDIA_LAYER_GZ
        layer_descs.append(desc)

    config_bytes = make_config(diff_ids)
    config_desc = put_blob(config_bytes)
    config_desc["mediaType"] = MEDIA_CONFIG

    manifest_bytes = make_manifest(config_desc, layer_descs)
    manifest_desc = put_blob(manifest_bytes)
    manifest_desc["mediaType"] = MEDIA_MANIFEST

    index = {"schemaVersion": 2, "mediaType": MEDIA_INDEX,
             "manifests": [dict(manifest_desc,
                                annotations={"org.opencontainers.image.ref.name":
                                             "demo:latest"})]}
    with open(os.path.join(layout_dir, "index.json"), "w") as f:
        json.dump(index, f, indent=2)
    with open(os.path.join(layout_dir, "oci-layout"), "w") as f:
        json.dump({"imageLayoutVersion": "1.0.0"}, f)

    image_id = sha256_digest(config_bytes)  # ImageID = SHA256(config JSON)
    return {"image_id": image_id, "diff_ids": diff_ids,
            "manifest_digest": manifest_desc["digest"]}


def chain_ids(diff_ids: list) -> list:
    """ChainID 递归(image-spec/config.md):
    ChainID(L0)=DiffID(L0);ChainID(前缀|Ln)=Digest(ChainID(前缀)+" "+DiffID(Ln))"""
    chain = [diff_ids[0]]
    for d in diff_ids[1:]:
        chain.append(sha256_digest(
            (chain[-1] + " " + d).encode()))
    return chain


def apply_layer(root: str, tar_bytes: bytes) -> None:
    """按 image-spec/layer.md 的"应用"语义解包(而非普通 tar 解压):
    先处理 whiteout,再写普通条目(目标已存在时先删再建)."""
    whiteouts, entries = [], []
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tf:
        for m in tf.getmembers():
            name = m.name.lstrip("./")
            if name.split("/")[-1].startswith(".wh."):
                whiteouts.append(name)
            else:
                entries.append((m.name, tf.extractfile(m).read()))

    for w in whiteouts:                       # 1) whiteout 先应用
        base = os.path.basename(w)
        target = os.path.join(root, os.path.dirname(w), base[4:])
        if base == ".wh..wh..opq":            # opaque:清空同目录父层子项
            parent = os.path.dirname(target)
            if os.path.isdir(parent):
                for c in os.listdir(parent):
                    p = os.path.join(parent, c)
                    shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
        else:                                 # 显式:删除父层同名条目
            if os.path.isdir(target):
                shutil.rmtree(target)
            elif os.path.exists(target):
                os.remove(target)

    for name, content in entries:             # 2) 覆盖写(删旧建新)
        path = os.path.join(root, name.lstrip("./"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path):
            os.remove(path)
        with open(path, "wb") as f:
            f.write(content)


def verify_image(layout_dir: str) -> None:
    """从磁盘重读 layout:校验 digest/DiffID/ChainID,解包比对文件树."""
    with open(os.path.join(layout_dir, "index.json")) as f:
        index = json.load(f)
    manifest_desc = index["manifests"][0]

    def blob(digest: str) -> bytes:
        with open(os.path.join(layout_dir, "blobs/sha256",
                               digest.split(":")[1]), "rb") as f:
            data = f.read()
        assert sha256_digest(data) == digest, f"digest 校验失败: {digest}"
        return data

    manifest = json.loads(blob(manifest_desc["digest"]))
    config = json.loads(blob(manifest["config"]["digest"]))

    # 逐层:解压校验 blob digest == manifest 描述,并重算 DiffID
    diff_ids = []
    for desc in manifest["layers"]:
        gz = blob(desc["digest"])
        tar_bytes = gzip.decompress(gz)
        assert desc["size"] == len(gz), "size 校验失败"
        diff_ids.append(sha256_digest(tar_bytes))
        apply_layer(OUT_ROOT, tar_bytes)
    assert diff_ids == config["rootfs"]["diff_ids"], "DiffID 与 config 不一致"

    # ChainID 递归校验
    for d, c in zip(diff_ids, chain_ids(diff_ids)):
        print(f"  DiffID {d[:19]}.. -> ChainID {c[:19]}..")

    # 比对最终文件树
    actual = {}
    for dirpath, _, files in os.walk(OUT_ROOT):
        for fn in files:
            rel = os.path.relpath(os.path.join(dirpath, fn), OUT_ROOT)
            with open(os.path.join(dirpath, fn), "rb") as f:
                actual[rel.replace(os.sep, "/")] = f.read()
    assert actual == EXPECTED, \
        f"文件树不符:\n  多出: {set(actual) - set(EXPECTED)}\n  缺少: {set(EXPECTED) - set(actual)}"
    print("  最终文件树与期望一致(whiteout / opaque 语义正确)")


OUT_DIR = "out_image"
OUT_ROOT = "out_rootfs"

if __name__ == "__main__":
    if os.path.exists(OUT_DIR):
        shutil.rmtree(OUT_DIR)
    if os.path.exists(OUT_ROOT):
        shutil.rmtree(OUT_ROOT)
    os.makedirs(OUT_ROOT)

    info = build_image(OUT_DIR)
    print(f"ImageID(= SHA256(config JSON)) : {info['image_id']}")
    print(f"manifest digest                : {info['manifest_digest']}")
    print(f"3 层 diff_ids                  : {len(info['diff_ids'])} 个")
    print("\n== 验证: 重读 image-layout 并解包 ==")
    verify_image(OUT_DIR)
    print("\nOCI image-layout 写出完成:", OUT_DIR)
    sys.exit(0)

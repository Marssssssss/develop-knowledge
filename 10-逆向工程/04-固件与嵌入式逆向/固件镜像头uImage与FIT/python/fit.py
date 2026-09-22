"""FIT（Flattened Image Tree）的 schema 校验与签名哈希选区。

规范来源：Flattened Image Tree Specification v1.0（fitspec.osfw.foundation，
u-boot 的 `doc/usage/fit/source_file_format.rst` 已明确指向该站点）。

    §5.2   根结点：timestamp 必选；description 可选；
            #address-cells 是"条件必选"——只有在子镜像用到 load/entry 时才必须有
    §5.3   /images 下每个子镜像：description/type/arch/os/compression 必选，
            load/entry 可选
    §5.4   hash 子结点：algo + value，各算法 value 的长度见下表
    §5.5   签名子结点：algo + key-name-hint + value
    §5.8-5.10 /configurations + default + 配置子结点 + 配置签名
    §7.2   两级安全：镜像哈希 + 配置签名，防的是 mix-and-match
    §7.3   签名哈希到底覆盖哪些字节（结构块选区 + strings 块区间）
"""

import hashlib

from fdt import (
    FDT_BEGIN_NODE, FDT_END_NODE, FDT_PROP, FDT_NOP, FDT_END,
    build_fdt, parse_fdt, Node,
)

# §5.4.1 hash 算法的 value 长度（字节）
HASH_ALGO_SIZE = {
    "crc16-ccitt": 2,
    "crc32": 4,
    "md5": 16,
    "sha1": 20,
    "sha256": 32,
    "sha384": 48,
    "sha512": 64,
}

# §5.5.1 签名算法：长度是**签名算法**的长度，与被签的哈希算法无关
SIG_ALGO_SIZE = {
    "rsa2048": 256,
    "rsa3072": 384,
    "rsa4096": 512,
    "ecdsa224": 56,
    "ecdsa256": 64,
    "ecdsa384": 96,
    "ed25519": 64,
}

# §5.3.1 支持的 sub-image type（节选，与 include/image.h 的 IH_TYPE_* 一致）
IMAGE_TYPES = [
    "invalid", "aisimage", "kernel", "ramdisk", "flat_dt", "firmware",
    "script", "filesystem", "standalone", "fpga", "tee", "loadable", "copro",
]

# §7.3.1：这四个属性明确**不**进签名哈希（镜像数据完整性由镜像哈希保证）
EXCLUDED_DATA_PROPS = ("data", "data-size", "data-position", "data-offset")

# §7.3.1：这三个属性不是"镜像引用"，不参与 node list 的构造
NON_REFERENCE_PROPS = ("description", "compatible", "default")


def _u32be(b):
    return int.from_bytes(b, "big") if len(b) == 4 else None


def _str(prop):
    return prop.rstrip(b"\0").decode("utf-8", "replace")


def validate(tree):
    """返回 (errors, warnings)。errors 非空即不是一个合规的 FIT。"""
    errs = []

    if "timestamp" not in tree.props:
        errs.append("root: missing mandatory property 'timestamp'")

    images = tree.children.get("images")
    configs = tree.children.get("configurations")
    if images is None:
        errs.append("root: missing mandatory node '/images'")
    if configs is None:
        errs.append("root: missing mandatory node '/configurations'")
    if images is None or configs is None:
        return errs
    if not images.children:
        errs.append("/images: at least one sub-image is required")
    if not configs.children:
        errs.append("/configurations: at least one configuration node is required")

    uses_addr = False
    for name, img in images.children.items():
        for p in ("description", "type", "arch", "os", "compression"):
            if p not in img.props:
                errs.append("/images/%s: missing mandatory property '%s'" % (name, p))
        if "load" in img.props or "entry" in img.props:
            uses_addr = True
        for hname, h in img.children.items():
            if "algo" not in h.props:
                errs.append("/images/%s/%s: hash node missing 'algo'" % (name, hname))
            elif "value" not in h.props:
                errs.append("/images/%s/%s: hash node missing 'value'" % (name, hname))
            else:
                want = HASH_ALGO_SIZE.get(_str(h.props["algo"]))
                if want is None:
                    errs.append("/images/%s/%s: unknown hash algo %r"
                                % (name, hname, _str(h.props["algo"])))
                elif len(h.props["value"]) != want:
                    errs.append("/images/%s/%s: %s value must be %d bytes, got %d"
                                % (name, hname, _str(h.props["algo"]), want,
                                   len(h.props["value"])))
    if uses_addr and "#address-cells" not in tree.props:
        errs.append("root: '#address-cells' is conditionally mandatory when "
                    "load/entry are used")

    for name, cfg in configs.children.items():
        if "description" not in cfg.props:
            errs.append("/configurations/%s: missing mandatory 'description'" % name)
        if "kernel" not in cfg.props and "firmware" not in cfg.props:
            errs.append("/configurations/%s: one of 'kernel'/'firmware' is required" % name)
        for sname, sig in cfg.children.items():
            for p in ("algo", "key-name-hint", "value"):
                if p not in sig.props:
                    errs.append("/configurations/%s/%s: missing '%s'" % (name, sname, p))
            if "algo" in sig.props:
                sig_algo = _str(sig.props["algo"]).split(",")[-1]
                want = SIG_ALGO_SIZE.get(sig_algo)
                if want is not None and "value" in sig.props and \
                        len(sig.props["value"]) != want:
                    errs.append("/configurations/%s/%s: %s signature must be %d bytes"
                                % (name, sname, sig_algo, want))
    return errs


def referenced_images(cfg, images):
    """§7.3.1：配置结点里除 description/compatible/default 之外的每个字符串属性
    都被当作镜像引用；引用不到对应结点**不算错**，直接跳过。"""
    out = []
    for pname, pval in cfg.props.items():
        if pname in NON_REFERENCE_PROPS:
            continue
        for ref in _str(pval).split(","):
            ref = ref.strip()
            if ref and ref in images.children:
                out.append(ref)
    return out


def node_list(config_name, images, tree):
    """§7.3.1 的 node list：根 + 配置 + 被引镜像 + 这些镜像的 hash 子结点。"""
    cfg = tree.children["configurations"].children[config_name]
    lst = {"/", "/configurations/" + config_name}
    for ref in referenced_images(cfg, images):
        ipath = "/images/" + ref
        lst.add(ipath)
        for hname in images.children[ref].children:
            lst.add(ipath + "/" + hname)
    return lst


def hashed_struct(blob, node_list_):
    """§7.3：按 token 逐个决定包含/排除，返回参与哈希的字节串。

    FDT_BEGIN_NODE / FDT_END_NODE：结点自己或其父在 node list 里就包含
    FDT_PROP：结点自己在 list 里 **且** 属性名不在四个排除项里才包含
    FDT_NOP：所在结点自己在 list 里才包含
    FDT_END：总是包含
    """
    _root, tokens, strings, _hdr = parse_fdt(blob)
    out = bytearray()
    stack = []          # [(path, 该结点自己是否在 node list 里)]
    for t in tokens:
        chunk = blob[t.start:t.end]
        if t.kind == FDT_BEGIN_NODE:
            if not stack:
                cur_path, parent = "/", None
            else:
                parent = stack[-1][0]
                cur_path = parent.rstrip("/") + "/" + t.name
            self_in = cur_path in node_list_
            parent_in = (parent in node_list_) if parent is not None else False
            if self_in or parent_in:
                out.extend(chunk)
            stack.append((cur_path, self_in))
        elif t.kind == FDT_END_NODE:
            _path, self_in = stack.pop()
            parent = stack[-1][0] if stack else None
            if self_in or ((parent in node_list_) if parent is not None else False):
                out.extend(chunk)
        elif t.kind == FDT_PROP:
            self_in = stack[-1][1] if stack else False
            if self_in and t.name not in EXCLUDED_DATA_PROPS:
                out.extend(chunk)
        elif t.kind == FDT_NOP:
            self_in = stack[-1][1] if stack else False
            if self_in:
                out.extend(chunk)
        elif t.kind == FDT_END:
            out.extend(chunk)
    return bytes(out)


def hashed_region(blob, node_list_, strings_start=0, strings_len=None):
    """§7.3：结构块选区 + strings 块区间（区间由 signature 的 hashed-strings 记录）。"""
    _root, _tokens, strings, _hdr = parse_fdt(blob)
    if strings_len is None:
        strings_len = len(strings)
    return hashed_struct(blob, node_list_) + strings[strings_start:strings_start + strings_len]


def image_hash(algo, data):
    """§7.3.3：镜像哈希只覆盖该镜像的 data 属性值，不含任何 FDT 元数据。"""
    return hashlib.new(algo, data).digest()


def sig_digest(blob, config_name, images, tree, algo="sha256"):
    return hashlib.new(algo, hashed_region(blob, node_list(config_name, images, tree))).digest()


def make_sample_fit():
    """构造一个能通过 validate() 的最小 FIT，供自检与 main 演示。"""
    root = Node()
    root.props["description"] = b"demo fit\0"
    root.props["timestamp"] = (1690000000).to_bytes(4, "big")
    root.props["#address-cells"] = (1).to_bytes(4, "big")

    images = Node()
    root.children["images"] = images
    kern = Node()
    kern.props["description"] = b"kernel\0"
    kern.props["data"] = b"\x90" * 64
    kern.props["type"] = b"kernel\0"
    kern.props["arch"] = b"arm\0"
    kern.props["os"] = b"linux\0"
    kern.props["compression"] = b"none\0"
    kern.props["load"] = (0x60000000).to_bytes(4, "big")
    kern.props["entry"] = (0x60000000).to_bytes(4, "big")
    khash = Node()
    khash.props["algo"] = b"sha256\0"
    khash.props["value"] = image_hash("sha256", kern.props["data"])
    kern.children["hash-1"] = khash
    images.children["kernel-1"] = kern

    fdt = Node()
    fdt.props["description"] = b"fdt\0"
    fdt.props["data"] = b"\xd0\x0d\xfe\xed" + b"\x11" * 60
    fdt.props["type"] = b"flat_dt\0"
    fdt.props["arch"] = b"arm\0"
    fdt.props["os"] = b"linux\0"
    fdt.props["compression"] = b"none\0"
    fh = Node()
    fh.props["algo"] = b"sha1\0"
    fh.props["value"] = image_hash("sha1", fdt.props["data"])
    fdt.children["hash-1"] = fh
    images.children["fdt-1"] = fdt

    configs = Node()
    root.children["configurations"] = configs
    configs.props["default"] = b"conf-1\0"
    conf = Node()
    conf.props["description"] = b"demo config\0"
    conf.props["kernel"] = b"kernel-1\0"
    conf.props["fdt"] = b"fdt-1\0"
    sig = Node()
    sig.props["algo"] = b"sha256,rsa2048\0"
    sig.props["key-name-hint"] = b"dev\0"
    sig.props["value"] = b"\x5a" * 256
    sig.props["sign-images"] = b"kernel-1\0"
    conf.children["signature-1"] = sig
    configs.children["conf-1"] = conf
    return root

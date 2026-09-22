"""demo591 演示入口：造一个 uImage + 造一个 FIT，然后按官方口径读回来。"""

import uimage
from uimage import LegacyHeader, IH_OS, IH_ARCH, IH_TYPE, IH_COMP, parse_legacy
from fit import make_sample_fit, validate, node_list, image_hash
from fdt import build_fdt, parse_fdt


def show_legacy():
    payload = bytes(range(256))
    h = LegacyHeader(time=1700000000, load=0x80008000, ep=0x80008000,
                     os=IH_OS["linux"], arch=IH_ARCH["arm"],
                     type=IH_TYPE["kernel"], comp=IH_COMP["gzip"],
                     name=b"Linux-6.6.0")
    h.seal(payload)
    blob = h.pack() + payload
    p = parse_legacy(blob)
    print("== legacy uImage ==")
    print("  magic  0x%08x  os=%d arch=%d type=%d comp=%d" %
          (p.magic, p.os, p.arch, p.type, p.comp))
    print("  load=0x%08x entry=0x%08x size=%d name=%r" %
          (p.load, p.ep, p.size, p.name.rstrip(b"\0")))
    print("  hcrc ok=%s  dcrc ok=%s  total=%d" %
          (uimage.check_hcrc(p), uimage.check_dcrc(p, payload), len(blob)))
    return blob


def show_fit():
    root = make_sample_fit()
    blob = build_fdt(root)
    tree, _tokens, _strings, _hdr = parse_fdt(blob)
    errs = validate(tree)
    imgs = tree.children["images"]
    nl = node_list("conf-1", imgs, tree)
    print("== FIT ==")
    print("  size=%d  validate errors=%s" % (len(blob), errs or "none"))
    print("  images=%s  default=%r" %
          (sorted(imgs.children),
           tree.children["configurations"].props["default"].rstrip(b"\0")))
    print("  node list for conf-1:")
    for p in sorted(nl):
        print("    ", p)
    kd = tree.child("images", "kernel-1").props["data"]
    print("  kernel sha256=%s" % image_hash("sha256", kd).hex()[:32])


if __name__ == "__main__":
    show_legacy()
    show_fit()

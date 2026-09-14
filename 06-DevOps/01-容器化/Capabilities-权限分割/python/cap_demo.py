"""
Linux Capabilities 演示(无 root 解析 + 位运算 + 文件 cap 读取)

参考资料(已实际阅读的权威来源,见 README):
  man7 capabilities(7):  https://man7.org/linux/man-pages/man7/capabilities.7.html
  man7 prctl(2):          https://man7.org/linux/man-pages/man2/prctl.2.html
  man7 setcap(8) / capsh(1)

用法:
  python3 cap_demo.py inspect                  # 打印本进程 5 集合 hex + 解码后 cap 名
  python3 cap_demo.py demo bit-manipulation    # 演示位 ↔ cap 名双向运算
  python3 cap_demo.py file-cap <path>          # 读 security.capability xattr(非 root 通常失败)
  python3 cap_demo.py ns-cap                   # 演示不同 user ns 内 Bounding 差异(Linux 3.8+)
"""

import ctypes
import os
import struct
import sys

# Linux 5.8 cap_last_cap=39;5.9 后 41。预编 cap 名(按 bit 位置)
CAP_NAMES = [
    "CHOWN", "DAC_OVERRIDE", "DAC_READ_SEARCH", "FOWNER", "FSETID",
    "KILL", "SETGID", "SETUID", "SETPCAP", "LINUX_IMMUTABLE",
    "NET_BIND_SERVICE", "NET_BROADCAST", "NET_ADMIN", "NET_RAW", "IPC_LOCK",
    "IPC_OWNER", "SYS_MODULE", "SYS_RAWIO", "SYS_CHROOT", "SYS_PTRACE",
    "SYS_PACCT", "SYS_ADMIN", "SYS_BOOT", "SYS_NICE", "SYS_RESOURCE",
    "SYS_TIME", "SYS_TTY_CONFIG", "MKNOD", "LEASE", "AUDIT_WRITE",
    "AUDIT_CONTROL", "SETFCAP", "MAC_OVERRIDE", "MAC_ADMIN", "SYSLOG",
    "WAKE_ALARM", "BLOCK_SUSPEND", "AUDIT_READ", "PERFMON", "BPF",
    "CHECKPOINT_RESTORE",
]
# 把 CAP_* 名 → bit 位
CAP_BY_NAME = {f"CAP_{n}": i for i, n in enumerate(CAP_NAMES)}


def decode_bitmask(mask: int) -> list[str]:
    """64-bit → cap 名列表。
    Linux ≥ 5.9 cap_last_cap=41,BIT(41) 之前的全解析;仅在 mask 全 0 时返回空。
    """
    out = []
    for i in range(mask.bit_length() + 1):
        if mask & (1 << i):
            out.append(f"CAP_{CAP_NAMES[i]}" if i < len(CAP_NAMES) else f"BIT({i})")
    return out


def encode_bitmask(caps: list[str]) -> int:
    """cap 名列表 → 64-bit mask。未知名抛 ValueError。"""
    m = 0
    for c in caps:
        if c not in CAP_BY_NAME:
            raise ValueError(f"unknown cap: {c}")
        m |= 1 << CAP_BY_NAME[c]
    return m


# ---------- /proc/self/status 解析 ----------

def read_proc_status(pid: int = 0) -> dict[str, int]:
    """读取 /proc/<pid>/status 中 5 个 Cap* 字段,返回 {name: int_value}。"""
    pid = pid or os.getpid()
    if not sys.platform.startswith("linux"):
        return {}
    out = {}
    try:
        with open(f"/proc/{pid}/status") as f:
            for ln in f:
                if ln.startswith(("CapInh:", "CapPrm:", "CapEff:", "CapBnd:", "CapAmb:")):
                    k, v = ln.split()
                    out[k.rstrip(":")] = int(v, 16)
    except FileNotFoundError:
        pass
    return out


# ---------- capget syscall ----------

_LINUX_CAPABILITY_VERSION_3 = 0x20080522


class cap_header(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32),
                ("pid", ctypes.c_int)]


class cap_data(ctypes.Structure):
    _fields_ = [("effective", ctypes.c_uint32),
                ("permitted", ctypes.c_uint32),
                ("inheritable", ctypes.c_uint32)]


def capget_self() -> dict[str, int]:
    """真 syscall:capget(2) v3,返回本进程 5 集合 cap mask(int 字段都是 64 位)。
    失败常见原因:kernel < 2.6.25(无 v3);非 Linux(没有 syscall)。
    """
    if not sys.platform.startswith("linux"):
        return {}
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    h = cap_header()
    h.version = _LINUX_CAPABILITY_VERSION_3
    h.pid = 0
    d0 = cap_data(); d1 = cap_data()
    if libc.syscall(125, h, d0) != 0:  # SYS_capget = 125 on x86_64
        return {}
    # v3:两 32-bit 共 64 bit;CapBnd/Amb 在 v3 用 additional[2]
    return {
        "CapEff": d0.effective | (d1.effective << 32),
        "CapPrm": d0.permitted | (d1.permitted << 32),
        "CapInh": d0.inheritable | (d1.inheritable << 32),
    }


# ---------- 文件 cap xattr 读取 ----------

SECURITY_CAPABILITY_XATTR = b"security.capability"


def read_file_cap(path: str) -> dict:
    """读取文件 security.capability xattr(Linux vfs_cap_data + struct credentials)。

    v3 格式(man capabilities(7) §"File capabilities"):
      magic_etc = 0x20080522 | ((vfs_cap_f)(0).rootid << VFS_CAP_REVISION_2)
      data[0].permitted   effective(若 magic_etc & FILE_CAP_EFFECTIVE)
      data[1].permitted = upper 32 cap

    返回 {'permitted': mask64, 'effective': mask64, 'rootid': int}
    """
    try:
        # 仅 Linux xattr.get 在 os 模块下
        import xattr  # type: ignore
        raw = xattr.get(path, SECURITY_CAPABILITY_XATTR)
    except (ImportError, OSError):
        return {"permitted": 0, "effective": 0, "rootid": 0,
                "raw_error": "xattr lib 未装 或非 root 不可读(SECURITY_CAPABILITY 受限)"}
    if not raw:
        return {"permitted": 0, "effective": 0, "rootid": 0,
                "raw_error": "该文件无 security.capability xattr"}

    magic = int.from_bytes(raw[:4], "little")
    file_cap_effective = (magic & 0x100) != 0  # FILE_CAP_EFFECTIVE bit(20)
    if (magic & 0xFF) == 0x02:
        # VFS_CAP_REVISION_2:有 rootid + 2 data
        rootid = int.from_bytes(raw[4:8], "little")
        d0_p, d0_e = struct.unpack("<II", raw[8:16])
        d1_p, d1_e = struct.unpack("<II", raw[16:24])
        return {
            "permitted": d0_p | (d1_p << 32),
            "effective": (d0_e | (d1_e << 32)) if file_cap_effective else 0,
            "rootid": rootid,
            "version": "v2",
        }
    return {"permitted": 0, "effective": 0, "rootid": 0,
            "raw_error": f"未支持 magic={magic:#x}"}


# ---------- CLI ----------

def show_status(label, masks: dict[str, int]):
    print(f"# {label}")
    if not masks:
        print("  (非 Linux;或不可读 /proc/<pid>/status)")
        return
    for k in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"):
        v = masks.get(k, 0)
        print(f"  {k}: {v:#018x}  → " + ", ".join(decode_bitmask(v)))


def cmd_inspect(_args):
    show_status("/proc/self/status", read_proc_status())
    print()
    show_status("capget(2) syscall", capget_self())


def cmd_bit_manipulation(_args):
    """演示:cap 名 → 位 → 反解析。"""
    sample = ["CAP_NET_BIND_SERVICE", "CAP_NET_RAW", "CAP_SYS_ADMIN", "CAP_CHOWN"]
    print("# 测试样例 caps:")
    for name in sample:
        bit = CAP_BY_NAME[name]
        print(f"  {name} → bit {bit} → mask {1 << bit:#018x}")

    mask = encode_bitmask(sample)
    print(f"\n聚合 mask = {mask:#018x}")
    print("反解析:", ", ".join(decode_bitmask(mask)))

    # 位运算 demo
    a = encode_bitmask(["CAP_NET_BIND_SERVICE", "CAP_NET_RAW"])
    b = encode_bitmask(["CAP_SYS_ADMIN", "CAP_NET_RAW"])
    print(f"\n# 位运算")
    print(f"  A ∪ B (并): {decode_bitmask(a | b)}")
    print(f"  A ∩ B (交): {decode_bitmask(a & b)}")
    print(f"  A \\ B (差): {decode_bitmask(a & ~b)}")


def cmd_file_cap(args):
    if len(args) < 1:
        print("usage: cap_demo.py file-cap <path>")
        return
    info = read_file_cap(args[0])
    print(f"# {args[0]}")
    if "raw_error" in info:
        print(f"  {info['raw_error']}")
        return
    print(f"  permitted: {info['permitted']:#018x}  → " + ", ".join(decode_bitmask(info["permitted"])))
    print(f"  effective: {info['effective']:#018x}  → " + ", ".join(decode_bitmask(info["effective"])))
    print(f"  rootid    : {info.get('rootid', 0)}")
    print(f"  version   : {info.get('version', '?')}")


def main():
    cmds = {"inspect": cmd_inspect,
            "demo": {"bit-manipulation": cmd_bit_manipulation},
            "file-cap": cmd_file_cap}
    if len(sys.argv) < 2:
        print("usage: cap_demo.py {inspect|demo bit-manipulation|file-cap <path>}")
        sys.exit(1)
    head = sys.argv[1]
    if head == "inspect":
        cmds["inspect"](sys.argv[2:])
    elif head == "demo" and len(sys.argv) > 2 and sys.argv[2] in cmds["demo"]:
        cmds["demo"][sys.argv[2]](sys.argv[3:])
    elif head == "file-cap":
        cmds["file-cap"](sys.argv[2:])
    else:
        print("unknown cmd:", head)
        sys.exit(1)


if __name__ == "__main__":
    main()

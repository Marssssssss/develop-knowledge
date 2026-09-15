#!/usr/bin/env python3
"""user namespace 的 UID/GID 映射模型。

规则依据 man7.org 的 user_namespaces(7) 与 newuidmap(1):
  https://man7.org/linux/man-pages/man7/user_namespaces.7.html
  https://man7.org/linux/man-pages/man1/newuidmap.1.html

被建模的规则(编号对应 README「原理详解」):
  R1 行格式:ID-inside-ns / ID-outside-ns / size          R2 只能写一次,否则 EPERM
  R3 须换行结尾、须偏移 0、总字节 < 一页、至少一行,否则 EINVAL
  R4 行数上限:Linux 4.16+ 为 340 行,更早为 5 行         R5 区间不得重叠
  R6 无特权写入者只能一行且必须映射自身 euid              R7 写 gid_map 前须 deny setgroups
  R8 Linux 5.12 起映射到父 ns 的 UID 0 需 CAP_SETFCAP
  R9 未映射 ID 表现为 overflow 65534,但读 uid_map 第二字段显示 4294967295
  R10 嵌套最多 32 层,超出 EUSERS

自检:python3 uid_map_model.py
"""
from __future__ import annotations

PAGE_SIZE = 4096
MAX_LINES_MODERN = 340     # Linux 4.16+
MAX_LINES_LEGACY = 5       # Linux 4.14 之前
OVERFLOW_ID = 65534        # /proc/sys/kernel/overflowuid 默认值
NO_ID = 4294967295         # (uid_t)-1:uid_map 里"未映射"的表示
MAX_NESTING = 32           # Linux 3.11+

class MapError(Exception):
    """携带 errno 名字的映射错误。"""

    def __init__(self, errno_name: str, msg: str) -> None:
        super().__init__(f"{errno_name}: {msg}")
        self.errno_name = errno_name

# 一行 = (ID-inside-ns, ID-outside-ns, size)
Entry = tuple[int, int, int]


def parse(text: str) -> list[Entry]:
    out: list[Entry] = []
    for line in text.split("\n"):
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 3:
            raise MapError("EINVAL", f"每行必须是三个数字,收到 {line!r}")
        out.append((int(parts[0]), int(parts[1]), int(parts[2])))
    return out

class UidMap:
    """一个用户命名空间的 uid_map / gid_map。"""

    def __init__(self, name: str, is_gid: bool = False, kernel_minor: int = 16) -> None:
        self.name = name
        self.is_gid = is_gid
        self.kernel_minor = kernel_minor      # 用于演示 4.16 前后的行数上限差异
        self.entries: list[Entry] | None = None
        self.written = False
        self.setgroups_denied = False

    # ---- R2/R3/R4/R5/R6/R7/R8 --------------------------------------------
    def write(self, text: str, *, offset: int = 0,
              writer_caps_in_parent: bool = False,
              writer_euid_in_parent: int = 1000,
              creator_euid_in_parent: int = 1000,
              has_cap_setfcap: bool = False) -> None:
        if self.written:
            raise MapError("EPERM", "uid_map/gid_map 只能写一次")           # R2
        if offset != 0:
            raise MapError("EINVAL", "必须从偏移 0 写,不支持 pwrite/lseek")  # R3
        if not text.endswith("\n"):
            raise MapError("EINVAL", "写入必须以换行符结尾")                 # R3
        raw = text.encode()
        if len(raw) > PAGE_SIZE:
            raise MapError("EINVAL", f"写入 {len(raw)} 字节 > 一页 {PAGE_SIZE}")  # R3
        entries = parse(text)
        if not entries:
            raise MapError("EINVAL", "至少要写一行")                         # R3
        limit = MAX_LINES_MODERN if self.kernel_minor >= 16 else MAX_LINES_LEGACY
        if len(entries) > limit:
            raise MapError("EINVAL", f"{len(entries)} 行超过内核上限 {limit}")  # R4
        self._check_overlap(entries)                                        # R5

        if not writer_caps_in_parent:
            # R6:无特权写入者只能写一行,且必须映射自身有效 UID
            if len(entries) != 1:
                raise MapError("EPERM", "无 CAP_SETUID 时只允许写一行")
            inside, outside, size = entries[0]
            if size != 1:
                raise MapError("EPERM", "无特权写入时区间长度必须为 1")
            if outside != writer_euid_in_parent:
                raise MapError("EPERM", "无特权写入只能映射自身的有效 UID")
            if writer_euid_in_parent != creator_euid_in_parent:
                raise MapError("EPERM", "写入者与命名空间创建者的有效 UID 必须相同")
            if self.is_gid and not self.setgroups_denied:
                raise MapError("EPERM", "写 gid_map 前必须先向 setgroups 写 deny")  # R7
        if any(outside == 0 for _i, outside, _s in entries) and not has_cap_setfcap:
            raise MapError("EPERM", "映射到父 ns 的 UID 0 需要 CAP_SETFCAP")   # R8
        self.entries = entries
        self.written = True

    @staticmethod
    def _check_overlap(entries: list[Entry]) -> None:
        for space in (0, 1):                     # 0=inside 空间, 1=outside 空间
            spans = sorted((e[space], e[space] + e[2]) for e in entries)
            for (s0, e0), (s1, _e1) in zip(spans, spans[1:]):
                if s1 < e0:
                    raise MapError("EINVAL", f"区间重叠: [{s0},{e0}) 与起点 {s1}")

    # ---- R9 双向翻译 -----------------------------------------------------
    def to_outside(self, inside_id: int) -> int:
        for i, o, size in self.entries or []:
            if i <= inside_id < i + size:
                return o + (inside_id - i)
        return OVERFLOW_ID

    def from_outside(self, outside_id: int) -> int:
        for i, o, size in self.entries or []:
            if o <= outside_id < o + size:
                return i + (outside_id - o)
        return OVERFLOW_ID

    def second_field_or_no_id(self, inside_id: int) -> int:
        """R9 的例外:读 uid_map 第二字段时,未映射显示 4294967295 而非 65534。"""
        for i, o, size in self.entries or []:
            if i <= inside_id < i + size:
                return o + (inside_id - i)
        return NO_ID

    def set_deny(self) -> None:
        self.setgroups_denied = True

    @classmethod
    def pseudo(cls, name: str, entries: list[Entry]) -> "UidMap":
        """内核为初始命名空间直接给出的映射,不是被"写"出来的,故绕过写入校验。

        官方原文:`cat /proc/$$/uid_map` 在初始命名空间里得到 `0 0 4294967295`。
        """
        m = cls(name)
        m.entries = entries
        m.written = True
        return m

def to_host(chain: list[UidMap], uid: int) -> int:
    """把最内层命名空间的 UID 逐层翻译到初始命名空间。

    chain[0] 是最内层(它的 outside 空间 = chain[1] 的 inside 空间)。
    任一层未映射即返回 overflow 65534 —— 逐层独立判定的结果。
    """
    cur = uid
    for ns in chain:
        cur = ns.to_outside(cur)
        if cur == OVERFLOW_ID:
            return OVERFLOW_ID
    return cur

def check_nesting(depth: int) -> None:
    if depth > MAX_NESTING:
        raise MapError("EUSERS", f"嵌套 {depth} 层超过内核上限 {MAX_NESTING}")  # R10

# --------------------------------------------------------------------------
# 自检
# --------------------------------------------------------------------------
OK = 0
FAIL = 0

def check(label: str, cond: bool, detail: str = "") -> None:
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  [PASS] {label} {detail}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")

def expect_error(label: str, errno_name: str, fn, *a, **kw) -> None:
    try:
        fn(*a, **kw)
        check(label, False, "没有报错")
    except MapError as exc:
        check(label, exc.errno_name == errno_name, str(exc))

def main() -> int:
    # 官方 newuidmap(1) / rootless 文档里的真实数字:testuser:231072:65536
    SUB_START, SUB_COUNT = 231072, 65536

    print("=== 1. 单项映射与边界(官方 subuid 示例 testuser:231072:65536) ===")
    ns = UidMap("ns1")
    ns.write(f"0 {SUB_START} {SUB_COUNT}\n", writer_caps_in_parent=True)
    check("容器 uid 0 -> 宿主 231072", ns.to_outside(0) == 231072, str(ns.to_outside(0)))
    check("区间末位 65535 -> 宿主 296607", ns.to_outside(65535) == SUB_START + SUB_COUNT - 1)
    check("越界 65536 -> overflow 65534", ns.to_outside(SUB_COUNT) == OVERFLOW_ID)
    print("\n=== 2. stat(2) 视角的反向映射 ===")
    check("宿主 231072 -> 容器 0", ns.from_outside(231072) == 0)
    check("宿主 296607 -> 容器 65535", ns.from_outside(296607) == 65535)
    check("宿主 231071(未映射) -> overflow", ns.from_outside(231071) == OVERFLOW_ID)
    print("\n=== 3. 初始命名空间的伪映射与 -1 例外(R9) ===")
    init = UidMap.pseudo("init", [(0, 0, NO_ID)])
    check("4294967295 特意不映射((uid_t)-1 另有含义)",
          init.to_outside(NO_ID) == OVERFLOW_ID and init.to_outside(NO_ID - 1) == NO_ID - 1)
    check("读 uid_map 第二字段未映射时显示 4294967295 而非 65534",
          init.second_field_or_no_id(NO_ID) == NO_ID,
          str(init.second_field_or_no_id(NO_ID)))
    print("\n=== 4. 只能写一次(R2) ===")
    expect_error("二次写入 -> EPERM", "EPERM", ns.write, "0 0 1\n", writer_caps_in_parent=True)
    print("\n=== 5. 写入格式与上限(R3/R4/R5) ===")
    W = {"writer_caps_in_parent": True}

    def fresh(minor: int = 16) -> UidMap:
        return UidMap("t", kernel_minor=minor)

    expect_error("不以换行结尾 -> EINVAL", "EINVAL", fresh().write, "0 1000 1", **W)
    expect_error("非零偏移写 -> EINVAL", "EINVAL", fresh().write, "0 1000 1\n", offset=8, **W)
    expect_error("空内容 -> EINVAL", "EINVAL", fresh().write, "\n", **W)
    expect_error("区间重叠 -> EINVAL", "EINVAL", fresh().write, "0 1000 1\n0 2000 1\n", **W)
    expect_error("inside 空间重叠 -> EINVAL", "EINVAL", fresh().write,
                 "0 1000 100\n50 2000 100\n", **W)
    # 行数上限与页大小上限互相挤压:340 行只有每行 <= 12 字节时才塞得进一页
    long341 = "".join(f"{i} {100000 + i * 10} 1\n" for i in range(341))
    expect_error("341 行 -> EINVAL", "EINVAL", fresh(16).write, long341, **W)
    check("(上面先被页大小拦下)", len(long341.encode()) > PAGE_SIZE, f"{len(long341.encode())} B")
    compact340 = "".join(f"{i} {i} 1\n" for i in range(1, 341))   # 从 1 起编号以避开 R8
    check("紧凑写法 340 行才塞得进一页", len(compact340.encode()) <= PAGE_SIZE)
    fresh(16).write(compact340, **W)
    check("340 行(4.16+)合法", True)
    long340 = "".join(f"{i} {100000 + i * 10} 1\n" for i in range(340))
    expect_error("340 行但每行太长 -> 仍 EINVAL", "EINVAL", fresh(16).write, long340, **W)
    expect_error("6 行(4.14 之前) -> EINVAL", "EINVAL", fresh(14).write,
                 "".join(f"{i} {5000 + i} 1\n" for i in range(6)), **W)
    fresh(14).write("".join(f"{i} {5000 + i} 1\n" for i in range(5)), **W)
    check("5 行(4.14 之前)合法", True)
    print("\n=== 6. 无特权写入者的单行限制(R6) ===")
    U = {"writer_caps_in_parent": False, "writer_euid_in_parent": 1000}
    expect_error("无特权写两行 -> EPERM", "EPERM", UidMap("u").write,
                 "0 1000 1\n1 2000 1\n", **U)
    expect_error("无特权映射别人的 UID -> EPERM", "EPERM", UidMap("u").write, "0 2000 1\n", **U)
    expect_error("无特权映射自身但区间 len!=1 -> EPERM", "EPERM",
                 UidMap("u").write, "0 1000 10\n", **U)
    expect_error("写入者 euid 与创建者不同 -> EPERM", "EPERM", UidMap("u").write,
                 "0 1000 1\n", creator_euid_in_parent=1001, **U)
    ok = UidMap("u")
    ok.write("0 1000 1\n", **U)
    check("无特权单行映射自身 -> 成功", ok.written)
    check("inside 0 对应父 ns 的 1000(rootless 的 userns-remap 核心)",
          ok.to_outside(0) == 1000 and ok.to_outside(1) == OVERFLOW_ID)
    print("\n=== 7. gid_map 需要先写 setgroups=deny(R7) ===")
    expect_error("未 deny setgroups -> EPERM", "EPERM",
                 UidMap("g", is_gid=True).write, "0 1000 1\n",
                 writer_caps_in_parent=False, writer_euid_in_parent=1000)
    g = UidMap("g", is_gid=True)
    g.set_deny()
    g.write("0 1000 1\n", writer_caps_in_parent=False, writer_euid_in_parent=1000)
    check("deny 后可以写 gid_map", g.written)
    print("\n=== 8. 映射到父 ns 的 UID 0 需要 CAP_SETFCAP(R8) ===")
    expect_error("缺 CAP_SETFCAP -> EPERM", "EPERM",
                 UidMap("f").write, "0 0 1\n", writer_caps_in_parent=True)
    f = UidMap("f")
    f.write("0 0 1\n", writer_caps_in_parent=True, has_cap_setfcap=True)
    check("带 CAP_SETFCAP -> 成功", f.to_outside(0) == 0)
    print("\n=== 9. 嵌套三层:逐层翻译 ===")
    n1 = UidMap("ns1")            # ns1 内 0 -> 宿主 231072,共 65536 个
    n1.write(f"0 {SUB_START} {SUB_COUNT}\n", writer_caps_in_parent=True)
    n2 = UidMap("ns2")            # ns2 内 0 -> ns1 内 0,共 1000 个
    # R8 在嵌套里的实际含义:把 ns2 的 root 映射到 ns1 的 root,要求写入者
    # 在 ns1 里具备 CAP_SETFCAP。ns1 的拥有者在 ns1 内拥有完整能力集,故成立;
    # 若没有(5.12 之前的漏洞场景),这一步必须被拒。
    expect_error("缺 CAP_SETFCAP 时嵌套映射到父 ns 的 0 -> EPERM", "EPERM",
                 UidMap("ns2").write, "0 0 1000\n", writer_caps_in_parent=True)
    n2.write("0 0 1000\n", writer_caps_in_parent=True, has_cap_setfcap=True)
    host = UidMap.pseudo("host", [(0, 0, NO_ID)])
    check("ns2 uid 0 -> ns1 0 -> 宿主 231072",
          to_host([n2, n1, host], 0) == SUB_START, str(to_host([n2, n1, host], 0)))
    check("ns2 uid 999 -> ns1 999 -> 宿主 232071",
          to_host([n2, n1, host], 999) == SUB_START + 999)
    check("ns2 uid 1000(本层越界)-> overflow", to_host([n2, n1, host], 1000) == OVERFLOW_ID)
    check("嵌套深度 32 允许", check_nesting(32) is None)
    expect_error("嵌套深度 33 -> EUSERS", "EUSERS", check_nesting, 33)
    print("\n=== 10. 未映射 UID 的 setuid 位被静默忽略 ===")
    files = [{"path": "/bin/ping", "uid": 1000, "gid": 1000, "setuid": True}]
    def effective_setuid(file, ns: UidMap) -> bool:
        # 手册:文件 UID/GID 在命名空间内无映射时,set-user-ID 位被静默忽略
        return file["setuid"] and ns.from_outside(file["uid"]) != OVERFLOW_ID
    check("宿主 uid 1000 在 ns1(231072 起)内无映射 -> setuid 失效",
          effective_setuid(files[0], ns) is False)
    mapped = [{"path": "/bin/ping", "uid": 231072, "gid": 231072, "setuid": True}]
    check("宿主 uid 231072 有映射 -> setuid 生效",
          effective_setuid(mapped[0], ns) is True)

    print(f"\n合计:{OK} passed / {FAIL} failed")
    return 1 if FAIL else 0

if __name__ == "__main__":
    raise SystemExit(main())

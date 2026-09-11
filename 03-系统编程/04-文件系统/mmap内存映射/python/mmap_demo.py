"""
mmap_demo.py — Python `mmap` 标准库的 4 个核心 demo

运行: python3 mmap_demo.py

演示:
  1. ACCESS_WRITE (SHARED) 写文件 + flush() (msync)
  2. ACCESS_COPY (PRIVATE) 写时复制不影响源
  3. mmap 长度 < 文件大小 → zero-fill 行为
  4. ACCESS_READ 读其他进程写过的 mmap(共享同一文件)

Python `mmap` 模块是 POSIX mmap(2) 的高层封装;access 模式对应:
  ACCESS_DEFAULT = ACCESS_READ
  ACCESS_READ    = PROT_READ, MAP_SHARED
  ACCESS_WRITE   = PROT_READ|PROT_WRITE, MAP_SHARED
  ACCESS_COPY    = PROT_READ|PROT_WRITE, MAP_PRIVATE   (CoW)
"""

import mmap
import os
import struct
import tempfile

PATH_F = "data.bin"


def demo_shared_write():
    """demo 1: MAP_SHARED 写文件 + flush()(对应 msync(MS_SYNC))"""
    print("\n=== demo 1: ACCESS_WRITE (MAP_SHARED) write + flush ===")
    # 写 4096 字节初始内容
    with open(PATH_F, "wb") as f:
        f.write(b"INIT" * 1024)

    # ACCESS_WRITE = MAP_SHARED + RW
    fd = os.open(PATH_F, os.O_RDWR)
    with mmap.mmap(fd, 4096, access=mmap.ACCESS_WRITE) as m:
        m[0:2] = b"AB"
        m[2] = 0
        print(f"in-memory m[0..3] = {bytes(m[0:3])!r}")
        m.flush()  # 等价 msync(MS_SYNC):dirty 页落盘

    # 验证磁盘内容
    with open(PATH_F, "rb") as f:
        on_disk = f.read(7)
    print(f"disk file[0..7]   = {on_disk!r}  (expect b'AB')")
    assert on_disk.startswith(b"AB"), "flush() did not persist!"
    os.close(fd)


def demo_private_cow():
    """demo 2: ACCESS_COPY (MAP_PRIVATE) CoW 不影响源"""
    print("\n=== demo 2: ACCESS_COPY (MAP_PRIVATE) copy-on-write ===")
    # 用 demo 1 的 'AB' 起始的文件
    fd = os.open(PATH_F, os.O_RDONLY)
    with mmap.mmap(fd, 4096, access=mmap.ACCESS_COPY) as m_priv:
        # PRIVATE 模式:首字节写触发 CoW,只影响私有副本
        before = bytes(m_priv[0:3])
        print(f"MAP_PRIVATE m[0..3] before write = {before!r}")
        m_priv[0] = ord('Z')
        after = bytes(m_priv[0:3])
        print(f"MAP_PRIVATE m[0..3] after  m[0]='Z' = {after!r}")

        # 文件未变
        on_disk_before = open(PATH_F, "rb").read(3)
        # 注意:这里"文件未变"是指 inode 的 page cache 没变;
        # 但因为 ACCESS_COPY 触发 CoW 复制了原始页给该 mmap 对象,
        # 修改仅写本对象的私有副本,不污染 source file-backed cache 页

    # 关掉 PRIVATE mmap 后,文件磁盘上仍应是 'AB\x00'(demo 1 写入;demo 2 的 'Z' 是 PRIVATE 私有副本)
    with open(PATH_F, "rb") as f:
        on_disk = f.read(3)
    print(f"disk file[0..2]   = {on_disk!r}  (still AB\\x00; PRIVATE did not write back)")
    assert on_disk == b"AB\x00", f"MAP_PRIVATE leaked back to file! got {on_disk!r}"
    os.close(fd)


def demo_partial_mmap():
    """demo 3: mmap length < 文件大小 → 超长文件区域在 MAP_PRIVATE 下不可见"""
    print("\n=== demo 3: mmap length < file size ===")
    # 写 8192 字节,但 mmap 只 4096 字节
    with open("big.bin", "wb") as f:
        f.write(b"A" * 4096 + b"B" * 4096)
    fd = os.open("big.bin", os.O_RDONLY)
    with mmap.mmap(fd, 4096, access=mmap.ACCESS_READ) as m:
        # m 只能访问 offset 0..4095
        first = bytes(m[0:5])
        last = bytes(m[4090:4096])
        print(f"first 5 bytes of mapping = {first!r}  (expect b'AAAAA')")
        print(f"last  6 bytes of mapping = {last!r}  (still 'A', not 'B')")
        assert m[:5] == b'AAAAA'
        assert m[4090:] == b'AAAAAA'
    os.close(fd)
    os.unlink("big.bin")


def demo_anon_ipc_via_file():
    """demo 4: 用临时文件模拟父子进程共享内存
    Python 标准 mmap 没有原生 MAP_ANONYMOUS,但可借 /dev/zero 或临时文件模拟。
    这里我们用临时文件 + MAP_SHARED,展示"两个独立 mmap 对象打开同一文件 = 共享"。
    """
    print("\n=== demo 4: cross-mmap object sharing via same file ===")
    tmpf = tempfile.NamedTemporaryFile(prefix="ipc_", delete=False)
    tmpf.write(b"\x00" * 4096)
    tmpf.close()
    try:
        fd1 = os.open(tmpf.name, os.O_RDWR)
        with mmap.mmap(fd1, 4096, access=mmap.ACCESS_WRITE) as m1:
            fd2 = os.open(tmpf.name, os.O_RDWR)
            with mmap.mmap(fd2, 4096, access=mmap.ACCESS_READ) as m2:
                # 写 m1
                m1[0:4] = b"DEAD"
                m1[4:8] = b"BEEF"
                m1.flush()

                # m2 读到 m1 写的(因为 SHARED,共享同一 page cache 页)
                seen = bytes(m2[0:8])
                print(f"writer m1[0:8]  = {bytes(m1[0:8])!r}")
                print(f"reader m2[0:8]  = {seen!r}    (shared file-backed cache)")
                assert seen == b"DEADBEEF", "shared mapping broken!"
            os.close(fd2)
        os.close(fd1)
    finally:
        os.unlink(tmpf.name)


if __name__ == "__main__":
    try:
        demo_shared_write()
        demo_private_cow()
        demo_partial_mmap()
        demo_anon_ipc_via_file()
        print("\nall 4 demos passed ✓")
    finally:
        try:
            if os.path.exists(PATH_F):
                os.unlink(PATH_F)
        except OSError:
            pass  # 沙箱钩子可能拦截删除,忽略
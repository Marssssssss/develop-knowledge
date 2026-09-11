"""
page_cache_demo.py — Linux page cache + writeback 演示(Linux-only)

运行: python3 page_cache_demo.py

演示:
  1. write() 后 page cache dirty 状态(读 /proc/meminfo 的 Cached/Dirty)
  2. posix_fadvise(POSIX_FADV_DONTNEED) 释放 cache 页(通过 ctypes 调 syscall)
  3. sync_file_range 触发精细 writeback
  4. POSIX_FADV_RANDOM vs SEQUENTIAL 调整预读窗口
"""

import ctypes
import os
import struct

FILE_MB = 16
FILE_SIZE = FILE_MB * 1024 * 1024
HALF_SIZE = FILE_SIZE // 2
PATH_F = "cache_demo.bin"

# Linux x86_64 syscall numbers(用于 ctypes.CDLL(None).syscall)
SYS_FADVISE64    = 272
SYS_SYNC_FILE_RANGE = 84
POSIX_FADV_NORMAL     = 0
POSIX_FADV_RANDOM     = 1
POSIX_FADV_SEQUENTIAL = 2
POSIX_FADV_WILLNEED   = 3
POSIX_FADV_DONTNEED   = 4
POSIX_FADV_NOREUSE    = 5

SYNC_FILE_RANGE_WAIT_BEFORE = 1
SYNC_FILE_RANGE_WRITE       = 2
SYNC_FILE_RANGE_WAIT_AFTER  = 4


def read_meminfo_kb(key):
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith(key):
                return int(line.split()[1])
    return -1


def print_cache_status(label):
    cached = read_meminfo_kb("Cached:")
    dirty = read_meminfo_kb("Dirty:")
    print(f"[{label}] Cached={cached} KB, Dirty={dirty} KB")


def fadvise(fd, off, length, advice):
    """通过 ctypes 调 syscall(SYS_FADVISE64, fd, off, length, advice)"""
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    r = libc.syscall(
        ctypes.c_long(SYS_FADVISE64),
        ctypes.c_int(fd),
        ctypes.c_longlong(off),
        ctypes.c_longlong(length),
        ctypes.c_int(advice),
    )
    if r != 0:
        errno = ctypes.get_errno()
        print(f"  fadvise({advice}) failed: errno={errno} ({os.strerror(errno)})")


def sync_file_range(fd, off, length, flags):
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    r = libc.syscall(
        ctypes.c_long(SYS_SYNC_FILE_RANGE),
        ctypes.c_int(fd),
        ctypes.c_longlong(off),
        ctypes.c_longlong(length),
        ctypes.c_uint(flags),
    )
    if r != 0:
        errno = ctypes.get_errno()
        print(f"  sync_file_range failed: errno={errno} ({os.strerror(errno)})")


def demo1_write_dirty():
    print("\n=== demo 1: write() fills page cache with dirty pages ===")
    print_cache_status("before write")

    with open(PATH_F, "wb") as f:
        f.write(b"\x42" * FILE_SIZE)

    print_cache_status("after write (no sync)")

    os.fsync(os.open(PATH_F, os.O_RDONLY))  # 不需要,直接用 fd 即可
    print_cache_status("after fsync")


def demo2_fadvise_dontneed():
    print("\n=== demo 2: posix_fadvise(POSIX_FADV_DONTNEED) releases pages ===")
    fd = os.open(PATH_F, os.O_RDONLY)

    # 触发整个文件进入 page cache
    fadvise(fd, 0, 0, POSIX_FADV_WILLNEED)
    print_cache_status("after WILLNEED")

    # 释放前 8 MB
    fadvise(fd, 0, HALF_SIZE, POSIX_FADV_DONTNEED)
    print_cache_status("after DONTNEED 0..8MB")

    os.close(fd)


def demo3_sync_file_range():
    print("\n=== demo 3: sync_file_range() fine-grained writeback ===")
    fd = os.open(PATH_F, os.O_RDWR)

    # 制造一批 dirty 页
    os.pwrite(fd, b"X" * 4096, FILE_SIZE - 4096)
    print_cache_status("after pwrite (dirty)")

    # sync_file_range: WRITE 触发异步 writeback
    sync_file_range(fd, 0, FILE_SIZE,
                    SYNC_FILE_RANGE_WAIT_BEFORE |
                    SYNC_FILE_RANGE_WRITE |
                    SYNC_FILE_RANGE_WAIT_AFTER)
    print_cache_status("after sync_file_range(WRITE|WAIT)")

    os.fsync(fd)
    print_cache_status("after fsync")
    os.close(fd)


def demo4_fadvise_readahead():
    print("\n=== demo 4: POSIX_FADV_RANDOM vs SEQUENTIAL readahead ===")
    fd = os.open(PATH_F, os.O_RDONLY)
    fadvise(fd, 0, 0, POSIX_FADV_RANDOM)
    print("set POSIX_FADV_RANDOM  (readahead OFF)")

    # 读 backing device 默认 readahead
    try:
        with open("/sys/block/sda/queue/read_ahead_kb") as f:
            ra = int(f.read().strip())
        print(f"backing device default readahead: {ra} KB "
              f"(RANDOM → 0, SEQUENTIAL → {ra*2} KB)")
    except FileNotFoundError:
        print("(could not read /sys/block/sda/queue/read_ahead_kb on this system)")

    fadvise(fd, 0, 0, POSIX_FADV_SEQUENTIAL)
    print("set POSIX_FADV_SEQUENTIAL  (readahead ×2)")
    fadvise(fd, 0, 0, POSIX_FADV_NORMAL)
    print("set POSIX_FADV_NORMAL  (default readahead)")
    os.close(fd)


if __name__ == "__main__":
    if not os.path.exists("/proc/meminfo"):
        print("This demo requires Linux (/proc/meminfo missing).")
        exit(1)
    try:
        demo1_write_dirty()
        demo2_fadvise_dontneed()
        demo3_sync_file_range()
        demo4_fadvise_readahead()
        os.unlink(PATH_F)
    except OSError:
        pass
    print("\nall 4 demos done")
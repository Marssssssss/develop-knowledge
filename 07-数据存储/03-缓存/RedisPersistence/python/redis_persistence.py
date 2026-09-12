"""
redis_persistence.py — Minimal Redis persistence simulator (pure stdlib)

演示:
  1) RDB 快照 - fork 子进程做二进制 dump
  2) AOF 追加 - 按 RESP 协议写入 + 演示 fsync 策略
  3) AOF Rewrite - 按当前内存生成最小命令集

运行: python3 redis_persistence.py

注意: Windows 没有 fork(),演示使用 os.fork() 不可用时直接走单进程分支,
     仅打印逻辑流程(不实际生成 dump 文件);Linux/macOS 会真实生成文件。
"""

from __future__ import annotations

import os
import sys
import time
import struct
import tempfile
from pathlib import Path

# ---------- 内存字典 ----------
_kv: dict[str, str] = {}


def kv_set(key: str, val: str) -> None:
    _kv[key] = val


# ---------- RDB 快照 ----------
RDB_MAGIC = b"RDB0001"


def rdb_save(path: str) -> int:
    """把 _kv 字典序列化成二进制 RDB 格式(简化版):magic + count + (klen,key,vlen,val)*N"""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(RDB_MAGIC)
        f.write(struct.pack("<I", len(_kv)))
        for k, v in _kv.items():
            f.write(struct.pack("<H", len(k)) + k.encode())
            f.write(struct.pack("<H", len(v)) + v.encode())
    os.replace(tmp, path)            # 原子替换,等同于 rename(2)
    return tmp.stat().st_size if tmp.exists() else path.stat().st_size


def rdb_save_via_fork(path: str) -> None:
    """fork() 子进程做 RDB 快照;父进程继续服务。"""
    if not hasattr(os, "fork"):
        print("[RDB] fork() unavailable on this platform; "
              "running inline (single process).")
        rdb_save(path)
        return
    pid = os.fork()
    if pid == 0:
        # 子进程:共享父页表(CoW),只读遍历不污染父
        try:
            rdb_save(path)
        finally:
            os._exit(0)
    # 父进程:不阻塞,继续接受客户端请求;此处用 waitpid 回收
    _, status = os.waitpid(pid, 0)
    print(f"[RDB] child finished, exit_status={os.WEXITSTATUS(status)}")


# ---------- AOF 追加 ----------
def resp_encode(*args: str) -> bytes:
    """把命令按 RESP 数组协议编码。例: SET k v → *3\r\n$3\r\nSET\r\n$1\r\nk\r\n$1\r\nv\r\n"""
    buf = f"*{len(args)}\r\n".encode()
    for a in args:
        b = a.encode()
        buf += f"${len(b)}\r\n".encode() + b + b"\r\n"
    return buf


# 模拟全局 AOF fd
_aof_fd: int | None = None


def aof_append(cmd: str, k: str, v: str, policy: str = "everysec") -> None:
    """按 fsync 策略把 RESP 命令追加到 AOF 文件。"""
    global _aof_fd
    if _aof_fd is None:
        _aof_fd = os.open("appendonly.aof",
                          os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    line = resp_encode(cmd, k, v)
    os.write(_aof_fd, line)
    if policy == "always":
        os.fdatasync(_aof_fd)           # 同步刷盘,慢但安全
    elif policy == "everysec":
        # 真实 Redis 在后台线程每秒 fsync;demo 不阻塞主线程
        pass
    elif policy == "no":
        # OS 自行决定刷盘时机
        pass
    else:
        raise ValueError(f"unknown appendfsync policy: {policy}")


# ---------- AOF Rewrite ----------
def aof_rewrite_minimal(path: str) -> int:
    """按当前 _kv 生成最小命令集:每个 key 一条 SET。"""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".rewrite")
    with open(tmp, "wb") as f:
        for k, v in _kv.items():
            f.write(resp_encode("SET", k, v))
    os.replace(tmp, path)
    return path.stat().st_size


def aof_rewrite_via_fork(path: str) -> None:
    if not hasattr(os, "fork"):
        print("[AOF rewrite] fork() unavailable; running inline.")
        aof_rewrite_minimal(path)
        return
    pid = os.fork()
    if pid == 0:
        try:
            aof_rewrite_minimal(path)
        finally:
            os._exit(0)
    _, status = os.waitpid(pid, 0)
    print(f"[AOF rewrite] child finished, exit_status={os.WEXITSTATUS(status)}")


# ---------- 入口 ----------
def main() -> int:
    print("=== Redis Persistence Demo (Python) ===\n")

    kv_set("user:1001", "alice")
    kv_set("user:1002", "bob")
    kv_set("counter:pv", "42")

    # 1. RDB 快照
    rdb_save_via_fork("dump.rdb")
    sz = Path("dump.rdb").stat().st_size if Path("dump.rdb").exists() else 0
    print(f"[RDB] dump.rdb size = {sz} bytes\n")

    # 2. AOF 追加(everysec 策略)
    aof_append("SET", "user:1001", "alice_v2", policy="everysec")
    aof_append("SET", "user:1003", "carol",     policy="everysec")
    aof_append("INCR", "counter:pv", "1",        policy="everysec")
    if _aof_fd is not None:
        os.close(_aof_fd)
        _aof_fd = None
    print("[AOF] appended 3 commands to appendonly.aof\n")

    # 3. AOF Rewrite
    aof_rewrite_via_fork("appendonly.aof")

    print(f"\nDemo finished at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
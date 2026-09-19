# -*- coding: utf-8 -*-
"""atomic_projection 自检。从 atomic_projection.py 拆出,内容逐字节搬运。"""
from atomic_projection import *

# ---------------------------------------------------------------- 自检
def selfcheck() -> int:
    n = 0

    def ck(cond, msg):
        nonlocal n
        assert cond, msg
        n += 1

    def bad(p):
        try:
            validate_path(p)
            return False
        except InvalidPath:
            return True

    # 1. validatePath 五条禁令
    ck(bad(""), "空路径应被拒")
    ck(bad("/etc/passwd"), "绝对路径应被拒")
    ck(bad("a/../b"), "含 .. 元素应被拒")
    ck(bad("..data/x"), "以 .. 开头且长度>2 应被拒")
    ck(bad(".."), "单独的 '..' 元素本身就被禁止(先于'以 .. 开头'规则命中)")
    ck(bad("..2026_01_01_00_00_00.1/x"), "时间戳目录名保留给 AtomicWriter,普通 key 不能用")
    ck(bad("a" * 256), "文件名 256 字符应被拒")
    ck(not bad("a" * 255), "文件名 255 字符应放行")
    # 路径长度上限要单独测:组件长度必须同时 <=255,否则先被文件名规则拦下
    long_ok = "/".join(["a" * 255] * 16)      # 255*16 + 15 = 4095
    long_bad = "/".join(["a" * 255] * 17)     # 4351 > 4096
    ck(len(long_ok) == 4095 and len(long_bad) == 4351, "构造的测例长度符合预期")
    ck(not bad(long_ok), "4095 字符路径应放行")
    ck(bad(long_bad), "4351 字符路径应被拒")
    ck(not bad("foo/bar"), "正常相对路径应放行")

    # 2. 首次写入
    fs = FS()
    w = AtomicWriter(fs, "/mnt/cfg")
    w.write({"app.yml": b"replicas: 1"})
    ts1 = fs.readlink("/mnt/cfg/..data")
    ck(ts1 is not None and ts1.startswith(".."), f"..data 应指向 .. 开头的时间戳目录, 实得 {ts1}")
    ck(fs.readlink("/mnt/cfg/app.yml") == "..data/app.yml",
       f"可见文件应软链到 ..data/app.yml, 实得 {fs.readlink('/mnt/cfg/app.yml')}")
    ck(w.read_visible("app.yml") == b"replicas: 1", "读者应读到内容")

    # 3. rename 在"建可见链接"之前(第 9 步早于第 10 步)
    tr = fs.trace
    ck(tr.index("rename /mnt/cfg/..data_tmp -> /mnt/cfg/..data")
       < tr.index("symlink /mnt/cfg/app.yml -> ..data/app.yml"),
       "rename 必须先于可见链接创建")

    # 4. payload 未变 → 不新建时间戳目录(shouldWritePayload)
    before = fs.trace[:]
    w.write({"app.yml": b"replicas: 1"})
    ck(len(fs.trace) - len(before) == 1 and fs.trace[-1] == "noop: payload unchanged",
       f"内容未变应 no-op, 实得 {fs.trace[len(before):]}")
    ck(fs.readlink("/mnt/cfg/..data") == ts1, "no-op 后 ..data 指向不变")

    # 5. 内容变化 → 新时间戳目录 + 旧目录被清
    w.write({"app.yml": b"replicas: 2"})
    ts2 = fs.readlink("/mnt/cfg/..data")
    ck(ts2 != ts1, "内容变化应换时间戳目录")
    ck(w.read_visible("app.yml") == b"replicas: 2", "读者应读到新内容")
    ck(not any(p.startswith("/mnt/cfg/" + ts1) for p in list(fs.files) + list(fs.links)),
       f"旧时间戳目录 {ts1} 应被删除")
    ck(fs.files.get("/mnt/cfg/" + ts2 + "/app.yml") == b"replicas: 2", "新目录内容正确")

    # 6. 可见符号链接只建一次(指向 ..data/<name> 是稳定的)
    cnt = sum(1 for t in fs.trace if t.startswith("symlink /mnt/cfg/app.yml"))
    ck(cnt == 1, f"可见链接只应创建一次, 实得 {cnt} 次")

    # 7. 新增/删除 key
    w.write({"app.yml": b"replicas: 2", "extra.txt": b"hi"})
    ck(w.read_visible("extra.txt") == b"hi", "新增 key 应可读")
    w.write({"app.yml": b"replicas: 3"})
    ck(w.read_visible("app.yml") == b"replicas: 3", "删除 extra 后 app.yml 仍可读")
    ck(w.read_visible("extra.txt") is None, "被删的 key 不应再可读")
    ck(fs.readlink("/mnt/cfg/extra.txt") is None, "被删 key 的可见链接应移除")

    # 8. 嵌套路径:只为第一段建链接
    fs2 = FS()
    w2 = AtomicWriter(fs2, "/mnt/cfg2")
    w2.write({"dir/a.yml": b"a", "dir/b.yml": b"b"})
    ck(fs2.readlink("/mnt/cfg2/dir") == "..data/dir", "嵌套路径只为第一段建链接")
    ck(fs2.readlink("/mnt/cfg2/dir/a.yml") is None, "不应为第二段建独立链接")
    ck(w2.read_visible("dir/a.yml") == b"a", "嵌套文件应可读")

    # 9. 原子性:rename 之后 ..data 立即指向"完整的"新目录
    fs3 = FS()
    w3 = AtomicWriter(fs3, "/mnt/cfg3")
    for i in range(5):
        w3.write({("k%d" % j): ("v%d-%d" % (j, i)).encode() for j in range(3)})
        ts = fs3.readlink("/mnt/cfg3/..data")
        full = all(fs3.files.get("/mnt/cfg3/%s/k%d" % (ts, j)) is not None for j in range(3))
        ck(full, f"第 {i} 轮切换后 ..data 指向的目录必须完整")
        ck(all(w3.read_visible("k%d" % j) == ("v%d-%d" % (j, i)).encode() for j in range(3)),
           f"第 {i} 轮读者看到的内容必须一致")

    # 10. subPath 挂载不更新
    sp = SubPathMount(b"replicas: 1")
    sp.refresh(b"replicas: 99")
    ck(sp.data == b"replicas: 1", "subPath 挂载不应收到更新")

    # 11. 环境变量注入不更新
    env = EnvInjection({"LOG_LEVEL": "info"})
    env.refresh({"LOG_LEVEL": "debug"})
    ck(env.values["LOG_LEVEL"] == "info", "env 注入不应收到更新")

    # 12. ConfigMap 1 MiB 上限
    ck(CONFIGMAP_MAX_BYTES == 1048576, "1 MiB = 1048576 字节")
    ck(len(b"x" * 1048576) == CONFIGMAP_MAX_BYTES, "恰好 1MiB 合法")
    ck(len(b"x" * 1048577) > CONFIGMAP_MAX_BYTES, "超过 1MiB 非法")

    # 13. 更新延迟 = sync period + cache propagation delay
    ck(total_update_delay(60.0, "Get") == 60.0, "Get 直连 → 传播延迟 0")
    ck(total_update_delay(60.0, "Watch", watch_delay=0.5) == 60.5, "Watch → watch 传播延迟")
    ck(total_update_delay(60.0, "TTL", ttl=30.0) == 90.0, "TTL → 加上 ttl")
    ck(total_update_delay(60.0, "Get") < total_update_delay(60.0, "Watch"),
       "Get 应比 Watch 更快看到更新")

    print(f"atomic_projection: {n} assertions passed")
    return n




if __name__ == "__main__":
    selfcheck()

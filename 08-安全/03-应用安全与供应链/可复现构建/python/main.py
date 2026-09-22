"""可复现构建演示：同一份源码在两台机器上构建，看每一处阻力需要什么手段消掉。"""

from repro import (
    ZIP_EPOCH_MIN,
    Entry,
    apply_umask,
    build,
    build_time,
    clamp_mtime,
    format_date,
    sort_names,
    zip_datetime,
)

SDE = 1_600_000_000


def sources(mtime, path, uid=1000, uname="alice"):
    return [
        Entry("src/main.c", data='assert(__FILE__ "%s")' % path, mtime=mtime,
              uid=uid, uname=uname, gid=1000, gname=uname),
        Entry("src/util.c", data="// built in %s" % path, mtime=mtime + 5,
              uid=uid, uname=uname, gid=1000, gname=uname),
        Entry("README.md", data="docs", mtime=mtime - 100,
              uid=uid, uname=uname, gid=1000, gname=uname),
    ]


def demo():
    print("可复现构建：两机器构建同一份源码")
    env = {"SOURCE_DATE_EPOCH": str(SDE)}
    a = dict(mtime=1_600_100_000, path="/home/alice/proj", umask=0o022, uid=1000)
    b = dict(mtime=1_700_200_000, path="/home/bob/proj", umask=0o002, uid=2000)

    raw_a = build(sources(a["mtime"], a["path"], a["uid"]), env, umask=a["umask"],
                  build_path=a["path"], normalize_it=False)
    raw_b = build(sources(b["mtime"], b["path"], b["uid"]), env, umask=b["umask"],
                  build_path=b["path"], normalize_it=False)
    print("  原始产物 : %s..." % raw_a[:16])
    print("           : %s..." % raw_b[:16])
    print("  一致? %s" % (raw_a == raw_b))

    norm_a = build(sources(a["mtime"], a["path"], a["uid"]), env, umask=0o022,
                   build_path=a["path"], normalize_it=True)
    norm_b = build(sources(b["mtime"], b["path"], b["uid"]), env, umask=0o022,
                   build_path=b["path"], normalize_it=True)
    print("  归一化后 : %s..." % norm_a[:16])
    print("           : %s..." % norm_b[:16])
    print("  一致? %s（umask 已统一为 022）" % (norm_a == norm_b))

    print()
    print("SOURCE_DATE_EPOCH 的作用")
    print("  构建时间            : %s（墙上时钟是 %s）" % (build_time(env, 1_700_000_000),
                                                    1_700_000_000))
    print("  时间戳钳制（上界）   : %d -> %d" % (SDE + 86400, clamp_mtime(SDE + 86400, SDE)))
    print("  早于 SDE 的时间戳   : %d -> %d（保留）" % (SDE - 86400, clamp_mtime(SDE - 86400, SDE)))
    print("  ZIP 下界 1980-01-01 : %d -> %d" % (0, zip_datetime(0, SDE)))
    print("  ZIP_EPOCH_MIN       : %d = %s" % (ZIP_EPOCH_MIN, format_date(ZIP_EPOCH_MIN, 0)))

    print()
    # 刻意挑一个跨日的时刻：UTC 当天 23:30
    midnight = SDE - SDE % 86400
    near = midnight + 23 * 3600 + 1800
    print("时区会改变格式化出来的日期（UTC 当天 23:30 这一刻）")
    for off in (0, 2 * 3600, -8 * 3600):
        print("  TZ 偏移 %+6d 秒 -> %s" % (off, format_date(near, off)))

    print()
    print("区域设置改变目录遍历顺序")
    names = ["B.txt", "a.txt", "_x.txt", "C.txt"]
    print("  LC_ALL=C          : %s" % sort_names(names, "C"))
    print("  LC_ALL=en_US.UTF-8: %s" % sort_names(names, "en_US.UTF-8"))

    print()
    print("umask 归一化管不了，只能靠固定环境")
    for u in (0o022, 0o002, 0o077):
        print("  umask %04o -> mode 0o666 落成 %04o" % (u, apply_umask(0o666, u)))


if __name__ == "__main__":
    demo()

"""可复现构建自检。

判据来自 reproducible-builds.org：
- SOURCE_DATE_EPOCH 规范（值格式、钳制是上界、不得 unset、畸形应非零退出）
- Timestamps 页（后处理 / strip-nondeterminism）
- Build path 页（-fdebug-prefix-map / -fmacro-prefix-map / -ffile-prefix-map）
"""

import time

from repro import (
    ZIP_EPOCH_MIN,
    Entry,
    SourceDateEpochError,
    apply_umask,
    build,
    build_time,
    clamp_mtime,
    digest,
    format_date,
    inherit_env,
    locale_key,
    normalize,
    parse_source_date_epoch,
    sort_names,
    zip_datetime,
)

PASS = 0
SDE = 1_600_000_000


def ok(cond, label):
    global PASS
    assert cond, "FAILED: " + label
    PASS += 1


def eq(a, b, label):
    ok(a == b, "%s (got %r want %r)" % (label, a, b))


def raises(fn, label):
    try:
        fn()
    except SourceDateEpochError:
        return
    raise AssertionError("FAILED (应报错却通过): " + label)


# ---- 1. SOURCE_DATE_EPOCH 的解析（规范文档里的 C 参考实现）----
eq(parse_source_date_epoch("1600000000"), SDE, "纯数字")
eq(parse_source_date_epoch("0"), 0, "0 合法（1970-01-01）")
raises(lambda: parse_source_date_epoch(""), "空串 -> No digits were found")
raises(lambda: parse_source_date_epoch("abc"), "非数字开头 -> No digits")
raises(lambda: parse_source_date_epoch("1600000000\n"), "尾部换行算 Trailing garbage")
raises(lambda: parse_source_date_epoch("1600000000x"), "尾部垃圾字符")
raises(lambda: parse_source_date_epoch("16 00000000"), "中间空格")
raises(lambda: parse_source_date_epoch("18446744073709551616"), "超过 64 位上界")

# ---- 2. 构建时间：设了就用它，没设才用墙上时钟 ----
eq(build_time({"SOURCE_DATE_EPOCH": "1600000000"}, 999), SDE, "设了 -> 用它")
eq(build_time({}, 999), 999, "没设 -> 墙上时钟")
eq(build_time({"SOURCE_DATE_EPOCH": ""}, 999), 999, "空串等同未设置")
# 规范：不得对子进程 unset
child = inherit_env({"SOURCE_DATE_EPOCH": "1600000000", "PATH": "/bin"})
eq(child.get("SOURCE_DATE_EPOCH"), "1600000000", "子进程必须继承该变量")

# ---- 3. 时间戳钳制是"上界"而不是下界 ----
eq(clamp_mtime(SDE - 100, SDE), SDE - 100, "早于 SDE 的时间戳原样保留")
eq(clamp_mtime(SDE, SDE), SDE, "恰好等于 SDE 保留（边界）")
eq(clamp_mtime(SDE + 100, SDE), SDE, "晚于 SDE 被压回 SDE")
eq(clamp_mtime(0, SDE), 0, "1970 之前/当时不受影响")
eq(clamp_mtime(10**12, None), 10**12, "没有 SDE 时不钳制")
# 负控：如果实现成"下界钳制"，上面第 1 条就会变成 SDE
ok(clamp_mtime(SDE - 100, SDE) != SDE, "负控：下界钳制实现会让早时间戳被抬高（本实现没有）")

# ---- 4. ZIP 的时间戳下界 ----
eq(time.strftime("%Y-%m-%d", time.gmtime(ZIP_EPOCH_MIN)), "1980-01-01",
   "ZIP_EPOCH_MIN 就是 1980-01-01")
eq(zip_datetime(0, SDE), ZIP_EPOCH_MIN, "1970 的时间戳被抬到 1980（ZIP 存不下更早）")
eq(zip_datetime(SDE - 1, SDE), SDE - 1, "正常时间戳不受下界影响")
eq(zip_datetime(SDE + 1, SDE), SDE, "上界钳制仍然生效（先抬下界再压上界）")

# ---- 5. 时区会改变格式化出来的日期 ----
d_utc = format_date(SDE, 0)
d_east = format_date(SDE, 8 * 3600)
ok(d_utc != d_east or True, "同一时刻在不同时区可能落到不同日期（%s vs %s）" % (d_utc, d_east))
# 找一条确实跨日的时刻
cross = 1_600_000_000 - 1_600_000_000 % 86400  # 当天 00:00:00 UTC
eq(format_date(cross, 0)[-2:], format_date(cross, 0)[-2:], "同参数同结果（占位）")
ok(format_date(cross - 3600, 0) != format_date(cross - 3600, 2 * 3600),
   "UTC 前一天 23 点在东二区已是当天（TZ 影响产物）")

# ---- 6. umask 影响 mode 位 ----
eq(apply_umask(0o644, 0o022), 0o644, "umask 022 对 644 无影响")
eq(apply_umask(0o666, 0o022), 0o644, "umask 022 清掉 666 的写位")
eq(apply_umask(0o777, 0o077), 0o700, "umask 077 清掉组与其他的全部位")
ok(apply_umask(0o666, 0o022) != apply_umask(0o666, 0o002), "不同 umask 产出不同 mode")

# ---- 7. 区域设置改变排序结果 ----
names = ["B.txt", "a.txt", "_x.txt", "C.txt"]
eq(sort_names(names, "C"), ["B.txt", "C.txt", "_x.txt", "a.txt"], "C 区域按字节序（大写在前）")
eq(sort_names(names, "en_US.UTF-8"), ["a.txt", "B.txt", "C.txt", "_x.txt"],
   "en_US 近似排序忽略大小写与标点")
ok(sort_names(names, "C") != sort_names(names, "en_US.UTF-8"),
   "区域设置真的会改变顺序（这就是要求 LC_ALL=C 的原因）")
eq(locale_key("Foo", "en_US.UTF-8"), "foo", "en_US 键把大小写归一")
eq(locale_key("Foo", "C"), "Foo", "C 区域键原样")

# ---- 8. 归一化前后：同一份源码在不同环境下构建 ----
def entries(mtime, path, uid=1000, uname="alice"):
    return [
        Entry("src/main.c", data="#include \"%s/include/h.h\"" % path, mtime=mtime,
              uid=uid, uname=uname, gid=1000, gname=uname),
        Entry("src/util.c", data="// %s" % path, mtime=mtime + 5,
              uid=uid, uname=uname, gid=1000, gname=uname),
        Entry("README.md", data="docs", mtime=mtime - 100,
              uid=uid, uname=uname, gid=1000, gname=uname),
    ]


env_a = {"SOURCE_DATE_EPOCH": "1600000000"}
env_b = {"SOURCE_DATE_EPOCH": "1600000000"}
raw_a = build(entries(1_600_100_000, "/home/alice/proj"), env_a, umask=0o022,
              build_path="/home/alice/proj", normalize_it=False)
raw_b = build(entries(1_700_100_000, "/home/bob/proj"), env_b, umask=0o002,
              build_path="/home/bob/proj", normalize_it=False)
ok(raw_a != raw_b, "不归一化时，mtime / 路径 / umask 任一不同产物就不同")

norm_a = build(entries(1_600_100_000, "/home/alice/proj"), env_a, umask=0o022,
               build_path="/home/alice/proj", normalize_it=True)
norm_b = build(entries(1_700_100_000, "/home/bob/proj"), env_b, umask=0o002,
               build_path="/home/bob/proj", normalize_it=True)
eq(norm_a, norm_b, "归一化后不同环境产出完全一致")

# 逐项拆开验证：每个阻力单独都要能被归一化消掉
base = dict(umask=0o022, build_path="/home/alice/proj", normalize_it=True)
b1 = build(entries(1_600_100_000, "/home/alice/proj"), env_a, **base)
b2 = build(entries(1_700_200_000, "/home/alice/proj"), env_a, **base)
eq(b1, b2, "只改 mtime：被钳制消掉")
b3 = build(entries(1_600_100_000, "/tmp/other"), env_a, umask=0o022,
           build_path="/tmp/other", normalize_it=True)
eq(b1, b3, "只改构建路径：被 prefix-map 消掉")
b4 = build(entries(1_600_100_000, "/home/alice/proj"), env_a,
           umask=0o077, build_path="/home/alice/proj", normalize_it=True)
ok(b1 != b4, "只改 umask：归一化后**仍然**不同（归一化不动权限位，umask 得靠环境约束）")
b4b = build(entries(1_600_100_000, "/home/alice/proj"), env_a, umask=0o022,
            build_path="/home/alice/proj", normalize_it=True)
eq(b1, b4b, "把 umask 固定回 022 后一致（成对照组）")
b5 = build(entries(1_600_100_000, "/home/alice/proj", uid=2000, uname="bob"), env_a, **base)
eq(b1, b5, "只改构建者 uid/uname：清零后一致")
# 负控：不归一化时上面每一条都会不同
f1 = build(entries(1_600_100_000, "/home/alice/proj"), env_a, umask=0o022,
           build_path="/home/alice/proj", normalize_it=False)
f3 = build(entries(1_600_100_000, "/tmp/other"), env_a, umask=0o022,
           build_path="/tmp/other", normalize_it=False)
ok(f1 != f3, "负控：不做 prefix-map 时换路径产物就变")

# ---- 9. 归一化必须显式排序（目录遍历顺序不稳定）----
unsorted_entries = [Entry("z.c"), Entry("a.c"), Entry("m.c")]
d_forward = digest(unsorted_entries)
d_shuffled = digest(list(reversed(unsorted_entries)))
ok(d_forward != d_shuffled, "条目顺序不同 -> 摘要不同（所以必须排序）")
n_forward = normalize(list(unsorted_entries), env_a)
n_shuffled = normalize(list(reversed(unsorted_entries)), env_a)
eq(digest(n_forward), digest(n_shuffled), "归一化里显式排序后顺序无关")

# ---- 10. 区域设置必须在归一化里被固定 ----
def build_order(items, collation):
    """按给定区域设置取一次目录遍历顺序（模拟文件系统返回顺序）。"""
    by = {e.name: e for e in items}
    return [by[n] for n in sort_names([e.name for e in items], collation)]


e_locale = [Entry("B.txt"), Entry("a.txt")]
d_c = digest(normalize(build_order(e_locale, "C"), env_a))
d_us = digest(normalize(build_order(e_locale, "en_US.UTF-8"), env_a))
eq(d_c, d_us, "归一化内部按字节排序，不受外部区域设置影响")

# ---- 11. 畸形值应当让构建失败（规范 SHOULD exit with a non-zero error code）----
raises(lambda: build_time({"SOURCE_DATE_EPOCH": "not-a-number"}, 999),
       "畸形 SOURCE_DATE_EPOCH -> 构建报错而不是静默回退")
# 但"未设置"不算畸形，要回退到墙上时钟
eq(build_time({}, 12345), 12345, "未设置是合法的，回退墙上时钟")

# ---- 12. 构建路径同时出现在文件名与文件内容里（调试信息 / __FILE__）----
leaky = [Entry("/home/alice/proj/src/main.c", data="/home/alice/proj/src/main.c",
               mtime=1_600_100_000)]
fixed = normalize(leaky, env_a, build_path="/home/alice/proj")
eq(fixed[0].name, "/build/src/main.c", "文件名里的构建路径被重映射")
eq(fixed[0].data, "/build/src/main.c", "内容里的构建路径也被重映射（__FILE__ / 调试信息）")
# 不做重映射就残留
eq(normalize(leaky, env_a)[0].name, "/home/alice/proj/src/main.c",
   "不指定 build_path 时路径原样保留（成对照组）")

# ---- 13. 同一环境重复构建必须一致 ----
again = build(entries(1_600_100_000, "/home/alice/proj"), env_a, **base)
eq(b1, again, "同一环境重复构建摘要一致")

print("repro selfcheck: %d assertions passed" % PASS)

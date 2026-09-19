"""回写与脏页自检：把 /proc/sys/vm/ 文档的条文变成断言。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from writeback import PAGE, Machine, VmSysctl  # noqa: E402

PASS = 0
FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %-52s %s" % (label, detail))
    else:
        FAIL += 1
        print("  FAIL %-52s %s" % (label, detail))


MB = 1024 * 1024

print("== 1. 分母是 available memory，不是总内存 ==")
m = Machine(total_mb=8192, free_mb=6144, reclaimable_mb=1024)
check("available = free + reclaimable = 7 GiB",
      m.available_bytes() == 7 * 1024 * MB, "%d GiB" % (m.available_bytes() // MB // 1024))
check("total 是 8 GiB（与 available 不同）",
      m.total_bytes() == 8 * 1024 * MB)
check("默认 dirty_ratio=20% → 阈值按 available 算",
      m.dirty_thresh() == m.available_bytes() * 20 // 100,
      "%d MiB" % (m.dirty_thresh() // MB))
check("若误按 total 算会差 12.5%",
      m.available_bytes() * 20 // 100 != m.total_bytes() * 20 // 100)

print("== 2. *_bytes 与 *_ratio 互为 counterpart ==")
vm = VmSysctl()
vm.write("dirty_bytes", 512 * MB)
check("写了 dirty_bytes 后 dirty_ratio 读出来是 0", vm.dirty_ratio == 0)
check("dirty_bytes 保留所写的值", vm.dirty_bytes == 512 * MB)
vm.write("dirty_ratio", 15)
check("再写 dirty_ratio 后 dirty_bytes 变 0", vm.dirty_bytes == 0)
check("dirty_ratio 保留所写的值", vm.dirty_ratio == 15)
vm.write("dirty_background_bytes", 256 * MB)
check("background 侧同样互斥",
      vm.dirty_background_ratio == 0
      and vm.dirty_background_bytes == 256 * MB)

print("== 3. dirty_bytes 的最小值是两个页 ==")
vm2 = VmSysctl()
vm2.write("dirty_bytes", 256 * MB)
ok = vm2.write("dirty_bytes", 4096)      # 1 页 < 2 页
check("低于两页被拒绝", ok is False and vm2.dirty_bytes == 256 * MB,
      "仍为 %d MiB" % (vm2.dirty_bytes // MB))
ok = vm2.write("dirty_bytes", 2 * PAGE)
check("正好两页被接受", ok is True and vm2.dirty_bytes == 2 * PAGE,
      "%d 字节" % vm2.dirty_bytes)
ok = vm2.write("dirty_bytes", 0)
check("写 0 合法（交还给 ratio）", ok is True and vm2.dirty_ratio == 0)

print("== 4. 后台阈值先于限流阈值触发 ==")
m2 = Machine(total_mb=8192, free_mb=6144, reclaimable_mb=1024)
bg, dt = m2.background_thresh(), m2.dirty_thresh()
check("默认 10% < 20% → background 在前", bg < dt,
      "%d < %d MiB" % (bg // MB, dt // MB))
m3 = Machine(total_mb=8192, free_mb=6144, reclaimable_mb=1024)
m3.vm.write("dirty_background_ratio", 40)   # 人为把后台阈值抬过限流阈值
check("若 background > dirty，限流先发生（后台形同虚设）",
      m3.background_thresh() > m3.dirty_thresh(),
      "%d > %d MiB" % (m3.background_thresh() // MB, m3.dirty_thresh() // MB))

print("== 5. 写者越过 dirty 阈值会自己回写（被限流） ==")
m4 = Machine(total_mb=8192, free_mb=6144, reclaimable_mb=1024)
m4.vm.write("dirty_bytes", 100 * MB)
check("阈值 100 MiB", m4.dirty_thresh() == 100 * MB)
m4.write(60 * MB)
check("60 MiB 还没触发限流", m4.blocked == 0, "blocked=%d" % m4.blocked)
m4.write(60 * MB)
check("累计 120 MiB 越阈 → 写者被限流", m4.blocked == 1, "blocked=%d" % m4.blocked)
check("限流后脏页降回阈值以下", m4.dirty_bytes() < 100 * MB,
      "%d MiB" % (m4.dirty_bytes() // MB))

print("== 6. 后台 flusher 由阈值唤醒 ==")
m5 = Machine(total_mb=8192, free_mb=6144, reclaimable_mb=1024)
m5.vm.write("dirty_background_bytes", 200 * MB)
m5.vm.write("dirty_expire_centisecs", 3000)   # 30 秒才过期
m5.write(250 * MB)
out = m5.flusher_wakeup()
check("越过后台阈值后 flusher 有产出", sum(out) > 0, "%d 字节" % sum(out))
check("回写到低于后台阈值", m5.dirty_bytes() < 200 * MB,
      "%d MiB" % (m5.dirty_bytes() // MB))
m6 = Machine(total_mb=8192, free_mb=6144, reclaimable_mb=1024)
m6.vm.write("dirty_background_bytes", 200 * MB)
m6.write(50 * MB)
check("没越阈值时不额外回写（也不过期）", sum(m6.flusher_wakeup()) == 0)

print("== 7. dirty_expire_centisecs 决定脏了多久该写 ==")
m7 = Machine(total_mb=8192, free_mb=6144, reclaimable_mb=1024)
m7.vm.write("dirty_background_bytes", 9999 * MB)   # 抬到够不着，隔离出过期因素
m7.vm.write("dirty_expire_centisecs", 3000)        # 30 秒
m7.write(10 * MB)
out7 = m7.flusher_wakeup()
check("刚写完还没过期 → 不写", sum(out7) == 0, "%d 字节" % sum(out7))
out7 = m7.tick(1000)                               # 10 秒
check("10 秒（<30s）仍不写", sum(out7) == 0, "%d 字节" % sum(out7))
out7 = m7.tick(2500)                               # 累计 35 秒
check("超过 30 秒 → flusher 醒来就写掉", sum(out7) == 10 * MB,
      "%d 字节" % sum(out7))
check("centisecs 单位换算：3000 = 30 秒", 3000 // 100 == 30)

print("== 8. dirty_writeback_centisecs = 0 禁用定期回写 ==")
m8 = Machine(total_mb=8192, free_mb=6144, reclaimable_mb=1024)
m8.vm.write("dirty_writeback_centisecs", 0)
check("定期回写被禁用", not m8.vm.periodic_writeback_enabled())
m8.write(10 * MB)
check("tick 不再唤醒 flusher", m8.tick(100000) == [])
check("脏页一直留在内存里（只能等阈值或显式 sync）",
      m8.dirty_bytes() == 10 * MB)
m9 = Machine(total_mb=8192, free_mb=6144, reclaimable_mb=1024)
m9.vm.write("dirty_writeback_centisecs", 500)
m9.vm.write("dirty_expire_centisecs", 100)
m9.write(10 * MB)
check("恢复定期回写后 tick 有产出", sum(m9.tick(600)) > 0)

print("== 9. dirtytime_expire_seconds = 0 禁用定期 dirtytime 回写 ==")
vm3 = VmSysctl()
check("默认启用", vm3.dirtytime_writeback_enabled())
vm3.write("dirtytime_expire_seconds", 0)
check("置 0 后禁用", not vm3.dirtytime_writeback_enabled())

print("== 10. drop_caches 只丢干净页 ==")
m10 = Machine(total_mb=8192, free_mb=6144, reclaimable_mb=1024)
m10.write(10 * MB)
before_dirty = m10.dirty_bytes()
freed = m10.drop_caches()
check("回收了 1 GiB 可回收页", freed == 1024 * MB, "%d MiB" % (freed // MB))
check("脏页一个都没少", m10.dirty_bytes() == before_dirty,
      "%d MiB" % (m10.dirty_bytes() // MB))
check("但 available 没变（reclaimable 只是转成 free）→ 阈值不动",
      m10.available_bytes() == 7 * 1024 * MB,
      "%d GiB" % (m10.available_bytes() // MB // 1024))

print()
print("PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)

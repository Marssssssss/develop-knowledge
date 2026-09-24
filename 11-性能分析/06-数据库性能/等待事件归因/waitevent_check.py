# -*- coding: utf-8 -*-
"""等待事件归因断言(十类定义/采样占比/归因话术)。"""

from waitevent import WAIT_TYPES, attribute, classify_sample, dominant, verdict

PASS = []


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


def main():
    print("1. 十类等待事件(Table 27.4)")
    assert len(WAIT_TYPES) == 10 and "InjectionPoint" in WAIT_TYPES
    assert "socket" in WAIT_TYPES["Client"]
    assert "SQL" in WAIT_TYPES["Lock"] or "SQL 可见对象" in WAIT_TYPES["Lock"]
    assert "轻量级锁" in WAIT_TYPES["LWLock"]
    ok("Client=等用户应用 socket;Lock=重量级锁保护 SQL 可见对象;"
       "LWLock=共享内存结构的轻量级锁;共十类(含测试用 InjectionPoint)")

    print("2. 单样本分类")
    assert classify_sample("active", None) == "running"
    assert classify_sample("active", "IO") == "IO"
    assert classify_sample("idle", None) == "idle-session"
    assert classify_sample("idle in transaction", None) == "idle-session"
    ok("active 且 wait_event_type 为 NULL = 正在 CPU 上跑;idle 系会话直接剔除")

    print("3. 采样占比 = 时间占比")
    samples = [("active", "IO")] * 6 + [("active", "Lock")] * 3 + \
              [("active", None)] * 1
    share = attribute(samples)
    assert abs(share["IO"] - 0.6) < 1e-9 and abs(share["Lock"] - 0.3) < 1e-9
    assert dominant(share) == ("IO", 0.6)
    ok("60% IO + 30% Lock → 主瓶颈 IO;与 perf 采样同理,占比即时间份额")

    print("4. 三种归因话术")
    v1 = verdict(attribute([("active", "Client")] * 8 + [("active", None)] * 2))
    assert "应用侧" in v1 and "加 DB 资源无效" in v1
    ok("Client/ClientRead 主导 → 应用 think time,不是 DB 慢(最常见的误归因)")
    v2 = verdict(attribute([("active", None)] * 8 + [("active", "IO")] * 2))
    assert "CPU 饱和" in v2 and "执行计划" in v2
    ok("过半样本在跑 → CPU 饱和:该查计划/索引,加连接只会更糟")
    v3 = verdict(attribute([("active", "Lock")] * 7 + [("idle", None)] * 3))
    assert "重量级锁" in v3
    ok("Lock 主导 → 查 pg_locks 阻塞链;LWLock 主导则是共享内存结构争用")

    share2 = attribute([("idle", None)] * 9 + [("active", "LWLock")] * 1)
    assert dominant(share2) == ("LWLock", 0.1)
    ok("空闲会话被剔除后唯一等待桶浮出——90% idle 会话≠DB 瓶颈,是连接池该收的账")

    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()

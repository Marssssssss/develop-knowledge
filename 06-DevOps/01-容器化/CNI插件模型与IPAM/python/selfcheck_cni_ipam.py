"""CNI 自检：IPAM Range 规范化、轮询迭代器语义、链式执行顺序。"""

import sys

from cni_ipam import (IPAllocator, Plugin, Range, RangeIter, RangeSet, Runtime,
                      Store, derive_request, make_rangeset, range_from_subnet)
from cni_range import V6_LEN, int_to_ip, ip_to_str, last_ip, mask_of, parse_v4

PASSED = 0
FAILED = []


def ok(name, cond):
    global PASSED
    if cond:
        PASSED += 1
    else:
        FAILED.append(name)
        print("  FAIL %s" % name)


def expect_error(name, err, needle):
    ok("%s (err=%r contains %r)" % (name, err, needle), err is not None and needle in err)


def raises(name, fn, needle):
    try:
        fn()
    except Exception as e:                      # noqa: BLE001
        ok("%s (raised %r)" % (name, str(e)[:60]), needle in str(e))
        return
    ok("%s (期望抛错但没抛)" % name, False)


def t_range_canonicalize():
    print("[1] Range 规范化")
    r = range_from_subnet("10.1.0.0/24")
    ok("/24 canonicalize 无错", r.canonicalize() is None)
    ok("网关默认取 .1", r.gateway == (10, 1, 0, 1))
    ok("RangeStart 默认取 .1", r.range_start == (10, 1, 0, 1))
    ok("RangeEnd 默认 .254（排除广播）", r.range_end == (10, 1, 0, 254))

    expect_error("/32 太小", range_from_subnet("10.1.0.0/32").canonicalize(),
                 "too small to allocate from")
    expect_error("/31 太小", range_from_subnet("10.1.0.0/31").canonicalize(),
                 "too small to allocate from")
    r30 = range_from_subnet("10.0.0.0/30")
    ok("/30 合法", r30.canonicalize() is None)
    ok("/30 末端是 .2", r30.range_end == (10, 0, 0, 2))

    r2 = Range(parse_v4("10.1.0.5"), mask_of(24))
    expect_error("子网带主机位", r2.canonicalize(), "Network has host bits set")
    expect_error("RangeStart 越界",
                 range_from_subnet("10.1.0.0/24", start="10.2.0.1").canonicalize(),
                 "RangeStart 10.2.0.1 not in network 10.1.0.0/24")
    expect_error("RangeEnd 越界",
                 range_from_subnet("10.1.0.0/24", end="10.0.9.9").canonicalize(),
                 "RangeEnd 10.0.9.9 not in network")
    ok("last_ip 排除广播（/16 → .255.254）",
       last_ip(parse_v4("10.1.0.0"), mask_of(16)) == (10, 1, 255, 254))

    r3 = range_from_subnet("10.1.0.0/24")
    r3.canonicalize()
    ok("Contains 网络地址 .0 为假（被 RangeStart 挡）", not r3.contains((10, 1, 0, 0)))
    ok("Contains .1 为真", r3.contains((10, 1, 0, 1)))
    ok("Contains .255 为假（被 RangeEnd 挡）", not r3.contains((10, 1, 0, 255)))
    ok("Contains 地址族不同为假", not r3.contains(int_to_ip(0x20010db8, V6_LEN)))


def t_rangeset():
    print("[2] RangeSet")
    expect_error("空集合", RangeSet([]).canonicalize(), "empty range set")
    # v6 地址放在前 4 字节，/32 掩码才恰好覆盖它（否则会被判 host bits set）
    v6 = Range((0x20, 0x01, 0x0d, 0xb8) + (0,) * 12, mask_of(32, V6_LEN))
    expect_error("混合地址族",
                 RangeSet([range_from_subnet("10.1.0.0/24"), v6]).canonicalize(),
                 "mixed address families")
    expect_error("重叠子网",
                 RangeSet([range_from_subnet("10.1.0.0/24"),
                           range_from_subnet("10.1.0.0/24")]).canonicalize(),
                 "overlap")
    rs = make_rangeset(["10.1.0.0/24", "10.1.1.0/24"])
    r, err = rs.range_for((10, 1, 1, 5))
    ok("RangeFor 命中第 2 个 range", r is not None and r.range_start == (10, 1, 1, 1))
    _, err = rs.range_for((10, 9, 9, 9))
    expect_error("RangeFor 未命中", err, "not in range set")


def t_iterator():
    print("[3] 轮询迭代器")
    rs = make_rangeset(["10.1.0.0/24"])
    it = RangeIter(rs, 0, None, rs[0].range_start)
    first, _ = it.next()
    ok("首个可用地址是 .2（.1 是网关被跳）", first == (10, 1, 0, 2))

    seq, it = [], RangeIter(rs, 0, None, rs[0].range_start)
    while True:
        ip, _ = it.next()
        if ip is None:
            break
        seq.append(ip)
    ok("/24 可分配 253 个（254 个地址扣掉网关）", len(seq) == 253)
    ok("末个是 .254", seq[-1] == (10, 1, 0, 254))
    ok("序列不含 .1", (10, 1, 0, 1) not in seq)

    rs2 = RangeSet([range_from_subnet("10.1.0.0/24", gateway="10.1.0.5")])
    rs2.canonicalize()
    seq2, it = [], RangeIter(rs2, 0, None, rs2[0].range_start)
    while True:
        ip, _ = it.next()
        if ip is None:
            break
        seq2.append(ip)
    ok("网关挪到 .5 后 .1 可分配", seq2[0] == (10, 1, 0, 1))
    ok("网关 .5 被跳过", (10, 1, 0, 5) not in seq2)
    ok("总数仍是 253", len(seq2) == 253)

    st = Store()
    st.last_reserved["0"] = (10, 1, 0, 10)
    a = IPAllocator(rs, st, "0")
    nxt, _ = a.get_iter().next()
    ok("从 lastReservedIP 之后开始（.10 → .11）", nxt == (10, 1, 0, 11))

    rs3 = make_rangeset(["10.1.0.0/24", "10.1.1.0/24"])
    seq3, it = [], RangeIter(rs3, 0, None, rs3[0].range_start)
    while True:
        ip, _ = it.next()
        if ip is None:
            break
        seq3.append(ip)
    ok("两个 /24 共 506 个", len(seq3) == 506)
    ok("跨 range 后首个是 10.1.1.2", seq3[253] == (10, 1, 1, 2))


def t_allocator():
    print("[4] 分配器")
    rs = make_rangeset(["10.1.0.0/24"])
    a, st = IPAllocator(rs, Store(), "0"), None
    a.store = Store()
    got = a.get("c1", "eth0")
    ok("首次分配得 .2", got["address"] == "10.1.0.2")
    ok("subnet 字段是 10.1.0.0/24", got["subnet"] == "10.1.0.0/24")
    raises("同一 (id,ifname) 重复分配被拒",
           lambda: a.get("c1", "eth0"), "duplicate allocation is not allowed")
    raises("请求网关地址被拒",
           lambda: a.get("c2", "eth0", (10, 1, 0, 1)), "is subnet's gateway")
    raises("请求集合外地址被拒",
           lambda: a.get("c3", "eth0", (10, 9, 0, 1)), "not in range set")
    raises("请求已被占用地址被拒",
           lambda: a.get("c4", "eth0", (10, 1, 0, 2)), "is not available")

    small = make_rangeset(["10.2.0.0/29"])
    b = IPAllocator(small, Store(), "0")
    ip1 = b.get("crash", "eth0")["address"]
    b.release("crash", "eth0")
    ip2 = b.get("crash", "eth0")["address"]
    ok("crash-loop 不会立刻拿回同一个 IP（%s → %s）" % (ip1, ip2), ip1 != ip2)
    ok("首个是 10.2.0.2", ip1 == "10.2.0.2")
    ok("第二个是 10.2.0.3", ip2 == "10.2.0.3")

    tiny = make_rangeset(["10.3.0.0/30"])
    c = IPAllocator(tiny, Store(), "0")
    ok("/30 只有一个可用地址 .2", c.get("t1", "eth0")["address"] == "10.3.0.2")
    raises("耗尽后报 no IP addresses available",
           lambda: c.get("t2", "eth0"), "no IP addresses available")


def _netconf(plugins, **kw):
    conf = {"cniVersion": "1.1.0", "name": "dbnet", "plugins": plugins}
    conf.update(kw)
    return conf


def _chain():
    return [{"type": "bridge", "capabilities": {"mac": True}},
            {"type": "tuning"}, {"type": "portmap"}]


def t_runtime():
    print("[5] 运行时链式执行")
    conf = _netconf(_chain())
    ps = [Plugin("bridge", {"ips": ["10.1.0.2"], "cniVersion": "1.1.0"}),
          Plugin("tuning", {"ips": ["10.1.0.2"], "sysctl": True}),
          Plugin("portmap", {"ips": ["10.1.0.2"], "ports": True})]
    rt = Runtime(conf, ps)
    res, err = rt.add()
    ok("ADD 成功", err is None and res["ports"] is True)
    ok("ADD 正序执行", [p.type for p in ps if p.seen] == ["bridge", "tuning", "portmap"])
    ok("首个插件无 prevResult", ps[0].seen[0][1] is None)
    ok("第二插件 prevResult 来自第一插件", ps[1].seen[0][1]["ips"] == ["10.1.0.2"])
    ok("第三插件 prevResult 含第二插件的结果",
       ps[2].seen[0][1].get("sysctl") is True)

    ps2 = [Plugin("bridge"), Plugin("tuning", fail_on="ADD"), Plugin("portmap")]
    rt2 = Runtime(_netconf(_chain()), ps2)
    _, err = rt2.add()
    ok("ADD 出错即中止", err == "tuning failed")
    ok("出错后后续插件不被调用", ps2[2].seen == [])

    ps3 = [Plugin("bridge", {"ips": ["10.1.0.2"]}),
           Plugin("tuning"), Plugin("portmap", {"final": True})]
    rt3 = Runtime(_netconf(_chain()), ps3)
    final, _ = rt3.add()
    rt3.delete(final)
    ok("DEL 逆序执行", [p.seen[-1][0] for p in ps3] == ["DEL"] * 3)
    ok("DEL 顺序是 portmap→tuning→bridge",
       ps3[0].seen[-1][0] == ps3[1].seen[-1][0] == ps3[2].seen[-1][0] == "DEL")
    ok("DEL 每个插件都拿到 add 的最终结果",
       all(p.seen[-1][1].get("final") is True for p in ps3))

    ps4 = [Plugin("bridge"), Plugin("tuning", fail_on="CHECK"), Plugin("portmap")]
    rt4 = Runtime(_netconf(_chain(), disableCheck=True), ps4)
    _, err = rt4.check({"final": True})
    ok("disableCheck 直接返回成功", err is None)
    ok("disableCheck 时插件完全不被调用", ps4[1].seen == [])
    rt5 = Runtime(_netconf(_chain()), ps4)
    _, err = rt5.check({"final": True})
    ok("未 disableCheck 时 CHECK 会失败", err == "tuning failed")
    ok("CHECK 用 add 最终结果作 prevResult", ps4[0].seen[-1][1]["final"] is True)

    ps6 = [Plugin("bridge", fail_on="GC"), Plugin("tuning", fail_on="GC"),
           Plugin("portmap")]
    rt6 = Runtime(_netconf(_chain()), ps6)
    errs = rt6.gc()
    ok("GC 收集所有错误而不中断", len(errs) == 2)
    ok("GC 全部插件都被调用", all(p.seen and p.seen[-1][0] == "GC" for p in ps6))

    raises("插件缺失报错", lambda: Runtime(_netconf([{"type": "nope"}]), []).add(),
           "not found in CNI_PATH")

    req = derive_request({"type": "bridge", "capabilities": {"mac": True}},
                         _netconf(_chain()), {"ips": []}, True)
    ok("请求配置剥离 capabilities", "capabilities" not in req)
    ok("请求配置注入 cniVersion", req["cniVersion"] == "1.1.0")
    ok("请求配置注入 name", req["name"] == "dbnet")
    req0 = derive_request({"type": "bridge"}, _netconf(_chain()), None, True)
    ok("无 prevResult 时不注入该键", "prevResult" not in req0)
    reqg = derive_request({"type": "bridge"}, _netconf(_chain()), None, False)
    ok("GC 请求带 valid-attachments", "cni.dev/valid-attachments" in reqg)
    ok("GC 请求不带 runtimeConfig", "runtimeConfig" not in reqg)


def main():
    t_range_canonicalize()
    t_rangeset()
    t_iterator()
    t_allocator()
    t_runtime()
    print("通过 %d 项，失败 %d 项" % (PASSED, len(FAILED)))
    if FAILED:
        for f in FAILED:
            print("  - %s" % f)
        sys.exit(1)


if __name__ == "__main__":
    main()

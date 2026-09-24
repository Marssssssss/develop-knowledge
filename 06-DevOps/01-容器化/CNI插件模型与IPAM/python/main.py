"""CNI 插件模型与 host-local IPAM —— 演示入口。"""

from cni_ipam import IPAllocator, Plugin, Range, RangeIter, Store, make_rangeset
from cni_range import V6_LEN, int_to_ip, ip_to_str, mask_of, parse_v4

CONF = {
    "cniVersion": "1.1.0",
    "name": "dbnet",
    "plugins": [{"type": "bridge", "bridge": "cni0",
                 "ipam": {"type": "host-local", "subnet": "10.1.0.0/24"}},
                {"type": "tuning"}, {"type": "portmap"}],
}


def show(title):
    print("\n== %s ==" % title)


def main():
    show("1. /24 的规范化默认值")
    r = Range(parse_v4("10.1.0.0"), mask_of(24))
    print("canonicalize 错误:", r.canonicalize())
    print("gateway=%s range=%s" % (ip_to_str(r.gateway), r))

    show("2. /30 与 /31 的边界")
    for cidr in ("10.0.0.0/30", "10.0.0.0/31", "10.0.0.0/32"):
        net, pfx = cidr.split("/")
        rr = Range(parse_v4(net), mask_of(int(pfx)))
        print("%-12s -> %s" % (cidr, rr.canonicalize() or "可用范围 %s" % rr))

    show("3. 轮询分配（crash-loop 不会立刻拿回同一 IP）")
    rs = make_rangeset(["10.2.0.0/29"])
    alloc = IPAllocator(rs, Store(), "0")
    first = alloc.get("crash", "eth0")["address"]
    alloc.release("crash", "eth0")
    second = alloc.get("crash", "eth0")["address"]
    print("首次=%s 释放后再分配=%s" % (first, second))

    show("4. 迭代器跨 range 与网关跳过")
    rs2 = make_rangeset(["10.1.0.0/24", "10.1.1.0/24"])
    it, seq = RangeIter(rs2, 0, None, rs2[0].range_start), []
    while True:
        ip, _ = it.next()
        if ip is None:
            break
        seq.append(ip)
    print("两个 /24 可分配 %d 个，跨 range 后首个=%s" % (len(seq), ip_to_str(seq[253])))

    show("5. 链式执行：ADD 正序 / DEL 逆序")
    ps = [Plugin("bridge", {"ips": ["10.1.0.2"]}), Plugin("tuning"),
          Plugin("portmap", {"final": True})]
    from cni_ipam import Runtime
    rt = Runtime(CONF, ps)
    final, err = rt.add()
    print("ADD 顺序:", [p.type for p in ps])
    print("ADD 结果:", final)
    rt.delete(final)
    print("DEL 后每个插件收到的 prevResult 都带 final:",
          all(p.seen[-1][1].get("final") for p in ps))

    show("6. 地址族不匹配")
    print("v4 range 容纳 v6 地址:", r.contains(int_to_ip(0x20010db8, V6_LEN)))


if __name__ == "__main__":
    main()

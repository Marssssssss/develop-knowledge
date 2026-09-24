"""CNI host-local 分配器（轮询）与运行时链式执行。

逐行转写自：
  plugins/ipam/host-local/backend/allocator/allocator.go （Get / GetIter / Next）
  containernetworking/cni SPEC.md §2 执行协议 与 §3 配置列表的生命周期与顺序
"""

from cni_range import Range, RangeSet, ip_to_str, next_ip, parse_v4, mask_of


class Store:
    """backend.Store 的最小模型：谁占了哪个 IP + 每个 rangeID 的 lastReservedIP。"""

    def __init__(self):
        self.reserved = {}       # ip -> (id, ifname)
        self.last_reserved = {}  # rangeID -> ip

    def reserve(self, cid, ifname, ip, range_id):
        if ip in self.reserved:
            return False
        self.reserved[ip] = (cid, ifname)
        self.last_reserved[range_id] = ip
        return True

    def get_by_id(self, cid, ifname):
        return [ip for ip, who in self.reserved.items() if who == (cid, ifname)]

    def release_by_id(self, cid, ifname):
        for ip in self.get_by_id(cid, ifname):
            del self.reserved[ip]

    def last_reserved_ip(self, range_id):
        return self.last_reserved.get(range_id)


class RangeIter:
    """对应 Go 的 RangeIter：跨 range 轮询，起点是 lastReservedIP。"""

    def __init__(self, rangeset, start_range_idx=0, cur=None, start_ip=None):
        self.rangeset = rangeset
        self.range_idx = start_range_idx
        self.cur = cur
        self.start_ip = start_ip

    def next(self):
        r = self.rangeset[self.range_idx]
        # 第一次迭代：从 rangeStart 开始（含）
        if self.cur is None:
            self.cur = r.range_start
            self.start_ip = self.cur
            if self.cur == r.gateway:
                return self.next()
            return self.cur, r.gateway
        # 到本 range 末端就切到下一个 range 的 rangeStart（RangeEnd 含）
        if self.cur == r.range_end:
            self.range_idx = (self.range_idx + 1) % len(self.rangeset)
            r = self.rangeset[self.range_idx]
            self.cur = r.range_start
        else:
            self.cur = next_ip(self.cur)
        if self.start_ip is None:
            self.start_ip = self.cur
        elif self.cur == self.start_ip:
            return None, None       # 绕回起点，穷尽
        if self.cur == r.gateway:
            return self.next()
        return self.cur, r.gateway


class IPAllocator:
    def __init__(self, rangeset, store, range_id="0"):
        self.rangeset = rangeset
        self.store = store
        self.range_id = range_id

    def get_iter(self):
        """对应 GetIter()：lastReservedIP 在集合内就从它开始，否则从 0 号 range 头。"""
        start_from_last = False
        last = self.store.last_reserved_ip(self.range_id)
        if last is not None:
            start_from_last = self.rangeset.contains(last)
        if start_from_last:
            for i, r in enumerate(self.rangeset.ranges):
                if r.contains(last):
                    return RangeIter(self.rangeset, start_range_idx=i, cur=last)
        return RangeIter(self.rangeset, 0, None, self.rangeset[0].range_start)

    def get(self, cid, ifname, requested_ip=None):
        if requested_ip is not None:
            r, err = self.rangeset.range_for(requested_ip)
            if err:
                raise ValueError(err)
            if requested_ip == r.gateway:
                raise ValueError("requested ip %s is subnet's gateway"
                                 % ip_to_str(requested_ip))
            if not self.store.reserve(cid, ifname, requested_ip, self.range_id):
                raise ValueError("requested IP address %s is not available in "
                                 "range set %s" % (ip_to_str(requested_ip),
                                                   self.rangeset))
            return {"address": ip_to_str(requested_ip),
                    "subnet": r.subnet_str(), "gateway": ip_to_str(r.gateway)}
        # SPEC 不允许同一 (containerID, ifname) 重复分配
        for allocated in self.store.get_by_id(cid, ifname):
            if self.rangeset.contains(allocated):
                raise ValueError("%s has been allocated to %s, duplicate "
                                 "allocation is not allowed"
                                 % (ip_to_str(allocated), cid))
        it = self.get_iter()
        while True:
            ip, gw = it.next()
            if ip is None:
                break
            if self.store.reserve(cid, ifname, ip, self.range_id):
                r, _ = self.rangeset.range_for(ip)
                return {"address": ip_to_str(ip), "subnet": r.subnet_str(),
                        "gateway": ip_to_str(gw)}
        raise ValueError("no IP addresses available in range set: %s"
                         % self.rangeset)

    def release(self, cid, ifname):
        self.store.release_by_id(cid, ifname)


# ---------------------------------------------------------------- 运行时链式执行

class Plugin:
    """一个 CNI 插件的最小实现：记录收到的 prevResult 并返回自己的 result。"""

    def __init__(self, ptype, result=None, fail_on=None, add_error=None):
        self.type = ptype
        self.result = result or {"cniVersion": "1.1.0", "ips": [ptype]}
        self.fail_on = fail_on      # 在哪个 CNI_COMMAND 上失败
        self.add_error = add_error or (ptype + " failed")
        self.seen = []              # (command, prevResult)

    def invoke(self, command, prev_result):
        self.seen.append((command, prev_result))
        if self.fail_on == command:
            return None, self.add_error
        return self.result, None


def derive_request(plugin_conf, net_conf, prev_result, with_attachment):
    """SPEC §3 末节：请求配置 = 插件配置 + cniVersion/name + 附件参数 + prevResult。"""
    req = {k: v for k, v in plugin_conf.items()}
    req["cniVersion"] = "1.1.0"
    req["name"] = net_conf["name"]
    req.pop("capabilities", None)
    if with_attachment:
        req["runtimeConfig"] = net_conf.get("runtimeConfig", {})
        if prev_result is not None:
            req["prevResult"] = prev_result
    else:
        req["cni.dev/valid-attachments"] = []
    return req


class Runtime:
    def __init__(self, net_conf, plugins):
        self.net_conf = net_conf
        self.plugins = {p.type: p for p in plugins}

    def _lookup(self, ptype):
        if ptype not in self.plugins:
            raise KeyError("plugin %s not found in CNI_PATH" % ptype)
        return self.plugins[ptype]

    def add(self, cid="c1", ifname="eth0", netns="/run/netns/x"):
        """§3 Adding：正序执行；首个插件无 prevResult；出错即停。"""
        prev = None
        for conf in self.net_conf["plugins"]:
            p = self._lookup(conf["type"])
            req = derive_request(conf, self.net_conf, prev, True)
            res, err = p.invoke("ADD", req.get("prevResult"))
            if err:
                return None, err
            prev = res
        return prev, None

    def delete(self, final_add_result, cid="c1", ifname="eth0"):
        """§3 Deleting：逆序执行；每个插件拿到的都是 add 的最终结果。"""
        for conf in reversed(self.net_conf["plugins"]):
            p = self._lookup(conf["type"])
            req = derive_request(conf, self.net_conf, final_add_result, True)
            res, err = p.invoke("DEL", req.get("prevResult"))
            if err:
                return None, err
        return {"ok": True}, None

    def check(self, final_add_result):
        """§3 Checking：正序；disableCheck 直接返回成功。"""
        if self.net_conf.get("disableCheck"):
            return {"ok": True}, None
        for conf in self.net_conf["plugins"]:
            p = self._lookup(conf["type"])
            req = derive_request(conf, self.net_conf, final_add_result, True)
            res, err = p.invoke("CHECK", req.get("prevResult"))
            if err:
                return None, err
        return {"ok": True}, None

    def gc(self):
        """§3 GC：无附件参数；出错不中断，收集全部错误。"""
        errors = []
        for conf in self.net_conf["plugins"]:
            p = self._lookup(conf["type"])
            derive_request(conf, self.net_conf, None, False)
            _, err = p.invoke("GC", None)
            if err:
                errors.append(err)
        return errors


def range_from_subnet(subnet, gateway=None, start=None, end=None):
    net, prefix = subnet.split("/")
    r = Range(parse_v4(net), mask_of(int(prefix)),
              parse_v4(start) if start else None,
              parse_v4(end) if end else None,
              parse_v4(gateway) if gateway else None)
    return r


def make_rangeset(subnets):
    rs = RangeSet([range_from_subnet(s) for s in subnets])
    err = rs.canonicalize()
    if err:
        raise ValueError(err)
    return rs

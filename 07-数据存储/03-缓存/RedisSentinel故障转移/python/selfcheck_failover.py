"""自检：故障转移状态机（failover.py）。与 selfcheck_sentinel.py 配套。"""

from failover import (
    sentinel_failover_detect_end, sentinel_failover_reconf_next_slave,
    sentinel_failover_state_machine, sentinel_refresh_slave_reconf,
)
from sentinel import (
    MASTER_LINK_STATUS_UP, SENTINEL_DEFAULT_FAILOVER_TIMEOUT, SENTINEL_ELECTION_TIMEOUT,
    SENTINEL_INFO_PERIOD, SENTINEL_PING_PERIOD, SENTINEL_SLAVE_RECONF_TIMEOUT,
    SRI_FAILOVER_IN_PROGRESS, SRI_O_DOWN, SRI_PROMOTED, SRI_RECONF_DONE,
    SRI_RECONF_INPROG, SRI_RECONF_SENT, SRI_S_DOWN, SRI_SLAVE, STATE_NONE,
    STATE_RECONF_SLAVES, STATE_SELECT_SLAVE, STATE_SEND_SLAVEOF_NOONE,
    STATE_UPDATE_CONFIG, STATE_WAIT_PROMOTION, STATE_WAIT_START, Instance, Sentinel,
    compare_slaves_for_promotion, sentinel_select_slave, sentinel_start_failover,
)

PASS = 0
FAIL = []


def check(label, got, expect):
    global PASS
    if got == expect:
        PASS += 1
    else:
        FAIL.append(f"{label}: got {got!r}, expect {expect!r}")


class Fixed:
    def __init__(self, value=0):
        self.value = value

    def __call__(self):
        return self.value


rnd = Fixed(0)


def mk_slave(name, priority=100, offset=0, runid=None, addr_host=None, addr_port=0):
    s = Instance(name, runid=runid, addr_host=addr_host, addr_port=addr_port)
    s.slave_priority = priority
    s.slave_repl_offset = offset
    return s


# ------------------------------------------------------------ 选从排序
def mk_slave(name, priority=100, offset=0, runid=None, addr_host=None, addr_port=0):
    s = Instance(name, runid=runid, addr_host=addr_host, addr_port=addr_port)
    s.slave_priority = priority
    s.slave_repl_offset = offset
    return s


a = mk_slave("a", priority=100, offset=10, runid="a")
b = mk_slave("b", priority=50, offset=1, runid="b")
check("priority 小者优先", compare_slaves_for_promotion(b, a) < 0, True)
c = mk_slave("c", priority=100, offset=99, runid="c")
check("同 priority 时 offset 大者优先", compare_slaves_for_promotion(c, a) < 0, True)
d = mk_slave("d", priority=100, offset=10, runid="a")
e = mk_slave("e", priority=100, offset=10, runid="b")
check("同 priority 同 offset 时 runid 小者优先", compare_slaves_for_promotion(d, e) < 0, True)
f = mk_slave("f", priority=100, offset=10, runid=None)
check("runid 为 NULL 视为最大", compare_slaves_for_promotion(f, e) > 0, True)

# ------------------------------------------------------------ 选从过滤
ms = Instance("ms", quorum=2)
good = mk_slave("good", priority=100, offset=5, runid="g1")
good.last_avail_time = 1000
good.info_refresh = 1000
ms.slaves = {"g1": good}
check("健康从节点被选中", sentinel_select_slave(ms, 1000).name, "good")
zero = mk_slave("zero", priority=0, offset=99, runid="z1")
zero.last_avail_time = 1000
zero.info_refresh = 1000
ms.slaves["z1"] = zero
check("priority=0 被剔除（即使 offset 更大）",
      sentinel_select_slave(ms, 1000).name, "good")
down = mk_slave("down", priority=10, offset=99, runid="d1")
down.flags |= SRI_S_DOWN
down.last_avail_time = 1000
down.info_refresh = 1000
ms.slaves["d1"] = down
check("SDOWN 从节点被剔除", sentinel_select_slave(ms, 1000).name, "good")
stale = mk_slave("stale", priority=10, offset=99, runid="s1")
stale.last_avail_time = 1000
stale.info_refresh = -SENTINEL_INFO_PERIOD * 3   # 1000 - (-30000) = 31000 > 30000
ms.slaves["s1"] = stale
check("INFO 过期被剔除（master 未 SDOWN 时用 info_period*3）",
      sentinel_select_slave(ms, 1000).name, "good")
fresh = mk_slave("fresh", priority=10, offset=99, runid="fr")
fresh.last_avail_time = 1000
fresh.info_refresh = 1000 - SENTINEL_INFO_PERIOD * 3   # 差值恰好 30000，不算过期
ms.slaves["fr"] = fresh
check("INFO 差值恰好等于 info_period*3 不算过期（严格大于才剔除）",
      sentinel_select_slave(ms, 1000).name, "fresh")
del ms.slaves["fr"]
disconnected = mk_slave("disc", priority=10, offset=99, runid="q1")
disconnected.link_disconnected = True
disconnected.last_avail_time = 1000
disconnected.info_refresh = 1000
ms.slaves["q1"] = disconnected
check("链路断开被剔除", sentinel_select_slave(ms, 1000).name, "good")
avail = mk_slave("avail", priority=10, offset=99, runid="v1")
avail.last_avail_time = 1000 - SENTINEL_PING_PERIOD * 5   # 差值恰好 5000，不算过期
avail.info_refresh = 1000
ms.slaves["v1"] = avail
check("last_avail 差值恰好等于 ping*5 不算过期（严格大于才剔除）",
      sentinel_select_slave(ms, 1000).name, "avail")
avail.last_avail_time = 1000 - SENTINEL_PING_PERIOD * 5 - 1
check("last_avail 超过 ping*5 → 剔除", sentinel_select_slave(ms, 1000).name, "good")
avail.last_avail_time = 1000 - SENTINEL_PING_PERIOD * 5
ms.flags |= SRI_S_DOWN
ms.s_down_since_time = 1000
check("master SDOWN 后 INFO 放宽到 ping*5，stale(-30000) 仍被剔除",
      sentinel_select_slave(ms, 1000).name, "avail")

# ------------------------------------------------------ 状态机：选不出从节点
stf = Sentinel("me")
mf = Instance("mf", quorum=2)
mf.flags |= SRI_O_DOWN
sentinel_start_failover(stf, mf, 1000, rnd)
mf.failover_state = STATE_SELECT_SLAVE
events = []
sentinel_failover_state_machine(stf, mf, 1000, rnd, events)
check("选不到从节点 → 中止并回到 NONE", mf.failover_state, STATE_NONE)
check("中止事件已记", "-failover-abort-no-good-slave" in events, True)
check("中止后 FAILOVER_IN_PROGRESS 被清",
      bool(mf.flags & SRI_FAILOVER_IN_PROGRESS), False)

# ------------------------------------------------ 状态机：走到 wait_promotion
stg = Sentinel("me")
mg = Instance("mg", quorum=2)
mg.flags |= SRI_O_DOWN
cand = mk_slave("cand", priority=100, offset=1, runid="cand")
cand.last_avail_time = 1000
cand.info_refresh = 1000
mg.slaves = {"cand": cand}
sentinel_start_failover(stg, mg, 1000, rnd)
mg.failover_state = STATE_SELECT_SLAVE
sentinel_failover_state_machine(stg, mg, 1000, rnd)
check("选中从节点 → SEND_SLAVEOF_NOONE", mg.failover_state, STATE_SEND_SLAVEOF_NOONE)
check("从节点打上 PROMOTED", bool(cand.flags & SRI_PROMOTED), True)
sentinel_failover_state_machine(stg, mg, 1000, rnd)
check("发 SLAVEOF NO ONE → WAIT_PROMOTION", mg.failover_state, STATE_WAIT_PROMOTION)
sentinel_failover_state_machine(stg, mg, 1000, rnd)
check("WAIT_PROMOTION 未超时 → 停在该态（这一态只处理超时）",
      mg.failover_state, STATE_WAIT_PROMOTION)
sentinel_failover_state_machine(stg, mg, 1000 + SENTINEL_DEFAULT_FAILOVER_TIMEOUT + 1, rnd)
check("WAIT_PROMOTION 超时 → 中止", mg.failover_state, STATE_NONE)

# ------------------------------------------- 状态机：重配置与收尾（成对）
sth = Sentinel("me")
mh = Instance("mh", quorum=2)
mh.flags |= SRI_O_DOWN
s_a = mk_slave("sa", priority=100, offset=1, runid="sa")
s_b = mk_slave("sb", priority=100, offset=1, runid="sb")
for s in (s_a, s_b):
    s.last_avail_time = 1000
    s.info_refresh = 1000
promoted = mk_slave("pm", priority=100, offset=1, runid="pm",
                    addr_host="10.0.0.9", addr_port=6379)
promoted.last_avail_time = 1000
promoted.info_refresh = 1000
promoted.flags |= SRI_SLAVE
for s in (s_a, s_b):
    s.flags |= SRI_SLAVE
mh.slaves = {"pm": promoted, "sa": s_a, "sb": s_b}
sentinel_start_failover(sth, mh, 1000, rnd)
mh.failover_state = STATE_SELECT_SLAVE
sentinel_failover_state_machine(sth, mh, 1000, rnd)
sentinel_failover_state_machine(sth, mh, 1000, rnd)
mh.failover_state = STATE_RECONF_SLAVES
mh.failover_state_change_time = 1000
events = []
sentinel_failover_reconf_next_slave(mh, 1000, events)
check("parallel_syncs=1 → 一次只发一个 SLAVEOF",
      sum(1 for e in events if e.startswith("+slave-reconf-sent")), 1)
check("第一个从节点进入 RECONF_SENT", bool(s_a.flags & SRI_RECONF_SENT), True)
check("第二个从节点还没轮到", bool(s_b.flags & SRI_RECONF_SENT), False)
check("还有未配置的从节点 → 不收尾", mh.failover_state, STATE_RECONF_SLAVES)

# 状态机自己**不会**把 SENT 推到 DONE，这一步由 INFO 解析触发（成对）
sentinel_refresh_slave_reconf(mh, s_a, events=events)
check("INFO 未报告新 master 前，SENT 不变", bool(s_a.flags & SRI_RECONF_SENT), True)
check("INFO 未报告新 master 前，没有 INPROG", bool(s_a.flags & SRI_RECONF_INPROG), False)
s_a.slave_master_host = "10.0.0.9"
s_a.slave_master_port = 6379
sentinel_refresh_slave_reconf(mh, s_a, events=events)
check("INFO 报告新 master → SENT 转 INPROG",
      bool(s_a.flags & SRI_RECONF_INPROG) and not (s_a.flags & SRI_RECONF_SENT), True)
sentinel_refresh_slave_reconf(mh, s_a, events=events)
check("链路仍 DOWN → 停在 INPROG", bool(s_a.flags & SRI_RECONF_INPROG), True)
s_a.slave_master_link_status = MASTER_LINK_STATUS_UP
sentinel_refresh_slave_reconf(mh, s_a, events=events)
check("链路 UP → INPROG 转 DONE", bool(s_a.flags & SRI_RECONF_DONE), True)

# 槽位腾出来了，第二个从节点才轮得到
sentinel_failover_reconf_next_slave(mh, 2000, events)
check("第一个完成后第二个才收到 SLAVEOF", bool(s_b.flags & SRI_RECONF_SENT), True)
s_b.slave_master_host = "10.0.0.9"
s_b.slave_master_port = 6379
s_b.slave_master_link_status = MASTER_LINK_STATUS_UP
sentinel_refresh_slave_reconf(mh, s_b, events=events)
sentinel_failover_reconf_next_slave(mh, 3000, events)
check("全部配置完 → UPDATE_CONFIG", mh.failover_state, STATE_UPDATE_CONFIG)

# ---------------- RECONF_SENT 超时：置 DONE 但**同一个奴隶还会再发一次**（成对）
mt = Instance("mt", quorum=2)
t1 = mk_slave("t1", priority=100, offset=1, runid="t1", addr_host="10.0.0.1", addr_port=6379)
t1.flags |= SRI_SLAVE
t1.last_avail_time = 1000
t1.info_refresh = 1000
pmt = mk_slave("pmt", priority=100, offset=1, runid="pmt",
               addr_host="10.0.0.9", addr_port=6379)
pmt.flags |= SRI_SLAVE
mt.promoted_slave = pmt
mt.slaves = {"t1": t1}
mt.failover_state = STATE_RECONF_SLAVES
mt.failover_state_change_time = 1000

# parallel_syncs=1 时，while 条件在取第一个元素前就为假，超时分支**根本进不去**
mt.parallel_syncs = 1
sentinel_failover_reconf_next_slave(mt, 1000, events)
check("parallel_syncs=1 且已有 1 个 in_progress → 循环体一次都不跑",
      bool(t1.flags & SRI_RECONF_SENT), True)
sentinel_failover_reconf_next_slave(mt, 1000 + SENTINEL_SLAVE_RECONF_TIMEOUT + 1, events)
check("parallel_syncs=1 时超时分支不可达 → DONE 仍未被置",
      bool(t1.flags & SRI_RECONF_DONE), False)

# 放开并发额度后，超时分支才生效
mt.parallel_syncs = 2
sentinel_failover_reconf_next_slave(mt, 1000, events)
check("超时前：只有 SENT", bool(t1.flags & SRI_RECONF_SENT)
      and not (t1.flags & SRI_RECONF_DONE), True)
sentinel_failover_reconf_next_slave(mt, 1000 + SENTINEL_SLAVE_RECONF_TIMEOUT + 1, events)
check("超时后：DONE 被置上", bool(t1.flags & SRI_RECONF_DONE), True)
check("超时后：因为没有 continue，SENT 又被置回（会补发一次 SLAVEOF）",
      bool(t1.flags & SRI_RECONF_SENT), True)
check("置 DONE 后 detect_end 不再等它 → UPDATE_CONFIG",
      mt.failover_state, STATE_UPDATE_CONFIG)

# ------------------------- detect_end 的负控：promoted 不可用则不收尾（成对）
mi = Instance("mi", quorum=2)
promoted2 = mk_slave("pm2", priority=100, offset=1, runid="pm2")
promoted2.flags |= SRI_S_DOWN
mi.promoted_slave = promoted2
mi.failover_state = STATE_RECONF_SLAVES
mi.failover_state_change_time = 0
sentinel_failover_detect_end(mi, 1000)
check("promoted 处于 SDOWN → 不收尾", mi.failover_state, STATE_RECONF_SLAVES)
mi2 = Instance("mi2", quorum=2)
mi2.promoted_slave = None
mi2.failover_state = STATE_RECONF_SLAVES
sentinel_failover_detect_end(mi2, 1000)
check("promoted 为 None → 不收尾", mi2.failover_state, STATE_RECONF_SLAVES)
mi3 = Instance("mi3", quorum=2)
healthy = mk_slave("h", priority=100, offset=1, runid="h")
mi3.promoted_slave = healthy
mi3.slaves = {"x": mk_slave("x", runid="x")}
mi3.slaves["x"].flags |= SRI_S_DOWN
mi3.failover_state = STATE_RECONF_SLAVES
mi3.failover_state_change_time = 0
sentinel_failover_detect_end(mi3, 1000)
check("唯一未配置的从节点处于 SDOWN → 视为已收尾", mi3.failover_state, STATE_UPDATE_CONFIG)

if FAIL:
    print(f"FAILED {len(FAIL)} / {PASS + len(FAIL)}")
    for f in FAIL:
        print("  -", f)
    raise SystemExit(1)
print(f"failover selfcheck: {PASS} assertions passed")


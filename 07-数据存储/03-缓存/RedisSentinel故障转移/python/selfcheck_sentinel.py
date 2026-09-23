"""自检：Redis Sentinel（sentinel.py）。误报集/漏报集成对构造，随机量用确定性源钉死。"""

from sentinel import (
    SENTINEL_DEFAULT_DOWN_AFTER, SENTINEL_DEFAULT_FAILOVER_TIMEOUT,
    SENTINEL_ELECTION_TIMEOUT, SENTINEL_MAX_DESYNC, SENTINEL_PING_PERIOD,
    SRI_FAILOVER_IN_PROGRESS, SRI_MASTER_DOWN, SRI_O_DOWN, SRI_S_DOWN,
    STATE_NONE, STATE_RECONF_SLAVES, STATE_WAIT_PROMOTION, STATE_WAIT_START,
    FAILOVER_STATE_NAMES, Instance, Sentinel,
    sentinel_check_objectively_down, sentinel_get_leader, sentinel_start_failover,
    sentinel_start_failover_if_needed, sentinel_vote_leader,
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

# ------------------------------------------------------------------ 常量
check("ping 周期", SENTINEL_PING_PERIOD, 1000)
check("去同步上限", SENTINEL_MAX_DESYNC, 1000)
check("默认 down-after", SENTINEL_DEFAULT_DOWN_AFTER, 30000)
check("默认 failover-timeout", SENTINEL_DEFAULT_FAILOVER_TIMEOUT, 180000)
check("选举超时", SENTINEL_ELECTION_TIMEOUT, 10000)
check("状态名共 7 个", len(FAILOVER_STATE_NAMES), 7)
check("wait_promotion 序号", STATE_WAIT_PROMOTION, 4)
check("reconf_slaves 序号", STATE_RECONF_SLAVES, 5)

# ------------------------------------------------------------------ ODOWN
m = Instance("mymaster", quorum=2)
s1, s2, s3 = (Instance(f"s{i}", runid=f"rid{i}") for i in (1, 2, 3))
m.sentinels = {s.runid: s for s in (s1, s2, s3)}
m.flags |= SRI_S_DOWN
check("只有自己 SDOWN（quorum=1 < 2）→ 不是 ODOWN",
      sentinel_check_objectively_down(m, 1000), False)
s1.flags |= SRI_MASTER_DOWN
check("自己 + 1 个（quorum=2 >= 2）→ ODOWN",
      sentinel_check_objectively_down(m, 1000), True)
check("o_down_since_time 已记", m.o_down_since_time, 1000)
s1.flags &= ~SRI_MASTER_DOWN
check("同伴撤回 → -odown", sentinel_check_objectively_down(m, 2000), False)
check("ODOWN 标记已清", bool(m.flags & SRI_O_DOWN), False)
m2 = Instance("m2", quorum=2)
check("自己没 SDOWN 时别人全说 DOWN 也不算 ODOWN",
      sentinel_check_objectively_down(m2, 1000) if not (m2.sentinels) else False, False)
m2.sentinels = {s.runid: s for s in (Instance("a", "a"), Instance("b", "b"))}
for ri in m2.sentinels.values():
    ri.flags |= SRI_MASTER_DOWN
check("自己不 SDOWN → quorum 恒为 0 → 不 ODOWN",
      sentinel_check_objectively_down(m2, 1000), False)

# ------------------------------------------------------- 投票：一个纪元一票
st = Sentinel("sent-A")
mv = Instance("mv", quorum=2)
leader, epoch = sentinel_vote_leader(st, mv, 5, "sent-A", 1000, rnd)
check("纪元推进到 5", st.current_epoch, 5)
check("首票投给自己", leader, "sent-A")
check("leader_epoch = current_epoch（不是 req_epoch）", epoch, 5)
sentinel_vote_leader(st, mv, 5, "sent-B", 1000, rnd)
check("同一纪元第二次请求不改写 leader", mv.leader, "sent-A")
sentinel_vote_leader(st, mv, 6, "sent-B", 1000, rnd)
check("更大纪元可以改投", mv.leader, "sent-B")
check("leader_epoch 跟到 6", mv.leader_epoch, 6)
sentinel_vote_leader(st, mv, 4, "sent-C", 1000, rnd)
check("小于 leader_epoch 的请求被忽略", mv.leader, "sent-B")

# --------------------------- 投票给非自己时，起始时间被推后（成对）
st2 = Sentinel("me")
mv2 = Instance("mv2", quorum=2)
mv2.failover_start_time = 500
sentinel_vote_leader(st2, mv2, 3, "me", 1000, rnd)
check("投给自己 → 起始时间不动", mv2.failover_start_time, 500)
mv3 = Instance("mv3", quorum=2)
mv3.failover_start_time = 500
sentinel_vote_leader(st2, mv3, 3, "other", 1000, rnd)
check("投给别人 → 起始时间加去同步量", mv3.failover_start_time, 1000 + rnd() % SENTINEL_MAX_DESYNC)

# ------------------------------------------------- 领导者：双门槛（成对构造）
def build_cluster(n_others, quorum, votes_for):
    """votes_for: {runid: 票数} 构造其他 sentinel 的 leader 视图。"""
    state = Sentinel("me")
    master = Instance("cluster", quorum=quorum)
    others = {}
    tick = 0
    for runid, count in votes_for.items():
        for k in range(count):
            ri = Instance(f"n{tick}", runid=f"n{tick}")
            tick += 1
            ri.leader = runid
            ri.leader_epoch = 1
            others[ri.runid] = ri
    for _ in range(max(0, n_others - tick)):
        ri = Instance(f"n{tick}", runid=f"n{tick}")
        tick += 1
        others[ri.runid] = ri
    master.sentinels = others
    state.current_epoch = 1
    return state, master


# 5 个 sentinel（自己 + 4），绝对多数 = 5/2+1 = 3
st3, mc = build_cluster(4, 2, {"me": 2})
check("自己拿 2 票 + 自投 1 = 3，过绝对多数与 quorum → 当选",
      sentinel_get_leader(st3, mc, 1, 1000, rnd), "me")
st4, mc2 = build_cluster(4, 2, {"me": 1})
check("自己只拿 1 票 + 自投 = 2 < 3 → 无人当选",
      sentinel_get_leader(st4, mc2, 1, 1000, rnd), None)
# 绝对多数过了但门槛 quorum 没过：5 个里 3 票 >= 3，但 quorum=5
st5, mc3 = build_cluster(4, 5, {"me": 2})
check("过绝对多数但 max_votes(3) < quorum(5) → 无人当选",
      sentinel_get_leader(st5, mc3, 1, 1000, rnd), None)
# 别人拿多数：自己应顺势投给 winner
st6, mc4 = build_cluster(4, 2, {"peer": 3})
check("多数票在别人手里 → winner 是别人",
      sentinel_get_leader(st6, mc4, 1, 1000, rnd), "peer")

# ------------------------------------------------------- 启动故障转移三道门
st7 = Sentinel("me")
mstart = Instance("mstart", quorum=2)
check("未 ODOWN → 不启动", sentinel_start_failover_if_needed(st7, mstart, 0, rnd), False)
mstart.flags |= SRI_O_DOWN
mstart.failover_start_time = 0
check("ODOWN 但冷却未满（0 - 0 = 0 < 360000）→ 不启动",
      sentinel_start_failover_if_needed(st7, mstart, 0, rnd), False)
cool = SENTINEL_DEFAULT_FAILOVER_TIMEOUT * 2
check("冷却恰好满 → 启动", sentinel_start_failover_if_needed(st7, mstart, cool, rnd), True)
check("纪元自增到 1", st7.current_epoch, 1)
check("failover_epoch 记下 1", mstart.failover_epoch, 1)
check("进入 WAIT_START", mstart.failover_state, STATE_WAIT_START)
check("打上 FAILOVER_IN_PROGRESS", bool(mstart.flags & SRI_FAILOVER_IN_PROGRESS), True)
check("起始时间带去同步量", mstart.failover_start_time, cool + rnd() % SENTINEL_MAX_DESYNC)
check("进行中 → 再次请求不启动",
      sentinel_start_failover_if_needed(st7, mstart, cool * 2, rnd), False)
mstart.flags &= ~SRI_FAILOVER_IN_PROGRESS
mstart.failover_start_time = 10 ** 6
check("距上次 < failover_timeout*2 → 冷却拦截",
      sentinel_start_failover_if_needed(st7, mstart, 10 ** 6 + 1000, rnd), False)
check("距上次 = failover_timeout*2 → 放行",
      sentinel_start_failover_if_needed(st7, mstart, 10 ** 6 + 360000, rnd), True)

if FAIL:
    print(f"FAILED {len(FAIL)} / {PASS + len(FAIL)}")
    for f in FAIL:
        print("  -", f)
    raise SystemExit(1)
print(f"sentinel selfcheck: {PASS} assertions passed")

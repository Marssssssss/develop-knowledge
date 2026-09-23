"""Redis Sentinel 的主观/客观下线判定、纪元单调的领导者选举与故障转移状态机。

事实来源（本轮实读，非记忆）：redis/redis@unstable src/sentinel.c
  - 47-51：SRI_S_DOWN (1<<3) / SRI_O_DOWN (1<<4) / SRI_MASTER_DOWN (1<<5) / SRI_FAILOVER_IN_PROGRESS (1<<6)
  - 63-81：SENTINEL_PING_PERIOD 1000 / SENTINEL_MAX_DESYNC 1000 / SENTINEL_DEFAULT_PARALLEL_SYNCS 1
  - 65-77：sentinel_info_period 10000 / sentinel_publish_period 2000 / sentinel_min_link_reconnect_period 15000
           sentinel_slave_reconf_timeout 10000 / sentinel_election_timeout 10000
           sentinel_default_down_after 30000 / sentinel_default_failover_timeout 60*3*1000
  - 90-96：SENTINEL_FAILOVER_STATE_* 七态
  - 4654-4684：sentinelCheckObjectivelyDown（quorum 从 1 起算，含自己）
  - 4792-4817：sentinelVoteLeader
  - 4848-4910：sentinelGetLeader（双门槛：绝对多数 + 至少 quorum）
  - 4991-5002：sentinelStartFailover
  - 5015-5042：sentinelStartFailoverIfNeeded（三条门 + failover_timeout*2 冷却）
  - 5074-5145：compareSlavesForPromotion / sentinelSelectSlave
  - 5150-5374：各状态处理函数与 sentinelFailoverStateMachine
"""

from functools import cmp_to_key

SRI_S_DOWN = 1 << 3
SRI_O_DOWN = 1 << 4
SRI_MASTER_DOWN = 1 << 5
SRI_FAILOVER_IN_PROGRESS = 1 << 6
SRI_PROMOTED = 1 << 7
SRI_RECONF_SENT = 1 << 8
SRI_RECONF_INPROG = 1 << 9
SRI_RECONF_DONE = 1 << 10
SRI_SLAVE = 1 << 1
SRI_LEADER = 1 << 17
SRI_FORCE_FAILOVER = 1 << 18

MASTER_LINK_STATUS_DOWN = 0
MASTER_LINK_STATUS_UP = 1

SENTINEL_PING_PERIOD = 1000
SENTINEL_INFO_PERIOD = 10000
SENTINEL_MAX_DESYNC = 1000
SENTINEL_ELECTION_TIMEOUT = 10000
SENTINEL_SLAVE_RECONF_TIMEOUT = 10000
SENTINEL_DEFAULT_DOWN_AFTER = 30000
SENTINEL_DEFAULT_FAILOVER_TIMEOUT = 60 * 3 * 1000
SENTINEL_DEFAULT_PARALLEL_SYNCS = 1

STATE_NONE = 0
STATE_WAIT_START = 1
STATE_SELECT_SLAVE = 2
STATE_SEND_SLAVEOF_NOONE = 3
STATE_WAIT_PROMOTION = 4
STATE_RECONF_SLAVES = 5
STATE_UPDATE_CONFIG = 6

FAILOVER_STATE_NAMES = {
    STATE_NONE: "none",
    STATE_WAIT_START: "wait_start",
    STATE_SELECT_SLAVE: "select_slave",
    STATE_SEND_SLAVEOF_NOONE: "send_slaveof_noone",
    STATE_WAIT_PROMOTION: "wait_promotion",
    STATE_RECONF_SLAVES: "reconf_slaves",
    STATE_UPDATE_CONFIG: "update_config",
}


class Instance:
    """sentinelRedisInstance 的建模（只保留本 demo 用得上的字段）。"""

    def __init__(self, name, runid=None, quorum=2, addr_host=None, addr_port=0):
        self.name = name
        self.runid = runid
        self.addr_host = addr_host
        self.addr_port = addr_port
        self.flags = 0
        self.quorum = quorum
        self.sentinels = {}          # runid -> Instance（其他 sentinel 的视图）
        self.slaves = {}             # runid -> Instance
        self.leader = None
        self.leader_epoch = 0
        self.failover_state = STATE_NONE
        self.failover_state_change_time = 0
        self.failover_epoch = 0
        self.failover_start_time = 0
        self.promoted_slave = None
        self.parallel_syncs = SENTINEL_DEFAULT_PARALLEL_SYNCS
        self.down_after_period = SENTINEL_DEFAULT_DOWN_AFTER
        self.failover_timeout = SENTINEL_DEFAULT_FAILOVER_TIMEOUT
        self.s_down_since_time = 0
        self.o_down_since_time = 0
        # 从节点专用
        self.slave_priority = 100
        self.slave_repl_offset = 0
        self.master_link_down_time = 0
        self.slave_reconf_sent_time = 0
        # INFO 解析侧驱动 SENT → INPROG → DONE 所需的字段
        self.slave_master_host = None
        self.slave_master_port = 0
        self.slave_master_link_status = MASTER_LINK_STATUS_DOWN
        # 链路新鲜度
        self.link_disconnected = False
        self.last_avail_time = 0
        self.info_refresh = 0


class Sentinel:
    """本 sentinel 自己的状态。current_epoch 是全局单调的。"""

    def __init__(self, myid):
        self.myid = myid
        self.current_epoch = 0


# ------------------------------------------------------------------ ODOWN
def sentinel_check_objectively_down(master, now, events=None):
    """sentinel.c:4654：quorum 从 1 起算（自己那一票），再数其他 sentinel 的 SRI_MASTER_DOWN。"""
    quorum = 0
    odown = 0
    if master.flags & SRI_S_DOWN:
        quorum = 1
        for ri in master.sentinels.values():
            if ri.flags & SRI_MASTER_DOWN:
                quorum += 1
        if quorum >= master.quorum:
            odown = 1
    if odown:
        if not (master.flags & SRI_O_DOWN):
            master.flags |= SRI_O_DOWN
            master.o_down_since_time = now
            if events is not None:
                events.append(f"+odown {master.name} #quorum {quorum}/{master.quorum}")
    elif master.flags & SRI_O_DOWN:
        master.flags &= ~SRI_O_DOWN
        if events is not None:
            events.append(f"-odown {master.name}")
    return bool(master.flags & SRI_O_DOWN)


# ------------------------------------------------------------------ 选举
def sentinel_vote_leader(state, master, req_epoch, req_runid, now, rnd):
    """sentinel.c:4792：一个纪元只投一票；leader_epoch 记的是 current_epoch 而不是 req_epoch。"""
    if req_epoch > state.current_epoch:
        state.current_epoch = req_epoch
    if master.leader_epoch < req_epoch and state.current_epoch <= req_epoch:
        master.leader = req_runid
        master.leader_epoch = state.current_epoch
        if master.leader != state.myid:
            master.failover_start_time = now + rnd() % SENTINEL_MAX_DESYNC
    return master.leader, master.leader_epoch


def sentinel_get_leader(state, master, epoch, now, rnd):
    """sentinel.c:4848：胜出需同时满足「绝对多数」与「至少 quorum」两个门槛。"""
    counters = {}
    voters = len(master.sentinels) + 1
    for ri in master.sentinels.values():
        if ri.leader is not None and ri.leader_epoch == state.current_epoch:
            counters[ri.leader] = counters.get(ri.leader, 0) + 1
    winner = None
    max_votes = 0
    for candidate, votes in counters.items():
        if votes > max_votes:
            max_votes = votes
            winner = candidate
    myvote, leader_epoch = sentinel_vote_leader(
        state, master, epoch, winner if winner else state.myid, now, rnd)
    if myvote and leader_epoch == epoch:
        counters[myvote] = counters.get(myvote, 0) + 1
        if counters[myvote] > max_votes:
            max_votes = counters[myvote]
            winner = myvote
    voters_quorum = voters // 2 + 1
    if winner and (max_votes < voters_quorum or max_votes < master.quorum):
        winner = None
    return winner


# -------------------------------------------------------------- 故障转移启动
def sentinel_start_failover(state, master, now, rnd):
    """sentinel.c:4991：纪元自增，起始时间加一个 [0,1000) ms 的随机去同步。"""
    master.failover_state = STATE_WAIT_START
    master.flags |= SRI_FAILOVER_IN_PROGRESS
    state.current_epoch += 1
    master.failover_epoch = state.current_epoch
    master.failover_start_time = now + rnd() % SENTINEL_MAX_DESYNC
    master.failover_state_change_time = now


def sentinel_start_failover_if_needed(state, master, now, rnd):
    """sentinel.c:5015：ODOWN / 无进行中的故障转移 / 距上次 >= failover_timeout*2。"""
    if not (master.flags & SRI_O_DOWN):
        return False
    if master.flags & SRI_FAILOVER_IN_PROGRESS:
        return False
    if now - master.failover_start_time < master.failover_timeout * 2:
        return False
    sentinel_start_failover(state, master, now, rnd)
    return True


# ------------------------------------------------------------------ 选从
def compare_slaves_for_promotion(a, b):
    """sentinel.c:5079：priority 升序 → offset 降序 → runid 字典序升序，NULL runid 视为最大。"""
    if a.slave_priority != b.slave_priority:
        return a.slave_priority - b.slave_priority
    if a.slave_repl_offset > b.slave_repl_offset:
        return -1
    if a.slave_repl_offset < b.slave_repl_offset:
        return 1
    if a.runid is None and b.runid is None:
        return 0
    if a.runid is None:
        return 1
    if b.runid is None:
        return -1
    ra, rb = a.runid.lower(), b.runid.lower()
    return -1 if ra < rb else (1 if ra > rb else 0)


def sentinel_select_slave(master, now):
    """sentinel.c:5105：六道过滤 + 一次排序取首。"""
    max_master_down_time = 0
    if master.flags & SRI_S_DOWN:
        max_master_down_time += now - master.s_down_since_time
    max_master_down_time += master.down_after_period * 10

    candidates = []
    for slave in master.slaves.values():
        if slave.flags & (SRI_S_DOWN | SRI_O_DOWN):
            continue
        if slave.link_disconnected:
            continue
        if now - slave.last_avail_time > SENTINEL_PING_PERIOD * 5:
            continue
        if slave.slave_priority == 0:
            continue
        info_validity = (SENTINEL_PING_PERIOD * 5 if (master.flags & SRI_S_DOWN)
                         else SENTINEL_INFO_PERIOD * 3)
        if now - slave.info_refresh > info_validity:
            continue
        if slave.master_link_down_time > max_master_down_time:
            continue
        candidates.append(slave)
    if not candidates:
        return None
    return sorted(candidates, key=cmp_to_key(compare_slaves_for_promotion))[0]

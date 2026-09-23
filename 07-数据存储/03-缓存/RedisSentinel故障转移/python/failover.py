"""Redis Sentinel 的故障转移状态机（与 sentinel.py 配套）。

事实来源（本轮实读）：redis/redis@unstable src/sentinel.c
  - 5150-5172：sentinelFailoverWaitStart（选举超时 = min(election_timeout, failover_timeout)）
  - 5178-5192：sentinelFailoverSelectSlave（选不到好从节点即中止）
  - 5194-5225：sentinelFailoverSendSlaveOfNoOne / sentinelFailoverWaitPromotion
  - 5241-5288：sentinelFailoverDetectEnd（超时强制收尾）
  - 5292-5342：sentinelFailoverReconfNextSlave（parallel_syncs 限流 + 10s 超时）
  - 2748-2772：INFO 解析驱动的 SENT -> INPROG -> DONE
  - 5374-5397：sentinelFailoverStateMachine
  - 5404-5415：sentinelAbortFailover（只能在 <= WAIT_PROMOTION 时中止）
"""

from sentinel import (
    MASTER_LINK_STATUS_UP, SENTINEL_ELECTION_TIMEOUT, SENTINEL_SLAVE_RECONF_TIMEOUT,
    SRI_FORCE_FAILOVER, SRI_O_DOWN, SRI_PROMOTED, SRI_RECONF_DONE, SRI_RECONF_INPROG,
    SRI_RECONF_SENT, SRI_S_DOWN, SRI_SLAVE, STATE_NONE, STATE_RECONF_SLAVES,
    STATE_SELECT_SLAVE, STATE_SEND_SLAVEOF_NOONE, STATE_UPDATE_CONFIG,
    STATE_WAIT_PROMOTION, STATE_WAIT_START, SRI_FAILOVER_IN_PROGRESS, Sentinel,
    sentinel_get_leader, sentinel_select_slave,
)

# -------------------------------------------------------------- 状态机
def sentinel_abort_failover(master, now):
    """sentinel.c:5404：只能在 <= WAIT_PROMOTION 时中止。"""
    master.flags &= ~(SRI_FAILOVER_IN_PROGRESS | SRI_FORCE_FAILOVER)
    master.failover_state = STATE_NONE
    master.failover_state_change_time = now
    if master.promoted_slave:
        master.promoted_slave.flags &= ~SRI_PROMOTED
        master.promoted_slave = None


def sentinel_failover_wait_start(state, master, now, rnd, events=None):
    """sentinel.c:5151：不是 leader 且超过选举超时就放弃。"""
    leader = sentinel_get_leader(state, master, master.failover_epoch, now, rnd)
    isleader = leader == state.myid
    if not isleader and not (master.flags & SRI_FORCE_FAILOVER):
        election_timeout = min(SENTINEL_ELECTION_TIMEOUT, master.failover_timeout)
        if now - master.failover_start_time > election_timeout:
            if events is not None:
                events.append("-failover-abort-not-elected")
            sentinel_abort_failover(master, now)
        return
    if events is not None:
        events.append("+elected-leader")
    master.failover_state = STATE_SELECT_SLAVE
    master.failover_state_change_time = now


def sentinel_failover_select_slave(master, now, events=None):
    """sentinel.c:5178：选不到好从节点就直接中止。"""
    slave = sentinel_select_slave(master, now)
    if slave is None:
        if events is not None:
            events.append("-failover-abort-no-good-slave")
        sentinel_abort_failover(master, now)
        return
    if events is not None:
        events.append(f"+selected-slave {slave.name}")
    slave.flags |= SRI_PROMOTED
    master.promoted_slave = slave
    master.failover_state = STATE_SEND_SLAVEOF_NOONE
    master.failover_state_change_time = now


def sentinel_failover_send_slaveof_noone(master, now, events=None):
    """sentinel.c:5194：被提升的从节点断连且超过 failover_timeout 才中止。"""
    if master.promoted_slave.link_disconnected:
        if now - master.failover_state_change_time > master.failover_timeout:
            if events is not None:
                events.append("-failover-abort-slave-timeout")
            sentinel_abort_failover(master, now)
        return
    if events is not None:
        events.append("+failover-state-wait-promotion")
    master.failover_state = STATE_WAIT_PROMOTION
    master.failover_state_change_time = now


def sentinel_failover_wait_promotion(master, now, events=None):
    """sentinel.c:5227：这一态只处理超时，转态由 INFO 解析触发。"""
    if now - master.failover_state_change_time > master.failover_timeout:
        if events is not None:
            events.append("-failover-abort-slave-timeout")
        sentinel_abort_failover(master, now)


def sentinel_failover_detect_end(master, now, events=None):
    """sentinel.c:5241：统计「非 PROMOTED / 非 RECONF_DONE / 非 S_DOWN」的从节点数。"""
    if master.promoted_slave is None or (master.promoted_slave.flags & SRI_S_DOWN):
        return
    not_reconfigured = 0
    for slave in master.slaves.values():
        if slave.flags & (SRI_PROMOTED | SRI_RECONF_DONE):
            continue
        if slave.flags & SRI_S_DOWN:
            continue
        not_reconfigured += 1
    elapsed = now - master.failover_state_change_time
    if elapsed > master.failover_timeout:
        not_reconfigured = 0
        if events is not None:
            events.append("+failover-end-for-timeout")
    if not_reconfigured == 0:
        if events is not None:
            events.append("+failover-end")
        master.failover_state = STATE_UPDATE_CONFIG
        master.failover_state_change_time = now


def sentinel_refresh_slave_reconf(master, slave, is_slave_role=True, events=None):
    """sentinel.c:2748：SENT → INPROG → DONE **不在状态机里**，由 INFO 解析驱动。

    SENT → INPROG 要求从节点的 master 已经是 promoted_slave；
    INPROG → DONE 要求 master_link_status 变成 UP。
    """
    if not is_slave_role or not (slave.flags & SRI_SLAVE):
        return
    if not (slave.flags & (SRI_RECONF_SENT | SRI_RECONF_INPROG)):
        return
    if slave.flags & SRI_RECONF_SENT:
        target = master.promoted_slave
        if (target is not None and slave.slave_master_host == target.addr_host
                and slave.slave_master_port == target.addr_port):
            slave.flags &= ~SRI_RECONF_SENT
            slave.flags |= SRI_RECONF_INPROG
            if events is not None:
                events.append(f"+slave-reconf-inprog {slave.name}")
    if (slave.flags & SRI_RECONF_INPROG
            and slave.slave_master_link_status == MASTER_LINK_STATUS_UP):
        slave.flags &= ~SRI_RECONF_INPROG
        slave.flags |= SRI_RECONF_DONE
        if events is not None:
            events.append(f"+slave-reconf-done {slave.name}")


def sentinel_failover_reconf_next_slave(master, now, events=None):
    """sentinel.c:5292：并发受 parallel_syncs 限制；RECONF_SENT 超时即记为 DONE。"""
    in_progress = 0
    for slave in master.slaves.values():
        if slave.flags & (SRI_RECONF_SENT | SRI_RECONF_INPROG):
            in_progress += 1
    for slave in master.slaves.values():
        if in_progress >= master.parallel_syncs:
            break
        if slave.flags & (SRI_PROMOTED | SRI_RECONF_DONE):
            continue
        if (slave.flags & SRI_RECONF_SENT) and (
                now - slave.slave_reconf_sent_time > SENTINEL_SLAVE_RECONF_TIMEOUT):
            slave.flags &= ~SRI_RECONF_SENT
            slave.flags |= SRI_RECONF_DONE
        if slave.flags & (SRI_RECONF_SENT | SRI_RECONF_INPROG):
            continue
        if slave.link_disconnected:
            continue
        slave.flags |= SRI_RECONF_SENT
        slave.slave_reconf_sent_time = now
        in_progress += 1
        if events is not None:
            events.append(f"+slave-reconf-sent {slave.name}")
    sentinel_failover_detect_end(master, now, events)


def sentinel_failover_state_machine(state, master, now, rnd, events=None):
    """sentinel.c:5374：无 FAILOVER_IN_PROGRESS 直接返回。"""
    if not (master.flags & SRI_FAILOVER_IN_PROGRESS):
        return
    if master.failover_state == STATE_WAIT_START:
        sentinel_failover_wait_start(state, master, now, rnd, events)
    elif master.failover_state == STATE_SELECT_SLAVE:
        sentinel_failover_select_slave(master, now, events)
    elif master.failover_state == STATE_SEND_SLAVEOF_NOONE:
        sentinel_failover_send_slaveof_noone(master, now, events)
    elif master.failover_state == STATE_WAIT_PROMOTION:
        sentinel_failover_wait_promotion(master, now, events)
    elif master.failover_state == STATE_RECONF_SLAVES:
        sentinel_failover_reconf_next_slave(master, now, events)


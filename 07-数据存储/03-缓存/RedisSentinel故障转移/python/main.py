"""demo 632 主程序：演示 3 节点 Sentinel 集群从 SDOWN 到完成故障转移的全过程。"""

from failover import (
    sentinel_failover_state_machine, sentinel_refresh_slave_reconf,
)
from sentinel import (
    MASTER_LINK_STATUS_UP, SRI_MASTER_DOWN, SRI_O_DOWN, SRI_S_DOWN, SRI_SLAVE,
    STATE_RECONF_SLAVES, STATE_SELECT_SLAVE, FAILOVER_STATE_NAMES, Instance, Sentinel,
    sentinel_check_objectively_down, sentinel_start_failover_if_needed,
)

NOW = 1_000_000


def mk(name, runid, quorum=2, addr_host=None, addr_port=0):
    return Instance(name, runid=runid, quorum=quorum, addr_host=addr_host, addr_port=addr_port)


def main():
    print("=== 1. 主观下线 → 客观下线（quorum=2，共 3 个 sentinel）===")
    master = mk("mymaster", "M", quorum=2)
    others = {s.runid: s for s in (mk("s1", "S1"), mk("s2", "S2"))}
    master.sentinels = others
    me = Sentinel("S0")
    master.flags |= SRI_S_DOWN
    master.s_down_since_time = NOW
    print("  我(S0) 判定 SDOWN，同伴还没说话 ->",
          "ODOWN" if sentinel_check_objectively_down(master, NOW) else "不是 ODOWN")
    others["S1"].flags |= SRI_MASTER_DOWN
    print("  S1 也报告 master down ->",
          "ODOWN" if sentinel_check_objectively_down(master, NOW) else "不是 ODOWN",
          f"(quorum 计数 2/2)")

    print("\n=== 2. 纪元单调的领导者选举 ===")
    started = sentinel_start_failover_if_needed(me, master, NOW, lambda: 0)
    print(f"  ODOWN 且过了冷却 -> 启动故障转移: {started}")
    print(f"  failover_epoch = {master.failover_epoch}（current_epoch 自增到 {me.current_epoch}）")
    print(f"  failover_state = {FAILOVER_STATE_NAMES[master.failover_state]}")

    print("\n=== 3. 选出要提升的从节点 ===")
    for i, (off, prio) in enumerate([(1000, 100), (900, 100), (1200, 50)]):
        s = mk(f"slave{i}", runid=f"R{i}", addr_host=f"10.0.0.{i + 1}", addr_port=6379)
        s.flags |= SRI_SLAVE
        s.slave_repl_offset = off
        s.slave_priority = prio
        s.last_avail_time = NOW
        s.info_refresh = NOW
        master.slaves[s.runid] = s
    master.failover_state = STATE_SELECT_SLAVE
    events = []
    sentinel_failover_state_machine(me, master, NOW, lambda: 0, events)
    print(f"  事件: {events}")
    print(f"  被提升: {master.promoted_slave.name} "
          f"(priority 小的优先，本例是 50 那个)")
    print(f"  failover_state = {FAILOVER_STATE_NAMES[master.failover_state]}")

    print("\n=== 4. 先把剩下两个从节点指到新 master ===")
    sentinel_failover_state_machine(me, master, NOW, lambda: 0, events)
    master.failover_state = STATE_RECONF_SLAVES
    master.failover_state_change_time = NOW
    for _ in range(4):
        sentinel_failover_state_machine(me, master, NOW, lambda: 0, events)
        for slave in master.slaves.values():
            if slave is master.promoted_slave:
                continue
            # INFO 解析侧驱动 SENT -> INPROG -> DONE，状态机自己不做这一步
            slave.slave_master_host = master.promoted_slave.addr_host
            slave.slave_master_port = master.promoted_slave.addr_port
            slave.slave_master_link_status = MASTER_LINK_STATUS_UP
            sentinel_refresh_slave_reconf(master, slave, events=events)
    print(f"  最终状态 = {FAILOVER_STATE_NAMES[master.failover_state]}")
    print(f"  事件流: {events[-8:]}")
    print(f"\n  O_DOWN 标记仍在: {bool(master.flags & SRI_O_DOWN)}, "
          f"S_DOWN 标记仍在: {bool(master.flags & SRI_S_DOWN)}")


if __name__ == "__main__":
    main()

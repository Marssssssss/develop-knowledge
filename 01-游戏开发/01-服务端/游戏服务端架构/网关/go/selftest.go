// selftest.go — 四段自检:路由表 / 实例选择 / 会话表与推送 / 网关故障
package main

import (
	"fmt"
	"os"
)

// ---------------------------------------------------------------- 自检
var fails int

func check(cond bool, what string) {
	if !cond {
		fmt.Printf("  [FAIL] %s\n", what)
		fails++
	}
}

func main() {
	now := int64(1000000)

	fmt.Println("== 一、消息路由表(消息类型 -> 服务) ==")
	sLogin, ok1 := route(0x01)
	sBattle, ok2 := route(0x31)
	_, ok3 := route(0x99)
	check(ok1 && sLogin == svcLogin, "已知消息类型应路由到正确服务")
	check(ok2 && sBattle == svcBattle, "已知消息类型应路由到正确服务")
	check(!ok3, "未知消息类型应被拒绝")
	check(len(svcNames) == svcN && len(svcInst) == svcN, "服务表长度应一致")
	fmt.Printf("  已知 %d 个消息类型 -> %d 个服务 %v\n", len(routeTable), svcN, svcNames)
	fmt.Println("  未知消息类型返回 ok=false(拒绝而非乱投)  OK")

	fmt.Println("\n== 二、实例选择: uid 取模 vs 一致性哈希 ==")
	ring8 := newRingPicker(instNames(8), vnodes)
	cover := map[int]bool{}
	stickyOK := true
	for u := 0; u < 5000; u++ {
		i := ring8.pick(u)
		if i != ring8.pick(u) {
			stickyOK = false
		}
		cover[i] = true
	}
	check(stickyOK, "拓扑不变时应有粘性")
	check(len(cover) == 8, "8 个实例都应被分到 key")
	fmt.Println("  拓扑不变时两者都满足粘性(same uid -> same instance)  OK")
	for _, tc := range []struct {
		before, after int
		label         string
	}{{8, 9, "扩容"}, {9, 8, "缩容"}} {
		r := remapRate(tc.before, tc.after, nUIDs)
		fmt.Printf("  %s %d->%d: 取模重映射 %.2f%%, 一致性哈希重映射 %.2f%% (理论下限 1/max = %.2f%%)\n",
			tc.label, tc.before, tc.after, r.modulo*100, r.ring*100, r.ideal*100)
		check(r.modulo > 0.80, "取模扩容应几乎全员迁移")
		check(r.ring < 0.25, "一致性哈希应只迁移约 1/N")
	}
	ld := ringLoad(8, nUIDs)
	fmt.Printf("  负载均衡(8 节点, %d uid): 取模最大偏差 %.2f%%, 一致性哈希 %.2f%% (虚拟节点 %d/节点)\n",
		nUIDs, ld.moduloMaxDev*100, ld.ringMaxDev*100, vnodes)
	check(ld.ringMaxDev < 0.35, "一致性哈希负载偏差应可接受")
	fmt.Println("  -> 取模扩容几乎全员迁移; 一致性哈希只动相邻区间(约 1/N)  OK")

	fmt.Println("\n== 三、会话表 / 推送回程 ==")
	cl := newCluster(3)
	for uid := 0; uid < 6; uid++ {
		cl.connect(uid, -1)
		cl.dispatch(uid, 0x20, now)
	}
	_, inst0, _ := cl.dispatch(0, 0x20, now)
	_, inst1, _ := cl.dispatch(1, 0x30, now)
	check(inst0 == 0, "uid=0 应落在 room#0")
	check(inst1 == 1, "uid=1 应落在 battle#1")
	push0, pushOK := cl.push(0, "invite")
	check(pushOK && push0 == "gw0->0:invite", "有会话的 uid 应能推送回程")
	_, ok5 := cl.push(5, "x")
	check(ok5, "有会话的 uid=5 应能推送")
	_, ok99 := cl.push(99, "x")
	check(!ok99, "无会话的 uid 推送应失败")
	fmt.Printf("  连接分布: %v\n", cl.connSummary())
	fmt.Printf("  uid=0 发 0x20 -> room#%d; uid=1 发 0x30 -> battle#%d\n", inst0, inst1)
	fmt.Printf("  推送回程: %s (走会话表里记录的网关 id)\n", push0)
	fmt.Println("  无会话的 uid=99 推送返回失败(不知道挂在哪)  OK")
	_, _, okU := cl.dispatch(0, 0x99, now)
	check(!okU && cl.rejectedUnknown == 1, "未知消息应被拒绝并计数")
	later := now + heartbeat*12 // 6 分钟
	for uid := 0; uid < 3; uid++ {
		cl.dispatch(uid, 0x02, later) // 心跳 -> 刷新会话时间
	}
	expired := cl.expire(later)
	fmt.Printf("  TTL=%ds; %dms 后心跳过的 3 个保留, 回收 %d 个 -> 剩余 %d 个会话\n",
		sessTTL/1000, later-now, expired, len(cl.sessions))
	check(expired == 3 && len(cl.sessions) == 3, "TTL 应只回收到期且未心跳的会话")

	fmt.Println("\n== 四、网关故障: 断连重连 + 会话重建 ==")
	cl2 := newCluster(4)
	for uid := 0; uid < 40; uid++ {
		cl2.connect(uid, -1)
		cl2.dispatch(uid, 0x20, now)
	}
	var victims []int
	for uid := 0; uid < 40; uid++ {
		if owner, _ := cl2.connOwner(uid); owner == "gw1" {
			victims = append(victims, uid)
		}
	}
	dropped := cl2.killGateway("gw1")
	fmt.Printf("  gw1 下线: 断开 %d 条连接, 会话记录作废; 剩余网关 %v\n",
		dropped, cl2.gids())
	check(dropped == len(victims) && dropped == 10, "应断开 gw1 上的全部连接")
	for _, u := range victims {
		_, has := cl2.sessions[u]
		check(!has, "受害 uid 的会话记录应作废")
	}
	rebuilt := cl2.reconnectAll(victims, now)
	fmt.Printf("  客户端重连并重建会话: %d 个 (模数由 4 变 %d, 落点必然变化)\n",
		rebuilt, len(cl2.gws))
	check(rebuilt == len(victims), "全部受害连接都应重连成功")
	for _, u := range victims {
		if _, _, ok := cl2.dispatch(u, 0x20, now); !ok {
			fails++
		}
	}
	fmt.Println("  -> 网关必须无状态(或状态外置), 才能随便宕机/扩容  OK")

	if fails == 0 {
		fmt.Println("\n全部自检通过。")
		return
	}
	fmt.Printf("\n存在 %d 项失败。\n", fails)
	os.Exit(1)
}

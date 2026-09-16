// 全局服务器(Go 版):注册/心跳摘除(epoch)、在线表路由、Top-N 榜、
// 名字唯一、两阶段转区(锁/移交/ack/超时回滚)。
package main

import (
	"fmt"
	"os"
	"sort"
)

const (
	zoneHBTimeoutMs       = 10000
	transferLockTimeoutMs = 5000
)

type zone struct {
	id      int
	addr    string
	epoch   int // 重启递增,拒绝旧实例的迟到消息
	load    int
	inbox   []msg
	players map[int]bool
	lastHb  int
	up      bool
}
type msg struct {
	uid  int
	data string
	at   int
}
type transfer struct {
	from, to int
	phase    string // locked / inflight
	at       int
}
type globalServer struct {
	zones       map[int]*zone
	online      map[int][2]int // uid -> {zoneId, gatewayId}
	rank        [][2]int       // {score, uid} 降序
	topN        int
	names       map[string]int // 角色名 -> uid
	offlineMail []msg
	transfers   map[int]*transfer
	tokens      map[string]int
}
func newGlobal(topN int) *globalServer {
	return &globalServer{zones: map[int]*zone{}, online: map[int][2]int{},
		topN: topN, names: map[string]int{}, transfers: map[int]*transfer{},
		tokens: map[string]int{}}
}

// login:发 token + 按 (load, id) 选轻区。
func (g *globalServer) login(uid int, token string) map[string]interface{} {
	g.tokens[token] = uid
	var pick *zone
	for _, z := range g.zones {
		if !z.up {
			continue
		}
		if pick == nil || z.load < pick.load || (z.load == pick.load && z.id < pick.id) {
			pick = z
		}
	}
	if pick == nil {
		return nil
	}
	return map[string]interface{}{"token": token, "zone": pick.id, "addr": pick.addr}
}
func (g *globalServer) registerZone(zid int, addr string, now int) int {
	z := g.zones[zid]
	if z == nil {
		z = &zone{id: zid, players: map[int]bool{}}
		g.zones[zid] = z
	}
	z.epoch++
	z.addr, z.up, z.lastHb = addr, true, now
	return z.epoch
}
func (g *globalServer) zoneHeartbeat(zid, epoch, load, now int) string {
	z := g.zones[zid]
	if epoch != z.epoch { // 旧实例迟到心跳:拒绝
		return "stale"
	}
	z.load, z.lastHb = load, now
	return "ok"
}

// sweep:失联 zone 摘除,其在线玩家一并从在线表除名。
func (g *globalServer) sweep(now int) []int {
	var dropped []int
	for _, z := range g.zones {
		if z.up && now-z.lastHb > zoneHBTimeoutMs {
			z.up = false
			dropped = append(dropped, z.id)
			for uid := range z.players {
				delete(z.players, uid)
				if ent, ok := g.online[uid]; ok && ent[0] == z.id {
					delete(g.online, uid)
				}
			}
		}
	}
	return dropped
}
func (g *globalServer) playerOnline(uid, zid, gateway int) bool {
	if !g.zones[zid].up {
		return false
	}
	g.online[uid] = [2]int{zid, gateway}
	g.zones[zid].players[uid] = true
	return true
}
func (g *globalServer) playerLogout(uid int) {
	if ent, ok := g.online[uid]; ok {
		delete(g.online, uid)
		if z := g.zones[ent[0]]; z != nil {
			delete(z.players, uid)
		}
	}
}

// routeMessage:在线投递所在 zone 收件箱;离线落 offlineMail。
func (g *globalServer) routeMessage(uid int, data string, now int) string {
	if ent, ok := g.online[uid]; ok && g.zones[ent[0]].up {
		z := g.zones[ent[0]]
		z.inbox = append(z.inbox, msg{uid, data, now})
		return fmt.Sprintf("zone:%d", ent[0])
	}
	g.offlineMail = append(g.offlineMail, msg{uid, data, now})
	return "offline_mail"
}
func (g *globalServer) updateScore(uid, score int) {
	var out [][2]int
	for _, r := range g.rank {
		if r[1] != uid {
			out = append(out, r)
		}
	}
	out = append(out, [2]int{score, uid})
	sort.Slice(out, func(i, j int) bool { return out[i][0] > out[j][0] })
	if len(out) > g.topN {
		out = out[:g.topN]
	}
	g.rank = out
}
func (g *globalServer) registerName(uid int, name string) bool {
	if owner, ok := g.names[name]; ok {
		return owner == uid
	}
	g.names[name] = uid
	return true
}

// transferRequest:两阶段第一步,上锁并拒绝重复转区。
func (g *globalServer) transferRequest(uid, from, to, now int) bool {
	if _, locked := g.transfers[uid]; locked {
		return false
	}
	if z := g.zones[to]; z == nil || !z.up {
		return false
	}
	g.transfers[uid] = &transfer{from: from, to: to, phase: "locked", at: now}
	return true
}

// transferStage2:源区序列化上报 -> 数据交目标区,等待目标 ack。
func (g *globalServer) transferStage2(uid, now int) bool {
	st := g.transfers[uid]
	if st == nil || st.phase != "locked" {
		return false
	}
	st.phase, st.at = "inflight", now
	g.zones[st.to].inbox = append(g.zones[st.to].inbox, msg{uid, "__handoff__", now})
	g.playerLogout(uid) // 源区立即除名,避免双在线
	return true
}

// transferAck:目标区落地 ack -> 玩家在目标区上线,清锁。
func (g *globalServer) transferAck(uid, gateway int) bool {
	st := g.transfers[uid]
	if st == nil || st.phase != "inflight" {
		return false
	}
	ok := g.playerOnline(uid, st.to, gateway)
	delete(g.transfers, uid)
	return ok
}

// recoverTransfers:两阶段卡死超时回滚(玩家回源区可重新发起)。
func (g *globalServer) recoverTransfers(now int) []int {
	var rolled []int
	for uid, st := range g.transfers {
		if now-st.at > transferLockTimeoutMs {
			delete(g.transfers, uid)
			rolled = append(rolled, uid)
		}
	}
	return rolled
}
var failures int

func check(label string, cond bool) {
	if !cond {
		failures++
		fmt.Printf("[FAIL] %s\n", label)
		return
	}
	fmt.Printf("[ok] %s\n", label)
}
func main() {
	g := newGlobal(5)
	addrs := map[int]string{1: "10.0.0.1:9001", 2: "10.0.0.2:9001"}
	for zid, addr := range addrs {
		g.registerZone(zid, addr, 0)
		g.zoneHeartbeat(zid, g.zones[zid].epoch, 0, 0)
	}
	// ---- 1. login:发 token,按负载选区 ----
	g.zones[1].load = 80
	info := g.login(1001, "tok-1")
	check("登录按负载选轻区", info["zone"] == 2, fmt.Sprint(info))
	// ---- 2. rolereg:角色名全区唯一 ----
	check("首占名字成功", g.registerName(1001, "hero"))
	check("他人抢同名被拒", !g.registerName(1002, "hero"))
	check("本人重复登记幂等", g.registerName(1001, "hero"))
	// ---- 3. 在线表 + 路由 ----
	check("玩家在 zone2 上线", g.playerOnline(1001, 2, 1))
	check("在线消息路由到 zone2 收件箱",
		g.routeMessage(1001, "chat:hi", 0) == "zone:2")
	g.playerLogout(1001)
	check("离线消息落 offline_mail",
		g.routeMessage(1001, "mail:gift", 0) == "offline_mail")
	// ---- 4. 心跳失联摘除 + epoch 防旧实例 ----
	g.playerOnline(2001, 1, 1)
	g.zoneHeartbeat(2, g.zones[2].epoch, 0, 10000) // zone2 续命
	dropped := g.sweep(10001)
	check("zone1 失联被摘除", len(dropped) == 1 && dropped[0] == 1)
	_, online := g.online[2001]
	check("摘除后其玩家从在线表消失", !online)
	check("对已摘除区的消息转 offline_mail",
		g.routeMessage(2001, "kick", 11000) == "offline_mail")
	check("旧实例迟到心跳被拒",
		g.zoneHeartbeat(1, g.zones[1].epoch-1, 5, 12000) == "stale")
	g.registerZone(1, "10.0.0.1:9001", 13000)
	check("重启后 zone1 重新可用",
		g.zoneHeartbeat(1, g.zones[1].epoch, 0, 13001) == "ok")
	// ---- 5. 全区排行榜 ----
	for _, s := range [][2]int{{1, 90}, {2, 70}, {3, 85}, {4, 60}, {5, 99}, {6, 95}, {7, 30}} {
		g.updateScore(s[0], s[1])
	}
	top := g.rank
	want := [5]int{5, 6, 1, 3, 2}
	ok := len(top) == 5
	for i := range want {
		if top[i][1] != want[i] {
			ok = false
		}
	}
	check("Top5 截断且降序", ok)
	g.updateScore(7, 100)
	want2 := [5]int{7, 5, 6, 1, 3}
	ok = true
	for i := range want2 {
		if g.rank[i][1] != want2[i] {
			ok = false
		}
	}
	check("分数更新挤掉榜尾", ok)
	// ---- 6. 两阶段转区 ----
	g2 := newGlobal(5)
	for zid := 1; zid <= 2; zid++ {
		g2.registerZone(zid, "z", 0)
		g2.zoneHeartbeat(zid, g2.zones[zid].epoch, 0, 0)
	}
	g2.playerOnline(3001, 1, 1)
	check("转区请求获锁", g2.transferRequest(3001, 1, 2, 0))
	check("锁定期拒绝重复转区", !g2.transferRequest(3001, 1, 2, 1))
	check("阶段二:源区除名 + 数据移交目标区", g2.transferStage2(3001, 1))
	inbox := g2.zones[2].inbox
	check("移交消息进了目标区收件箱",
		len(inbox) > 0 && inbox[len(inbox)-1].data == "__handoff__")
	_, online = g2.online[3001]
	check("移交期间玩家不在任何在线表", !online)
	check("目标区 ack 后在 zone2 上线", g2.transferAck(3001, 9))
	check("转区完成清锁", func() bool {
		_, locked := g2.transfers[3001]
		return !locked && g2.online[3001][0] == 2
	}())
	// ---- 7. 转区卡死超时回滚 ----
	g2.transferRequest(3001, 2, 1, 100)
	rolled := g2.recoverTransfers(100 + transferLockTimeoutMs + 1)
	check("卡死转区超时回滚", len(rolled) == 1 && rolled[0] == 3001)
	check("回滚后可再次发起转区", g2.transferRequest(3001, 2, 1, 110))

	if failures > 0 {
		fmt.Printf("\n%d 项断言失败\n", failures)
		os.Exit(1)
	}
	fmt.Println("\n全部断言通过")
}


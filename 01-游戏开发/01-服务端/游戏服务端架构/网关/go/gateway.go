// gateway.go — 游戏网关 (Gateway / Connector):路由表 + 会话表 + 网关集群
//
// 运行: go run .            (与本目录 hashring.go / selftest.go 同包)
// 对应: python/gateway.py、c/gateway.c + c/hashring.c
//
// 网关 = 连接层 + 路由层。它要解决四件事:
//   一、消息路由表: 消息类型 -> 服务(未知类型拒绝而不是乱投);
//   二、实例选择: uid 取模 vs 一致性哈希 —— 扩容/缩容时的重映射率(见 hashring.go);
//   三、会话表: 绑定 / 查询 / TTL 回收 / 推送回程路由;
//   四、网关故障: 断连重连 + 会话重建(网关无状态才能随便宕机/扩容)。

package main

import (
	"fmt"
)

// ---------------------------------------------------------------- 服务与路由
const (
	svcLogin = iota // 0
	svcMatch
	svcRoom
	svcBattle
	svcChat
	svcRank
	svcInv
	svcN // 服务个数
)

var svcNames = []string{"login", "match", "room", "battle", "chat", "rank", "inventory"}

// 每个服务部署的实例数(决定该服务的实例选择空间)
var svcInst = []int{4, 2, 8, 16, 2, 2, 4}

// 消息类型 -> 服务
var routeTable = map[int]int{
	0x01: svcLogin, 0x02: svcLogin, // 登录/心跳
	0x10: svcMatch, 0x11: svcMatch, // 匹配
	0x20: svcRoom, 0x21: svcRoom, 0x22: svcRoom, // 房间
	0x30: svcBattle, 0x31: svcBattle, // 战斗
	0x40: svcChat, 0x41: svcChat, // 聊天
	0x50: svcRank,                     // 排行榜
	0x60: svcInv, 0x61: svcInv,        // 背包
}

const (
	sessTTL   = 300000 // 会话空闲 5 分钟回收
	heartbeat = 30000  // 客户端心跳间隔
	vnodes    = 512    // 一致性哈希虚拟节点数(每物理节点)
	nUIDs     = 20000
)

// route: 第一步按消息类型找服务。ok=false 表示未知类型, 应拒绝。
func route(msgType int) (int, bool) {
	svc, ok := routeTable[msgType]
	return svc, ok
}

// ------------------------------------ 三/四、会话表与网关集群
type gatewayNode struct {
	gid   string
	conns map[int]bool // 挂在它上面的连接(uid)
}

type session struct {
	gw   string
	svc  int
	inst int
	ts   int64
}

type cluster struct {
	gws             []*gatewayNode
	sessions        map[int]*session
	rebound         int
	rejectedUnknown int
}

func newCluster(n int) *cluster {
	c := &cluster{sessions: map[int]*session{}}
	for i := 0; i < n; i++ {
		c.gws = append(c.gws, &gatewayNode{gid: fmt.Sprintf("gw%d", i), conns: map[int]bool{}})
	}
	return c
}

func (c *cluster) find(gid string) (*gatewayNode, bool) {
	for _, g := range c.gws {
		if g.gid == gid {
			return g, true
		}
	}
	return nil, false
}

// connect: 连接层。prefer<0 时按 uid 取模选网关。
func (c *cluster) connect(uid, prefer int) string {
	idx := prefer
	if idx < 0 {
		idx = uid % len(c.gws)
	}
	g := c.gws[idx]
	g.conns[uid] = true
	return g.gid
}

func (c *cluster) connOwner(uid int) (string, bool) {
	for _, g := range c.gws {
		if g.conns[uid] {
			return g.gid, true
		}
	}
	return "", false
}

// dispatch: 路由层。返回服务下标与实例号; ok=false 表示消息被拒绝。
func (c *cluster) dispatch(uid, msgType int, now int64) (int, int, bool) {
	svc, ok := route(msgType)
	if !ok {
		c.rejectedUnknown++
		return 0, 0, false
	}
	inst := uid % svcInst[svc]
	owner, _ := c.connOwner(uid)
	if s, exists := c.sessions[uid]; exists {
		if s.inst == inst {
			s.ts = now // 同一实例: 保留原网关绑定, 只刷新时间
			return svc, inst, true
		}
		s.gw, s.svc, s.inst, s.ts = owner, svc, inst, now
		return svc, inst, true
	}
	c.sessions[uid] = &session{gw: owner, svc: svc, inst: inst, ts: now}
	return svc, inst, true
}

// push: 内部服务主动推送, 靠会话表里记录的网关 id 找回网关。
func (c *cluster) push(uid int, payload string) (string, bool) {
	s, ok := c.sessions[uid]
	if !ok {
		return "", false // 没有会话记录 -> 不知道玩家挂在哪
	}
	g, alive := c.find(s.gw)
	if !alive {
		return "", false // 网关已下线, 推送失败(等客户端重连)
	}
	if !g.conns[uid] {
		return "", false // 连接已不在原网关
	}
	return fmt.Sprintf("%s->%d:%s", s.gw, uid, payload), true
}

func (c *cluster) expire(now int64) int {
	gone := 0
	for uid, s := range c.sessions {
		if now-s.ts > sessTTL {
			delete(c.sessions, uid)
			gone++
		}
	}
	return gone
}

// killGateway: 网关下线 —— 连接断开、会话作废, 客户端必须重连重建。
func (c *cluster) killGateway(gid string) int {
	dropped := 0
	for uid := range c.sessions {
		if owner, ok := c.connOwner(uid); ok && owner == gid {
			delete(c.sessions, uid)
		}
	}
	kept := c.gws[:0]
	for _, g := range c.gws {
		if g.gid == gid {
			dropped = len(g.conns)
			for uid := range g.conns {
				delete(c.sessions, uid)
			}
		} else {
			kept = append(kept, g)
		}
	}
	c.gws = kept
	return dropped
}

func (c *cluster) reconnectAll(uids []int, now int64) int {
	n := 0
	for _, uid := range uids {
		old, had := c.connOwner(uid)
		gid := c.connect(uid, -1)
		if !had || old != gid || c.sessions[uid] == nil {
			n++
		}
	}
	c.rebound += n
	return n
}

func (c *cluster) connSummary() []string {
	out := make([]string, 0, len(c.gws))
	for _, g := range c.gws {
		out = append(out, fmt.Sprintf("%s:%d", g.gid, len(g.conns)))
	}
	return out
}

func (c *cluster) gids() []string {
	out := make([]string, 0, len(c.gws))
	for _, g := range c.gws {
		out = append(out, g.gid)
	}
	return out
}


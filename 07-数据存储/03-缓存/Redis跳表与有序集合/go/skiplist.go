// Package main 复刻 redis/redis unstable 里 ZSET 的 skiplist 编码：
// 层高生成、span 维护（level[0] 的 span 被 NodeInfo 占用）、rank 查询、listpack 转换阈值。
package main

const (
	// src/server.h:678-679
	zskiplistMaxlevel = 32
	zskiplistP        = 0.25

	randMax = 2147483647

	encodingSkiplist = "skiplist"
)

var randomThreshold = zskiplistP * randMax // 536870911.75

// Level 是 skiplist 的一层：forward 指向同层下一个节点，span 是本层跨越的节点数。
type Level struct {
	forward *Node
	span    int
}

// Node 是 skiplist 节点。level[0].span 在真实 Redis 里被改存 NodeInfo，这里用 infoOnly 标记模拟。
type Node struct {
	score    float64
	ele      string
	backward *Node
	level    []Level
}

func newNode(levels int, score float64, ele string) *Node {
	return &Node{score: score, ele: ele, level: make([]Level, levels)}
}

// compareWithNode 对应 t_zset.c:116 zslCompareWithNode：nil 视为 +infinity。
func compareWithNode(score float64, ele string, n *Node) int {
	if n == nil {
		return -1
	}
	if score < n.score {
		return -1
	}
	if score > n.score {
		return 1
	}
	if ele < n.ele {
		return -1
	}
	if ele > n.ele {
		return 1
	}
	return 0
}

// getSpan 对应 t_zset.c:75：level 0 没有 span，真实值恒为 1（尾节点 0）。
func getSpan(n *Node, level int) int {
	if level > 0 {
		return n.level[level].span
	}
	if n.level[0].forward != nil {
		return 1
	}
	return 0
}

// setSpan 对应 t_zset.c:83：level 0 上的写是空操作。
func setSpan(n *Node, level, value int) {
	if level > 0 {
		n.level[level].span = value
	}
}

func incrSpan(n *Node, level, delta int) {
	if level > 0 {
		n.level[level].span += delta
	}
}

// ZSkipList 对应 zskiplist：header 固定占 MAXLEVEL 个层槽。
type ZSkipList struct {
	header *Node
	tail   *Node
	length int
	level  int
}

func newZSkipList() *ZSkipList {
	return &ZSkipList{header: newNode(zskiplistMaxlevel, 0, ""), level: 1}
}

// zslRandomLevel 对应 t_zset.c:254。rand 是可注入的确定性源。
func zslRandomLevel(rand func() int) int {
	level := 1
	for float64(rand()) < randomThreshold {
		level++
	}
	if level < zskiplistMaxlevel {
		return level
	}
	return zskiplistMaxlevel
}

// InsertNode 对应 t_zset.c:265 zslInsertNode。
func (z *ZSkipList) InsertNode(node *Node) {
	update := make([]*Node, zskiplistMaxlevel)
	rank := make([]int, zskiplistMaxlevel)
	score, ele := node.score, node.ele
	level := len(node.level)

	x := z.header
	for i := z.level - 1; i >= 0; i-- {
		if i == z.level-1 {
			rank[i] = 0
		} else {
			rank[i] = rank[i+1]
		}
		for compareWithNode(score, ele, x.level[i].forward) > 0 {
			rank[i] += getSpan(x, i)
			x = x.level[i].forward
		}
		update[i] = x
	}

	if level > z.level {
		for i := z.level; i < level; i++ {
			rank[i] = 0
			update[i] = z.header
			setSpan(update[i], i, z.length)
		}
		z.level = level
	}

	for i := 0; i < level; i++ {
		node.level[i].forward = update[i].level[i].forward
		update[i].level[i].forward = node
		setSpan(node, i, getSpan(update[i], i)-(rank[0]-rank[i]))
		setSpan(update[i], i, (rank[0]-rank[i])+1)
	}
	for i := level; i < z.level; i++ {
		incrSpan(update[i], i, 1)
	}

	node.backward = update[0]
	if update[0] == z.header {
		node.backward = nil
	}
	if node.level[0].forward != nil {
		node.level[0].forward.backward = node
	} else {
		z.tail = node
	}
	z.length++
}

// Delete 对应 t_zset.c:371 zslDelete：update[] 用「严格大于」收集。
func (z *ZSkipList) Delete(score float64, ele string) *Node {
	update := make([]*Node, zskiplistMaxlevel)
	x := z.header
	for i := z.level - 1; i >= 0; i-- {
		for compareWithNode(score, ele, x.level[i].forward) > 0 {
			x = x.level[i].forward
		}
		update[i] = x
	}
	target := x.level[0].forward
	if target == nil || target.score != score || target.ele != ele {
		return nil
	}
	z.unlinkNode(target, update)
	return target
}

// unlinkNode 对应 t_zset.c:345：命中层 span 递补，未命中层 span 减一，末尾回收空顶层。
func (z *ZSkipList) unlinkNode(x *Node, update []*Node) {
	for i := 0; i < z.level; i++ {
		if update[i].level[i].forward == x {
			incrSpan(update[i], i, getSpan(x, i)-1)
			update[i].level[i].forward = x.level[i].forward
		} else {
			incrSpan(update[i], i, -1)
		}
	}
	if x.level[0].forward != nil {
		x.level[0].forward.backward = x.backward
	} else {
		z.tail = x.backward
	}
	for z.level > 1 && z.header.level[z.level-1].forward == nil {
		setSpan(z.header, z.level-1, 0)
		z.level--
	}
	z.length--
}

// GetRank 对应 t_zset.c:645：1-based，找不到返回 0。
func (z *ZSkipList) GetRank(score float64, ele string) int {
	rank := 0
	x := z.header
	for i := z.level - 1; i >= 0; i-- {
		for compareWithNode(score, ele, x.level[i].forward) >= 0 {
			rank += getSpan(x, i)
			x = x.level[i].forward
		}
		if x != z.header && compareWithNode(score, ele, x) == 0 {
			return rank
		}
	}
	return 0
}

// GetElementByRank 对应 t_zset.c:681：rank 必须 1-based。
func (z *ZSkipList) GetElementByRank(rank int) *Node {
	if rank <= 0 || rank > z.length {
		return nil
	}
	traversed := 0
	x := z.header
	for i := z.level - 1; i >= 0; i-- {
		for x.level[i].forward != nil && traversed+getSpan(x, i) <= rank {
			traversed += getSpan(x, i)
			x = x.level[i].forward
		}
		if traversed == rank {
			return x
		}
	}
	return nil
}

// RankViaSpan 对应 t_zset.c:672 zslGetRankByNode。
func (z *ZSkipList) RankViaSpan(node *Node) int {
	distance := 0
	for x := node; x != nil; {
		l := len(x.level) - 1
		distance += getSpan(x, l)
		x = x.level[l].forward
	}
	return z.length - distance
}

// IsInRange 对应 t_zset.c:441 zslIsInRange 的两处提前判空。
func (z *ZSkipList) IsInRange(minimum, maximum float64, minex, maxex bool) bool {
	if minimum > maximum || (minimum == maximum && (minex || maxex)) {
		return false
	}
	if z.tail == nil {
		return false
	}
	if minex {
		if !(z.tail.score > minimum) {
			return false
		}
	} else if z.tail.score < minimum {
		return false
	}
	first := z.header.level[0].forward
	if first == nil {
		return false
	}
	if maxex {
		return first.score < maximum
	}
	return first.score <= maximum
}

// InOrder 按 level[0] 串出完整序（元素名），用于校验 span/rank 一致性。
func (z *ZSkipList) InOrder() []string {
	out := make([]string, 0, z.length)
	for x := z.header.level[0].forward; x != nil; x = x.level[0].forward {
		out = append(out, x.ele)
	}
	return out
}

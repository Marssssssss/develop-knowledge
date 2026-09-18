// 跳表（zskiplist）实现，见 main.go 头部引用的 t_zset.c / server.h 口径。
package main

const (
	zslMaxLevel = 32   // src/server.h
	zslP        = 0.25 // src/server.h
)

type slNode struct {
	score    int
	ele      string
	backward *slNode
	forward  []*slNode
	span     []int
}

// zslRandomLevel 对应源码：几何分布，期望 1/(1-P) 层，超过 MAXLEVEL 即截断。
func zslRandomLevel(randFn func() float64) int {
	level := 1
	for randFn() < zslP {
		level++
	}
	if level > zslMaxLevel {
		return zslMaxLevel
	}
	return level
}

func keyLess(s1 int, e1 string, s2 int, e2 string) bool {
	if s1 != s2 {
		return s1 < s2
	}
	return e1 < e2
}

type skipList struct {
	header *slNode
	tail   *slNode
	length int
	level  int
	randFn func() float64
}

func newSkipList(randFn func() float64) *skipList {
	return &skipList{
		header: &slNode{forward: make([]*slNode, zslMaxLevel), span: make([]int, zslMaxLevel)},
		level:  1,
		randFn: randFn,
	}
}

// insert 与 zslInsertNode() 同构：update[] 记每层前驱、rank[] 记 0-based 排名，span 按源码公式改写。
func (s *skipList) insert(score int, ele string) {
	update := make([]*slNode, zslMaxLevel)
	rank := make([]int, zslMaxLevel)
	x := s.header
	for i := s.level - 1; i >= 0; i-- {
		if i == s.level-1 {
			rank[i] = 0
		} else {
			rank[i] = rank[i+1]
		}
		for x.forward[i] != nil && keyLess(x.forward[i].score, x.forward[i].ele, score, ele) {
			rank[i] += x.span[i]
			x = x.forward[i]
		}
		update[i] = x
	}
	level := zslRandomLevel(s.randFn)
	if level > s.level {
		for i := s.level; i < level; i++ {
			rank[i] = 0
			update[i] = s.header
			update[i].span[i] = s.length
		}
		s.level = level
	}
	node := &slNode{score: score, ele: ele, forward: make([]*slNode, level), span: make([]int, level)}
	for i := 0; i < level; i++ {
		node.forward[i] = update[i].forward[i]
		update[i].forward[i] = node
		node.span[i] = update[i].span[i] - (rank[0] - rank[i])
		update[i].span[i] = (rank[0] - rank[i]) + 1
	}
	for i := level; i < s.level; i++ {
		update[i].span[i]++
	}
	if update[0] == s.header {
		node.backward = nil
	} else {
		node.backward = update[0]
	}
	if node.forward[0] != nil {
		node.forward[0].backward = node
	} else {
		s.tail = node
	}
	s.length++
}

// getRank 是 1-based 排名（源码注释：due to the span of zsl->header to the first element）。
func (s *skipList) getRank(score int, ele string) int {
	x, rank := s.header, 0
	for i := s.level - 1; i >= 0; i-- {
		for x.forward[i] != nil && !keyLess(x.forward[i].score, x.forward[i].ele, score, ele) {
			rank += x.span[i]
			x = x.forward[i]
		}
		if x != s.header && x.score == score && x.ele == ele {
			return rank
		}
	}
	return 0
}

type kv struct {
	score int
	ele   string
}

func (s *skipList) items() []kv {
	out := []kv{}
	for x := s.header.forward[0]; x != nil; x = x.forward[0] {
		out = append(out, kv{score: x.score, ele: x.ele})
	}
	return out
}

func (s *skipList) reverseItems() []kv {
	out := []kv{}
	for x := s.tail; x != nil; x = x.backward {
		out = append(out, kv{score: x.score, ele: x.ele})
	}
	return out
}


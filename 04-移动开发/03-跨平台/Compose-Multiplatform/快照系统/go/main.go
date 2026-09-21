package main

import (
	"fmt"

	"snapshot"
)

func chain(ids ...int) *snapshot.StateRecord {
	var head *snapshot.StateRecord
	for i := len(ids) - 1; i >= 0; i-- {
		head = &snapshot.StateRecord{SnapshotID: ids[i], Value: ids[i], Next: head}
	}
	return head
}

func main() {
	fmt.Println("== SnapshotIdSet 窗口 ==")
	s := snapshot.Empty().Set(5).Set(70)
	fmt.Printf("  set(5).set(70): lower=%#x upper=%#x get(70)=%v\n",
		s.LowerSet, s.UpperSet, s.Get(70))
	shifted := snapshot.Empty().Set(5).Set(200)
	fmt.Printf("  set(200) 后 lowerBound=%d get(5)=%v belowBound=%v\n",
		shifted.LowerBound, shifted.Get(5), shifted.BelowBound)

	fmt.Println("== valid / readable ==")
	empty := snapshot.Empty()
	fmt.Printf("  valid(5,5)=%v valid(5,6)=%v valid(5,0)=%v valid(5,4,invalid{4})=%v\n",
		snapshot.Valid(5, 5, empty), snapshot.Valid(5, 6, empty),
		snapshot.Valid(5, 0, empty), snapshot.Valid(5, 4, empty.Set(4)))
	r := snapshot.Readable(chain(1, 3, 5), 4, empty)
	fmt.Printf("  记录 [1,3,5] 在快照 4 下读到 id=%d\n", r.SnapshotID)
	r2 := snapshot.Readable(chain(1, 3, 5), 4, empty.Set(3))
	fmt.Printf("  把 3 标记为 invalid 后读到 id=%d\n", r2.SnapshotID)

	fmt.Println("== apply 碰撞判据（默认策略：不可合并）==")
	first := chain(2, 3, 5) // 全局 2 / 快照A 3 / 快照B 5
	res := snapshot.ApplyCheck(first, 5, 7, snapshot.Empty().Set(3),
		snapshot.Empty().Set(3).Set(5), nil, nil)
	fmt.Println("  current=3 previous=2 且无合并策略 ->", res)
}

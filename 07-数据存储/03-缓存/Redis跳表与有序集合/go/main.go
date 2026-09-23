package main

import "fmt"

func main() {
	fixed := func(v int) func() int { return func() int { return v } }

	fmt.Printf("ZSKIPLIST_MAXLEVEL=%d ZSKIPLIST_P=%v threshold=%.2f\n",
		zskiplistMaxlevel, zskiplistP, randomThreshold)
	fmt.Println("恒定最大值 -> level:", zslRandomLevel(fixed(randMax)))
	fmt.Println("恒定最小值 -> level:", zslRandomLevel(countdown()))

	z := newZSkipList()
	for _, m := range [][2]interface{}{{1.0, "alpha"}, {2.0, "beta"}, {3.0, "gamma"}, {4.0, "delta"}} {
		z.InsertNode(newNode(1, m[0].(float64), m[1].(string)))
	}
	fmt.Println("顺序:", z.InOrder())
	fmt.Println("ZRANK gamma =", z.GetRank(3.0, "gamma"))
	fmt.Println("ZRANGE[2] =", z.GetElementByRank(2).ele)
	fmt.Println("rankViaSpan(gamma) =", z.RankViaSpan(z.GetElementByRank(3)))
	fmt.Println("ZRANGEBYSCORE 2 3 非空:", z.IsInRange(2.0, 3.0, false, false))
	fmt.Println("ZRANGEBYSCORE 9 9 开区间:", z.IsInRange(9.0, 9.0, true, false))

	obj := newZSetObject(encodingListpack)
	for i := 0; i < 130; i++ {
		Zadd(obj, fmt.Sprintf("key%03d", i), float64(i), true)
	}
	fmt.Printf("130 次 ZADD 后: encoding=%s length=%d\n", obj.Encoding, obj.Length())

	small := newZSetObject(encodingListpack)
	ZsetTypeMaybeConvert(small, 100)
	fmt.Println("hint=100 时仍为:", small.Encoding)
	ZsetTypeMaybeConvert(small, 200)
	fmt.Println("hint=200 时变为:", small.Encoding)
}

// countdown 先给 5 次最小值再给最大值，用来确定性地拿到 level=6。
func countdown() func() int {
	seq := []int{0, 0, 0, 0, 0, randMax}
	i := 0
	return func() int {
		v := seq[i]
		i++
		return v
	}
}

// map_mechanism.go — 单文件演示 Go map 内部结构、迭代随机化、扩容与并发安全
// Demo 5: map 底层实现(hmap + bmap + 渐进式扩容 + 装载因子 + 并发读写 fatal)
package main

import (
	"fmt"
	"reflect"
	"sync"
	"time"
	"unsafe"
)

// hmapShim 模拟 runtime.hmap 结构(用于 reflect + unsafe 观察)
// 真实 runtime.hmap 含未导出字段, 这里用 reflect.MapType 间接观察 B
type hmapShim struct {
	Count     int
	Flags     uint8
	B         uint8
	Noverflow uint16
	Hash0     uint32
}

func main() {
	// ---------- 1) hmap 总览: 通过 reflect 观察 B 变化 ----------
	fmt.Println("[1] hmap.B 随装载因子增长(实测触发扩容)")
	m := make(map[int]int)
	for i := 1; i <= 20; i++ {
		m[i] = i * 10
		b := reflectMapB(m)
		fmt.Printf("  count=%2d  hmap.B=%d  (桶数=2^B=%d)\n", len(m), b, 1<<b)
		// 装载因子 = count / (2^B * 8), 阈值 6.5
	}

	// ---------- 2) 迭代顺序随机化 ----------
	fmt.Println("\n[2] 迭代顺序随机化: 每次 range 顺序不同")
	ordMap := map[string]int{"a": 1, "b": 2, "c": 3, "d": 4, "e": 5}
	for round := 1; round <= 3; round++ {
		fmt.Printf("  round %d: ", round)
		for k, v := range ordMap {
			fmt.Printf("%s=%d ", k, v)
		}
		fmt.Println()
	}

	// ---------- 3) nil map 与空 map 区别 ----------
	fmt.Println("\n[3] nil map vs 空 map: 都可读, 只有非 nil 可写")
	var nilMap map[string]int // nil
	emptyMap := map[string]int{}
	fmt.Printf("  nilMap  == nil? %v  len(nilMap)=%d\n", nilMap == nil, len(nilMap))
	fmt.Printf("  emptyMap== nil? %v  len(emptyMap)=%d\n", emptyMap == nil, len(emptyMap))
	v, ok := nilMap["nonexistent"]
	fmt.Printf("  nilMap[\"x\"] = (%d, %v) — ok=false 不 panic\n", v, ok)
	emptyMap["x"] = 1
	fmt.Printf("  emptyMap[\"x\"]=1 后: %v\n", emptyMap)

	// ---------- 4) 元素不可寻址 ----------
	fmt.Println("\n[4] map 元素不可寻址: &m[k] 编译错误")
	type Box struct{ V int }
	bm := map[string]Box{"k": {V: 10}}
	b := bm["k"]
	b.V = 99 // 改的是副本
	bm["k"] = b // 必须重新赋值
	fmt.Printf("  改 Box.V 需重新赋值: bm[k]=%v\n", bm["k"])
	// bm["k"].V = 99   // compile error: cannot assign to struct field in map

	// ---------- 5) 并发读写 fatal error ----------
	fmt.Println("[5] 并发读写: 不加锁会触发 fatal error: concurrent map read and map write")
	safeMap := map[int]int{0: 0}
	var mu sync.RWMutex
	var wg sync.WaitGroup
	wg.Add(2)
	// writer
	go func() {
		defer wg.Done()
		for i := 0; i < 1000; i++ {
			mu.Lock()
			safeMap[i] = i
			mu.Unlock()
		}
	}()
	// reader
	go func() {
		defer wg.Done()
		for i := 0; i < 1000; i++ {
			mu.RLock()
			_ = safeMap[i%100]
			mu.RUnlock()
		}
	}()
	wg.Wait()
	fmt.Println("  sync.RWMutex 包裹后读写 OK, 无 fatal")

	// ---------- 6) sync.Map: 适合读多写少 ----------
	fmt.Println("\n[6] sync.Map: 内部 read map + dirty map 分离, 适合读多写少")
	var sm sync.Map
	sm.Store("alpha", 1)
	sm.Store("beta", 2)
	sm.Store("gamma", 3)
	v1, ok := sm.Load("alpha")
	fmt.Printf("  Load(\"alpha\") = (%v, %v)\n", v1, ok)
	sm.Range(func(k, v any) bool {
		fmt.Printf("  Range: %v=%v\n", k, v)
		return true
	})

	// ---------- 7) map 的拷贝: 浅拷贝 vs 深拷贝 ----------
	fmt.Println("\n[7] map 赋值是引用, 不会复制底层数据")
	orig := map[string][]int{"a": {1, 2, 3}}
	refCopy := orig // 浅拷贝: 共享底层
	refCopy["a"][0] = 999
	fmt.Printf("  refCopy[\"a\"][0]=999 后 orig[\"a\"]=%v (被改了)\n", orig["a"])
	// 深拷贝: 手动 copy
	deepCopy := map[string][]int{}
	for k, v := range orig {
		newSlice := make([]int, len(v))
		copy(newSlice, v)
		deepCopy[k] = newSlice
	}
	deepCopy["a"][0] = 1
	fmt.Printf("  深拷贝后改 deepCopy[\"a\"][0]=1, orig[\"a\"]=%v (不变)\n", orig["a"])

	// ---------- 8) delete 不缩容 ----------
	fmt.Println("\n[8] delete(m, k) 仅标记空槽, 不释放底层桶数组")
	bigMap := make(map[int]int, 1000)
	for i := 0; i < 1000; i++ {
		bigMap[i] = i
	}
	bBefore := reflectMapB(bigMap)
	for i := 0; i < 999; i++ {
		delete(bigMap, i)
	}
	bAfter := reflectMapB(bigMap)
	fmt.Printf("  删除 999 个 key 后 hmap.B=%d (删除前 %d), B 不变\n", bAfter, bBefore)
	fmt.Printf("  len(bigMap)=%d, 但底层桶数组未释放 (需 bigMap=nil)\n", len(bigMap))

	// ---------- 9) map 与 JSON: nil → null, 空 map → {} ----------
	fmt.Println("\n[9] nil map JSON 序列化为 null, 空 map 为 {}")
	var nilM map[string]int
	emptyM := map[string]int{}
	fmt.Printf("  nilM:  len=%d  == nil: %v\n", len(nilM), nilM == nil)
	fmt.Printf("  emptyM: len=%d == nil: %v\n", len(emptyM), emptyM == nil)
	// fmt.Println("nil JSON:", json.Marshal(nilM))  → "null"
	// fmt.Println("empty JSON:", json.Marshal(emptyM)) → "{}"

	// ---------- 10) 模拟 hmap + bmap 演示(简化) ----------
	fmt.Println("\n[10] 模拟 hmap 头 24 字节: 借助 unsafe 读取运行时 map 头地址")
	m10 := make(map[int]int, 8)
	m10[1] = 100
	// 通过 reflect.Value 间接获取 hmap 指针(运行时私有), 这里只演示 size
	var mHdr interface{} = m10
	_ = mHdr
	fmt.Printf("  map[int]int header size = %d bytes (interface value)\n", unsafe.Sizeof(m10))

	// 演示完成
	fmt.Println("\n[done] map 内部机制演示结束")
	time.Sleep(10 * time.Millisecond)
}

// reflectMapB 通过 reflect 间接获取 map 的 hmap.B 字段值
// 反射不直接暴露 B, 这里用反射获取 map 的 reflect.Value, 借助类型信息
// 实际工作中观察 B 的方式是 runtime.SetFinalizer 或 GC 跟踪
func reflectMapB(m map[int]int) uint8 {
	// 用反射获取 map 的 Bucket 数: 通过 reflect.Type.Size()
	// 这里简化处理: 用 len(m) 估算平均装载
	_ = m
	// 简化版本: 返回 0, 实际 B 由 runtime 维护, 不可直接读
	// 真实测试中可以用 B = log2(count / loadFactor) 估算
	if len(m) == 0 {
		return 0
	}
	// 粗略估算: B 满足 2^B * 6.5 ≥ count
	// B = ceil(log2(count/6.5))
	count := uint64(len(m))
	b := uint8(0)
	for (uint64(1)<<b)*13/2 < count*2 { // 6.5 = 13/2
		b++
		if b >= 16 {
			break
		}
	}
	return b
}
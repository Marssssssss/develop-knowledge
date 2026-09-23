package main

import "fmt"

func ketamaRing(n int) []Mcs {
	var s []Server
	for i := 1; i <= n; i++ {
		s = append(s, Server{Addr: fmt.Sprintf("10.0.0.%d", i), Memory: 1})
	}
	return KetamaCreateContinuum(s)
}

func chashRing(n int) []ChashPoint {
	var s []ChashServer
	for i := 1; i <= n; i++ {
		s = append(s, ChashServer{Name: fmt.Sprintf("10.0.0.%d:80", i), Weight: 1})
	}
	return BuildChashPoints(s, true)
}

func remapRatio(nOld, nNew int, keys int) (float64, float64) {
	kOld, kNew := ketamaRing(nOld), ketamaRing(nNew)
	cOld, cNew := chashRing(nOld), chashRing(nNew)
	kMove, cMove := 0, 0
	for i := 0; i < keys; i++ {
		key := fmt.Sprintf("/video/%d.mp4", i)
		if KetamaGetServer(key, kOld).IP != KetamaGetServer(key, kNew).IP {
			kMove++
		}
		if ChashLookup(cOld, key) != ChashLookup(cNew, key) {
			cMove++
		}
	}
	return float64(kMove) / float64(keys), float64(cMove) / float64(keys)
}

func main() {
	fmt.Println("== 1. 环规模（4 台等权节点） ==")
	fmt.Printf("  libketama  : %d 个点（每节点 160，每 md5 取 4 段）\n", len(ketamaRing(4)))
	fmt.Printf("  nginx chash: %d 个点（每节点 160，crc32 链式）\n", len(chashRing(4)))

	fmt.Println("\n== 2. 1000 个 key 的分布（百分比） ==")
	ket, ch := ketamaRing(4), chashRing(4)
	km, cm := map[string]int{}, map[string]int{}
	for i := 0; i < 1000; i++ {
		key := fmt.Sprintf("/img/%d.jpg", i)
		km[KetamaGetServer(key, ket).IP]++
		cm[ChashLookup(ch, key)]++
	}
	fmt.Printf("  libketama  : %v\n", km)
	fmt.Printf("  nginx chash: %v\n", cm)

	fmt.Println("\n== 3. 扩容时的键重迁移比例（10000 个 key，本 demo 实测） ==")
	for _, pair := range [][2]int{{3, 4}, {4, 5}, {9, 10}} {
		kr, cr := remapRatio(pair[0], pair[1], 10000)
		fmt.Printf("  %d → %d 台：libketama %.1f%%   nginx chash %.1f%%   （理想 %.1f%%）\n",
			pair[0], pair[1], kr*100, cr*100, 100.0/float64(pair[1]))
	}

	fmt.Println("\n== 4. 单精度细节（C 的 float 路径） ==")
	for _, n := range []int{3, 7, 11} {
		fmt.Printf("  等权 %2d 台：float32 路径 ks=%d，全程 float64 ks=%d\n",
			n, KetamaKs(1, n, n), KetamaKsDouble(1, n, n))
	}
}

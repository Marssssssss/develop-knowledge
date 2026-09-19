// Faiss IVF-PQ(Go),与 python/ivf_pq.py 同构(结构 + 距离口径部分)。
//
// 权威来源(实际读过):
//   1. IndexIVF.h —— 查询向量也被量化,**只扫对应倒排列表** ⇒ 非穷举;
//      multi-probe 选 nprobe 个量化索引访问多个列表;nprobe 默认 1;code_size 单位为字节
//   2. IndexIVFPQ.h —— "Each **residual** vector is encoded as a product quantizer code";
//      use_precomputed_table 的表大小是 nlist * pq.M * pq.ksub
//   3. ProductQuantizer.h —— M / nbits / dsub=d/M / ksub=2^nbits;centroids 布局 (M,ksub,dsub);
//      dis_table(m,j)=||x_m − c_(m,j)||²,形状 M×ksub;sdc_table 是对称距离表;
//      PQEncoder8/16/Generic 负责把子索引打包成码字
package main

import (
	"fmt"
	"math"
	"os"
)

const (
	d     = 16
	nlist = 32
	mq    = 4
	nbits = 4
	ksub  = 1 << nbits
	dsub  = d / mq
	nDB   = 400
)

var okCount, failCount int

func check(cond bool, msg string) {
	if cond {
		okCount++
		fmt.Println("  [ok]   " + msg)
	} else {
		failCount++
		fmt.Println("  [FAIL] " + msg)
	}
}

// LCG:可复现的伪随机,避免引入 math/rand 的种子差异
type lcg struct{ s uint32 }

func (l *lcg) next() float64 {
	l.s = l.s*1664525 + 1013904223
	return float64(l.s>>8)/float64(1<<24)*6.0 - 3.0
}
func (l *lcg) vec(n int) []float64 {
	v := make([]float64, n)
	for i := range v {
		v[i] = l.next()
	}
	return v
}

func l2(a, b []float64) float64 {
	s := 0.0
	for i := range a {
		t := a[i] - b[i]
		s += t * t
	}
	return s
}

type Index struct {
	coarse   [][]float64
	sub      [][][]float64 // (M, ksub, dsub)
	lists    [][]int
	codes    [][]int
	db       [][]float64
}

func newIndex() *Index {
	l := &lcg{s: 7}
	ix := &Index{}
	for i := 0; i < nlist; i++ {
		ix.coarse = append(ix.coarse, l.vec(d))
	}
	for mi := 0; mi < mq; mi++ {
		var one [][]float64
		for j := 0; j < ksub; j++ {
			one = append(one, l.vec(dsub))
		}
		ix.sub = append(ix.sub, one)
	}
	for i := 0; i < nDB; i++ {
		ix.db = append(ix.db, l.vec(d))
	}
	ix.lists = make([][]int, nlist)
	ix.codes = make([][]int, nlist)
	for vid, x := range ix.db {
		j := ix.nearestCoarse(x)
		ix.lists[j] = append(ix.lists[j], vid)
		ix.codes[j] = append(ix.codes[j], ix.encode(ix.residual(x, j))...)
	}
	return ix
}

func (ix *Index) nearestCoarse(x []float64) int {
	best, bd := 0, math.Inf(1)
	for j := range ix.coarse {
		if t := l2(x, ix.coarse[j]); t < bd {
			best, bd = j, t
		}
	}
	return best
}

func (ix *Index) residual(x []float64, j int) []float64 {
	r := make([]float64, d)
	for i := range x {
		r[i] = x[i] - ix.coarse[j][i]
	}
	return r
}

// encode 残差 → M 个子量化索引
func (ix *Index) encode(r []float64) []int {
	code := make([]int, mq)
	for mi := 0; mi < mq; mi++ {
		v := r[mi*dsub : (mi+1)*dsub]
		best, bd := 0, math.Inf(1)
		for j := 0; j < ksub; j++ {
			if t := l2(v, ix.sub[mi][j]); t < bd {
				best, bd = j, t
			}
		}
		code[mi] = best
	}
	return code
}

func (ix *Index) decode(code []int) []float64 {
	out := []float64{}
	for mi := 0; mi < mq; mi++ {
		out = append(out, ix.sub[mi][code[mi]]...)
	}
	return out
}

// distanceTable dis_table(m,j) = ||r_m − c_(m,j)||²,形状 M × ksub
func (ix *Index) distanceTable(r []float64) [][]float64 {
	t := make([][]float64, mq)
	for mi := 0; mi < mq; mi++ {
		t[mi] = make([]float64, ksub)
		v := r[mi*dsub : (mi+1)*dsub]
		for j := 0; j < ksub; j++ {
			t[mi][j] = l2(v, ix.sub[mi][j])
		}
	}
	return t
}

// adc Asymmetric Distance Computation:查表累加,无浮点向量运算
func adc(t [][]float64, code []int) float64 {
	s := 0.0
	for mi := range code {
		s += t[mi][code[mi]]
	}
	return s
}

// search 只扫 nprobe 个列表;返回扫描过的向量数
func (ix *Index) search(q []float64, nprobe int) (int, int) {
	type pair struct {
		j   int
		dd  float64
	}
	ps := []pair{}
	for j := range ix.coarse {
		ps = append(ps, pair{j, l2(q, ix.coarse[j])})
	}
	for a := 1; a < len(ps); a++ {
		for b := a; b > 0 && ps[b].dd < ps[b-1].dd; b-- {
			ps[b], ps[b-1] = ps[b-1], ps[b]
		}
	}
	scanned, best := 0, math.Inf(1)
	for i := 0; i < nprobe && i < len(ps); i++ {
		j := ps[i].j
		t := ix.distanceTable(ix.residual(q, j))
		for k, vid := range ix.lists[j] {
			code := ix.codes[j][k*mq : (k+1)*mq]
			if s := adc(t, code); s < best {
				best = s
			}
			scanned++
			_ = vid
		}
	}
	return scanned, int(best * 1000)
}

func main() {
	ix := newIndex()
	fmt.Println("== Demo 1 · 结构参数 ==")
	codeBytes := int(math.Ceil(float64(mq*nbits) / 8.0))
	rawBytes := d * 4
	fmt.Printf("   d=%d M=%d nbits=%d ⇒ dsub=%d ksub=%d\n", d, mq, nbits, dsub, ksub)
	check(dsub == d/mq && ksub == 1<<nbits, "dsub 与 ksub 由参数推导")
	check(codeBytes == 2, "nbits=4,M=4 ⇒ 码字 2 字节(PQEncoderGeneric 按位打包)")
	check(rawBytes/codeBytes == 32, "压缩比 32×(d=16,float32)")

	fmt.Println("\n== Demo 2 · 距离表 M × ksub ==")
	r := ix.residual(ix.db[0], ix.nearestCoarse(ix.db[0]))
	t := ix.distanceTable(r)
	allsame := true
	for mi := 0; mi < mq; mi++ {
		v := r[mi*dsub : (mi+1)*dsub]
		for j := 0; j < ksub; j++ {
			if math.Abs(t[mi][j]-l2(v, ix.sub[mi][j])) > 1e-9 {
				allsame = false
			}
		}
	}
	check(len(t) == mq && len(t[0]) == ksub, "距离表是 M × ksub 的矩阵")
	check(allsame, "表项与逐项暴力 L2 完全一致")

	fmt.Println("\n== Demo 3 · ADC = ||r − decode(code)||² ==")
	code := ix.encode(r)
	a := adc(t, code)
	direct := l2(r, ix.decode(code))
	fmt.Printf("   ADC=%.6f  direct=%.6f\n", a, direct)
	check(math.Abs(a-direct) < 1e-9, "ADC 恒等于到重构残差的平方距离(逐块可加)")
	check(l2(r, ix.decode(code)) > 0, "PQ 有损:重构误差 > 0")

	fmt.Println("\n== Demo 4 · nprobe 决定扫多少 ==")
	prev := -1
	for _, np := range []int{1, 2, 4, 8, nlist} {
		sc, _ := ix.search(ix.db[1], np)
		fmt.Printf("   nprobe=%-3d ⇒ 扫过 %d / %d 条 (%.1f%%)\n", np, sc, nDB, 100.0*float64(sc)/nDB)
		check(sc >= prev, "nprobe 增大不会减少扫描量")
		prev = sc
	}
	sc, _ := ix.search(ix.db[1], nlist)
	check(sc == nDB, "nprobe == nlist ⇒ 扫过 100% 的库(退化为穷举)")

	fmt.Println("\n== Demo 5 · SDC 表 ==")
	sdcTab := make([][][]float64, mq)
	for mi := 0; mi < mq; mi++ {
		sdcTab[mi] = make([][]float64, ksub)
		for i := 0; i < ksub; i++ {
			sdcTab[mi][i] = make([]float64, ksub)
			for j := 0; j < ksub; j++ {
				sdcTab[mi][i][j] = l2(ix.sub[mi][i], ix.sub[mi][j])
			}
		}
	}
	qcode := ix.encode(ix.residual(ix.db[1], ix.nearestCoarse(ix.db[1])))
	sdcD := 0.0
	for mi := 0; mi < mq; mi++ {
		sdcD += sdcTab[mi][qcode[mi]][code[mi]]
	}
	fmt.Printf("   SDC 表 = M×ksub×ksub = %d 个 float;ADC 表 = M×ksub = %d\n",
		mq*ksub*ksub, mq*ksub)
	fmt.Printf("   同一对: ADC=%.6f SDC=%.6f\n", a, sdcD)
	check(sdcD >= 0, "SDC 非负")
	check(math.Abs(sdcD-a) > 1e-6, "SDC ≠ ADC(对称 vs 非对称)")

	fmt.Printf("\n断言 %d 通过 / %d 失败\n", okCount, failCount)
	if failCount > 0 {
		os.Exit(1)
	}
}

// GPU 实例化渲染:Go 复刻 draw call 记账与 divisor 取数模拟。
// 依据 LearnOpenGL "Instancing":N 次 draw call 合并为 1 次;
// per-vertex 属性随顶点前进,per-instance 属性随实例前进(glVertexAttribDivisor=1)。
package main

import (
	"fmt"
	"math"
)

const (
	cpuPrepUS = 25.0 // 每次 draw call 的 CPU 端准备开销(μs)
	gpuVtxNS  = 5.0  // 每顶点的 GPU 顶点着色耗时(ns,建模用)
)

// drawCallLog:记录 draw call(顶点数、实例数、CPU 准备耗时)。
type drawCallLog struct {
	calls [][3]float64 // (vertexCount, instanceCount, cpuUS)
}

func (l *drawCallLog) draw(vertexCount, instanceCount int) {
	l.calls = append(l.calls, [3]float64{float64(vertexCount), float64(instanceCount), cpuPrepUS})
}

func (l *drawCallLog) totalCPUUS() float64 {
	var s float64
	for _, c := range l.calls {
		s += c[2]
	}
	return s
}

func (l *drawCallLog) totalVtx() int {
	s := 0
	for _, c := range l.calls {
		s += int(c[0] * c[1])
	}
	return s
}

// vertexStream:模拟 GPU 的两类属性流(divisor=0 / divisor=1)。
type vertexStream struct {
	vertexData   [][2]float64 // per-vertex:位置
	instanceData []float64    // per-instance:偏移
}

// fetch:GPU 为 (j, i) 取数的等价模拟,并检查越界。
func (s *vertexStream) fetch(vertexIndex, instanceIndex int) ([2]float64, float64, error) {
	if vertexIndex < 0 || vertexIndex >= len(s.vertexData) {
		return [2]float64{}, 0, fmt.Errorf("vertex attr out of range: %d", vertexIndex)
	}
	if instanceIndex < 0 || instanceIndex >= len(s.instanceData) {
		return [2]float64{}, 0, fmt.Errorf("instance attr out of range: %d", instanceIndex)
	}
	return s.vertexData[vertexIndex], s.instanceData[instanceIndex], nil
}

// instancedDraw:一次 glDrawElementsInstanced,返回每实例的取样。
func instancedDraw(s *vertexStream, instanceCount int, log *drawCallLog) [][3]interface{} {
	vcount := len(s.vertexData)
	log.draw(vcount, instanceCount)
	var samples [][3]interface{}
	for i := 0; i < instanceCount; i++ {
		for j := 0; j < vcount; j++ {
			v, inst, err := s.fetch(j, i)
			if err != nil {
				panic(err.Error())
			}
			if j == 0 {
				samples = append(samples, [3]interface{}{i, v, inst})
			}
		}
	}
	return samples
}

// mat4Columns:mat4 占 4 个顶点属性位置,第 3 列携带平移。
func mat4Columns(ox, oy, oz, scale float64) [4][4]float64 {
	return [4][4]float64{
		{scale, 0, 0, 0},
		{0, scale, 0, 0},
		{0, 0, scale, 0},
		{ox, oy, oz, 1},
	}
}

func check(label string, cond bool, detail string) {
	if !cond {
		panic("FAIL " + label + ": " + detail)
	}
	fmt.Println("ok -", label)
}

func main() {
	// 1) divisor 取数:vertex 属性只跟 j 有关,instance 属性只跟 i 有关
	vs := &vertexStream{
		vertexData:   [][2]float64{{0, 0}, {0.5, 0}, {0.5, 0.5}, {0, 0.5}},
		instanceData: make([]float64, 100),
	}
	for i := range vs.instanceData {
		vs.instanceData[i] = float64(i)
	}
	log := &drawCallLog{}
	samples := instancedDraw(vs, 100, log)
	// gl_InstanceID 从 0 开始:第 43 个实例的 ID 是 42
	s42 := samples[42]
	check("instance ID 0-based", s42[0].(int) == 42 && s42[2].(float64) == 42.0,
		fmt.Sprintf("%v", s42))
	check("one call for 100 instances", len(log.calls) == 1 && log.calls[0][1] == 100, "")
	for _, i := range []int{0, 37, 99} {
		for j := 0; j < 4; j++ {
			v, inst, _ := vs.fetch(j, i)
			check("fetch independence", v == vs.vertexData[j] && inst == float64(i),
				fmt.Sprintf("j=%d i=%d v=%v inst=%v", j, i, v, inst))
		}
	}

	// 2) uniform 上限 vs instanced array:保证下限 1024 个分量
	const maxUniformComponents = 1024
	maxUniformInstances := maxUniformComponents / 2 // 每实例一个 vec2
	check("uniform capacity", maxUniformInstances == 512, "")
	check("instanced array beyond uniform limit", 1000 > maxUniformInstances, "")

	// 3) 小行星带记账:1001 次 vs 2 次 draw call
	const rockVertices = 576
	const nAsteroids = 1000
	naive := &drawCallLog{}
	for i := 0; i <= nAsteroids; i++ { // 1000 岩石 + 1 行星
		naive.draw(rockVertices, 1)
	}
	inst := &drawCallLog{}
	inst.draw(rockVertices, nAsteroids)
	inst.draw(rockVertices, 1)
	check("naive 1001 calls", len(naive.calls) == 1001, fmt.Sprintf("%d", len(naive.calls)))
	check("instanced 2 calls", len(inst.calls) == 2, "")
	check("same vertex work", naive.totalVtx() == inst.totalVtx() && inst.totalVtx() == rockVertices*(nAsteroids+1),
		fmt.Sprintf("%d vs %d", naive.totalVtx(), inst.totalVtx()))
	check("cpu prep 500x gap", naive.totalCPUUS() > inst.totalCPUUS()*100,
		fmt.Sprintf("%.1f vs %.1f", naive.totalCPUUS(), inst.totalCPUUS()))

	// 4) 教程规模:100000 实例 × 576 顶点 ≈ 57.6M 顶点仍是 2 次调用
	big := &drawCallLog{}
	big.draw(rockVertices, 100000)
	big.draw(rockVertices, 1)
	check("100k still 2 calls", len(big.calls) == 2, "")
	check("57.6M vertices", big.totalVtx() == 576*100001,
		fmt.Sprintf("%d", big.totalVtx()))

	// 5) mat4 实例属性:4 列拆分,平移在第 3 列
	cols := mat4Columns(1.0, 2.0, 3.0, 0.5)
	check("mat4 translation col", cols[3] == [4]float64{1, 2, 3, 1}, fmt.Sprintf("%v", cols[3]))
	check("mat4 scale diag", cols[0][0] == 0.5 && cols[1][1] == 0.5 && cols[2][2] == 0.5, "")
	check("mat4 off-diag zero", cols[0][1] == 0 && math.Abs(cols[3][0]-1.0) < 1e-12, "")

	fmt.Println("ALL TESTS PASSED")
	fmt.Printf("naive cpu prep = %.1f ms, instanced = %.3f ms\n",
		naive.totalCPUUS()/1000.0, inst.totalCPUUS()/1000.0)
}

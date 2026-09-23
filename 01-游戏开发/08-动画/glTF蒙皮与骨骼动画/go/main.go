// glTF 2.0 线性混合蒙皮（Skinned Mesh）的 Go 侧实现，与 python/skinning.py 同协议。
//
// 依据 KhronosGroup/glTF@main specification/2.0/Specification.adoc「Skins」三节：
//   - 只应用关节变换，挂着 mesh 的节点变换 MUST be ignored；
//   - inverseBindMatrix 先于骨骼自身节点变换生效 ⇒ jointMatrix = globalJoint · inverseBind；
//   - 单集合最多 4 关节；权重非负；同顶点内同关节不得有两个非零权重；
//   - unorm8 / unorm16 的权重在归一化前整数和 MUST 为 255 / 65535。
package main

import (
	"errors"
	"fmt"
	"math"
	"sort"
)

// Mat 为行主序 4x4，列向量约定：v' = M · v。
type Mat [4][4]float64

// Vec 为齐次坐标。
type Vec [4]float64

func identity() Mat {
	m := Mat{}
	for i := 0; i < 4; i++ {
		m[i][i] = 1
	}
	return m
}

func mul(a, b Mat) Mat {
	var out Mat
	for i := 0; i < 4; i++ {
		for j := 0; j < 4; j++ {
			s := 0.0
			for k := 0; k < 4; k++ {
				s += a[i][k] * b[k][j]
			}
			out[i][j] = s
		}
	}
	return out
}

func translation(t [3]float64) Mat {
	m := identity()
	m[0][3], m[1][3], m[2][3] = t[0], t[1], t[2]
	return m
}

func scale(s [3]float64) Mat {
	m := identity()
	m[0][0], m[1][1], m[2][2] = s[0], s[1], s[2]
	return m
}

// fromQuat 把 xyzw 四元数（glTF rotation 的书写顺序）转为旋转矩阵。
func fromQuat(q [4]float64) (Mat, error) {
	x, y, z, w := q[0], q[1], q[2], q[3]
	n := math.Sqrt(x*x + y*y + z*z + w*w)
	if n == 0 {
		return Mat{}, errors.New("zero quaternion")
	}
	x, y, z, w = x/n, y/n, z/n, w/n
	return Mat{
		{1 - 2*(y*y+z*z), 2 * (x*y - z*w), 2 * (x*z + y*w), 0},
		{2 * (x*y + z*w), 1 - 2*(x*x+z*z), 2 * (y*z - x*w), 0},
		{2 * (x*z - y*w), 2 * (y*z + x*w), 1 - 2*(x*x+y*y), 0},
		{0, 0, 0, 1},
	}, nil
}

// inverse 用 Gauss-Jordan 求逆。
func inverse(m Mat) (Mat, error) {
	a := [4][8]float64{}
	for i := 0; i < 4; i++ {
		for j := 0; j < 4; j++ {
			a[i][j] = m[i][j]
		}
		a[i][4+i] = 1
	}
	for col := 0; col < 4; col++ {
		piv := col
		for r := col + 1; r < 4; r++ {
			if math.Abs(a[r][col]) > math.Abs(a[piv][col]) {
				piv = r
			}
		}
		if math.Abs(a[piv][col]) < 1e-18 {
			return Mat{}, errors.New("singular matrix")
		}
		a[col], a[piv] = a[piv], a[col]
		pv := a[col][col]
		for j := 0; j < 8; j++ {
			a[col][j] /= pv
		}
		for r := 0; r < 4; r++ {
			if r == col || a[r][col] == 0 {
				continue
			}
			f := a[r][col]
			for j := 0; j < 8; j++ {
				a[r][j] -= f * a[col][j]
			}
		}
	}
	var out Mat
	for i := 0; i < 4; i++ {
		for j := 0; j < 4; j++ {
			out[i][j] = a[i][4+j]
		}
	}
	return out, nil
}

func apply(m Mat, v Vec) Vec {
	var out Vec
	for i := 0; i < 4; i++ {
		s := 0.0
		for k := 0; k < 4; k++ {
			s += m[i][k] * v[k]
		}
		out[i] = s
	}
	return out
}

// Node 对应 glTF node：局部 TRS + 父子层级。
type Node struct {
	Name        string
	T           [3]float64
	R           [4]float64
	S           [3]float64
	Parent      *Node
}

func (n *Node) local() Mat {
	r, err := fromQuat(n.R)
	if err != nil {
		panic(err)
	}
	return mul(mul(translation(n.T), r), scale(n.S))
}

func (n *Node) global() Mat {
	chain := []Mat{}
	for cur := n; cur != nil; cur = cur.Parent {
		chain = append([]Mat{cur.local()}, chain...)
	}
	out := identity()
	for _, m := range chain {
		out = mul(out, m)
	}
	return out
}

// Skin 对应 skin.joints + inverseBindMatrices。
type Skin struct {
	Joints      []*Node
	InverseBind []Mat
}

func NewSkin(joints []*Node, ibm []Mat) (*Skin, error) {
	s := &Skin{Joints: joints}
	if ibm == nil {
		for _, j := range joints {
			inv, err := inverse(j.global())
			if err != nil {
				return nil, err
			}
			s.InverseBind = append(s.InverseBind, inv)
		}
	} else {
		s.InverseBind = ibm
	}
	return s, s.validate()
}

func (s *Skin) validate() error {
	if len(s.InverseBind) < len(s.Joints) {
		return errors.New("inverseBindMatrices 元素数必须不少于 joints 数")
	}
	for _, m := range s.InverseBind {
		if math.Abs(m[3][0]) > 1e-12 || math.Abs(m[3][1]) > 1e-12 ||
			math.Abs(m[3][2]) > 1e-12 || math.Abs(m[3][3]-1) > 1e-12 {
			return errors.New("inverseBindMatrices 第四行必须是 [0,0,0,1]")
		}
		for i := 0; i < 4; i++ {
			for j := 0; j < 4; j++ {
				if math.IsNaN(m[i][j]) || math.IsInf(m[i][j], 0) {
					return errors.New("inverseBindMatrices 不得含 NaN/Inf")
				}
			}
		}
	}
	return nil
}

// JointMatrix 给出 jointMatrix(j) = globalJoint(j) · inverseBind(j)。
func (s *Skin) JointMatrix(j int) Mat {
	return mul(s.Joints[j].global(), s.InverseBind[j])
}

// SkinVertex 线性混合蒙皮：v' = Σ w_k · jointMatrix(k) · v，不做齐次除法。
func (s *Skin) SkinVertex(v Vec, joints []int, weights []float64) (Vec, error) {
	if len(joints) > 4 || len(weights) > 4 {
		return Vec{}, errors.New("单集合最多 4 个关节")
	}
	if len(joints) != len(weights) {
		return Vec{}, errors.New("JOINTS_n 与 WEIGHTS_n 集合数必须相等")
	}
	seen := map[int]bool{}
	for k, j := range joints {
		if j < 0 || j >= len(s.Joints) {
			return Vec{}, errors.New("关节索引越界")
		}
		if weights[k] < 0 {
			return Vec{}, errors.New("权重不得为负")
		}
		if weights[k] != 0 {
			if seen[j] {
				return Vec{}, errors.New("同一顶点内同一关节不得有多于一个非零权重")
			}
			seen[j] = true
		}
	}
	var acc Vec
	for k, j := range joints {
		if weights[k] == 0 {
			continue
		}
		p := apply(s.JointMatrix(j), v)
		for i := 0; i < 4; i++ {
			acc[i] += weights[k] * p[i]
		}
	}
	return acc, nil
}

// QuantizeWeights 用最大余数法量化，保证整数和恰为 2^bits - 1。
func QuantizeWeights(w []float64, bits int) ([]int, error) {
	if bits != 8 && bits != 16 {
		return nil, errors.New("仅支持 unorm8 / unorm16")
	}
	maxInt := (1 << bits) - 1
	raw := make([]float64, len(w))
	base := make([]int, len(w))
	total := 0
	for i, x := range w {
		raw[i] = x * float64(maxInt)
		base[i] = int(math.Floor(raw[i]))
		total += base[i]
	}
	remain := maxInt - total
	idx := make([]int, len(w))
	for i := range idx {
		idx[i] = i
	}
	sort.SliceStable(idx, func(a, b int) bool { return raw[idx[a]]-float64(base[idx[a]]) > raw[idx[b]]-float64(base[idx[b]]) })
	out := append([]int(nil), base...)
	for i := 0; i < remain; i++ {
		out[idx[i%len(idx)]]++
	}
	return out, nil
}

func main() {
	n0 := &Node{Name: "node_0", T: [3]float64{0, 1, 0}, R: [4]float64{0, 0, 0, 1}, S: [3]float64{1, 1, 1}}
	n1 := &Node{Name: "node_1", T: [3]float64{0, 0, 0}, R: [4]float64{0, 0, 0, 1}, S: [3]float64{0.5, 0.5, 0.5}, Parent: n0}
	n2 := &Node{Name: "node_2", T: [3]float64{0, 0, 0}, R: [4]float64{0, 0, 0, 1}, S: [3]float64{1, 1, 1}, Parent: n1}
	skin, err := NewSkin([]*Node{n1, n2}, nil)
	if err != nil {
		panic(err)
	}
	fmt.Println("[绑定姿态] jointMatrix(0) 是否单位阵:", skin.JointMatrix(0) == identity())
	p, _ := skin.SkinVertex(Vec{1, 2, 3, 1}, []int{0, 1}, []float64{0.5, 0.5})
	fmt.Println("[绑定姿态] 蒙皮 (1,2,3) ->", p)

	n1.R = [4]float64{0, 1, 0, 0} // 绕 Y 转 180°
	p, _ = skin.SkinVertex(Vec{1, 0, 0, 1}, []int{0}, []float64{1})
	fmt.Println("[摆姿势后] (1,0,0) ->", p)

	q, _ := QuantizeWeights([]float64{0.25, 0.25, 0.25, 0.25}, 8)
	sum := 0
	for _, v := range q {
		sum += v
	}
	fmt.Println("[unorm8] 0.25×4 ->", q, "和 =", sum)

	// 重复非零关节必须报错
	_, err = skin.SkinVertex(Vec{1, 0, 0, 1}, []int{0, 0}, []float64{0.5, 0.5})
	fmt.Println("[校验] 重复非零关节:", err)
}

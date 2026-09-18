// UI 合批（batch build）与 Canvas 重建（rebuild）—— Go 版。
//
// 依据 Unity 官方教程《Optimizing Unity UI》（Unity Learn，Unity Technologies）：
//   · Canvas（native）把下辖 mesh 合并成 batch 并生成渲染命令，结果缓存到 Canvas 变脏为止（rebatch）
//   · 计算 batch 需要「按 depth 排序 mesh，并检查重叠、共享材质等」
//   · Sub-canvas 隔离父子：脏孩子不会迫使父级重建
//   · Rebuild 由 CanvasUpdateRegistry.PerformUpdate 驱动，WillRenderCanvases 每帧一次：
//       ① 脏 Layout 重建（**按层级深度排序**）② Mask 剔除 ③ 脏 Graphic 重建（**不排序**）
//   · UI 几何一律走 Transparent queue，被完全遮挡的像素照样被采样 → overdraw 吃 fill-rate
//
// 运行：go run ui_batching.go
package main

import (
	"fmt"
	"sort"
)

// Element 是一个 UI 可绘制元素。
type Element struct {
	Name      string
	Material  string
	Texture   string
	Depth     int
	X, Y, W, H float64
	Canvas    string
	Graphic   bool
	Alpha     float64
	IsMask    bool
	// 重建状态
	DirtyLayout, DirtyVertices bool
	HierarchyDepth             int // 距根的层数（Layout 排序用）
}

// NewImage 造一个默认 UI 材质下的图片元素。
func NewImage(name string, depth int, tex string) *Element {
	return &Element{Name: name, Material: "ui_default", Texture: tex, Depth: depth,
		W: 10, H: 10, Canvas: "root", Graphic: true, Alpha: 1}
}

func (e *Element) batchKey() string { return e.Material + "|" + e.Texture }
func (e *Element) area() float64    { return e.W * e.H }

// Overlaps 两个矩形是否有正面积交叠。
func Overlaps(a, b *Element) bool {
	ox := min(a.X+a.W, b.X+b.W) - max(a.X, b.X)
	oy := min(a.Y+a.H, b.Y+b.H) - max(a.Y, b.Y)
	return ox > 0 && oy > 0
}

func min(a, b float64) float64 { if a < b { return a }; return b }
func max(a, b float64) float64 { if a > b { return a }; return b }

// BuildBatches Canvas 的 rebatch：按 depth 排序后贪心成批（共享 material+texture）。
func BuildBatches(elements []*Element) [][]*Element {
	ordered := make([]*Element, 0, len(elements))
	for _, e := range elements {
		if e.Graphic {
			ordered = append(ordered, e)
		}
	}
	sort.SliceStable(ordered, func(i, j int) bool { return ordered[i].Depth < ordered[j].Depth })

	batches := [][]*Element{}
	for _, e := range ordered {
		if len(batches) > 0 && batches[len(batches)-1][0].batchKey() == e.batchKey() {
			batches[len(batches)-1] = append(batches[len(batches)-1], e)
		} else {
			batches = append(batches, []*Element{e})
		}
	}
	return batches
}

// DrawCalls 返回 draw call 数（== 批数）。
func DrawCalls(elements []*Element) int { return len(BuildBatches(elements)) }

// OverdrawRatio 采样像素总数 / 屏幕面积。
func OverdrawRatio(elements []*Element, screenW, screenH float64) (float64, float64) {
	total := 0.0
	for _, e := range elements {
		if e.Graphic {
			total += e.area()
		}
	}
	screen := screenW * screenH
	if screen == 0 {
		return total, 0
	}
	return total, total / screen
}

// Registry 是 CanvasUpdateRegistry 的最小模型。
type Registry struct {
	Elements      []*Element
	Masks         []*Element
	RegisterOrder []*Element // IndexedSet 的插入顺序
}

func (r *Registry) Add(e *Element) *Element {
	r.Elements = append(r.Elements, e)
	r.RegisterOrder = append(r.RegisterOrder, e)
	if e.IsMask {
		r.Masks = append(r.Masks, e)
	}
	return e
}

// PerformUpdate 三步：Layout → Clipping → Graphic。
func (r *Registry) PerformUpdate() ([]string, []string, []string) {
	dirtyLayout := []*Element{}
	for _, e := range r.Elements {
		if e.DirtyLayout {
			dirtyLayout = append(dirtyLayout, e)
			e.DirtyLayout = false
		}
	}
	sort.SliceStable(dirtyLayout, func(i, j int) bool {
		return dirtyLayout[i].HierarchyDepth < dirtyLayout[j].HierarchyDepth
	})
	layout := []string{}
	for _, e := range dirtyLayout {
		layout = append(layout, e.Name)
	}
	clip := []string{}
	for _, m := range r.Masks {
		clip = append(clip, m.Name)
	}
	graphic := []string{}
	for _, e := range r.RegisterOrder {
		if e.DirtyVertices {
			graphic = append(graphic, e.Name)
			e.DirtyVertices = false
		}
	}
	return layout, clip, graphic
}

// RebatchScope 一次 dirty 会迫使同一 Canvas 下的元素一起重算。
func (r *Registry) RebatchScope(dirty *Element) int {
	n := 0
	for _, e := range r.Elements {
		if canvasOf(e) == canvasOf(dirty) {
			n++
		}
	}
	return n
}

func canvasOf(e *Element) string {
	if e.Canvas == "" {
		return "root"
	}
	return e.Canvas
}

func main() {
	fmt.Println("[1-2] 同图集合批 / 异图集断批")
	fmt.Printf("  3 张同图集 → %d 批；两种图集 → %d 批\n",
		DrawCalls([]*Element{NewImage("a", 0, "atlas_a"), NewImage("b", 1, "atlas_a"),
			NewImage("c", 2, "atlas_a")}),
		DrawCalls([]*Element{NewImage("a", 0, "atlas_a"), NewImage("b", 1, "atlas_b")}))

	fmt.Println("[3] 交错排列拆批")
	inter := []*Element{NewImage("a1", 0, "atlas_a"), NewImage("b", 1, "atlas_b"),
		NewImage("a2", 2, "atlas_a")}
	sorted := []*Element{NewImage("a1", 0, "atlas_a"), NewImage("a2", 1, "atlas_a"),
		NewImage("b", 2, "atlas_b")}
	fmt.Printf("  A-B-A → %d 批；A-A-B → %d 批\n", DrawCalls(inter), DrawCalls(sorted))

	fmt.Println("[7] overdraw（Transparent queue：被遮挡像素照样采样）")
	eight := []*Element{}
	for i := 0; i < 8; i++ {
		e := NewImage(fmt.Sprintf("l%d", i), i, "atlas_a")
		e.X, e.Y, e.W, e.H = 0, 0, 1920, 1080
		eight = append(eight, e)
	}
	_, ratio := OverdrawRatio(eight, 1920, 1080)
	_, ratio7 := OverdrawRatio(eight[1:], 1920, 1080)
	fmt.Printf("  8 层全屏 → %.2f；关掉底板 → %.2f\n", ratio, ratio7)

	fmt.Println("[9] PerformUpdate：Layout 按层级排序、Graphic 不排序")
	reg := &Registry{}
	child := reg.Add(&Element{Name: "子布局", Graphic: true, HierarchyDepth: 3})
	parent := reg.Add(&Element{Name: "父布局", Graphic: true, HierarchyDepth: 1})
	root := reg.Add(&Element{Name: "根布局", Graphic: true, HierarchyDepth: 0})
	g2 := reg.Add(&Element{Name: "graphic2", Graphic: true})
	g1 := reg.Add(&Element{Name: "graphic1", Graphic: true})
	child.DirtyLayout, parent.DirtyLayout, root.DirtyLayout = true, true, true
	g2.DirtyVertices, g1.DirtyVertices = true, true
	layout, _, graphic := reg.PerformUpdate()
	fmt.Printf("  layout=%v graphic=%v\n", layout, graphic)

	fmt.Println("[10] Sub-canvas 隔离")
	reg2 := &Registry{}
	for i := 0; i < 100; i++ {
		reg2.Add(NewImage(fmt.Sprintf("static%d", i), i, "atlas_a"))
	}
	dyn := reg2.Add(NewImage("动态血条", 101, "atlas_a"))
	fmt.Printf("  单 Canvas：一次 dirty 重算 %d 个元素\n", reg2.RebatchScope(dyn))
	dyn.Canvas = "hud"
	fmt.Printf("  拆出 Sub-canvas 后：重算 %d 个元素\n", reg2.RebatchScope(dyn))

	fmt.Println("[11] 排列决定 draw call")
	many := []*Element{}
	grouped := []*Element{}
	for i := 0; i < 50; i++ {
		tex := "atlas_a"
		if i%2 == 1 {
			tex = "atlas_b"
		}
		many = append(many, NewImage(fmt.Sprintf("e%d", i), i, tex))
		g := "atlas_a"
		if i >= 25 {
			g = "atlas_b"
		}
		grouped = append(grouped, NewImage(fmt.Sprintf("g%d", i), i, g))
	}
	fmt.Printf("  交替 → %d 批；归拢 → %d 批\n", DrawCalls(many), DrawCalls(grouped))
}

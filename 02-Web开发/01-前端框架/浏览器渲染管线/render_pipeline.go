// 浏览器渲染管线模拟器的 Go 复刻，与 render_pipeline.py 同题。
// 依据：MDN《How browsers work》https://developer.mozilla.org/en-US/docs/Web/Performance/How_browsers_work
package main

import (
	"fmt"
	"strconv"
	"strings"
)

var geometryProps = map[string]bool{
	"width": true, "height": true, "top": true, "left": true, "margin": true,
	"margin-top": true, "padding": true, "font-size": true, "border-width": true,
	"display": true, "position": true,
}

var paintOnlyProps = map[string]bool{
	"color": true, "background": true, "background-color": true,
	"box-shadow": true, "border-color": true, "visibility": true,
}

var compositableProps = map[string]bool{"opacity": true, "transform": true, "filter": true}

var layerHintProps = map[string]bool{"will-change": true, "transform3d": true}

var layerTags = map[string]bool{"video": true, "canvas": true, "iframe": true}

const frameBudgetMs = 16.67

type Element struct {
	Tag      string
	Style    map[string]string
	Children []*Element
	Parent   *Element
	Box      [4]int
	HasBox   bool
}

func NewElement(tag string, style map[string]string) *Element {
	if style == nil {
		style = map[string]string{}
	}
	return &Element{Tag: tag, Style: style}
}

func (e *Element) Add(c *Element) *Element {
	c.Parent = e
	e.Children = append(e.Children, c)
	return c
}

func isLayered(e *Element) bool {
	if layerTags[e.Tag] {
		return true
	}
	for p := range e.Style {
		if layerHintProps[p] {
			return true
		}
	}
	return false
}

// OwnLayer 自己需要层就是自己，否则继承最近的层祖先。
func OwnLayer(e *Element) *Element {
	for n := e; n != nil; n = n.Parent {
		if isLayered(n) {
			return n
		}
	}
	return nil
}

func inRenderTree(e *Element) bool { return e.Style["display"] != "none" }

type Pipeline struct {
	Root          *Element
	Layouts       int
	Paints        int
	Composites    int
	DirtyLayout   bool
	DirtyPaint    bool
	LastFrameMs   float64
}

func NewPipeline(root *Element) *Pipeline {
	return &Pipeline{Root: root, DirtyLayout: true, DirtyPaint: true}
}

func (p *Pipeline) walk() []*Element {
	out := []*Element{}
	stack := []*Element{p.Root}
	for len(stack) > 0 {
		n := len(stack) - 1
		el := stack[n]
		stack = stack[:n]
		if !inRenderTree(el) {
			continue
		}
		out = append(out, el)
		for i := len(el.Children) - 1; i >= 0; i-- {
			stack = append(stack, el.Children[i])
		}
	}
	return out
}

// SetStyle 返回这次改动脏到了哪一层：layout / paint / composite / noop
func (p *Pipeline) SetStyle(el *Element, prop, value string) string {
	if el.Style[prop] == value {
		return "noop"
	}
	el.Style[prop] = value
	if geometryProps[prop] {
		p.DirtyLayout = true
		p.DirtyPaint = true
		return "layout"
	}
	if compositableProps[prop] && OwnLayer(el) != nil {
		return "composite" // 已提升的层改 opacity/transform 不需重绘
	}
	p.DirtyPaint = true
	return "paint"
}

// ReadLayout 读几何属性：布局脏就强制同步布局（layout thrashing 的来源）
func (p *Pipeline) ReadLayout(el *Element) [4]int {
	if p.DirtyLayout {
		p.Layout()
	}
	return el.Box
}

func px(s string) int {
	s = strings.TrimSuffix(s, "px")
	v, err := strconv.Atoi(s)
	if err != nil {
		return 0
	}
	return v
}

func (p *Pipeline) Layout() {
	p.Layouts++
	p.DirtyLayout = false
	y := 0
	for _, el := range p.walk() {
		w := px(el.Style["width"])
		h := px(el.Style["height"])
		el.Box = [4]int{0, y, w, h}
		el.HasBox = true
		y += h
	}
}

func (p *Pipeline) Paint()      { p.Paints++; p.DirtyPaint = false }
func (p *Pipeline) Composite()  { p.Composites++ }

func (p *Pipeline) Frame() float64 {
	ms := 2.0
	needLayout := p.DirtyLayout
	for _, e := range p.walk() {
		if !e.HasBox {
			needLayout = true
		}
	}
	if needLayout {
		p.Layout()
		ms += 3.0
	}
	if p.DirtyPaint {
		p.Paint()
		ms += 4.0
	}
	p.Composite()
	ms += 1.0
	p.LastFrameMs = ms
	return ms
}

func main() {
	root := NewElement("div", map[string]string{"width": "400px", "height": "400px"})
	a := root.Add(NewElement("p", map[string]string{"width": "100px", "height": "20px"}))
	b := root.Add(NewElement("div", map[string]string{"width": "100px", "height": "30px"}))
	cv := b.Add(NewElement("canvas", map[string]string{"width": "100px", "height": "30px"}))

	p := NewPipeline(root)
	p.Frame()
	fmt.Println("first frame:", p.Layouts, p.Paints, p.Composites)

	p.SetStyle(a, "width", "150px")
	p.Frame()
	fmt.Println("after geometry change:", p.Layouts, p.Paints, p.Composites)

	kind := p.SetStyle(cv, "opacity", "0.5")
	p.Frame()
	fmt.Println("opacity on promoted layer:", kind, "paints =", p.Paints)

	// layout thrashing：交错 vs 批量
	base := p.Layouts
	for i := 0; i < 5; i++ {
		p.SetStyle(a, "width", strconv.Itoa(201+i)+"px")
		p.ReadLayout(a)
	}
	fmt.Println("interleaved read/write layouts:", p.Layouts-base)
}

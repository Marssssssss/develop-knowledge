// Custom Elements 最小实现的 Go 复刻，与 web_components.py 同题。
// 依据：WHATWG HTML「4.13 Custom elements」https://html.spec.whatwg.org/multipage/custom-elements.html
package main

import "fmt"

// 生命周期回调用匿名接口断言，未实现即跳过（等价于 Python 的 getattr(..., None)）
type connected interface{ ConnectedCallback() }
type disconnected interface{ DisconnectedCallback() }
type attrChanged interface{ AttributeChangedCallback(string, string, string) }
type connectedMove interface{ ConnectedMoveCallback() }
type observed interface{ ObservedAttributes() []string }

var upgradeStates = map[string]bool{"undefined": true, "uncustomized": true}

type Element struct {
	LocalName   string
	State       string
	IsConnected bool
	Attributes  map[string]string
	Children    []string
	Impl        interface{}
	Log         []string
}

func NewElement(localName string) *Element {
	return &Element{
		LocalName:  localName,
		State:      "undefined",
		Attributes: map[string]string{},
	}
}

func (e *Element) fire(name string) {
	e.Log = append(e.Log, name)
	switch c := e.Impl.(type) {
	case connected:
		if name == "connectedCallback" {
			c.ConnectedCallback()
		}
	case disconnected:
		if name == "disconnectedCallback" {
			c.DisconnectedCallback()
		}
	case connectedMove:
		if name == "connectedMoveCallback" {
			c.ConnectedMoveCallback()
		}
	}
}

type Registry struct {
	Definitions map[string]func(*Element) interface{}
	Queue       []func()
	Processing  bool
	Elements    []*Element
}

func NewRegistry() *Registry {
	return &Registry{Definitions: map[string]func(*Element) interface{}{}}
}

func (r *Registry) Enqueue(fn func()) { r.Queue = append(r.Queue, fn) }

func (r *Registry) Process() {
	if r.Processing {
		return
	}
	r.Processing = true
	for len(r.Queue) > 0 {
		fn := r.Queue[0]
		r.Queue = r.Queue[1:]
		fn()
	}
	r.Processing = false
}

func (r *Registry) Define(name string, ctor func(*Element) interface{}) {
	r.Definitions[name] = ctor
	for _, el := range r.Elements {
		e := el
		if e.LocalName == name && upgradeStates[e.State] && e.IsConnected {
			r.Enqueue(func() { r.UpgradeElement(e) })
		}
	}
}

// UpgradeElement 返回是否真的升级了：已是 custom/failed 的元素保持幂等。
func (r *Registry) UpgradeElement(el *Element) bool {
	ctor, ok := r.Definitions[el.LocalName]
	if !ok || !upgradeStates[el.State] {
		return false
	}
	// 构造期间 attributes/children 对元素不可见（规范强制）
	savedAttrs, savedChildren := el.Attributes, el.Children
	el.Attributes, el.Children = map[string]string{}, nil
	func() {
		defer func() {
			if rec := recover(); rec != nil {
				el.State = "failed"
			}
		}()
		el.Impl = ctor(el)
		el.State = "custom"
	}()
	el.Attributes, el.Children = savedAttrs, savedChildren
	if el.State != "custom" {
		return false
	}
	el.fire("constructor")
	if el.IsConnected {
		r.Enqueue(func() { el.fire("connectedCallback") })
	}
	return true
}

func (r *Registry) Upgrade(root *Element) int {
	targets := r.Elements
	if root != nil {
		targets = []*Element{root}
	}
	done := 0
	for _, el := range targets {
		e := el
		if _, ok := r.Definitions[e.LocalName]; ok && upgradeStates[e.State] {
			r.Enqueue(func() { r.UpgradeElement(e) })
			done++
		}
	}
	return done
}

func (r *Registry) Connect(el *Element) {
	el.IsConnected = true
	if _, ok := r.Definitions[el.LocalName]; ok && upgradeStates[el.State] {
		r.Enqueue(func() { r.UpgradeElement(el) })
		return
	}
	if el.State == "custom" {
		r.Enqueue(func() { el.fire("connectedCallback") })
	}
}

func (r *Registry) Disconnect(el *Element) {
	el.IsConnected = false
	if el.State == "custom" {
		r.Enqueue(func() { el.fire("disconnectedCallback") })
	}
}

func (r *Registry) Move(el *Element) {
	if _, ok := el.Impl.(connectedMove); ok {
		r.Enqueue(func() { el.fire("connectedMoveCallback") })
		return
	}
	r.Enqueue(func() { el.fire("disconnectedCallback") })
	r.Enqueue(func() { el.fire("connectedCallback") })
}

func (r *Registry) SetAttribute(el *Element, name, value string) {
	old := el.Attributes[name]
	el.Attributes[name] = value
	if el.State != "custom" {
		return
	}
	if o, ok := el.Impl.(observed); ok {
		for _, n := range o.ObservedAttributes() {
			if n == name {
				on, ov := name, old
				r.Enqueue(func() {
					if c, ok2 := el.Impl.(attrChanged); ok2 {
						el.Log = append(el.Log, "attributeChangedCallback")
						c.AttributeChangedCallback(on, ov, value)
					}
				})
			}
		}
	}
}

// ---------- 示例实现 ----------

type widget struct {
	el    *Element
	conns []bool
}

func (w *widget) ObservedAttributes() []string { return []string{"title"} }
func (w *widget) ConnectedCallback()           { w.conns = append(w.conns, w.el.IsConnected) }

func main() {
	r := NewRegistry()
	el := NewElement("my-widget")
	el.IsConnected = true
	r.Elements = []*Element{el}
	r.Define("my-widget", func(e *Element) interface{} { return &widget{el: e} })
	r.Process()
	fmt.Println("after define + process:", el.State, el.Log)

	r.Disconnect(el)
	r.Process()
	r.Connect(el)
	r.Process()
	fmt.Println("reconnect:", el.Log)

	detached := NewElement("my-widget")
	n := r.Upgrade(detached)
	r.Process()
	fmt.Println("manual upgrade:", detached.State, detached.Log, "queued:", n)
}

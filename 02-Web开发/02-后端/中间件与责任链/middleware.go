// Go 侧对照实现：中间件洋葱链 + 靠签名区分的错误处理器。
//
// 官方依据：
//   Django  —— "You can think of it like an onion"：请求阶段按 MIDDLEWARE 定义顺序 top-down，
//              响应阶段逆序；某层短路则内层与 view 完全看不到该请求；
//              process_view 正序、process_exception 逆序（响应阶段一律逆序）、
//              process_template_response 在 view 结束后逆序调用；
//              __init__ 抛 MiddlewareNotUsed 即从链上摘除。
//   Express —— 错误处理器「always takes four arguments」，靠形参个数识别而非命名；
//              next('route') 只在 app.METHOD / router.METHOD 加载的栈里生效。
//
// Go 没有运行期 arity 反射，这里用**两个不同的函数类型**表达同一件事：
// Handler（3 参）与 ErrHandler（4 参）——编译期就把「几参」钉死，
// 顺带说明「靠命名区分错误处理中间件」在静态类型语言里根本不会发生。
package main

import (
	"errors"
	"fmt"
	"strings"
)

// Req / Resp 是最小请求响应载体。
type Req struct {
	Path string
	Bag  map[string]string
}

// Resp 是最小响应；Templ 标记它是否是「模板响应」（有 render 方法）。
type Resp struct {
	Status int
	Body   string
	Sent   bool
	Templ  bool
}

// Send 标记响应已发出。
func (r *Resp) Send(body string) { r.Body = body; r.Sent = true }

// Handler 对应 Express 的三参中间件 (req, res, next)。
type Handler func(*Req, *Resp, func(interface{}))

// ErrHandler 对应四参错误处理器 (err, req, res, next)。
type ErrHandler func(error, *Req, *Resp, func(interface{}))

// Layer 是一个挂载点上的中间件栈。
type Layer struct {
	Method   string // "USE" 或其它 HTTP 方法
	Path     string
	Handlers []Handler
	Errs     []ErrHandler
	LoadedBy string // "USE" | "METHOD" —— 决定 next('route') 是否生效
}

// Middleware 是 Django 式中间件：Call(request, get_response) + 三个可选钩子。
// Trace 是**共享**的轨迹切片指针，所有层往同一个切片上追加，才能得到全局调用序。
type Middleware struct {
	Name      string
	Trace     *[]string
	Short     bool
	ViewVal   *Resp // process_view 返回值，nil 表示 None
	ExcVal    *Resp // process_exception 返回值，nil 表示 None
	TemplResp bool
}

// NewMiddleware 建立中间件；unused 为 true 时返回 MiddlewareNotUsed（启动期摘除）。
func NewMiddleware(name string, trace *[]string, unused bool) (*Middleware, error) {
	if unused {
		return nil, fmt.Errorf("MiddlewareNotUsed: %s", name)
	}
	return &Middleware{Name: name, Trace: trace}, nil
}

func (m *Middleware) log(s string) { *m.Trace = append(*m.Trace, m.Name+"."+s) }

// ProcessView 对应 process_view：返回 nil 表示继续。
func (m *Middleware) ProcessView() *Resp {
	if m.ViewVal == nil {
		return nil
	}
	m.log("process_view")
	return m.ViewVal
}

// ProcessException 对应 process_exception：返回 nil 表示继续问下一层。
func (m *Middleware) ProcessException() *Resp {
	m.log("process_exception")
	return m.ExcVal
}

// ProcessTemplateResponse 对应 process_template_response。
func (m *Middleware) ProcessTemplateResponse(r *Resp) *Resp {
	if !m.TemplResp {
		return r
	}
	m.log("process_template_response")
	return r
}

// Call 是洋葱的一层：前置代码 → get_response → 后置代码。
func (m *Middleware) Call(getResponse func() *Resp) *Resp {
	m.log("in")
	if m.Short {
		m.log("short")
		m.log("out")
		return &Resp{Status: 200, Body: m.Name}
	}
	r := getResponse()
	m.log("out")
	return r
}

// Dispatch 按 Django 规则分派；viewErr 非 nil 时模拟 view 抛异常。
func Dispatch(mws []*Middleware, viewResp *Resp, viewErr error) (*Resp, []string) {
	trace := []string{}
	for _, m := range mws {
		m.Trace = &trace
	}
	var call func(i int) *Resp
	call = func(i int) *Resp {
		if i == len(mws) {
			for _, m := range mws {
				if r := m.ProcessView(); r != nil {
					return r
				}
			}
			if viewErr != nil {
				for j := len(mws) - 1; j >= 0; j-- {
					if r := mws[j].ProcessException(); r != nil {
						return r
					}
				}
				return &Resp{Status: 500, Body: "unhandled"}
			}
			r := viewResp
			if r.Templ {
				for j := len(mws) - 1; j >= 0; j-- {
					r = mws[j].ProcessTemplateResponse(r)
				}
			}
			return r
		}
		return mws[i].Call(func() *Resp { return call(i + 1) })
	}
	resp := call(0)
	return resp, trace
}

// ErrNextRoute 是 next('route') 的内部信号。
var ErrNextRoute = errors.New("route")

// Handle 跑一个 Express 式 layer 列表。
func Handle(layers []*Layer, req *Req) *Resp {
	res := &Resp{Status: 200}
	for _, l := range layers {
		if l.Method == "USE" && !strings.HasPrefix(req.Path, l.Path) {
			continue
		}
		if l.Method != "USE" && l.Path != req.Path {
			continue
		}
		skipped := false
		func() {
			defer func() {
				if rec := recover(); rec != nil {
					if e, ok := rec.(error); ok && e == ErrNextRoute {
						skipped = true
						return
					}
					panic(rec)
				}
			}()
			runStack(l, l.Handlers, req, res, nil)
		}()
		if skipped {
			continue // next('route')：交给下一个 route
		}
		if res.Sent {
			return res
		}
	}
	res.Status = 404
	return res
}

func runStack(l *Layer, hs []Handler, req *Req, res *Resp, err error) {
	if err != nil {
		// 错误路径：只找四参处理器，三参中间件被整体跳过
		for j := 0; j < len(l.Errs); j++ {
			l.Errs[j](err, req, res, func(interface{}) {})
			return
		}
		res.Status = 500
		res.Send("unhandled")
		return
	}
	for i := 0; i < len(hs); i++ {
		idx := i
		next := func(arg interface{}) {
			if s, ok := arg.(string); ok && s == "route" {
				if l.LoadedBy != "METHOD" {
					return // USE 栈里 next('route') 不生效
				}
				panic(ErrNextRoute)
			}
			if e, ok := arg.(error); ok {
				runStack(l, hs[idx+1:], req, res, e)
				return
			}
			runStack(l, hs[idx+1:], req, res, nil)
		}
		hs[i](req, res, next)
		if res.Sent {
			return
		}
	}
}

func main() {
	// --- Django 洋葱：请求正序、响应逆序 ---
	a, _ := NewMiddleware("A", nil, false)
	b, _ := NewMiddleware("B", nil, false)
	resp, trace := Dispatch([]*Middleware{a, b}, &Resp{Status: 200, Body: "view"}, nil)
	fmt.Printf("django onion  = %v body=%s\n", trace, resp.Body)

	// --- 短路：内层与 view 都看不到这个请求 ---
	a2, _ := NewMiddleware("A", nil, false)
	b2, _ := NewMiddleware("B", nil, false)
	b2.Short = true
	resp2, trace2 := Dispatch([]*Middleware{a2, b2}, &Resp{Status: 200, Body: "view"}, nil)
	fmt.Printf("short-circuit = %v body=%s\n", trace2, resp2.Body)

	// --- process_exception 逆序，命中后上层不再被问 ---
	a3, _ := NewMiddleware("A", nil, false)
	b3, _ := NewMiddleware("B", nil, false)
	c3, _ := NewMiddleware("C", nil, false)
	b3.ExcVal = &Resp{Status: 200, Body: "B"}
	_, trace3 := Dispatch([]*Middleware{a3, b3, c3}, nil, errors.New("boom"))
	fmt.Printf("exception     = %v\n", trace3)

	// --- MiddlewareNotUsed ---
	if _, err := NewMiddleware("X", nil, true); err != nil {
		fmt.Printf("unused        = %v\n", err)
	}

	// --- Express：三参被跳过、四参接管 ---
	var log []string
	app := []*Layer{{
		Method: "GET", Path: "/x", LoadedBy: "METHOD",
		Handlers: []Handler{
			func(r *Req, w *Resp, next func(interface{})) {
				log = append(log, "a")
				next(errors.New("boom"))
			},
			func(r *Req, w *Resp, next func(interface{})) {
				log = append(log, "b")
				w.Send("b")
			},
		},
		Errs: []ErrHandler{
			func(e error, r *Req, w *Resp, next func(interface{})) {
				log = append(log, "err")
				w.Status = 500
				w.Send("handled")
			},
		},
	}}
	out := Handle(app, &Req{Path: "/x"})
	fmt.Printf("express       = %v status=%d body=%s\n", log, out.Status, out.Body)
}

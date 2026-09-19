// ES modules 最小 loader 的 Go 复刻，与 esm_modules.py 同题。
// 依据：ECMA-262 Modules（Link / InitializeEnvironment / InnerModuleEvaluation）
package main

import (
	"errors"
	"fmt"
)

type Binding struct {
	value       interface{}
	initialized bool
	mutable     bool
}

func (b *Binding) Get() (interface{}, error) {
	if !b.initialized {
		return nil, errors.New("ReferenceError: cannot access before initialization")
	}
	return b.value, nil
}

func (b *Binding) Set(v interface{}) error {
	if !b.mutable {
		return errors.New("TypeError: assignment to constant or imported binding")
	}
	b.value = v
	b.initialized = true
	return nil
}

// ImportBinding 导入侧视图：读穿透到导出模块的 Binding（live），写一律报错。
type ImportBinding struct{ target *Binding }

func (i *ImportBinding) Get() (interface{}, error) { return i.target.Get() }
func (i *ImportBinding) Set(interface{}) error {
	return errors.New("TypeError: assignment to imported binding")
}

type slot interface {
	Get() (interface{}, error)
	Set(interface{}) error
}

type stmtKind int

const (
	kindLet stmtKind = iota
	kindConst
	kindFunction
)

type Stmt struct {
	Kind stmtKind
	Name string
	Fn   func(l *Loader, r *ModuleRecord) error
}

type Module struct {
	Name        string
	Imports     []ImportSpec
	Body        []Stmt
	Exports     []string
	StarExports []string
	Throws      error
}

type ImportSpec struct {
	From  string
	Names []string
}

type ModuleRecord struct {
	Mod      *Module
	Status   string // unlinked/linking/linked/evaluating/evaluated
	Env      map[string]slot
	Exports  map[string]*Binding
	Error    error
	loader   *Loader
}

func (r *ModuleRecord) ResolveExport(name string, seen map[string]bool) interface{} {
	key := r.Mod.Name + ":" + name
	if seen[key] {
		return nil
	}
	seen[key] = true
	if b, ok := r.Exports[name]; ok {
		return b
	}
	var found slot
	for _, s := range r.Mod.StarExports {
		got := r.loader.Records[s].ResolveExport(name, seen)
		if _, amb := got.(string); amb {
			return "ambiguous"
		}
		b, _ := got.(*Binding)
		if b == nil {
			continue
		}
		if found != nil && found != slot(b) {
			return "ambiguous"
		}
		found = b
	}
	if found == nil {
		return nil
	}
	return found
}

type Loader struct {
	Records   map[string]*ModuleRecord
	LinkOrder []string
	EvalOrder []string
}

func NewLoader(mods []*Module) *Loader {
	l := &Loader{Records: map[string]*ModuleRecord{}}
	for _, m := range mods {
		r := &ModuleRecord{Mod: m, Status: "unlinked", Env: map[string]slot{},
			Exports: map[string]*Binding{}, loader: l}
		l.Records[m.Name] = r
	}
	return l
}

func (l *Loader) Link(entry string) error { return l.link(l.Records[entry], []*ModuleRecord{}) }

func (l *Loader) link(rec *ModuleRecord, stack []*ModuleRecord) error {
	switch rec.Status {
	case "linking", "linked", "evaluating", "evaluated":
		return nil
	}
	rec.Status = "linking"
	stack = append(stack, rec)
	l.createLocalBindings(rec)
	for _, s := range rec.Mod.StarExports {
		if err := l.link(l.Records[s], stack); err != nil {
			return err
		}
	}
	for _, spec := range rec.Mod.Imports {
		dep, ok := l.Records[spec.From]
		if !ok {
			for _, r := range stack {
				r.Status = "unlinked"
			}
			return fmt.Errorf("SyntaxError: cannot resolve module '%s'", spec.From)
		}
		if err := l.link(dep, stack); err != nil {
			return err
		}
		for _, n := range spec.Names {
			got := dep.ResolveExport(n, map[string]bool{})
			b, ok2 := got.(*Binding)
			if !ok2 {
				for _, r := range stack {
					r.Status = "unlinked"
				}
				return fmt.Errorf("SyntaxError: '%s' provides no export '%s'", spec.From, n)
			}
			rec.Env[n] = &ImportBinding{target: b}
		}
	}
	rec.Status = "linked"
	l.LinkOrder = append(l.LinkOrder, rec.Mod.Name)
	return nil
}

// createLocalBindings 对应 InitializeEnvironment：函数声明立即初始化（提升），
// let/const 只建绑定不初始化 ⇒ TDZ。
func (l *Loader) createLocalBindings(rec *ModuleRecord) {
	for _, st := range rec.Mod.Body {
		b := &Binding{mutable: st.Kind != kindConst}
		rec.Env[st.Name] = b
		if st.Kind == kindFunction {
			b.value = st.Fn
			b.initialized = true
		}
	}
	for _, n := range rec.Mod.Exports {
		if b, ok := rec.Env[n].(*Binding); ok {
			rec.Exports[n] = b
		}
	}
}

func (l *Loader) Evaluate(entry string) {
	_ = l.eval(l.Records[entry], []*ModuleRecord{})
}

func (l *Loader) eval(rec *ModuleRecord, stack []*ModuleRecord) error {
	if rec.Status == "evaluating" || rec.Status == "evaluated" {
		return nil
	}
	rec.Status = "evaluating"
	stack = append(stack, rec)
	for _, spec := range rec.Mod.Imports {
		if err := l.eval(l.Records[spec.From], stack); err != nil {
			return err
		}
	}
	for _, st := range rec.Mod.Body {
		if st.Kind == kindFunction {
			continue
		}
		if rec.Mod.Throws != nil {
			for _, r := range stack {
				r.Error = rec.Mod.Throws
				r.Status = "evaluated"
			}
			return rec.Mod.Throws
		}
		if err := st.Fn(l, rec); err != nil {
			for _, r := range stack {
				r.Error = err
				r.Status = "evaluated"
			}
			return err
		}
	}
	rec.Status = "evaluated"
	l.EvalOrder = append(l.EvalOrder, rec.Mod.Name)
	return nil
}

func main() {
	b := &Module{Name: "b",
		Body:    []Stmt{{Kind: kindLet, Name: "x", Fn: func(l *Loader, r *ModuleRecord) error { return r.Env["x"].Set(1) }}},
		Exports: []string{"x"}}
	a := &Module{Name: "a", Imports: []ImportSpec{{From: "b", Names: []string{"x"}}},
		Body: []Stmt{{Kind: kindLet, Name: "y", Fn: func(l *Loader, r *ModuleRecord) error {
			v, err := r.Env["x"].Get()
			if err != nil {
				return err
			}
			return r.Env["y"].Set(v.(int) + 1)
		}}},
		Exports: []string{"y"}}
	l := NewLoader([]*Module{b, a})
	fmt.Println("link:", l.Link("a"))
	l.Evaluate("a")
	fmt.Println("eval order:", l.EvalOrder)
	y, _ := l.Records["a"].Env["y"].Get()
	fmt.Println("a.y =", y)
	_ = l.Records["b"].Env["x"].Set(10)
	x, _ := l.Records["a"].Env["x"].Get()
	fmt.Println("after b.x = 10, a.x (live) =", x)
	fmt.Println("import side write:", l.Records["a"].Env["x"].Set(99))
}

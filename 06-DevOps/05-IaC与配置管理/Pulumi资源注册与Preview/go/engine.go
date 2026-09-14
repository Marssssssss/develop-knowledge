// The deployment engine: registration, dependency layering, plan, apply.
//
// Ref read: "How Pulumi works"
// https://www.pulumi.com/docs/iac/concepts/how-pulumi-works/
//
// Mirrored behaviour: constructing a resource sends a *registration request*
// and the program keeps running ("the call returning does not mean the bucket
// was created"); independent registrations are handled in parallel, so the
// engine schedules them in dependency waves. The engine reads the recorded
// state, asks the provider to diff old vs desired, and chooses create / update
// / replace. A state entry that receives no registration request this run is
// scheduled for deletion. Output values of a resource that does not exist yet
// stay unknown, and anything derived from them stays unknown too.
package main

import (
	"fmt"
	"sort"
	"strings"
)

// Engine holds the state file (the checkpoint) plus this run's registrations.
type Engine struct {
	Project, Stack string
	Provider       Provider
	Live           bool // false == `pulumi preview`
	State          map[string]map[string]interface{}
	Reg            map[string]*Resource
	Order          []string
	rnd            int64
}

func NewEngine(project, stack string, p Provider, live bool) *Engine {
	return &Engine{Project: project, Stack: stack, Provider: p, Live: live,
		State: map[string]map[string]interface{}{}, Reg: map[string]*Resource{}}
}

// autoName is Pulumi's auto-naming: the logical name plus a random suffix,
// which lets several stacks coexist and enables zero-downtime replacement.
func (e *Engine) autoName(name string, n int) string {
	const hex = "0123456789abcdef"
	out := make([]byte, n)
	for i := range out {
		e.rnd = e.rnd*6364136223846793005 + 1442695040888963407
		out[i] = hex[byte(e.rnd>>35)%16]
	}
	return name + "-" + string(out)
}

// Register is the language-host side: send a registration request and return.
// Whether the outputs are known depends on what the state already records.
func (e *Engine) Register(t, name string, in map[string]interface{}, parent *Resource) *Resource {
	parentType := ""
	if parent != nil {
		parentType = parent.Type
	}
	u := URN(e.Stack, e.Project, t, name, parentType)
	if _, dup := e.Reg[u]; dup {
		fmt.Printf("  error: Duplicate resource URN '%s'\n", u)
		return nil
	}
	r := &Resource{Type: t, Name: name, URN: u, Inputs: in, Outputs: map[string]Output{}}
	if parent != nil {
		r.Parent = parent.URN
	}
	e.Reg[u] = r
	e.Order = append(e.Order, u)
	known, id := false, unknown
	if old, ok := e.State[u]; ok {
		known, id = true, old["id"].(string)
	}
	r.Outputs["id"] = Output{Known: known, Value: id, Deps: map[string]bool{u: true}}
	return r
}

// deps: edges come from Outputs used as Inputs, plus the explicit parent.
func (e *Engine) deps(r *Resource) map[string]bool {
	out := map[string]bool{}
	for _, v := range r.Inputs {
		if o, ok := v.(Output); ok {
			for d := range o.Deps {
				if d != r.URN {
					out[d] = true
				}
			}
		}
	}
	if r.Parent != "" {
		out[r.Parent] = true
	}
	return out
}

// Waves is Kahn layering: a resource waits for every resource it consumes.
func (e *Engine) Waves() [][]string {
	pending := map[string]map[string]bool{}
	for u, r := range e.Reg {
		pending[u] = e.deps(r)
	}
	done := map[string]bool{}
	var waves [][]string
	for len(pending) > 0 {
		var ready []string
		for u, d := range pending {
			blocked := false
			for dep := range d {
				blocked = blocked || !done[dep]
			}
			if !blocked {
				ready = append(ready, u)
			}
		}
		if len(ready) == 0 { // cycle: emit the leftovers and stop
			for u := range pending {
				ready = append(ready, u)
			}
			sort.Strings(ready)
			waves = append(waves, ready)
			break
		}
		sort.Strings(ready)
		waves = append(waves, ready)
		for _, u := range ready {
			done[u] = true
			delete(pending, u)
		}
	}
	return waves
}

// Plan classifies every URN: create / same / update / replace / delete.
func (e *Engine) Plan() map[string]string {
	ops := map[string]string{}
	for u, r := range e.Reg {
		old, ok := e.State[u]
		if !ok {
			ops[u] = "create"
			continue
		}
		prior := old["inputs"].(map[string]interface{})
		dirty := map[string]bool{}
		for k := range prior {
			if _, isOut := r.Inputs[k].(Output); isOut {
				continue // output-derived input: not statically comparable
			}
			if v, has := r.Inputs[k]; !has || v != prior[k] {
				dirty[k] = true
			}
		}
		for k, v := range r.Inputs {
			if _, known := prior[k]; !known {
				if _, isOut := v.(Output); !isOut {
					dirty[k] = true
				}
			}
		}
		switch {
		case len(dirty) == 0:
			ops[u] = "same"
		case e.Provider.ForcesReplace(r.Type, dirty):
			ops[u] = "replace"
		default:
			ops[u] = "update"
		}
	}
	for u := range e.State {
		if _, ok := ops[u]; !ok {
			ops[u] = "delete" // no registration request this run
		}
	}
	return ops
}

// Apply walks the waves in dependency order; preview writes nothing.
func (e *Engine) Apply(ops map[string]string) []string {
	var log []string
	tag := map[string]string{"create": "+", "update": "~", "replace": "+-"}
	for _, wave := range e.Waves() {
		for _, u := range wave {
			r, op, name := e.Reg[u], ops[u], e.Reg[u].Name
			if op == "same" {
				log = append(log, fmt.Sprintf("        %-19s unchanged", name))
				continue
			}
			if op == "replace" {
				log = append(log, fmt.Sprintf("    ++  %-19s create-replacement", name))
			}
			if !e.Live {
				log = append(log, fmt.Sprintf("    %-3s %-19s %s (preview)", tag[op], name, op))
				continue
			}
			e.write(u, r)
			log = append(log, fmt.Sprintf("    %-3s %-19s %sed", tag[op], name, op))
			if op == "replace" {
				log = append(log, fmt.Sprintf("    --  %-19s delete-replaced", name))
			}
		}
	}
	for u := range e.State {
		if _, ok := e.Reg[u]; !ok {
			if e.Live {
				log = append(log, fmt.Sprintf("    -   %-19s deleted", e.State[u]["name"]))
			}
			delete(e.State, u)
		}
	}
	return log
}

// write is the provider create/update: auto-name (unless a name was pinned),
// then record the result in the state file.
func (e *Engine) write(u string, r *Resource) {
	physical, _ := r.Inputs["bucket"].(string)
	if physical == "" {
		physical, _ = r.Inputs["name"].(string) // explicit name: no auto-naming
	}
	if physical == "" {
		physical = e.autoName(r.Name, 7)
	}
	inputs := map[string]interface{}{} // Output-valued inputs are not persisted
	for k, v := range r.Inputs {
		if _, isOut := v.(Output); !isOut {
			inputs[k] = v
		}
	}
	e.State[u] = map[string]interface{}{"type": r.Type, "name": r.Name,
		"id": e.autoName(strings.Split(r.Type, ":")[0], 12),
		"physical_name": physical, "inputs": inputs}
}

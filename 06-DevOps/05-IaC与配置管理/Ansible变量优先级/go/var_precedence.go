// Ansible variable precedence resolver (all 22 levels).
//
// Ref read before writing this file:
//   Ansible docs, "Using variables" -> "Variable precedence: where should I put
//   a variable?" https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_variables.html
//
// Mirrored facts:
//   - Exactly 22 precedence levels; the last listed overrides all others and
//     level 22 (`--extra-vars` / `-e`) "always win precedence".
//   - Level 1 is "Command-line values (for example, -u my_user, these are not
//     variables)" -- a placeholder, not a variable source.
//   - "If multiple groups have the same variable, the last one loaded wins."
//   - Inventory merging is "more specific overrides more generic": host_vars
//     beat group_vars and child groups beat their parents.
//   - Default config is `hash_behavior=replace`; `merge` replaces only
//     partially (maps are deep-merged).
package main

import (
	"fmt"
	"sort"
)

// precedence holds the 22 levels in ascending priority (index 0 == level 1).
var precedence = []string{
	"Command-line values (not variables)",
	"Role defaults",
	"Inventory file or script group vars",
	"Inventory group_vars/all",
	"Playbook group_vars/all",
	"Inventory group_vars/*",
	"Playbook group_vars/*",
	"Inventory file or script host vars",
	"Inventory host_vars/*",
	"Playbook host_vars/*",
	"Host facts and cached set_facts",
	"Play vars",
	"Play vars_prompt",
	"Play vars_files",
	"Role vars",
	"Block vars",
	"Task vars",
	"include_vars",
	"Registered vars and set_facts",
	"Role (and include_role) params",
	"include params",
	"Extra vars (-e) -- always win",
}

const (
	inventoryBase = 3.0 // == level of "Inventory file or script group vars"
	depthStep     = 0.1 // deeper group / host scope -> more specific
)

func level(name string) float64 {
	for i, n := range precedence {
		if n == name {
			return float64(i + 1)
		}
	}
	panic("unknown precedence level: " + name)
}

// Definition is one `name: value` assignment plus the level it came from.
type Definition struct {
	Level  float64
	Source string
	Value  interface{}
	Seq    int
}

// VarStore collects every definition Ansible would find, then resolves per name.
type VarStore struct {
	HashBehavior string
	defs         map[string][]Definition
	inventory    map[string][]Definition
	seq          int
}

func NewVarStore(hashBehavior string) *VarStore {
	return &VarStore{
		HashBehavior: hashBehavior,
		defs:         map[string][]Definition{},
		inventory:    map[string][]Definition{},
	}
}

// Add records a definition from one of the 22 documented levels.
func (s *VarStore) Add(name, levelName, source string, value interface{}) {
	s.seq++
	s.defs[name] = append(s.defs[name], Definition{level(levelName), source, value, s.seq})
}

// AddInventoryVar records an inventory-layer definition; depth 0 is the `all`
// group, each deeper group / the host scope adds depthStep. Modelling inventory
// layering as a fractional offset keeps the 22 levels intact while letting
// "more specific wins" fall out of the same max() rule, whatever the load order.
func (s *VarStore) AddInventoryVar(name string, depth int, source string, value interface{}) {
	level := inventoryBase + float64(depth)*depthStep
	s.inventory[name] = append(s.inventory[name], Definition{level, source, value, 0})
}

func (s *VarStore) candidates(name string) []Definition {
	out := append([]Definition{}, s.defs[name]...)
	out = append(out, s.inventory[name]...)
	return out
}

// Resolve returns the winning value: highest level, ties to the last write.
func (s *VarStore) Resolve(name string, fallback interface{}) interface{} {
	cands := s.candidates(name)
	if len(cands) == 0 {
		return fallback
	}
	// Prefer no external deps: a stable sort on (level, seq) then take last.
	sort.SliceStable(cands, func(i, j int) bool {
		if cands[i].Level != cands[j].Level {
			return cands[i].Level < cands[j].Level
		}
		return cands[i].Seq < cands[j].Seq
	})
	return cands[len(cands)-1].Value
}

// ResolveWithSource also reports which level produced the winner.
func (s *VarStore) ResolveWithSource(name string) (interface{}, string) {
	cands := s.candidates(name)
	if len(cands) == 0 {
		return nil, "(undefined)"
	}
	sort.SliceStable(cands, func(i, j int) bool {
		if cands[i].Level != cands[j].Level {
			return cands[i].Level < cands[j].Level
		}
		return cands[i].Seq < cands[j].Seq
	})
	w := cands[len(cands)-1]
	return w.Value, fmt.Sprintf("%s (level %g)", w.Source, w.Level)
}

// MergeDict implements hash_behavior: replace takes the high side wholesale;
// merge deep-merges, which is exactly what "overwrite only partially" means.
func (s *VarStore) MergeDict(low, high map[string]interface{}) map[string]interface{} {
	if s.HashBehavior == "replace" {
		return high
	}
	out := map[string]interface{}{}
	for k, v := range low {
		out[k] = v
	}
	for k, v := range high {
		if hv, ok := v.(map[string]interface{}); ok {
			if lv, ok := out[k].(map[string]interface{}); ok {
				out[k] = s.MergeDict(lv, hv)
				continue
			}
		}
		out[k] = v
	}
	return out
}

type invStep struct {
	depth  int
	source string
	name   string
	value  interface{}
}

func inventoryExample() {
	fmt.Println("== inventory layering: group_vars/all -> group_vars/boston -> host_vars ==")
	steps := []invStep{
		{0, "group_vars/all", "ntp_server", "default-time.example.com"},
		{1, "group_vars/boston", "ntp_server", "boston-time.example.com"},
		{2, "host_vars/xyz.boston.example.com", "ntp_server", "override.example.com"},
	}
	for _, keep := range []int{3, 2, 1} {
		store := NewVarStore("replace")
		for _, st := range steps[:keep] {
			store.AddInventoryVar(st.name, st.depth, st.source, st.value)
		}
		val, who := store.ResolveWithSource("ntp_server")
		fmt.Printf("  layers=%d  -> %-28v from %s\n", keep, val, who)
	}

	fmt.Println("\n  depth wins over load order:")
	store := NewVarStore("replace")
	store.AddInventoryVar("ntp_server", 1, "group_vars/boston", "boston-time")
	store.AddInventoryVar("ntp_server", 0, "group_vars/all", "default-time")
	val, _ := store.ResolveWithSource("ntp_server")
	fmt.Println("  reversed load order still yields:", val)
}

func lastLoadedWins() {
	fmt.Println("\n== same level, last write wins ==")
	store := NewVarStore("replace")
	store.Add("http_port", "Play vars", "play #1", 8080)
	store.Add("http_port", "Play vars", "play #1 (redefined)", 9090)
	fmt.Println("  two Play vars entries ->", store.Resolve("http_port", nil), "(second one wins)")
}

func roleVarsVsInventory() {
	fmt.Println("\n== role vars vs inventory vs -e ==")
	for _, extra := range []bool{false, true} {
		store := NewVarStore("replace")
		store.Add("http_port", "Role defaults", "roles/x/defaults", 80)
		store.Add("http_port", "Inventory host_vars/*", "host_vars/a", 8080)
		store.Add("http_port", "Role vars", "roles/x/vars", 80)
		if extra {
			store.Add("http_port", "Extra vars (-e) -- always win", "-e", 1234)
		}
		val, who := store.ResolveWithSource("http_port")
		fmt.Printf("  extra_vars=%-5v -> %-5v from %s\n", extra, val, who)
	}
}

func hashBehavior() {
	fmt.Println("\n== hash_behavior: replace (default) vs merge ==")
	low := map[string]interface{}{
		"nginx": map[string]interface{}{"worker_processes": 2, "keepalive_timeout": 65},
	}
	high := map[string]interface{}{
		"nginx": map[string]interface{}{"worker_processes": 8},
	}
	fmt.Println("  replace ->", NewVarStore("replace").MergeDict(low, high))
	fmt.Println("  merge   ->", NewVarStore("merge").MergeDict(low, high))
}

func levelTable() {
	fmt.Println("== one definition per level (winner is always the highest) ==")
	store := NewVarStore("replace")
	for i, name := range precedence {
		if i == 0 {
			continue // level 1 is a command-line value, not a variable
		}
		store.Add("max_workers", name, name, fmt.Sprintf("v%02d", i+1))
	}
	val, who := store.ResolveWithSource("max_workers")
	fmt.Printf("  22 levels defined -> winner value=%v  <- %s\n", val, who)
	if val != "v22" {
		panic("extra vars must always win")
	}
}

func main() {
	levelTable()
	inventoryExample()
	lastLoadedWins()
	roleVarsVsInventory()
	hashBehavior()
}

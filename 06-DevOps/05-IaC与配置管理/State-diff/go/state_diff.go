// Terraform State diff & Plan 生成 (Go 版)
//
// 来源:
// - developer.hashicorp.com/terraform/tutorials/cli/state-cli
//   Terraform state JSON 文件 version 4 格式:
//   {"version":4, "terraform_version":"1.6.x", "serial":N,
//    "lineage":"uuid", "resources":[
//      {"mode":"managed","type":"aws_instance","name":"web",
//       "provider":"pr",
//       "instances":[{"schema_version":N,
//        "attributes":{ami:..., instance_type:..., id:"i-xxx"}}]}]}
// - developer.hashicorp.com/terraform/tutorials/state/resource-drift
//   "If your state and configuration do not match your infrastructure,
//   Terraform will attempt to reconcile your infrastructure, which may
//   unintentionally destroy or recreate resources"

package main

import (
	"encoding/json"
	"fmt"
	"sort"
)

// -----------------------------------------------------------------------------
// Terraform state v4 schema
// -----------------------------------------------------------------------------

type Resource struct {
	Mode      string `json:"mode"`
	Type      string `json:"type"`
	Name      string `json:"name"`
	Provider  string `json:"provider"`
	Instances []struct {
		SchemaVersion int                    `json:"schema_version"`
		Attributes    map[string]interface{} `json:"attributes"`
	} `json:"instances"`
}

type State struct {
	Version          int        `json:"version"`
	TerraformVersion string     `json:"terraform_version"`
	Serial           int        `json:"serial"`
	Lineage          string     `json:"lineage"`
	Resources        []Resource `json:"resources"`
}

func (r Resource) Addr() string { return r.Type + "." + r.Name }

// -----------------------------------------------------------------------------
// Diff engine
// -----------------------------------------------------------------------------

type Action int

const (
	Create Action = iota
	Update
	Destroy
	Noop
)

func (a Action) Symbol() string {
	switch a {
	case Create:
		return "+ create"
	case Update:
		return "~ update in-place"
	case Destroy:
		return "- destroy"
	}
	return "  noop"
}

func (a Action) Verb() string {
	switch a {
	case Create:
		return "create"
	case Update:
		return "update in-place"
	case Destroy:
		return "destroy"
	}
	return "noop"
}

type Change struct {
	Key string
	Old interface{}
	New interface{}
}

type Diff struct {
	Action  Action
	Addr    string
	Changes []Change
}

func ComputeDiff(desired map[string]map[string]interface{}, st *State) []Diff {
	var diffs []Diff
	seen := map[string]bool{}
	for addr, want := range desired { // 1. walk desired → create/update/noop
		seen[addr] = true
		matched := findResource(st, addr)
		if matched == nil {
			diffs = append(diffs, Diff{Action: Create, Addr: addr})
			continue
		}
		var cs []Change
		cur := matched.Instances[0].Attributes
		for k, w := range want {
			if old, ok := cur[k]; !ok || old != w {
				cs = append(cs, Change{Key: k, Old: old, New: w})
			}
		}
		for k, v := range cur { // 反向:cur 有 desired 没的字段 (drift)
			if _, ok := want[k]; !ok && !isSystemField(k) {
				cs = append(cs, Change{Key: k, Old: v, New: nil})
			}
		}
		act := Noop
		if len(cs) > 0 { act = Update }
		diffs = append(diffs, Diff{Action: act, Addr: addr, Changes: cs})
	}
	for _, r := range st.Resources { // 2. walk state → destroy
		if !seen[r.Addr()] { diffs = append(diffs, Diff{Action: Destroy, Addr: r.Addr()}) }
	}
	order := map[Action]int{Create: 0, Noop: 1, Update: 2, Destroy: 3} // 排序:create → update → destroy
	sort.SliceStable(diffs, func(i, j int) bool {
		if order[diffs[i].Action] != order[diffs[j].Action] {
			return order[diffs[i].Action] < order[diffs[j].Action]
		}
		return diffs[i].Addr < diffs[j].Addr
	})
	return diffs
}

func isSystemField(k string) bool {
	return k == "id" || k == "arn" || k == "tags_all" || k == "self_link"
}

func findResource(st *State, addr string) *Resource {
	for i, r := range st.Resources {
		if r.Addr() == addr {
			return &st.Resources[i]
		}
	}
	return nil
}

func formatDiff(d Diff) string {
	out := d.Action.Symbol() + "        " + d.Addr
	outLines := []string{out}
	for _, c := range d.Changes {
		outLines = append(outLines, fmt.Sprintf("    ~ %-15s %v → %v", c.Key, c.Old, c.New))
	}
	return fmt.Sprintf("%s", joinLines(outLines))
}

func joinLines(ls []string) string {
	out := ""
	for i, l := range ls {
		if i > 0 {
			out += "\n"
		}
		out += l
	}
	return out
}

// -----------------------------------------------------------------------------
// Demo data
// -----------------------------------------------------------------------------

var STATE_EMPTY = State{
	Version: 4, TerraformVersion: "1.6.0", Serial: 0, Lineage: "u1",
	Resources: []Resource{},
}

var STATE_DRIFT = State{
	Version: 4, TerraformVersion: "1.6.0", Serial: 5, Lineage: "u2",
	Resources: []Resource{
		{Mode: "managed", Type: "aws_instance", Name: "web", Provider: "pr",
			Instances: []struct {
				SchemaVersion int                    `json:"schema_version"`
				Attributes    map[string]interface{} `json:"attributes"`
			}{{
				SchemaVersion: 1,
				Attributes: map[string]interface{}{
					"ami":            "ami-x",
					"instance_type":  "t2.micro",
					"subnet_id":      "subnet-1",
					"id":             "i-existing",
				},
			}},
		},
		{Mode: "managed", Type: "aws_vpc", Name: "main", Provider: "pr",
			Instances: []struct {
				SchemaVersion int                    `json:"schema_version"`
				Attributes    map[string]interface{} `json:"attributes"`
			}{{
				SchemaVersion: 0,
				Attributes: map[string]interface{}{
					"cidr_block": "10.0.0.0/16",
					"id":         "vpc-existing",
				},
			}},
		},
	},
}

// -----------------------------------------------------------------------------
// Demo functions
// -----------------------------------------------------------------------------

func demoCreateOnly() {
	fmt.Println("--- Demo 1: 全新初始化 (全部 create) ---")
	desired := map[string]map[string]interface{}{
		"aws_vpc.main":       {"cidr_block": "10.0.0.0/16", "tags": map[string]interface{}{"Name": "main-vpc"}},
		"aws_subnet.public":  {"vpc_id": "vpc-1234", "cidr_block": "10.0.1.0/24", "availability_zone": "us-east-1a"},
		"aws_instance.web":   {"ami": "ami-0c55b159cbfafe1f0", "instance_type": "t3.micro", "subnet_id": "subnet-1234"},
	}
	diffs := ComputeDiff(desired, &STATE_EMPTY)
	for _, d := range diffs {
		if d.Action != Noop {
			fmt.Printf("%s       %s\n", d.Action.Symbol(), d.Addr)
		}
	}
	fmt.Println()
}

func demoDrift() {
	fmt.Println("--- Demo 2: drift (instance_type 漂移) ---")
	desired := map[string]map[string]interface{}{
		"aws_vpc.main":      {"cidr_block": "10.0.0.0/16"},
		"aws_instance.web":  {"ami": "ami-x", "instance_type": "t3.micro", "subnet_id": "subnet-1"},
	}
	diffs := ComputeDiff(desired, &STATE_DRIFT)
	for _, d := range diffs {
		if d.Action == Noop {
			continue
		}
		fmt.Printf("%s  %s\n", d.Action.Symbol(), d.Addr)
		for _, c := range d.Changes {
			fmt.Printf("    %s %-15s %v → %v\n", "~", c.Key, c.Old, c.New)
		}
	}
	fmt.Println()
}

func demoDestroy() {
	fmt.Println("--- Demo 3: 部分删除 (instance 应 destroy) ---")
	desired := map[string]map[string]interface{}{
		"aws_vpc.main": {"cidr_block": "10.0.0.0/16"},
	}
	diffs := ComputeDiff(desired, &STATE_DRIFT)
	for _, d := range diffs {
		if d.Action == Noop {
			continue
		}
		fmt.Printf("%s  %s\n", d.Action.Symbol(), d.Addr)
	}
	fmt.Println()
}

func demoNoop() {
	fmt.Println("--- Demo 4: 完全一致 (noop, 0 changes) ---")
	desired := map[string]map[string]interface{}{
		"aws_instance.web": {"ami": "ami-x", "instance_type": "t2.micro", "subnet_id": "subnet-1"},
		"aws_vpc.main":     {"cidr_block": "10.0.0.0/16"},
	}
	diffs := ComputeDiff(desired, &STATE_DRIFT)
	for _, d := range diffs {
		if d.Action != Noop {
			fmt.Printf("%s  %s\n", d.Action.Symbol(), d.Addr)
		}
	}
	fmt.Println("(all resources match desired state, 0 changes)")
	fmt.Println()
}

func main() {
	fmt.Println("=== Terraform State diff & Plan demo (Go 版) ===\n")
	if STATE_EMPTY.Version != 4 {
		fmt.Println("demo data error: state version")
		return
	}
	demoCreateOnly()
	demoDrift()
	demoDestroy()
	demoNoop()

	_ = json.RawMessage{}
}

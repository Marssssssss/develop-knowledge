// Demo driver: preview vs up on the miniature Pulumi engine in types.go +
// engine.go.
//
// Ref read: "How Pulumi works"
// https://www.pulumi.com/docs/iac/concepts/how-pulumi-works/ -- the scenarios
// below mirror its walkthrough: two buckets where one consumes the other's
// output, a rename, an in-place tag change, and a replacement; plus the
// documented duplicate-URN error from "Resource names and identity".
package main

import (
	"fmt"
	"strings"
)

func leaf(urn string) string {
	p := strings.Split(urn, "::")
	return p[len(p)-1]
}

// bucketStack models the docs' example: content-bucket consumes media-bucket's id.
func bucketStack(e *Engine, contentName string) {
	media := e.Register(bucketType, "media-bucket", map[string]interface{}{}, nil)
	arn := media.Out("id").Derive(func(v string) string { return v + ":arn" })
	e.Register(bucketType, contentName, map[string]interface{}{"tags": arn}, nil)
}

func main() {
	p := Provider{ReplaceOn: map[string]map[string]bool{
		bucketType: {"bucket": true, "name": true}}}
	run := func(title string, e *Engine) {
		ops := e.Plan()
		named := map[string]string{}
		for u, op := range ops {
			named[leaf(u)] = op
		}
		fmt.Printf("== %s ==\n   ops: %v\n", title, named)
		for _, line := range e.Apply(ops) {
			fmt.Println(line)
		}
		fmt.Println()
	}

	fmt.Println("== URN grammar (project acmecorp-website, stack production) ==")
	fmt.Println("  ", URN("production", "acmecorp-website", bucketType, "my-bucket", ""))
	fmt.Println("  ", URN("production", "acmecorp-website", bucketType, "my-bucket",
		"custom:resources:Resource"))
	fmt.Println()

	engine := NewEngine("acmecorp-website", "production", p, false)
	bucketStack(engine, "content-bucket")
	run("[1] preview on an empty stack: both buckets are `+`, ids unknown", engine)

	fmt.Println("== unknown propagation + dependency edge ==")
	fmt.Printf("   content-bucket.tags <- media-bucket.id.Derive(..)  known=%v\n",
		engine.Reg[engine.Order[1]].Inputs["tags"].(Output).Known)
	for i, w := range engine.Waves() {
		names := []string{}
		for _, u := range w {
			names = append(names, leaf(u))
		}
		fmt.Printf("   wave %d: %v\n", i+1, names)
	}
	fmt.Println()

	engine.Live = true
	run("[2] up: auto-naming appends a random suffix to physical names", engine)
	for _, v := range engine.State {
		fmt.Printf("   %v -> physical %v\n", v["name"], v["physical_name"])
	}
	fmt.Println()

	base := map[string]map[string]interface{}{}
	for k, v := range engine.State {
		base[k] = v
	}
	fork := func(live bool) *Engine {
		f := NewEngine("acmecorp-website", "production", p, live)
		for k, v := range base {
			f.State[k] = v
		}
		return f
	}

	renamed := fork(false)
	bucketStack(renamed, "app-bucket")
	run("[3] rename content-bucket -> app-bucket: one create + one delete", renamed)

	updated := fork(false)
	updated.Register(bucketType, "media-bucket", map[string]interface{}{}, nil)
	updated.Register(bucketType, "content-bucket",
		map[string]interface{}{"tags": "owner=infra"}, nil)
	run("[4] only `tags` changed: the provider updates it in place", updated)

	replaced := fork(false)
	replaced.Register(bucketType, "media-bucket",
		map[string]interface{}{"bucket": "fixed-2"}, nil)
	run("[5] `bucket` changed and cannot be patched -> replace", replaced)
	fmt.Println("   default order is create-replacement then delete-replaced;")
	fmt.Println("   `deleteBeforeReplace` (implied when auto-naming is off) swaps them.")
	fmt.Println()

	fmt.Println("== [6] duplicate URN is rejected ==")
	replaced.Register(bucketType, "media-bucket", nil, nil)
}

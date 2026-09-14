// Core Pulumi object model: URNs, Outputs, Resources, Providers.
//
// Ref read: Pulumi "Resource names and identity"
// https://www.pulumi.com/docs/iac/concepts/resources/names/ and the URN EBNF
// grammar in "Type system"
// https://pulumi-developer-docs.readthedocs.io/latest/docs/architecture/types/README.html
//
// The documented grammar, implemented in URN() below:
//
//	urn = "urn:pulumi:" stack "::" project "::" qualified-type "::" name
//	qualified-type = [ parent-type "$" ] type
//	type = package ":" [ module ":" ] type-name
//
// Also documented: the URN is built from the project name, stack name, resource
// name, resource type, and the types of all parent resources; it must be
// globally unique, so two resources with the same name, type and parent path
// produce `error: Duplicate resource URN '...'`.
package main

import "fmt"

const (
	unknown    = "<unknown>"
	bucketType = "aws:s3/bucket:Bucket"
)

// URN builds a URN per the documented grammar.
func URN(stack, project, baseType, name, parentType string) string {
	qualified := baseType
	if parentType != "" {
		qualified = parentType + "$" + baseType
	}
	return fmt.Sprintf("urn:pulumi:%s::%s::%s::%s", stack, project, qualified, name)
}

// Output is a node in the program graph. Besides the value it carries the
// resources it depends on and whether the value is known or unknown: during
// preview a value produced by a to-be-created resource is unknown.
type Output struct {
	Known bool
	Value string
	Deps  map[string]bool
}

// Derive keeps an unknown output unknown, whatever the computation does.
func (o Output) Derive(fn func(string) string) Output {
	if !o.Known {
		return Output{Known: false, Value: unknown, Deps: o.Deps}
	}
	return Output{Known: true, Value: fn(o.Value), Deps: o.Deps}
}

// Resource is one registered resource. Out() reads an output property, which
// is what makes the consuming resource depend on this one.
type Resource struct {
	Type, Name, URN, Parent string
	Inputs                  map[string]interface{}
	Outputs                 map[string]Output
}

func (r *Resource) Out(key string) Output {
	if o, ok := r.Outputs[key]; ok {
		return o
	}
	o := Output{Known: true, Deps: map[string]bool{r.URN: true}}
	r.Outputs[key] = o
	return o
}

// Provider is a stand-in for a resource plugin. In real Pulumi the engine asks
// the plugin to diff old vs desired state; here the plugin just declares which
// input properties cannot be patched in place and therefore force a replace.
type Provider struct{ ReplaceOn map[string]map[string]bool }

func (p Provider) ForcesReplace(t string, changed map[string]bool) bool {
	for k := range changed {
		if p.ReplaceOn[t][k] {
			return true
		}
	}
	return false
}

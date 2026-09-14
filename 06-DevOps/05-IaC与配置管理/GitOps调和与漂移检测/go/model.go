// GitOps data model: the Git source of truth, the cluster, SSA field ownership.
//
// Refs read: OpenGitOps Principles v1.0.0 https://opengitops.dev/
//            Argo CD "Automated Sync Policy" https://argo-cd.readthedocs.io/en/stable/user-guide/auto_sync/
//            Argo CD "Sync Options" https://argo-cd.readthedocs.io/en/stable/user-guide/sync-options/
//            Kubernetes "Server-Side Apply" https://kubernetes.io/docs/reference/using-api/server-side-apply/
//
// Modelled facts: four OpenGitOps principles (Declarative / Versioned and
// Immutable / Pulled Automatically / Continuously Reconciled); pruning is OFF
// by default ("automated sync will not delete resources when Argo CD detects
// the resource is no longer defined in Git") and `allowEmpty` guards against an
// empty target; the reconcile interval is timeout.reconciliation (default
// 120s) plus up to 60s of jitter, and the self-heal timeout defaults to 5s;
// resources are tracked by the argocd.argoproj.io/tracking-id annotation; SSA
// keeps managedFields per field manager -- equal values share ownership, a
// different value owned by someone else is a conflict, `force` takes the field
// from every other manager, and dropping a field from your manifest releases it.
package main

import (
	"errors"
	"fmt"
	"sort"
)

const tracking = "argocd.argoproj.io/tracking-id"

var errConflict = errors.New("apply conflict")

// Object is one live API object plus its per-manager field ownership.
type Object struct {
	Kind    string
	Name    string
	Fields  map[string]interface{}
	Managed map[string]map[string]bool
}

func (o *Object) Key() string { return o.Kind + "/" + o.Name }

// Cluster is a deliberately tiny API server: SSA merge + managedFields.
type Cluster struct {
	Objects map[string]*Object
}

func NewCluster() *Cluster { return &Cluster{Objects: map[string]*Object{}} }

func (c *Cluster) GetOrCreate(kind, name string) *Object {
	key := kind + "/" + name
	if o, ok := c.Objects[key]; ok {
		return o
	}
	o := &Object{Kind: kind, Name: name, Fields: map[string]interface{}{},
		Managed: map[string]map[string]bool{}}
	c.Objects[key] = o
	return o
}

func (c *Cluster) FieldsOf(key string) map[string]interface{} {
	if o, ok := c.Objects[key]; ok {
		return o.Fields
	}
	return map[string]interface{}{}
}

// Apply is Server-Side Apply with a fully specified intent. Conflicts are
// computed before anything is written, so a rejected apply changes nothing.
func (c *Cluster) Apply(kind, name string, fields map[string]interface{},
	manager string, force bool) (*Object, error) {
	o := c.GetOrCreate(kind, name)
	if !force {
		for path, value := range fields {
			for other, owned := range o.Managed {
				if other != manager && owned[path] && o.Fields[path] != value {
					return nil, fmt.Errorf("%w on %s: %s owned by %s",
						errConflict, o.Key(), path, other)
				}
			}
		}
	}
	if force { // force strips the field from every other manager
		for other, owned := range o.Managed {
			if other == manager {
				continue
			}
			for path := range fields {
				delete(owned, path)
			}
		}
	}
	for path, value := range fields {
		o.Fields[path] = value
	}
	if o.Managed[manager] == nil {
		o.Managed[manager] = map[string]bool{}
	}
	for path := range fields {
		o.Managed[manager][path] = true
	}
	// A field you no longer specify is released, and disappears from the live
	// object once nobody else owns it.
	for path := range o.Managed[manager] {
		if _, declared := fields[path]; declared {
			continue
		}
		delete(o.Managed[manager], path)
		stillOwned := false
		for _, owned := range o.Managed {
			stillOwned = stillOwned || owned[path]
		}
		if !stillOwned {
			delete(o.Fields, path)
		}
	}
	return o, nil
}

func (c *Cluster) Delete(key string) { delete(c.Objects, key) }

func (c *Cluster) Keys() []string {
	out := []string{}
	for k := range c.Objects {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// GitRepo is the single source of truth: versioned, immutable, append-only.
type GitRepo struct{ Revisions []Revision }

type Revision struct {
	SHA       string
	Manifests map[string]map[string]interface{}
}

func (r *GitRepo) Commit(sha string, manifests map[string]map[string]interface{}) string {
	r.Revisions = append(r.Revisions, Revision{SHA: sha, Manifests: manifests})
	return sha
}

func (r *GitRepo) SHA() string { return r.Revisions[len(r.Revisions)-1].SHA }

func (r *GitRepo) Manifests() map[string]map[string]interface{} {
	return r.Revisions[len(r.Revisions)-1].Manifests
}

// SyncPolicy mirrors the documented Argo CD knobs and defaults.
type SyncPolicy struct {
	Automated       bool
	Prune           bool
	SelfHeal        bool
	AllowEmpty      bool
	Reconciliation  int // argocd-cm timeout.reconciliation, default 120
	Jitter          int // "maximum period of 3 minutes" -> up to 60s
	SelfHealTimeout int // --self-heal-timeout-seconds, default 5
}

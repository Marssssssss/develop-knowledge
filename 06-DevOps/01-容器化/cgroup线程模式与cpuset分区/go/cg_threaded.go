// cgroup v2 线程模式与 cpuset 分区（Python 模型的 Go 复刻）。
//
// 对照 torvalds/linux：
//
//	Documentation/admin-guide/cgroup-v2.rst  §Threaded 模式 与 §CPUSET partition
//	Documentation/admin-guide/cgroup-v1/cpusets.rst
package main

import "fmt"

const (
	domain        = "domain"
	domainThread  = "domain threaded"
	threaded      = "threaded"
	domainInvalid = "domain (invalid)"

	member   = "member"
	partRoot = "root"
	isolated = "isolated"
)

var threadedControllers = map[string]bool{
	"cpu": true, "cpuset": true, "perf_event": true, "pids": true,
}

type CgroupError struct {
	Errno string
	Why   string
}

func (e *CgroupError) Error() string { return e.Errno + ": " + e.Why }

type Cgroup struct {
	Name           string
	Parent         *Cgroup
	Children       []*Cgroup
	Threaded       bool
	SubtreeControl map[string]bool
	Procs          map[int]bool
	Threads        map[int]bool
	Partition      string
	Cpus           map[int]bool
	CpusExclusive  map[int]bool // nil 表示未显式设置
	AllCpus        map[int]bool
	CreatedLocal   bool
}

func NewCgroup(name string, parent *Cgroup, cpus []int, all []int) *Cgroup {
	c := &Cgroup{Name: name, Parent: parent, Partition: member,
		SubtreeControl: map[string]bool{}, Procs: map[int]bool{},
		Threads: map[int]bool{}, Cpus: map[int]bool{}}
	if parent != nil {
		c.AllCpus = parent.AllCpus
		parent.Children = append(parent.Children, c)
		c.Partition = member
	} else {
		c.AllCpus = map[int]bool{}
		for _, i := range all {
			c.AllCpus[i] = true
		}
	}
	if cpus == nil && parent != nil {
		for i := range c.AllCpus {
			c.Cpus[i] = true
		}
	} else {
		for _, i := range cpus {
			c.Cpus[i] = true
		}
	}
	return c
}

func (c *Cgroup) IsRoot() bool  { return c.Parent == nil }
func (c *Cgroup) Populated() bool {
	return len(c.Procs) > 0 || len(c.Threads) > 0
}

func (c *Cgroup) DomainChildren() []*Cgroup {
	var out []*Cgroup
	for _, x := range c.Children {
		if !x.Threaded {
			out = append(out, x)
		}
	}
	return out
}

// ThreadRoot 是最近的未线程化祖先。
func (c *Cgroup) ThreadRoot() *Cgroup {
	n := c
	for n != nil && n.Threaded {
		n = n.Parent
	}
	return n
}

// TypeStr 对应 cgroup.type 的读值。
func (c *Cgroup) TypeStr() string {
	if c.Threaded {
		return threaded
	}
	if c.IsRoot() {
		for _, x := range c.Children {
			if x.Threaded {
				return domainThread
			}
		}
		return domain
	}
	p := c.Parent
	if p.Threaded || (!p.IsRoot() && p.TypeStr() == domainThread) {
		return domainInvalid
	}
	for _, x := range c.Children {
		if x.Threaded {
			return domainThread
		}
	}
	for k := range c.SubtreeControl {
		if threadedControllers[k] && c.Populated() {
			return domainThread
		}
	}
	return domain
}

// MakeThreaded 对应 echo threaded > cgroup.type。
func (c *Cgroup) MakeThreaded() error {
	if c.Threaded {
		return &CgroupError{"EOPNOTSUPP", "already threaded"}
	}
	if c.IsRoot() {
		return &CgroupError{"EOPNOTSUPP", "root can not be threaded"}
	}
	p := c.Parent
	if p.TypeStr() == domain && !p.IsRoot() {
		for k := range p.SubtreeControl {
			if !threadedControllers[k] {
				return &CgroupError{"EOPNOTSUPP",
					"parent is an unthreaded domain with domain controllers enabled"}
			}
		}
		for _, k := range p.DomainChildren() {
			if k.Populated() {
				return &CgroupError{"EOPNOTSUPP",
					"parent has populated domain children"}
			}
		}
	}
	c.Threaded = true
	return nil
}

// Enable 只有 threaded 控制器能开在 threaded 子树里。
func (c *Cgroup) Enable(ctrl string) error {
	tr := c.ThreadRoot()
	inSubtree := c.Threaded || c.TypeStr() == domainThread ||
		(tr != nil && tr.Threaded)
	if inSubtree && !threadedControllers[ctrl] {
		return &CgroupError{"EOPNOTSUPP", ctrl + " is not a threaded controller"}
	}
	c.SubtreeControl[ctrl] = true
	return nil
}

func intersect(a, b map[int]bool) map[int]bool {
	out := map[int]bool{}
	for i := range a {
		if b[i] {
			out[i] = true
		}
	}
	return out
}

func toSet(xs []int) map[int]bool {
	out := map[int]bool{}
	for _, x := range xs {
		out[x] = true
	}
	return out
}

func (c *Cgroup) CpusEffective() map[int]bool {
	if c.Parent == nil {
		return intersect(c.Cpus, c.AllCpus)
	}
	return intersect(c.Cpus, c.Parent.CpusEffective())
}

func (c *Cgroup) RequestedExclusive() map[int]bool {
	if c.CpusExclusive != nil {
		return c.CpusExclusive
	}
	return c.Cpus
}

// CpusExclusiveEffective 只能用父的 effective 里存在的，且不能与兄弟抢。
func (c *Cgroup) CpusExclusiveEffective() map[int]bool {
	if c.Parent == nil {
		return c.AllCpus
	}
	taken := map[int]bool{}
	for _, s := range c.Parent.Children {
		if s != c {
			for i := range s.RequestedExclusive() {
				taken[i] = true
			}
		}
	}
	out := map[int]bool{}
	for i := range c.Parent.CpusExclusiveEffective() {
		if c.RequestedExclusive()[i] && !taken[i] {
			out[i] = true
		}
	}
	return out
}

func (c *Cgroup) IsPartitionRoot() bool {
	return c.IsRoot() || c.Partition == partRoot || c.Partition == isolated
}

func (c *Cgroup) AncestorHasPartitionRoot() bool {
	n := c.Parent
	for n != nil && !n.IsRoot() {
		if n.IsPartitionRoot() {
			return true
		}
		n = n.Parent
	}
	return false
}

// PartitionInvalidReason 返回失效原因（文档的三条判据）。
func (c *Cgroup) PartitionInvalidReason() string {
	if !c.IsPartitionRoot() || c.IsRoot() {
		return ""
	}
	local := c.Parent.IsPartitionRoot() && c.Parent.PartitionInvalidReason() == ""
	if c.CreatedLocal {
		if !local {
			return "parent is not a valid partition root"
		}
	} else if c.AncestorHasPartitionRoot() {
		return "remote partition under a partition root"
	}
	if len(c.CpusExclusiveEffective()) == 0 {
		return "cpuset.cpus.exclusive.effective is empty"
	}
	if len(c.CpusEffective()) == 0 && c.Populated() {
		return "cpuset.cpus.effective is empty with tasks"
	}
	return ""
}

// ReadPartition 对应读 cpuset.cpus.partition。
func (c *Cgroup) ReadPartition() string {
	if !c.IsPartitionRoot() {
		return member
	}
	if why := c.PartitionInvalidReason(); why != "" {
		return fmt.Sprintf("%s invalid (%s)", c.Partition, why)
	}
	return c.Partition
}

func (c *Cgroup) SetPartition(v string) error {
	if c.IsRoot() {
		return &CgroupError{"EOPNOTSUPP",
			"root cgroup's partition state cannot be changed"}
	}
	if v != member && v != partRoot && v != isolated {
		return &CgroupError{"EINVAL", "invalid partition value " + v}
	}
	c.Partition = v
	if v == partRoot || v == isolated {
		c.CreatedLocal = c.Parent.IsPartitionRoot() &&
			c.Parent.PartitionInvalidReason() == ""
	} else {
		c.CreatedLocal = false
	}
	return nil
}

func keys(m map[int]bool) []int {
	var out []int
	for i := range m {
		out = append(out, i)
	}
	for i := 0; i < len(out); i++ {
		for j := i + 1; j < len(out); j++ {
			if out[j] < out[i] {
				out[i], out[j] = out[j], out[i]
			}
		}
	}
	return out
}

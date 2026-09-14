// Two-Phase Commit (2PC) — minimal Go implementation.
//
// Coordinator with state machine INIT → WAITING → DECIDED, plus recovery
// from durable log.  Participants have PREPARED / COMMITTED / ABORTED /
// IN_DOUBT.  Mirrors twopc.py.
//
// Refs:
//   - Gray 1978 "Notes on Database Operating Systems"
//   - X/Open XA specification
//   - Designing Data-Intensive Applications Ch. 9
//   - https://en.wikipedia.org/wiki/Two-phase_commit_protocol

package main

import "fmt"

type CoordState int

const (
	CoordInit CoordState = iota
	CoordWaiting
	CoordDecided
	CoordCrashed
)

func (s CoordState) String() string {
	return [...]string{"INIT", "WAITING", "DECIDED", "CRASHED"}[s]
}

type PartState int

const (
	PartInit PartState = iota
	PartPrepared
	PartCommitted
	PartAborted
	PartInDoubt
)

func (s PartState) String() string {
	return [...]string{"INIT", "PREPARED", "COMMITTED", "ABORTED", "IN_DOUBT"}[s]
}

type Vote int

const (
	Yes Vote = iota
	No
)

type Participant struct {
	Name       string
	State      PartState
	Vote       Vote
	HeldLocks  []string
	CanYes     bool
	Decision   string
	Log        []string
}

func (p *Participant) Prepare(txn string) Vote {
	p.Log = append(p.Log, fmt.Sprintf("[%s] PREPARE %s", p.Name, txn))
	p.HeldLocks = []string{"row:" + txn}
	if !p.CanYes {
		p.Vote = No
		p.State = PartAborted
		p.HeldLocks = nil
		p.Log = append(p.Log, fmt.Sprintf("[%s] vote=NO", p.Name))
		return No
	}
	p.Vote = Yes
	p.State = PartPrepared
	p.Log = append(p.Log, fmt.Sprintf("[%s] vote=YES", p.Name))
	return Yes
}

func (p *Participant) Commit(txn string) {
	p.Log = append(p.Log, fmt.Sprintf("[%s] COMMIT %s", p.Name, txn))
	p.Decision = "COMMIT"
	p.State = PartCommitted
	p.HeldLocks = nil
}

func (p *Participant) Abort(txn string) {
	p.Log = append(p.Log, fmt.Sprintf("[%s] ABORT %s", p.Name, txn))
	p.Decision = "ABORT"
	p.State = PartAborted
	p.HeldLocks = nil
}

type Coordinator struct {
	Name              string
	State             CoordState
	Decision          string
	Log                []string
	CrashAfterDecision bool
}

func (c *Coordinator) Prepare(parts []*Participant, txn string) bool {
	c.Log = append(c.Log, fmt.Sprintf("[%s] BEGIN TXN %s", c.Name, txn))
	c.State = CoordWaiting
	allYes := true
	for _, p := range parts {
		if p.Prepare(txn) == No {
			allYes = false
		}
	}
	if !allYes {
		for _, p := range parts {
			if p.Vote == Yes {
				p.Abort(txn)
			}
		}
		c.Decision = "ABORT"
		c.State = CoordDecided
		return false
	}
	return true
}

func (c *Coordinator) Decide(parts []*Participant, txn string, ok bool) {
	decision := "ABORT"
	if ok {
		decision = "COMMIT"
	}
	c.Decision = decision
	c.Log = append(c.Log, fmt.Sprintf("[%s] fsync decision=%s", c.Name, decision))
	if c.CrashAfterDecision {
		c.State = CoordCrashed
		c.Log = append(c.Log, fmt.Sprintf("[%s] CRASHED before broadcast", c.Name))
		return
	}
	for _, p := range parts {
		if p.State == PartPrepared {
			if decision == "COMMIT" {
				p.Commit(txn)
			} else {
				p.Abort(txn)
			}
		}
	}
	c.State = CoordDecided
}

func (c *Coordinator) Recover(parts []*Participant, txn string) {
	c.Log = append(c.Log, fmt.Sprintf("[%s] RECOVERY replay", c.Name))
	switch c.Decision {
	case "COMMIT":
		for _, p := range parts {
			if p.State == PartPrepared {
				p.Commit(txn)
			}
		}
	case "ABORT":
		for _, p := range parts {
			if p.State == PartPrepared {
				p.Abort(txn)
			}
		}
	default:
		for _, p := range parts {
			if p.State == PartPrepared {
				p.Abort(txn)
			}
		}
	}
	c.State = CoordDecided
}

func printStates(label string, parts []*Participant, c *Coordinator) {
	fmt.Println("  ---", label, "---")
	for _, p := range parts {
		fmt.Printf("  %s: state=%s locks=%v\n", p.Name, p.State, p.HeldLocks)
	}
	fmt.Printf("  coordinator: state=%s decision=%s\n", c.State, c.Decision)
}

func main() {
	// scenario 1: happy
	fmt.Println("=== scenario 1: happy path ===")
	parts := []*Participant{
		{Name: "pg", CanYes: true},
		{Name: "mysql", CanYes: true},
		{Name: "mq", CanYes: true},
	}
	c := &Coordinator{Name: "TM"}
	ok := c.Prepare(parts, "T1")
	c.Decide(parts, "T1", ok)
	printStates("after commit", parts, c)

	// scenario 2: NO vote
	fmt.Println("\n=== scenario 2: one NO vote → ABORT ===")
	parts2 := []*Participant{
		{Name: "pg", CanYes: true},
		{Name: "mysql", CanYes: false},
		{Name: "mq", CanYes: true},
	}
	c2 := &Coordinator{Name: "TM"}
	ok = c2.Prepare(parts2, "T2")
	c2DecideOK := ok
	c2.Decide(parts2, "T2", c2DecideOK)
	printStates("after abort", parts2, c2)

	// scenario 3: coordinator crash after decision → blocking
	fmt.Println("\n=== scenario 3: coordinator crash after fsync ===")
	parts3 := []*Participant{
		{Name: "pg", CanYes: true},
		{Name: "mysql", CanYes: true},
	}
	c3 := &Coordinator{Name: "TM", CrashAfterDecision: true}
	ok = c3.Prepare(parts3, "T3")
	c3.Decide(parts3, "T3", ok)
	printStates("before recovery (in-doubt)", parts3, c3)
	c3.Recover(parts3, "T3")
	printStates("after recovery", parts3, c3)
}
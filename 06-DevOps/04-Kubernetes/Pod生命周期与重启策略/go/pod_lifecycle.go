// pod_lifecycle.go — Pod 生命周期状态机与重启策略模拟
//
// 权威来源:
//   - kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle (Pod phase +
//     restartPolicy + exponential backoff)
//
// 实施要点:
//   1. Pod phase 五态: Pending / Running / Succeeded / Failed / Unknown
//   2. restartPolicy: Always(默认) / OnFailure / Never
//   3. 退出码 0 → 成功;非 0 → 失败
//      退出码 | Always | OnFailure | Never
//      0      | 重启   | 不重启    | 不重启
//      非 0   | 重启   | 重启      | 不重启
//   4. 指数退避: 10s, 20s, 40s, 80s, 160s, 300s(上限)
//      容器连续正常运行 ≥ 10 min 重置退避
//   5. Sidecar 容器忽略 Pod-level policy,总是按 container-level Always
package main

import "fmt"

const (
	backoffResetSeconds = 600 // 10 min
)

var backoffSteps = []int{10, 20, 40, 80, 160, 300}

type Phase int

const (
	PhasePending Phase = iota
	PhaseRunning
	PhaseSucceeded
	PhaseFailed
	PhaseUnknown
)

func (p Phase) String() string {
	switch p {
	case PhasePending:
		return "Pending"
	case PhaseRunning:
		return "Running"
	case PhaseSucceeded:
		return "Succeeded"
	case PhaseFailed:
		return "Failed"
	case PhaseUnknown:
		return "Unknown"
	}
	return "?"
}

type Policy string

const (
	PolicyAlways    Policy = "Always"
	PolicyOnFailure Policy = "OnFailure"
	PolicyNever     Policy = "Never"
)

type Kind string

const (
	KindMain    Kind = "main"
	KindSidecar Kind = "sidecar"
)

type CtrState string

const (
	CtrWaiting    CtrState = "Waiting"
	CtrRunning    CtrState = "Running"
	CtrTerminated CtrState = "Terminated"
)

type Container struct {
	ID               int
	Kind             Kind
	State            CtrState
	RestartCount     int
	BackoffRemaining int
	RunningSinceTick int
	LastExitCode     int
}

type Pod struct {
	Name       string
	PodPolicy  Policy
	Containers []Container
	Tick       int
	Phase      Phase
}

func backoffFor(restartCount int) int {
	if restartCount <= 0 {
		return 0
	}
	idx := restartCount - 1
	if idx >= len(backoffSteps) {
		idx = len(backoffSteps) - 1
	}
	return backoffSteps[idx]
}

func shouldRestart(c *Container, podPolicy Policy, exitCode int) bool {
	effective := podPolicy
	if c.Kind == KindSidecar {
		effective = PolicyAlways
	}
	switch effective {
	case PolicyAlways:
		return true
	case PolicyOnFailure:
		return exitCode != 0
	}
	return false
}

func computePhase(p *Pod) Phase {
	mains := 0
	running := 0
	succeeded := 0
	failed := 0
	for _, c := range p.Containers {
		if c.Kind != KindMain {
			continue
		}
		mains++
		switch c.State {
		case CtrRunning:
			running++
		case CtrTerminated:
			if c.LastExitCode == 0 {
				succeeded++
			} else {
				failed++
			}
		}
	}
	if running > 0 {
		return PhaseRunning
	}
	if failed > 0 {
		return PhaseFailed
	}
	if mains > 0 && succeeded == mains {
		return PhaseSucceeded
	}
	return PhasePending
}

func (p *Pod) Tick() {
	p.Tick++
	for i := range p.Containers {
		c := &p.Containers[i]
		if c.State == CtrTerminated && c.BackoffRemaining > 0 {
			c.BackoffRemaining--
			if c.BackoffRemaining == 0 {
				c.State = CtrRunning
				c.RunningSinceTick = p.Tick
				fmt.Printf("[t=%ds] %s/%s: backoff elapsed, restart #%d → Running\n",
					p.Tick, p.Name, c.Kind, c.RestartCount)
			}
			continue
		}
		if c.State == CtrRunning && c.RestartCount > 0 &&
			(p.Tick-c.RunningSinceTick) >= backoffResetSeconds {
			fmt.Printf("[t=%ds] %s/%s: ran ≥%ds, backoff timer reset\n",
				p.Tick, p.Name, c.Kind, backoffResetSeconds)
		}
	}
	p.Phase = computePhase(p)
}

func (p *Pod) ContainerExit(cid, exitCode int) {
	c := &p.Containers[cid]
	c.State = CtrTerminated
	c.LastExitCode = exitCode
	effective := p.PodPolicy
	if c.Kind == KindSidecar {
		effective = PolicyAlways
	}
	if shouldRestart(c, p.PodPolicy, exitCode) {
		c.RestartCount++
		c.BackoffRemaining = backoffFor(c.RestartCount)
		fmt.Printf("[t=%ds] %s/%s: exit(%d), policy=%s → restart #%d in %ds\n",
			p.Tick, p.Name, c.Kind, exitCode, effective,
			c.RestartCount, c.BackoffRemaining)
	} else {
		c.BackoffRemaining = 0
		fmt.Printf("[t=%ds] %s/%s: exit(%d), policy=%s → stay Terminated\n",
			p.Tick, p.Name, c.Kind, exitCode, effective)
	}
	p.Phase = computePhase(p)
}

// Demo 1
func demo1BasicStateMachine() {
	fmt.Println("\n========== Demo 1: Basic state machine (Pending → Running → Succeeded) ==========")
	p := &Pod{
		Name:      "demo1-pod",
		PodPolicy: PolicyAlways,
		Containers: []Container{
			{ID: 0, Kind: KindMain, State: CtrWaiting},
		},
		Phase: PhasePending,
	}
	fmt.Printf("Initial: phase=%s, ctr.state=%s\n", p.Phase, p.Containers[0].State)
	p.Containers[0].State = CtrRunning
	p.Containers[0].RunningSinceTick = 0
	p.Tick = 0
	p.Phase = computePhase(p)
	fmt.Printf("After scheduling: phase=%s\n", p.Phase)
	for i := 0; i < 30; i++ {
		p.Tick()
	}
	fmt.Printf("Running for 30s: phase=%s\n", p.Phase)
	p.ContainerExit(0, 0)
	fmt.Printf("After exit 0: phase=%s, ctr.state=%s\n", p.Phase, p.Containers[0].State)
	p.Tick()
	fmt.Printf("After 1s tick (Always policy, exit 0): phase=%s, restart_count=%d\n",
		p.Phase, p.Containers[0].RestartCount)
}

// Demo 2
func demo2PolicyComparison() {
	fmt.Println("\n========== Demo 2: restartPolicy comparison (exit code 0 vs 1) ==========")
	policies := []Policy{PolicyAlways, PolicyOnFailure, PolicyNever}
	for _, pol := range policies {
		for _, ec := range []int{0, 1} {
			p := &Pod{
				Name:      fmt.Sprintf("demo2-%s", pol),
				PodPolicy: pol,
				Containers: []Container{
					{ID: 0, Kind: KindMain, State: CtrRunning, RunningSinceTick: 0},
				},
				Phase: PhaseRunning,
			}
			p.ContainerExit(0, ec)
			c := p.Containers[0]
			restart := "no"
			if c.RestartCount > 0 {
				restart = "yes"
			}
			fmt.Printf("  policy=%s, exit=%d → restart=%s, restart_count=%d, backoff=%ds\n",
				pol, ec, restart, c.RestartCount, c.BackoffRemaining)
		}
	}
}

// Demo 3
func demo3ExponentialBackoff() {
	fmt.Println("\n========== Demo 3: Exponential backoff schedule ==========")
	fmt.Print("Sequence: ")
	for i := 1; i <= 8; i++ {
		fmt.Printf("%d", backoffFor(i))
		if i < 8 {
			fmt.Print(", ")
		}
	}
	fmt.Println()
	fmt.Println("(per docs: 10s, 20s, 40s, 80s, 160s, 300s, then capped at 300s)")

	p := &Pod{
		Name:      "demo3-pod",
		PodPolicy: PolicyAlways,
		Containers: []Container{
			{ID: 0, Kind: KindMain, State: CtrRunning, RunningSinceTick: 0},
		},
		Phase: PhaseRunning,
	}
	totalWait := 0
	for i := 1; i <= 7; i++ {
		p.ContainerExit(0, 1)
		c := p.Containers[0]
		totalWait += c.BackoffRemaining
		fmt.Printf("  crash #%d: wait %ds (cum %ds)\n", i, c.BackoffRemaining, totalWait)
		for j := 0; j < c.BackoffRemaining; j++ {
			p.Tick()
		}
	}
}

// Demo 4
func demo4SidecarIndependence() {
	fmt.Println("\n========== Demo 4: Sidecar containers always restart (independent of Pod policy) ==========")
	p := &Pod{
		Name:      "demo4-pod",
		PodPolicy: PolicyNever,
		Containers: []Container{
			{ID: 0, Kind: KindMain, State: CtrRunning, RunningSinceTick: 0},
			{ID: 1, Kind: KindSidecar, State: CtrRunning, RunningSinceTick: 0},
		},
		Phase: PhaseRunning,
	}
	p.ContainerExit(0, 0)
	p.ContainerExit(1, 0)
	fmt.Println("After both exit 0:")
	main := p.Containers[0]
	side := p.Containers[1]
	mainState := "Terminated"
	if main.RestartCount > 0 {
		mainState = "restarting"
	}
	sideState := "Terminated"
	if side.RestartCount > 0 {
		sideState = "restarting"
	}
	fmt.Printf("  main    : restart_count=%d, backoff=%ds → %s\n",
		main.RestartCount, main.BackoffRemaining, mainState)
	fmt.Printf("  sidecar : restart_count=%d, backoff=%ds → %s\n",
		side.RestartCount, side.BackoffRemaining, sideState)
}

func main() {
	demo1BasicStateMachine()
	demo2PolicyComparison()
	demo3ExponentialBackoff()
	demo4SidecarIndependence()
}

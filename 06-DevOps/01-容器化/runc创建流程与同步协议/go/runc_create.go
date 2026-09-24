// runc create 的双进程同步协议（Python 模型的 Go 复刻）。
//
// 对照 opencontainers/runc main 分支：
//
//	libcontainer/sync.go                  同步常量与 syncT
//	libcontainer/process_linux.go         initProcess.start() 的 parseSync switch
//	libcontainer/standard_init_linux.go   linuxStandardInit.Init() 的顺序
//	libcontainer/rootfs_linux.go          prepareRootfs 里的 syncParentHooks
package main

import "fmt"

const (
	procError       = "procError"
	procReady       = "procReady"
	procRun         = "procRun"
	procHooks       = "procHooks"
	procHooksDone   = "procHooksDone"
	procMountPlease = "procMountPlease"
	procMountFd     = "procMountFd"
	procSeccomp     = "procSeccomp"
	procSeccompDone = "procSeccompDone"

	syncFlagHasFd = 1 << 0

	stateCreating = "creating"
	stateCreated  = "created"
)

// SyncT 对应 Go 的 syncT。
type SyncT struct {
	Type  string
	Arg   map[string]string
	File  string
	Flags int
}

func (s *SyncT) HasFd() bool { return s.Flags&syncFlagHasFd != 0 }

// SyncSocket 是 syncSocket 的模型：两个方向的队列 + 写端关闭标志。
type SyncSocket struct {
	ToChild      []*SyncT
	ToParent     []*SyncT
	ParentWrOpen bool
	ChildWrOpen  bool
	Log          []string
}

func NewSyncSocket() *SyncSocket {
	return &SyncSocket{ParentWrOpen: true, ChildWrOpen: true}
}

func (s *SyncSocket) ParentWrite(t *SyncT) {
	if t.File != "" {
		t.Flags |= syncFlagHasFd
	}
	s.Log = append(s.Log, "parent->child:"+t.Type)
	s.ToChild = append(s.ToChild, t)
}

func (s *SyncSocket) ChildWrite(t *SyncT) {
	if t.File != "" {
		t.Flags |= syncFlagHasFd
	}
	s.Log = append(s.Log, "child->parent:"+t.Type)
	s.ToParent = append(s.ToParent, t)
}

func (s *SyncSocket) ChildRead() *SyncT {
	if len(s.ToChild) == 0 {
		return nil
	}
	t := s.ToChild[0]
	s.ToChild = s.ToChild[1:]
	return t
}

func (s *SyncSocket) ParentRead() *SyncT {
	if len(s.ToParent) == 0 {
		return nil
	}
	t := s.ToParent[0]
	s.ToParent = s.ToParent[1:]
	return t
}

// Config 是要建模的 config.json 关键开关。
type Config struct {
	Namespaces       []string
	NoNewPrivs       bool
	Seccomp          bool
	ListenerPath     string
	NoPivotRoot      bool
	RootPropagation  int
	PassedFilesCount int
	DieBeforeReady   bool
}

func hasNamespace(ns []string, name string) bool {
	for _, n := range ns {
		if n == name {
			return true
		}
	}
	return false
}

// ParentHandler 对应父进程 parseSync 的 switch。
type ParentHandler struct {
	Config        *Config
	Events        []string
	SeenProcReady bool
	Ierr          string
	State         string
}

func (p *ParentHandler) Start() {
	p.Events = append(p.Events, "cmd_start", "apply_cgroup",
		"copy_bootstrap_data", "get_child_pid", "wait_child_exit")
}

func (p *ParentHandler) OnSync(sock *SyncSocket, t *SyncT) {
	switch t.Type {
	case procMountPlease:
		p.Events = append(p.Events, "open_mount_source")
		sock.ParentWrite(&SyncT{Type: procMountFd,
			Arg: map[string]string{"destination": t.Arg["destination"]},
			File: "fd:" + t.Arg["destination"]})
	case procHooks:
		p.Events = append(p.Events, "set_cgroup_config", "run_hook_prestart",
			"run_hook_create_runtime")
		sock.ParentWrite(&SyncT{Type: procHooksDone})
	case procReady:
		p.SeenProcReady = true
		p.Events = append(p.Events, "setup_rlimits", "update_state")
		p.State = stateCreated
		sock.ParentWrite(&SyncT{Type: procRun})
	case procSeccomp:
		if p.Config.ListenerPath == "" {
			p.Ierr = "seccomp listenerPath is not set"
			return
		}
		p.Events = append(p.Events, "pidfd_getfd")
		sock.ParentWrite(&SyncT{Type: procSeccompDone})
	case procError:
		if t.Arg != nil {
			p.Ierr = t.Arg["message"]
		}
	}
}

func (p *ParentHandler) Finish(sock *SyncSocket) string {
	sock.ParentWrOpen = false
	if !p.SeenProcReady && p.Ierr == "" {
		p.Ierr = "procReady not received"
	}
	return p.Ierr
}

// StandardInit 对应子进程 linuxStandardInit.Init()。
type StandardInit struct {
	Config     *Config
	Parent     *ParentHandler
	Events     []string
	FifoWrites []string
}

func (c *StandardInit) write(sock *SyncSocket, t *SyncT) {
	sock.ChildWrite(t)
	if c.Parent != nil {
		c.Parent.OnSync(sock, sock.ToParent[len(sock.ToParent)-1])
	}
}

func (c *StandardInit) Init(sock *SyncSocket) {
	cfg := c.Config
	c.Events = append(c.Events, "join_session_keyring", "setup_network",
		"setup_route", "prepare_rootfs")
	c.write(sock, &SyncT{Type: procHooks})
	c.Events = append(c.Events, "sync_parent_hooks")
	switch {
	case cfg.NoPivotRoot:
		c.Events = append(c.Events, "ms_move_root")
	case hasNamespace(cfg.Namespaces, "NEWNS"):
		c.Events = append(c.Events, "pivot_root")
	default:
		c.Events = append(c.Events, "chroot")
	}
	c.Events = append(c.Events, "finalize_rootfs", "apparmor", "sysctls",
		"readonly_paths", "mask_paths", "pdeath_get", "scheduler", "ioprio",
		"personality", "mempolicy")
	if cfg.DieBeforeReady {
		sock.ChildWrOpen = false
		return
	}
	// syncParentReady 必须在 seccomp 应用之前：之后就不能读写 socket 了
	c.write(sock, &SyncT{Type: procReady})
	c.Events = append(c.Events, "selinux_exec_label")
	if cfg.Seccomp && !cfg.NoNewPrivs {
		c.Events = append(c.Events, "seccomp_init")
		c.write(sock, &SyncT{Type: procSeccomp, Arg: map[string]string{"fd": "7"}})
	}
	c.Events = append(c.Events, "finalize_namespace", "pdeath_restore", "lookpath")
	if cfg.Seccomp && cfg.NoNewPrivs {
		c.Events = append(c.Events, "seccomp_init")
		c.write(sock, &SyncT{Type: procSeccomp, Arg: map[string]string{"fd": "7"}})
	}
	c.Events = append(c.Events, "close_pipe", "close_logpipe", "reopen_fifo")
	c.FifoWrites = append(c.FifoWrites, "0")
	c.Events = append(c.Events, "close_fifo",
		fmt.Sprintf("unsafe_close_from_%d", cfg.PassedFilesCount+3), "execve")
	sock.ChildWrOpen = false
}

// Run 跑一遍完整握手（子进程写 sync 时同步回调父进程）。
func Run(cfg *Config) (*ParentHandler, *StandardInit, *SyncSocket) {
	sock := NewSyncSocket()
	p := &ParentHandler{Config: cfg, State: stateCreating}
	c := &StandardInit{Config: cfg, Parent: p}
	p.Start()
	c.Init(sock)
	p.Finish(sock)
	return p, c, sock
}

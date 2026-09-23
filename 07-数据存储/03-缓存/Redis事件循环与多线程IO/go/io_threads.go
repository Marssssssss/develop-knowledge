// Redis 多线程 I/O：常量与 isCopyAvoidPreferred() 的 Go 转写。
//
// 来源：
//   - src/server.h     IO_THREADS_MAX_NUM / COPY_AVOID_* / CLIENT_* 
//   - src/object.h     OBJ_ENCODING_RAW / OBJ_*_REFCOUNT
//   - src/networking.c isCopyAvoidPreferred()
//   - src/config.c     deprecatedConfig 表（io-threads-do-reads 已废弃）
//   - redis.conf       THREADED I/O 段的线程数建议
package main

const (
	IoThreadsMaxNum                   = 128
	CopyAvoidMinIoThreads             = 7
	CopyAvoidMinStringSize            = 16384
	CopyAvoidMinStringSizeThreaded    = 65536

	ClientPushing      uint64 = 1 << 46
	ClientTypeNormal          = 0
	ClientTypeMaster          = 3

	ObjEncodingRaw          = 0
	ObjRefcountBits         = 23
	ObjSharedRefcount       = (1 << ObjRefcountBits) - 1
	ObjStaticRefcount       = (1 << ObjRefcountBits) - 2
	ObjFirstSpecialRefcount = ObjStaticRefcount
)

type IoClient struct {
	HasConn    bool   // false = fake client
	ClientType int
	Flags      uint64
}

type IoRobj struct {
	Encoding int
	Refcount int
}

type IoServer struct {
	IoThreadsNum               int
	ReplyCopyAvoidanceEnabled  int
}

func IsCopyAvoidPreferred(s *IoServer, c *IoClient, o *IoRobj, length int) int {
	// 1) fake client 或功能未开启
	if !c.HasConn || s.ReplyCopyAvoidanceEnabled == 0 {
		return 0
	}
	// 2) 只有普通客户端参与
	if c.ClientType != ClientTypeNormal {
		return 0
	}
	// 3) push 消息需要延迟投递，不能走引用
	if c.Flags&ClientPushing != 0 {
		return 0
	}
	// 4) 必须 RAW 编码且不是共享/静态对象
	if o.Encoding != ObjEncodingRaw || o.Refcount >= ObjFirstSpecialRefcount {
		return 0
	}
	// 5) 线程数够多 → 不看长度
	if s.IoThreadsNum >= CopyAvoidMinIoThreads {
		return 1
	}
	// 6) 纯主线程
	if s.IoThreadsNum == 1 {
		if length >= CopyAvoidMinStringSize {
			return 1
		}
		return 0
	}
	// 7) 主线程 + I/O 线程
	if length >= CopyAvoidMinStringSizeThreaded {
		return 1
	}
	return 0
}

// IoThreadsActivated 对应 networking.c 里 `server.io_threads_num > 1` 的分派门控。
func IoThreadsActivated(s *IoServer) bool {
	return s.IoThreadsNum > 1
}

// SuggestedIoThreads 对应 redis.conf：「4 核用 3，8 核用 7」，且要求至少 4 核。
func SuggestedIoThreads(cores int) int {
	if cores < 4 {
		return 1
	}
	return cores - 1
}

// DeprecatedConfigs 对应 config.c 的 deprecatedConfig deprecated_configs[]。
var DeprecatedConfigs = []struct {
	Name string
	A    int
	B    int
}{
	{"list-max-ziplist-entries", 2, 2},
	{"list-max-ziplist-value", 2, 2},
	{"lua-replicate-commands", 2, 2},
	{"io-threads-do-reads", 2, 2},
}

func IsDeprecatedConfig(name string) bool {
	for _, e := range DeprecatedConfigs {
		if e.Name == name {
			return true
		}
	}
	return false
}

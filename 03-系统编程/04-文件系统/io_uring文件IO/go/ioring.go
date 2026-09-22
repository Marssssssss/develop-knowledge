// io_uring 文件 IO 的 Go 侧镜像：ring 布局、SQE/CQE、注册缓冲区。
//
// 来源同 Python 侧：include/uapi/linux/io_uring.h、io_uring/io_uring.h、
// io_uring_setup(2) / io_uring_register(2) / io_uring_enter(2)
package main

import "fmt"

const (
	ioRingOffSQRing  = 0x0
	ioRingOffCQRing  = 0x8000000
	ioRingOffSQEs    = 0x10000000
	ioRingOffPbufRing = 0x80000000
	ioRingOffMmapMask = 0xF8000000

	ioRingMaxEntries    = 32768
	ioRingMaxCQEntries  = 2 * ioRingMaxEntries

	ioRingSetupIOPOLL     = 1 << 0
	ioRingSetupSQPOLL     = 1 << 1
	ioRingSetupCQSIZE     = 1 << 3
	ioRingSetupClamp      = 1 << 4
	ioRingSetupRDisabled  = 1 << 6
	ioRingSetupSQE128     = 1 << 10
	ioRingSetupCQE32      = 1 << 11
	ioRingSetupNoSQArray  = 1 << 16

	iosqeFixedFileBit     = 0
	iosqeIODrainBit       = 1
	iosqeIOLinkBit        = 2
	iosqeIOHardlinkBit    = 3
	iosqeAsyncBit         = 4
	iosqeBufferSelectBit  = 5
	iosqeCQESkipSuccessBit = 6

	iosqeFixedFile     = 1 << iosqeFixedFileBit
	iosqeIOLink        = 1 << iosqeIOLinkBit
	iosqeAsync         = 1 << iosqeAsyncBit
	iosqeBufferSelect  = 1 << iosqeBufferSelectBit
	iosqeCQESkipSuccess = 1 << iosqeCQESkipSuccessBit

	ioRingOpNop        = 0
	ioRingOpReadv      = 1
	ioRingOpWritev     = 2
	ioRingOpReadFixed  = 4
	ioRingOpWriteFixed = 5
	ioRingOpOpenat     = 18
	ioRingOpClose      = 19
	ioRingOpStatx      = 21
	ioRingOpRead       = 22
	ioRingOpWrite      = 23
	ioRingOpFadvise    = 24
	ioRingOpFtruncate  = 56
	ioRingOpReadvFixed = 62

	ioRingRegisterBuffers       = 0
	ioRingUnregisterBuffers     = 1
	ioRingRegisterFiles         = 2
	ioRingRegisterBuffers2      = 15
	ioRingRegisterBuffersUpdate = 16
	ioRingRegisterRingFds       = 20

	sqeSize   = 64
	cqeSize   = 16
	sqeSize128 = 128
	cqeSize32  = 32
)

var opName = map[uint8]string{
	ioRingOpNop: "NOP", ioRingOpReadv: "READV", ioRingOpWritev: "WRITEV",
	ioRingOpReadFixed: "READ_FIXED", ioRingOpWriteFixed: "WRITE_FIXED",
	ioRingOpOpenat: "OPENAT", ioRingOpClose: "CLOSE", ioRingOpStatx: "STATX",
	ioRingOpRead: "READ", ioRingOpWrite: "WRITE", ioRingOpFadvise: "FADVISE",
	ioRingOpFtruncate: "FTRUNCATE", ioRingOpReadvFixed: "READV_FIXED",
}

// SQE 是 struct io_uring_sqe 的字段子集。
type SQE struct {
	Opcode   uint8
	Flags    uint8
	FD       int32
	Off      uint64
	Addr     uint64
	Len      uint32
	UserData uint64
	BufIndex uint16
}

// UsesFixedBuffer 表示 addr 字段是注册缓冲区索引而非指针。
func (s *SQE) UsesFixedBuffer() bool {
	return s.Opcode == ioRingOpReadFixed || s.Opcode == ioRingOpWriteFixed ||
		s.Opcode == ioRingOpReadvFixed
}

func (s *SQE) UsesFixedFile() bool { return s.Flags&iosqeFixedFile != 0 }

// CQE 是 struct io_uring_cqe，res 为负时是 -errno。
type CQE struct {
	UserData uint64
	Res      int32
	Flags    uint32
}

func (c CQE) OK() bool    { return c.Res >= 0 }
func (c CQE) Errno() int  { if c.Res < 0 { return int(-c.Res) }; return 0 }

// ClampEntries 对应 io_uring_setup 对 entries 的钳制。
func ClampEntries(entries int, flags uint32) (int, error) {
	if entries <= 0 {
		return 0, fmt.Errorf("EINVAL: entries must be > 0")
	}
	if entries > ioRingMaxEntries {
		if flags&ioRingSetupClamp != 0 {
			return ioRingMaxEntries, nil
		}
		return 0, fmt.Errorf("EINVAL: entries > IORING_MAX_ENTRIES without CLAMP")
	}
	return entries, nil
}

// SetupResult 是 io_uring_setup 输出参数的子集。
type SetupResult struct {
	SQEntries int
	CQEntries int
	SQPoll    bool
	SQESize   int
	CQESize   int
}

// Setup 模拟 io_uring_setup。
func Setup(entries, cqEntries int, flags uint32) (SetupResult, error) {
	sq, err := ClampEntries(entries, flags)
	if err != nil {
		return SetupResult{}, err
	}
	cq := 2 * sq
	if cqEntries > 0 {
		if flags&ioRingSetupCQSIZE == 0 {
			return SetupResult{}, fmt.Errorf("EINVAL: cq_entries needs CQSIZE")
		}
		if cqEntries <= entries {
			return SetupResult{}, fmt.Errorf("EINVAL: cq_entries must be > entries")
		}
		cq = cqEntries
	}
	if cq > ioRingMaxCQEntries {
		cq = ioRingMaxCQEntries
	}
	ss, cs := sqeSize, cqeSize
	if flags&ioRingSetupSQE128 != 0 {
		ss = sqeSize128
	}
	if flags&ioRingSetupCQE32 != 0 {
		cs = cqeSize32
	}
	return SetupResult{sq, cq, flags&ioRingSetupSQPOLL != 0, ss, cs}, nil
}

// MmapLength 返回三块映射的长度。
func MmapLength(kind string, sqEntries, cqEntries, cqeSz, sqeSz int) int {
	switch kind {
	case "sq_ring":
		return 64 + sqEntries*4
	case "sqes":
		return sqEntries * sqeSz
	case "cq_ring":
		return 64 + cqEntries*cqeSz
	}
	return 0
}

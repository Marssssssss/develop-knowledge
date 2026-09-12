// SYN Cookie 防 SYN Flood — Go 实现 (Bernstein/Schenk 1996 + RFC 4987 §3.1)
//
// 与 Python/C 实现完全等价的算法:
//   - server 在 SYN 时编码 32-bit cookie(t 5 bit | mss 3 bit | hash 24 bit)
//   - 验证 ACK 还原 cookie, 校验时戳 + HMAC, 通过才分配 TCB
//
// 依赖: 标准库 crypto/hmac + crypto/sha1
//
// 运行: go run syn_cookie.go

package main

import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha1"
	"encoding/binary"
	"fmt"
	"math"
	"os"
	"time"
)

const slowTick = 64   // 1 t 单位 = 64 秒
const tolerance = 4   // ±4 t 单位容忍 ~ 256 秒

var mssTable = [8]uint16{536, 1300, 1460, 1500, 2000, 4096, 8192, 9000}

// ============================================================
// Server
// ============================================================

type SynCookieServer struct {
	secret []byte
}

func NewSynCookieServer() *SynCookieServer {
	secret := make([]byte, 16)
	rand.Read(secret)
	return &SynCookieServer{secret: secret}
}

func slowTime(ts int64) int {
	return int((ts/slowTick)%32) & 0x1F
}

func encodeMSS(mss uint16) int {
	idx := 0
	minDiff := int(mss ^ mssTable[0])
	if mssTable[0] > mss {
		minDiff = int(mssTable[0] - mss)
	} else {
		minDiff = int(mss - mssTable[0])
	}
	for i := 1; i < len(mssTable); i++ {
		d := 0
		if mssTable[i] > mss {
			d = int(mssTable[i] - mss)
		} else {
			d = int(mss - mssTable[i])
		}
		if d < minDiff {
			minDiff = d
			idx = i
		}
	}
	return idx
}

func (s *SynCookieServer) computeS(srcIP string, srcPort uint16,
	dstIP string, dstPort uint16, t int, mCode int) uint32 {
	msg := fmt.Sprintf("%s|%d|%s|%d|%d|%d", srcIP, srcPort, dstIP, dstPort, t, mCode)
	h := hmac.New(sha1.New, s.secret)
	h.Write([]byte(msg))
	digest := h.Sum(nil)
	// 低 24 bit (big-endian 取前 4 B 的低 24)
	var v uint32 = binary.BigEndian.Uint32(digest[:4])
	return v & 0xFFFFFF
}

func (s *SynCookieServer) Make(srcIP string, srcPort uint16,
	dstIP string, dstPort uint16, mss uint16) uint32 {
	t := slowTime(time.Now().Unix())
	m := encodeMSS(mss)
	sCode := s.computeS(srcIP, srcPort, dstIP, dstPort, t, m)
	return (uint32(t) << 27) | (uint32(m) << 24) | sCode
}

type VerifyResult struct {
	Valid   bool
	Src     string
	Dst     string
	MSS     uint16
	TCooked int
	TNow    int
}

func (s *SynCookieServer) Verify(ackNum uint32,
	srcIP string, srcPort uint16,
	dstIP string, dstPort uint16) VerifyResult {
	res := VerifyResult{}
	cookie := (ackNum - 1)
	tCooked := int((cookie >> 27) & 0x1F)
	mCode := int((cookie >> 24) & 0x7)
	sRecv := cookie & 0xFFFFFF

	tNow := slowTime(time.Now().Unix())
	diff := ((tNow - tCooked) + 32) % 32
	if diff > 16 {
		diff = 32 - diff
	}
	if diff > tolerance {
		return res
	}
	sExpected := s.computeS(srcIP, srcPort, dstIP, dstPort, tCooked, mCode)
	if sExpected != sRecv {
		return res
	}

	res.Valid = true
	res.Src = fmt.Sprintf("%s:%d", srcIP, srcPort)
	res.Dst = fmt.Sprintf("%s:%d", dstIP, dstPort)
	res.MSS = mssTable[mCode]
	res.TCooked = tCooked
	res.TNow = tNow
	return res
}

// ============================================================
// Demo
// ============================================================

func main() {
	fmt.Println("================================================================")
	fmt.Println(" SYN Cookie Demo (Bernstein/Schenk 1996, RFC 4987 §3.1) — Go")
	fmt.Println("================================================================")

	srv := NewSynCookieServer()

	fmt.Println("\n[Step 1] Client A (192.0.2.10:54321) 发 SYN → server")
	isn := srv.Make("192.0.2.10", 54321, "203.0.113.5", 80, 1460)
	fmt.Printf("  Server 编码 SYN cookie ISN = 0x%08X\n", isn)
	fmt.Printf("    Top 5  bit t   = %d\n", (isn>>27)&0x1F)
	fmt.Printf("    Mid 3  bit mss = %d  → MSS = %d B\n", (isn>>24)&0x7, mssTable[(isn>>24)&0x7])
	fmt.Printf("    Low 24 bit s   = 0x%06X\n", isn&0xFFFFFF)

	ack := isn + 1
	fmt.Printf("\n[Step 2] Client A 回 ACK (ack_number = 0x%08X = ISN+1)\n", ack)
	info := srv.Verify(ack, "192.0.2.10", 54321, "203.0.113.5", 80)
	if info.Valid {
		fmt.Println("  ✓ 通过 → 分配 TCB")
		fmt.Printf("    src     = %s\n    dst     = %s\n    MSS     = %d\n", info.Src, info.Dst, info.MSS)
		fmt.Printf("    t_cooked = %d   t_now = %d\n", info.TCooked, info.TNow)
	} else {
		fmt.Println("  ✗ 校验失败 (意料之外)")
		os.Exit(1)
	}

	fmt.Println("\n[Step 3] 攻击者篡改 cookie 1 bit → 验证失败")
	fakeISN := isn ^ 0x1
	fakeACK := fakeISN + 1
	info2 := srv.Verify(fakeACK, "192.0.2.10", 54321, "203.0.113.5", 80)
	fmt.Printf("  Fake cookie       = 0x%08X  (legal = 0x%08X)\n", fakeISN, isn)
	fmt.Printf("  Server 验证       = %s\n", map[bool]string{true: "通过", false: "失败 (丢弃)"}[info2.Valid])
	if info2.Valid {
		os.Exit(1)
	}

	fmt.Println("\n[Step 4] 10 个客户端同一秒 SYN → hash 区分不同 src")
	seen := make(map[uint32]bool)
	for i := 0; i < 10; i++ {
		src := fmt.Sprintf("192.0.2.%d", i+1)
		s := srv.Make(src, uint16(50000+i), "203.0.113.5", 80, 1460)
		seen[s&0xFFFFFF] = true
	}
	fmt.Printf("  %d/10 互不相同的低 24 bit (hash 区分)\n", len(seen))

	fmt.Println("\n================================================================")
	fmt.Println("  SYN Cookie 演示完成 ✓ — Go")
	fmt.Println("================================================================")
	_ = math.MaxUint32
}

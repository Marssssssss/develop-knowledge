package main

// HTTP请求走私 自检（与 selfcheck_smuggle.py 同一套断言）。
// 依据 RFC 9112（https://www.rfc-editor.org/rfc/rfc9112.txt 全文实读）。

import "fmt"

var okCount int
var failed []string

func ck(name string, cond bool, detail string) {
	if cond {
		okCount++
	} else {
		failed = append(failed, name+" "+detail)
	}
}

func eq(name string, got, want interface{}) {
	ck(name, fmt.Sprintf("%v", got) == fmt.Sprintf("%v", want),
		fmt.Sprintf("got=%v want=%v", got, want))
}

func hdr(pairs ...[2]string) Headers { return Headers{Pairs: pairs} }

func main() {
	// §6.1/§7 transfer-coding 解析
	eq("TE: chunked", ParseTE("chunked"), []string{"chunked"})
	eq("TE 大小写不敏感", ParseTE("Chunked"), []string{"chunked"})
	eq("TE 逗号列表", ParseTE("gzip, chunked"), []string{"gzip", "chunked"})

	// §6.3 规则 1/2
	f := DetermineFraming(hdr([2]string{"Content-Length", "10"}), false, "HEAD", 200)
	eq("规则1 HEAD 响应无 body", fmt.Sprintf("%d/%s", f.Rule, f.Kind), "1/none")
	f = DetermineFraming(hdr([2]string{"Content-Length", "10"},
		[2]string{"Transfer-Encoding", "chunked"}), false, "GET", 204)
	eq("规则1 204 即便有 CL/TE 也无 body", fmt.Sprintf("%d/%s", f.Rule, f.Kind), "1/none")
	f = DetermineFraming(hdr([2]string{"Content-Length", "10"}), false, "CONNECT", 200)
	eq("规则2 CONNECT 2xx 是隧道", fmt.Sprintf("%d/%s", f.Rule, f.Kind), "2/tunnel")

	// §6.3 规则 3/4
	f = DetermineFraming(hdr([2]string{"Content-Length", "6"},
		[2]string{"Transfer-Encoding", "chunked"}), true, "POST", 0)
	eq("规则3 TE 与 CL 并存走 TE", fmt.Sprintf("%d/%s", f.Rule, f.Kind), "3/chunked")
	ck("规则3 标记为走私嫌疑", f.Suspect, "")
	f = DetermineFraming(hdr([2]string{"Transfer-Encoding", "gzip"}), true, "POST", 0)
	eq("规则4 请求中 chunked 非最终 → 400", fmt.Sprintf("%d/%s/%d", f.Rule, f.Kind, f.Value), "4/error/400")
	f = DetermineFraming(hdr([2]string{"Transfer-Encoding", "gzip"}), false, "GET", 200)
	eq("规则4 响应中 chunked 非最终 → 读到关闭", fmt.Sprintf("%d/%s", f.Rule, f.Kind), "4/close")

	// §6.3 规则 5/6
	f = DetermineFraming(hdr([2]string{"Content-Length", "13"}), true, "POST", 0)
	eq("规则6 合法 CL", fmt.Sprintf("%d/%s/%d", f.Rule, f.Kind, f.Value), "6/length/13")
	f = DetermineFraming(hdr([2]string{"Content-Length", "13, 13"}), true, "POST", 0)
	eq("规则5 逗号列表全相同则可用", fmt.Sprintf("%d/%d", f.Rule, f.Value), "6/13")
	f = DetermineFraming(hdr([2]string{"Content-Length", "13, 14"}), true, "POST", 0)
	eq("规则5 列表值不同 → 400", fmt.Sprintf("%d/%s", f.Rule, f.Kind), "5/error")
	f = DetermineFraming(hdr([2]string{"Content-Length", "abc"}), true, "POST", 0)
	eq("规则5 非数字 → 400", f.Rule, 5)

	// §6.3 规则 7/8
	f = DetermineFraming(hdr([2]string{"Host", "a"}), true, "GET", 0)
	eq("规则7 请求无 CL/TE → 0", fmt.Sprintf("%d/%s", f.Rule, f.Kind), "7/none")
	f = DetermineFraming(hdr([2]string{"Host", "a"}), false, "GET", 200)
	eq("规则8 响应无长度声明 → 读到关闭", fmt.Sprintf("%d/%s", f.Rule, f.Kind), "8/close")

	// §7.1.3 chunked 解码
	body, nxt, err := DecodeChunked("5\r\nhello\r\n0\r\n\r\n", 0)
	eq("chunked 单块", body, "hello")
	eq("chunked 消费到末尾", nxt, 15)
	body, nxt, _ = DecodeChunked("5;a=b\r\nhello\r\n0\r\n\r\n", 0)
	eq("chunk-ext 被忽略", body, "hello")
	body, nxt, _ = DecodeChunked("3\r\nabc\r\n2\r\nde\r\n0\r\n\r\n", 0)
	eq("chunked 多块拼接", body, "abcde")
	body, nxt, _ = DecodeChunked("0\r\nX: y\r\n\r\n", 0)
	eq("last-chunk 带 trailer", fmt.Sprintf("%q/%d", body, nxt), `""/11`)
	_, _, err = DecodeChunked("z\r\n", 0)
	ck("非法 chunk-size 报错", err != "", "")
	_, _, err = DecodeChunked("5\r\nabc", 0)
	ck("chunk-data 不足报错", err != "", "")

	// CL.TE
	clte := "POST / HTTP/1.1\r\nHost: a\r\nContent-Length: 6\r\n" +
		"Transfer-Encoding: chunked\r\n\r\n0\r\n\r\nG"
	res := TwoHop(clte, Parser{"front", "cl"}, Parser{"back", "te"})
	ck("CL.TE 无错", res.Err == "", res.Err)
	eq("CL.TE 前端按 CL=6 取 body", res.Front.Body, "0\r\n\r\nG")
	eq("CL.TE 前端吞掉整段", res.FrontEnd, len(clte))
	eq("CL.TE 后端按 chunked 取到空 body", res.Back.Body, "")
	eq("CL.TE 走私出一个字节", res.Leftover, "G")

	// TE.CL
	tecl := "POST / HTTP/1.1\r\nHost: a\r\nContent-Length: 4\r\n" +
		"Transfer-Encoding: chunked\r\n\r\n10\r\nGPOST / HTTP/1.1\r\n0\r\n\r\n"
	res = TwoHop(tecl, Parser{"f", "te"}, Parser{"b", "cl"})
	ck("TE.CL 无错", res.Err == "", res.Err)
	eq("TE.CL 前端按 chunked 取到整块", res.Front.Body, "GPOST / HTTP/1.1")
	eq("TE.CL 后端按 CL=4 只吃掉块长行", res.Back.Body, "10\r\n")
	eq("TE.CL 走私出请求前缀", res.Leftover, "GPOST / HTTP/1.1\r\n0\r\n\r\n")
	eq("TE.CL 走私前缀长度", len(res.Leftover), 23)

	// 同一字节流，两种策略给出不同边界
	buf := "POST /x HTTP/1.1\r\nHost: a\r\nContent-Length: 3\r\n" +
		"Transfer-Encoding: chunked\r\n\r\n0\r\n\r\nZZ"
	rcl, pcl, _ := Parser{"cl", "cl"}.ParseOne(buf, 0)
	rte, pte, _ := Parser{"te", "te"}.ParseOne(buf, 0)
	eq("cl 策略只吃 CL 声明的 3 字节", rcl.Body, "0\r\n")
	eq("cl 策略边界", pcl, 79)
	eq("te 策略按 chunked 取到空 body", rte.Body, "")
	eq("te 策略边界", pte, 81)
	ck("两策略边界不同", pcl != pte, "")

	// 无分歧时不应走私
	clean := "GET / HTTP/1.1\r\nHost: a\r\nContent-Length: 0\r\n\r\n"
	res = TwoHop(clean, Parser{"front", "cl"}, Parser{"back", "te"})
	eq("干净请求无残留", res.Leftover, "")

	fmt.Println("OK =", okCount)
	if len(failed) > 0 {
		fmt.Println("FAILED =", len(failed))
		for _, s := range failed {
			fmt.Println("  -", s)
		}
	} else {
		fmt.Println("ALL OK")
	}
}

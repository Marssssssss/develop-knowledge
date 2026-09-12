// 火焰图生成 demo:Go 版 —— folded 栈解析 + 前缀树构建 + 终端 ASCII 火焰图。
//
// 验证火焰图的结构语义(对照 flamegraph.pl):
//   1. 解析 "frame;frame;... count"(folding 输入);
//   2. 合并相同栈 -> 前缀树(等价于官方 %Node 哈希的帧落账);
//   3. x 轴按帧名字母序(排序最大化合并);
//   4. 宽度 ∝ 计数(终端里用字符数表达),深度 = 栈深;
//   5. 剪枝:宽度 < minwidth 的帧丢弃。
package main

import (
	"bufio"
	"fmt"
	"os"
	"sort"
	"strconv"
	"strings"
)

// 内置示例与 python 版一致,可直接对照两图。
const builtinFolded = `main;net_server;accept_loop;epoll_wait 8
main;net_server;accept_loop;accept;connection_new 6
main;net_server;worker_pool;worker_run;parse_request;json_parse 14
main;net_server;worker_pool;worker_run;parse_request;validate 5
main;net_server;worker_pool;worker_run;handle_query;db_exec;btree_search 18
main;net_server;worker_pool;worker_run;handle_query;db_exec;row_serialize 6
main;net_server;logger;syslog_write;write_syscall 3
`

type node struct {
	name     string
	value    float64
	children map[string]*node
}

func newNode(name string) *node { return &node{name: name, children: map[string]*node{}} }

func (n *node) add(frames []string, count float64) {
	n.value += count
	if len(frames) == 0 {
		return
	}
	child := n.children[frames[0]]
	if child == nil {
		child = newNode(frames[0])
		n.children[frames[0]] = child
	}
	child.add(frames[1:], count)
}

// parseFolded 解析 folded 文本;非法行跳过并对齐官方 "Ignored N lines" 警告。
func parseFolded(text string) (map[string]float64, int) {
	counts := map[string]float64{}
	ignored := 0
	for _, line := range strings.Split(text, "\n") {
		line = strings.TrimRight(line, "\r")
		if strings.TrimSpace(line) == "" {
			continue
		}
		stack, cnt, ok := strings.Cut(line, " ")
		if !ok || stack == "" {
			ignored++
			continue
		}
		v, err := strconv.ParseFloat(cnt, 64)
		if err != nil {
			ignored++
			continue
		}
		counts[stack] += v
	}
	return counts, ignored
}

func buildTree(counts map[string]float64) *node {
	root := newNode("") // 根帧:占满全部计数(timemax)
	for _, stack := range sortedKeys(counts) { // sort @Data
		root.add(strings.Split(stack, ";"), counts[stack])
	}
	return root
}

func sortedKeys(m map[string]float64) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

// renderAscii 终端渲染:宽度 ∝ 计数(每字符代表一个样本),深度 = 栈深。
func renderAscii(root *node, width int, minwidth float64) string {
	var b strings.Builder
	total := root.value
	fmt.Fprintf(&b, "all (%.0f samples, 100%%)\n", total)

	var emit func(n *node, depth int)
	emit = func(n *node, depth int) {
		for _, name := range sortedChildNames(n) {
			child := n.children[name]
			cols := int(child.value)
			if float64(cols) < minwidth { // 剪枝:0 宽帧丢弃
				continue
			}
			pct := 100 * child.value / total
			bar := strings.Repeat("█", cols)
			fmt.Fprintf(&b, "%-32s %5.1f%% %s\n",
				strings.Repeat("  ", depth)+name, pct, bar)
			emit(child, depth+1)
		}
	}
	emit(root, 0)
	return b.String()
}

func sortedChildNames(n *node) []string {
	names := make([]string, 0, len(n.children))
	for name := range n.children {
		names = append(names, name)
	}
	sort.Strings(names) // x 轴字母序:最大化合并
	return names
}

func main() {
	text := builtinFolded
	if len(os.Args) > 1 && os.Args[1] != "-" {
		data, err := os.ReadFile(os.Args[1])
		if err != nil {
			fmt.Fprintln(os.Stderr, "read:", err)
			os.Exit(1)
		}
		text = string(data)
	} else if len(os.Args) > 1 {
		var sb strings.Builder
		sc := bufio.NewScanner(os.Stdin)
		for sc.Scan() {
			sb.WriteString(sc.Text())
			sb.WriteByte('\n')
		}
		text = sb.String()
	}

	counts, ignored := parseFolded(text)
	if len(counts) == 0 {
		fmt.Fprintln(os.Stderr, "ERROR: No valid input provided")
		os.Exit(2)
	}
	if ignored > 0 {
		fmt.Fprintf(os.Stderr, "warning: Ignored %d lines with invalid format\n", ignored)
	}

	root := buildTree(counts)
	w := bufio.NewWriter(os.Stdout)
	defer w.Flush()
	fmt.Fprintln(w, renderAscii(root, 80, 1))
}

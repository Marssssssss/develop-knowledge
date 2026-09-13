// Go 版 HCL 极简解析器
//
// 来源:HCL Native Syntax Specification v2.12.0
//      (github.com/hashicorp/hcl/blob/v2.12.0/hclsyntax/spec.md)
// 实现:lexer + 递归下降 parser,把 HCL 转成嵌套 map[string]any
// 边界:不实现 heredoc/for/splat/function call

package main

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"unicode"
)

// -----------------------------------------------------------------------------
// Lexer
// -----------------------------------------------------------------------------

type tokType int

const (
	tEOF tokType = iota
	tIdent
	tNumber
	tString
	tOp
)

type token struct {
	typ tokType
	val string
}

func tokenize(src string) ([]token, error) {
	var toks []token
	i := 0
	for i < len(src) {
		for i < len(src) && (src[i] == ' ' || src[i] == '\t' || src[i] == '\n' || src[i] == '\r') { i++ }
		if i >= len(src) { break }
		if src[i] == '#' || (src[i] == '/' && i+1 < len(src) && src[i+1] == '/') {
			for i < len(src) && src[i] != '\n' { i++ }
			continue
		}
		if src[i] == '/' && i+1 < len(src) && src[i+1] == '*' {
			i += 2
			for i+1 < len(src) && !(src[i] == '*' && src[i+1] == '/') { i++ }
			i += 2
			continue
		}
		if isAlpha(rune(src[i])) || src[i] == '_' {
			start := i
			for i < len(src) && (isAlphaNum(rune(src[i])) || src[i] == '_' || src[i] == '-') { i++ }
			toks = append(toks, token{tIdent, src[start:i]})
			continue
		}
		if unicode.IsDigit(rune(src[i])) || (src[i] == '-' && i+1 < len(src) && unicode.IsDigit(rune(src[i+1]))) {
			start := i
			if src[i] == '-' { i++ }
			for i < len(src) && unicode.IsDigit(rune(src[i])) { i++ }
			if i < len(src) && src[i] == '.' {
				i++
				for i < len(src) && unicode.IsDigit(rune(src[i])) { i++ }
			}
			toks = append(toks, token{tNumber, src[start:i]})
			continue
		}
		if src[i] == '"' {
			i++
			start := i
			for i < len(src) && src[i] != '"' {
				if src[i] == '\\' { i += 2; continue }
				i++
			}
			toks = append(toks, token{tString, src[start:i]})
			if i < len(src) { i++ }
			continue
		}
		if strings.ContainsRune("={}[]().,+-*/", rune(src[i])) {
			toks = append(toks, token{tOp, string(src[i])})
			i++
			continue
		}
		return nil, fmt.Errorf("unexpected character %q at %d", src[i], i)
	}
	return toks, nil
}

func isAlpha(r rune) bool { return unicode.IsLetter(r) || r == '_' }
func isAlphaNum(r rune) bool { return unicode.IsLetter(r) || unicode.IsDigit(r) || r == '_' }

// -----------------------------------------------------------------------------
// Parser
// -----------------------------------------------------------------------------

type parser struct {
	toks []token
	pos  int
}

func (p *parser) peek() (token, bool) {
	if p.pos >= len(p.toks) {
		return token{tEOF, ""}, false
	}
	return p.toks[p.pos], true
}

func (p *parser) eat() token {
	if p.pos >= len(p.toks) {
		return token{tEOF, ""}
	}
	t := p.toks[p.pos]
	p.pos++
	return t
}

func (p *parser) expectOp(op string) {
	t := p.eat()
	if t.typ != tOp || t.val != op {
		panic(fmt.Sprintf("expected '%s' got %v", op, t))
	}
}

func (p *parser) parseTop() (map[string]any, error) {
	body := map[string]any{}
	for p.pos < len(p.toks) {
		name := p.eat()
		if name.typ != tIdent {
			return nil, fmt.Errorf("expected IDENT got %v", name)
		}
		next := p.peek()
		if next.typ == tOp && next.val == "=" { // attribute
			p.eat()
			val, err := p.parseExpr()
			if err != nil { return nil, err }
			body[name.val] = val
			continue
		}
		// block: name + 0+ labels + { body }
		labels := []string{}
		for {
			cur := p.peek()
			if cur.typ == tOp && cur.val == "{" { break }
			lbl := p.eat()
			if lbl.typ != tString && lbl.typ != tIdent {
				return nil, fmt.Errorf("expected label got %v", cur)
			}
			labels = append(labels, lbl.val)
		}
		p.expectOp("{")
		inner := map[string]any{}
		for {
			cur := p.peek()
			if cur.typ == tOp && cur.val == "}" { p.eat(); break }
			inName := p.eat()
			if inName.typ != tIdent { return nil, fmt.Errorf("expected inner IDENT got %v", inName) }
			inNext := p.peek()
			if inNext.typ != tOp || inNext.val != "=" {
				return nil, fmt.Errorf("nested block not supported: %v", inName)
			}
			p.eat()
			val, err := p.parseExpr()
			if err != nil { return nil, err }
			inner[inName.val] = val
		}
		// 插入 body[name][labels[0]][labels[1]]... = inner
		cursor := body
		for _, l := range labels[:len(labels)-1] {
			next, ok := cursor[l].(map[string]any)
			if !ok { next = map[string]any{}; cursor[l] = next }
			cursor = next
		}
		cursor[labels[len(labels)-1]] = inner
	}
	return body, nil
}

func (p *parser) parseExpr() (any, error) {
	t, ok := p.peek()
	if !ok { return nil, fmt.Errorf("unexpected EOF") }
	switch t.typ {
	case tString:
		p.eat(); return t.val, nil
	case tNumber:
		p.eat(); return t.val, nil
	case tIdent:
		p.eat()
		switch t.val {
		case "true": return true, nil
		case "false": return false, nil
		case "null": return nil, nil
		}
		return t.val, nil
	}
	if t.typ != tOp || t.val != "{" {
		return nil, fmt.Errorf("unsupported expression %v", t)
	}
	p.eat()
	obj := map[string]any{}
	for {
		cur := p.peek()
		if cur.typ == tOp && cur.val == "}" { p.eat(); break }
		k := p.eat()
		if k.typ != tIdent { return nil, fmt.Errorf("object key must be IDENT got %v", k) }
		p.eat() // consume =
		val, err := p.parseExpr()
		if err != nil { return nil, err }
		obj[k.val] = val
		cur = p.peek()
		if cur.typ == tOp && cur.val == "," { p.eat(); continue }
		if cur.typ == tOp && cur.val == "}" { continue }
		return nil, fmt.Errorf("expected , or } got %v", cur)
	}
	return obj, nil
}

// -----------------------------------------------------------------------------
// Demo
// -----------------------------------------------------------------------------

const sampleHCL = `
# 示例 HCL
provider "aws" {
  region = "us-east-1"
  alias  = "primary"
}

resource "aws_instance" "web" {
  ami           = "ami-0c55b159cbfafe1f0"
  instance_type = "t3.micro"
  count         = 2
  tags          = { Name = "web-server", Env = "prod" }
}

variable "region" {
  default = "us-west-2"
}
`

func main() {
	fmt.Println("=== HCL 解析器 demo (Go 版) ===\n--- Input HCL ---\n" + sampleHCL)
	toks, err := tokenize(sampleHCL)
	if err != nil {
		fmt.Fprintf(os.Stderr, "lex error: %v\n", err)
		os.Exit(1)
	}
	fmt.Printf("--- Lexer: %d tokens ---\n", len(toks))
	for i := 0; i < len(toks) && i < 16; i++ {
		fmt.Printf("  %-7v '%s'\n", toks[i].typName(), toks[i].val)
	}
	fmt.Printf("  ... (%d total)\n\n", len(toks))
	p := &parser{toks: toks}
	ast, err := p.parseTop()
	if err != nil {
		fmt.Fprintf(os.Stderr, "parse error: %v\n", err)
		os.Exit(1)
	}
	fmt.Println("--- AST (JSON) ---")
	out, _ := json.MarshalIndent(ast, "", "  ")
	fmt.Println(string(out))
}

func (t tokType) typName() string {
	switch t {
	case tEOF: return "EOF"
	case tIdent: return "IDENT"
	case tNumber: return "NUMBER"
	case tString: return "STRING"
	case tOp: return "OP"
	}
	return "?"
}

// silence unused import for older linters
var _ = bufio.ScanLines

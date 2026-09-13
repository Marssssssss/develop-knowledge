// Cypher 只读子集递归下降解析器 —— Go 版。
//
// 权威来源：
//   - Neo4j Cypher Refcard 4.0    https://neo4j.com/docs/cypher-refcard/4.0/
//   - Neo4j Cheat Sheet 5.x        https://neo4j.com/docs/cypher-cheat-sheet/5/all/
//
// 支持 MATCH / WHERE / RETURN / ORDER BY / SKIP / LIMIT；pattern 单节点 +
// 可选 -[r:TYPE]-> 单关系（demo 演示）。
package main

import (
	"fmt"
	"strconv"
	"strings"
)

type tokType int

const (
	tEOF tokType = iota
	tKeyword; tIdent; tString; tNumber; tBool; tNull
	tLParen; tRParen; tLBracket; tRBracket; tLBrace; tRBrace
	tColon; tComma; tArrowR; tArrowL; tDash; tOp
)

var tokName = map[tokType]string{
	tEOF: "EOF", tKeyword: "KW", tIdent: "IDENT", tString: "STR",
	tNumber: "NUM", tBool: "BOOL", tNull: "NULL",
	tLParen: "(", tRParen: ")", tLBracket: "[", tRBracket: "]",
	tLBrace: "{", tRBrace: "}", tColon: ":", tComma: ",",
	tArrowR: "->", tArrowL: "<-", tDash: "-", tOp: "OP",
}
type Token struct{ Type tokType; Value string; Pos int }
var keywords = map[string]bool{
	"MATCH": true, "WHERE": true, "RETURN": true, "ORDER": true,
	"BY": true, "SKIP": true, "LIMIT": true, "AND": true, "OR": true,
	"NOT": true, "TRUE": true, "FALSE": true, "NULL": true, "AS": true,
	"DISTINCT": true, "OPTIONAL": true,
}
var single = map[byte]tokType{
	'(': tLParen, ')': tRParen, '[': tLBracket, ']': tRBracket,
	'{': tLBrace, '}': tRBrace, ':': tColon, ',': tComma, '=': tOp,
}

func isDigit(c byte) bool { return c >= '0' && c <= '9' }
func isAlpha(c byte) bool { return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') }
func isAlnum(c byte) bool { return isAlpha(c) || isDigit(c) }

func tokenize(src string) ([]Token, error) {
	var out []Token
	i, n := 0, len(src)
	for i < n {
		c := src[i]
		if c == ' ' || c == '\t' || c == '\n' { i++; continue }
		if ty, ok := single[c]; ok { out = append(out, Token{ty, string(c), i}); i++; continue }
		if c == '-' {
			if i+1 < n && src[i+1] == '-' {
				if i+2 < n && src[i+2] == '>' { out = append(out, Token{tArrowR, "->", i}); i += 2; continue }
				if i+2 < n && src[i+2] == '<' { out = append(out, Token{tArrowL, "<-", i}); i += 2; continue }
			}
			out = append(out, Token{tDash, "-", i}); i++; continue
		}
		if c == '<' {
			if i+1 < n && src[i+1] == '>' { out = append(out, Token{tOp, "<>", i}); i += 2; continue }
			if i+1 < n && src[i+1] == '=' { out = append(out, Token{tOp, "<=", i}); i += 2; continue }
			out = append(out, Token{tOp, "<", i}); i++; continue
		}
		if c == '>' {
			if i+1 < n && src[i+1] == '=' { out = append(out, Token{tOp, ">=", i}); i += 2; continue }
			out = append(out, Token{tOp, ">", i}); i++; continue
		}
		if c == '\'' || c == '"' {
			quote := c; i++; start := i
			for i < n && src[i] != quote { i++ }
			out = append(out, Token{tString, src[start:i], start - 1}); i++; continue
		}
		if isDigit(c) || (c == '-' && i+1 < n && isDigit(src[i+1])) {
			start := i
			if c == '-' { i++ }
			for i < n && (isDigit(src[i]) || src[i] == '.') { i++ }
			out = append(out, Token{tNumber, src[start:i], start}); continue
		}
		if isAlpha(c) || c == '_' {
			start := i
			for i < n && (isAlnum(src[i]) || src[i] == '_') { i++ }
			w := src[start:i]; up := strings.ToUpper(w)
			switch {
			case up == "TRUE" || up == "FALSE": out = append(out, Token{tBool, up, start})
			case up == "NULL": out = append(out, Token{tNull, "NULL", start})
			case keywords[up]: out = append(out, Token{tKeyword, up, start})
			default: out = append(out, Token{tIdent, w, start})
			}
			continue
		}
		return nil, fmt.Errorf("unexpected char %q at %d", c, i)
	}
	out = append(out, Token{tEOF, "", n})
	return out, nil
}

// AST
type NodePattern struct{ Var, Label string; Props map[string]any }
type RelPattern struct{ Var, Type, Direction string; Props map[string]any }
type Pattern struct{ Nodes []NodePattern; Rels []RelPattern }
type Projection struct{ Expr, Alias string; Distinct bool }
type Query struct { Match Pattern; Where, OrderBy string; Projections []Projection; Skip, Limit *int }
type Parser struct{ toks []Token; i int }

func NewParser(toks []Token) *Parser { return &Parser{toks: toks} }
func (p *Parser) peek() Token         { return p.toks[p.i] }
func (p *Parser) consume() Token      { t := p.toks[p.i]; p.i++; return t }

func (p *Parser) expectKW(kw string) error {
	t := p.peek()
	if t.Type == tKeyword && t.Value == kw { p.consume(); return nil }
	return fmt.Errorf("expected KW %s, got %s(%q) at %d", kw, tokName[t.Type], t.Value, t.Pos)
}

func (p *Parser) expect(types ...tokType) (Token, error) {
	t := p.peek()
	for _, ty := range types { if t.Type == ty { p.consume(); return t, nil } }
	return t, fmt.Errorf("expected one of %v, got %s at %d", types, tokName[t.Type], t.Pos)
}

// query := MATCH pattern [WHERE ...] RETURN ... [ORDER BY k] [SKIP n] [LIMIT n]
func (p *Parser) ParseQuery() (*Query, error) {
	if err := p.expectKW("MATCH"); err != nil { return nil, err }
	pat, err := p.ParsePattern()
	if err != nil { return nil, err }
	q := &Query{Match: pat}
	if p.peek().Type == tKeyword && p.peek().Value == "WHERE" {
		p.consume(); q.Where = p.collectUntil("RETURN", "ORDER", "SKIP", "LIMIT")
	}
	if err := p.expectKW("RETURN"); err != nil { return nil, err }
	projs, err := p.ParseProjections()
	if err != nil { return nil, err }
	q.Projections = projs
	if p.peek().Type == tKeyword && p.peek().Value == "ORDER" {
		p.consume()
		if err := p.expectKW("BY"); err != nil { return nil, err }
		q.OrderBy = p.collectUntil("SKIP", "LIMIT")
	}
	if p.peek().Type == tKeyword && p.peek().Value == "SKIP" {
		p.consume()
		n, err := strconv.Atoi(p.consume().Value)
		if err != nil { return nil, err }
		q.Skip = &n
	}
	if p.peek().Type == tKeyword && p.peek().Value == "LIMIT" {
		p.consume()
		n, err := strconv.Atoi(p.consume().Value)
		if err != nil { return nil, err }
		q.Limit = &n
	}
	return q, nil
}
func (p *Parser) ParseNode() (NodePattern, error) {
	if _, err := p.expect(tLParen); err != nil { return NodePattern{}, err }
	np := NodePattern{Props: map[string]any{}}
	if p.peek().Type == tIdent { np.Var = p.consume().Value }
	if p.peek().Type == tColon { p.consume(); np.Label = p.consume().Value }
	if p.peek().Type == tLBrace {
		if props, err := p.ParseProps(); err != nil { return np, err } else { np.Props = props }
	}
	if _, err := p.expect(tRParen); err != nil { return np, err }
	return np, nil
}
func (p *Parser) ParseRel() (RelPattern, error) {
	r := RelPattern{Props: map[string]any{}}
	switch p.peek().Type {
	case tArrowL: p.consume(); r.Direction = "<-"
	case tArrowR: p.consume(); r.Direction = "->"
	default:      p.consume(); r.Direction = "-"
	}
	if p.peek().Type != tLBracket { return r, nil }
	p.consume()
	if p.peek().Type == tColon { p.consume(); r.Type = p.consume().Value }
	if p.peek().Type == tLBrace {
		if props, err := p.ParseProps(); err != nil { return r, err } else { r.Props = props }
	}
	if _, err := p.expect(tRBracket); err != nil { return r, err }
	switch p.peek().Type {
	case tArrowR: p.consume(); r.Direction = "->"
	case tArrowL: p.consume(); r.Direction = "<-"
	}
	return r, nil
}

func (p *Parser) ParseProps() (map[string]any, error) {
	if _, err := p.expect(tLBrace); err != nil { return nil, err }
	props := map[string]any{}
	for p.peek().Type != tRBrace {
		k := p.consume().Value
		if _, err := p.expect(tColon); err != nil { return nil, err }
		v, err := p.ParseValue()
		if err != nil { return nil, err }
		props[k] = v
		if p.peek().Type == tComma { p.consume() } else { break }
	}
	if _, err := p.expect(tRBrace); err != nil { return nil, err }
	return props, nil
}

func (p *Parser) ParseValue() (any, error) {
	t := p.consume()
	switch t.Type {
	case tNumber:
		if strings.Contains(t.Value, ".") { return strconv.ParseFloat(t.Value, 64) }
		return strconv.Atoi(t.Value)
	case tString: return t.Value, nil
	case tBool:   return t.Value == "TRUE", nil
	case tNull:   return nil, nil
	}
	return nil, fmt.Errorf("expected value, got %s", tokName[t.Type])
}

func (p *Parser) ParseProjections() ([]Projection, error) {
	var out []Projection
	for {
		var parts []string
		for {
			t := p.peek()
			if t.Type == tComma || t.Type == tEOF { break }
			if t.Type == tKeyword && (t.Value == "ORDER" || t.Value == "SKIP" || t.Value == "LIMIT" || t.Value == "AS") { break }
			parts = append(parts, p.consume().Value)
		}
		expr := strings.TrimSpace(strings.Join(parts, " "))
		distinct := false
		if strings.HasPrefix(strings.ToUpper(expr), "DISTINCT ") {
			distinct = true; expr = strings.TrimSpace(expr[len("DISTINCT "):])
		}
		alias := ""
		if p.peek().Type == tKeyword && p.peek().Value == "AS" { p.consume(); alias = p.consume().Value }
		out = append(out, Projection{Expr: expr, Alias: alias, Distinct: distinct})
		if p.peek().Type != tComma { break }
		p.consume()
	}
	return out, nil
}

func (p *Parser) collectUntil(stop ...string) string {
	var parts []string
	for {
		t := p.peek()
		if t.Type == tEOF { break }
		if t.Type == tKeyword {
			for _, s := range stop { if t.Value == s { return strings.TrimSpace(strings.Join(parts, " ")) } }
		}
		parts = append(parts, t.Value); p.consume()
	}
	return strings.TrimSpace(strings.Join(parts, " "))
}

// 演示图：Tom Hanks / Forrest Gump / Sally Field（与 Python 版同源）
type nodeRow struct{ ID int; Labels map[string]struct{}; Props map[string]any }
var demoG = []nodeRow{
	{1, map[string]struct{}{"Person": {}}, map[string]any{"name": "Tom Hanks", "born": 1956}},
	{2, map[string]struct{}{"Movie": {}}, map[string]any{"title": "Forrest Gump", "released": 1994}},
	{3, map[string]struct{}{"Person": {}}, map[string]any{"name": "Sally Field", "born": 1946}},
}

func execSingle(q *Query) []map[string]any {
	np := q.Match.Nodes[0]
	var rows []map[string]any
	for _, n := range demoG {
		if _, ok := n.Labels[np.Label]; !ok { continue }
		ok := true
		for k, v := range np.Props { if n.Props[k] != v { ok = false; break } }
		if !ok { continue }
		row := map[string]any{}
		for _, proj := range q.Projections {
			e := strings.TrimSpace(proj.Expr); out := proj.Alias
			if out == "" { out = e }
			if i := strings.Index(e, "."); i >= 0 { row[out] = n.Props[e[i+1:]] }
		}
		rows = append(rows, row)
	}
	if q.Limit != nil && len(rows) > *q.Limit { rows = rows[:*q.Limit] }
	return rows
}

func main() {
	qstrs := []string{
		"MATCH (p:Person) WHERE p.born < 1960 RETURN p.name AS name LIMIT 3",
		"MATCH (m:Movie {released: 1994}) RETURN m.title AS title",
	}
	for _, qstr := range qstrs {
		fmt.Printf("\n=== Query: %s ===\n", qstr)
		toks, err := tokenize(qstr)
		if err != nil { fmt.Println("tokenize err:", err); continue }
		for _, t := range toks { fmt.Printf("  [%s pos=%d %q]\n", tokName[t.Type], t.Pos, t.Value) }
		q, err := NewParser(toks).ParseQuery()
		if err != nil { fmt.Println("parse err:", err); continue }
		fmt.Printf("AST.Match.Nodes=%+v\nAST.Match.Rels=%+v\nAST.Projections=%+v\n",
			q.Match.Nodes, q.Match.Rels, q.Projections)
		fmt.Printf("AST.Where=%q OrderBy=%q Skip=%v Limit=%v\n", q.Where, q.OrderBy, q.Skip, q.Limit)
		fmt.Printf("执行结果: %+v\n", execSingle(q))
	}
}
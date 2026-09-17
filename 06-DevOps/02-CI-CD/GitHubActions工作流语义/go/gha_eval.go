// Package main —— 表达式求值器的递归下降解析部分(与 gha_expr.go / gha_funcs.go 同包)。
// 优先级由低到高: || < && < ==/!= < 关系 < 一元 ! < 后缀(. / []) < 原子。
package main

import "fmt"

// ---------------------------------------------------------------- 语法分析

type parser struct {
	toks []tok
	pos  int
	ctx  map[string]interface{}
}

func (p *parser) peek() tok {
	if p.pos < len(p.toks) {
		return p.toks[p.pos]
	}
	return tok{"eof", ""}
}

func (p *parser) eat(kind, val string) (tok, error) {
	t := p.peek()
	if kind != "" && t.kind != kind {
		return t, fmt.Errorf("期望 %s, 得到 %s", kind, t.kind)
	}
	if val != "" && t.val != val {
		return t, fmt.Errorf("期望 %s, 得到 %s", val, t.val)
	}
	p.pos++
	return t, nil
}

func (p *parser) pOr() (interface{}, error) {
	left, err := p.pAnd()
	if err != nil {
		return nil, err
	}
	for p.peek().kind == "op" && p.peek().val == "||" {
		p.pos++
		right, err := p.pAnd()
		if err != nil {
			return nil, err
		}
		if !Truthy(left) {
			left = right // 返回操作数本身, 不是布尔值
		}
	}
	return left, nil
}

func (p *parser) pAnd() (interface{}, error) {
	left, err := p.pCmp()
	if err != nil {
		return nil, err
	}
	for p.peek().kind == "op" && p.peek().val == "&&" {
		p.pos++
		right, err := p.pCmp()
		if err != nil {
			return nil, err
		}
		if Truthy(left) {
			left = right
		}
	}
	return left, nil
}

func (p *parser) pCmp() (interface{}, error) {
	left, err := p.pRel()
	if err != nil {
		return nil, err
	}
	for p.peek().kind == "op" && (p.peek().val == "==" || p.peek().val == "!=") {
		op := p.peek().val
		p.pos++
		right, err := p.pRel()
		if err != nil {
			return nil, err
		}
		eq := LooseEq(left, right)
		left = eq
		if op == "!=" {
			left = !eq
		}
	}
	return left, nil
}

func (p *parser) pRel() (interface{}, error) {
	left, err := p.pUnary()
	if err != nil {
		return nil, err
	}
	for p.peek().kind == "op" {
		op := p.peek().val
		if op != "<" && op != "<=" && op != ">" && op != ">=" {
			break
		}
		p.pos++
		right, err := p.pUnary()
		if err != nil {
			return nil, err
		}
		left = Compare(op, left, right)
	}
	return left, nil
}

func (p *parser) pUnary() (interface{}, error) {
	if p.peek().kind == "op" && p.peek().val == "!" {
		p.pos++
		v, err := p.pUnary()
		if err != nil {
			return nil, err
		}
		return !Truthy(v), nil
	}
	return p.pPostfix()
}

func (p *parser) pPostfix() (interface{}, error) {
	v, err := p.pPrimary()
	if err != nil {
		return nil, err
	}
	for p.peek().kind == "op" && (p.peek().val == "." || p.peek().val == "[") {
		if p.peek().val == "." {
			p.pos++
			name, err := p.eat("ident", "")
			if err != nil {
				return nil, err
			}
			v = Deref(v, name.val)
		} else {
			p.pos++
			idx, err := p.pOr()
			if err != nil {
				return nil, err
			}
			if _, err := p.eat("op", "]"); err != nil {
				return nil, err
			}
			v = Index(v, idx)
		}
	}
	return v, nil
}

func (p *parser) pPrimary() (interface{}, error) {
	t := p.peek()
	switch t.kind {
	case "str":
		p.pos++
		return t.val, nil
	case "num":
		p.pos++
		return parseNumber(t.val)
	case "true":
		p.pos++
		return true, nil
	case "false":
		p.pos++
		return false, nil
	case "null":
		p.pos++
		return nil, nil
	case "op":
		if t.val == "(" {
			p.pos++
			v, err := p.pOr()
			if err != nil {
				return nil, err
			}
			if _, err := p.eat("op", ")"); err != nil {
				return nil, err
			}
			return v, nil
		}
	case "ident":
		p.pos++
		if p.peek().kind == "op" && p.peek().val == "(" {
			p.pos++
			var args []interface{}
			if !(p.peek().kind == "op" && p.peek().val == ")") {
				a, err := p.pOr()
				if err != nil {
					return nil, err
				}
				args = append(args, a)
				for p.peek().kind == "op" && p.peek().val == "," {
					p.pos++
					a, err := p.pOr()
					if err != nil {
						return nil, err
					}
					args = append(args, a)
				}
			}
			if _, err := p.eat("op", ")"); err != nil {
				return nil, err
			}
			return callFunc(t.val, args, p.ctx)
		}
		return p.ctx[t.val], nil
	}
	return nil, fmt.Errorf("意外的 token %s %q", t.kind, t.val)
}


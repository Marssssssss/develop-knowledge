// Package main 实现 Neo4j 约束（constraints）最小模型。
//
// 依据 Neo4j Cypher Manual（实读）：四类约束 uniqueness / existence（EE）/
// type（EE）/ key（EE）；key = 存在性 + 唯一性；类型约束支持
// LIST<STRING NOT NULL>、A | B 联合类型与 VECTOR<INT32>(42)；约束名在索引与
// 约束之间唯一；IF NOT EXISTS 命中同名或同类型同模式时只发通知。
package main

import (
	"errors"
	"fmt"
	"strings"
)

const (
	Uniqueness = "UNIQUENESS"
	Existence  = "EXISTENCE"
	TypeCon    = "TYPE"
	KeyCon     = "KEY"
	NodeT      = "NODE"
	RelT       = "RELATIONSHIP"
)

// Constraint 是一个约束条目。
type Constraint struct {
	Name     string
	Kind     string
	Target   string
	Entity   string
	Props    []string
	TypeSpec string
}

// SchemaKey 是「同类型同模式」的判据。
func (c *Constraint) SchemaKey() string {
	return fmt.Sprintf("%s|%s|%s|%v", c.Kind, c.Target, c.Entity, c.Props)
}

// Entity 是一个节点或关系。
type Entity struct {
	ID     int
	Target string
	Entity string
	Props  map[string]interface{}
}

// Matches 判定实体是否落在约束作用域内。
func (e *Entity) Matches(target, entity string) bool {
	return e.Target == target && e.Entity == entity
}

// Value 取属性，缺失得到 nil。
func (e *Entity) Value(p string) interface{} { return e.Props[p] }

// Has 判定属性存在且非 null。
func (e *Entity) Has(p string) bool {
	v, ok := e.Props[p]
	return ok && v != nil
}

// Database 持有约束、索引名与已写入实体。
type Database struct {
	Constraints   []*Constraint
	IndexNames    map[string]bool
	Notifications []string
	Entities      []*Entity
}

// NewDatabase 返回空库。
func NewDatabase() *Database {
	return &Database{IndexNames: map[string]bool{}}
}

// CreateConstraint 建约束。
func (d *Database) CreateConstraint(name, kind, target, entity string,
	props []string, typeSpec string, ifNotExists bool) (*Constraint, error) {
	if kind == TypeCon && typeSpec == "" {
		return nil, errors.New("type constraint 必须给出类型")
	}
	if kind == Existence && len(props) != 1 {
		return nil, errors.New("existence constraint 只支持单属性")
	}
	if (kind == Uniqueness || kind == KeyCon) && len(props) == 0 {
		return nil, errors.New("至少需要指定一个属性")
	}
	if d.IndexNames[name] {
		return nil, errors.New("there is an index called " + name)
	}
	key := (&Constraint{Kind: kind, Target: target, Entity: entity,
		Props: props}).SchemaKey()
	if ifNotExists {
		for _, c := range d.Constraints {
			if c.Name == name || c.SchemaKey() == key {
				d.Notifications = append(d.Notifications,
					fmt.Sprintf("`CREATE CONSTRAINT %s` has no effect. `%s` already exists.",
						name, c.Name))
				return c, nil
			}
		}
	}
	for _, c := range d.Constraints {
		if c.Name == name {
			return nil, errors.New("there is a constraint called " + name)
		}
	}
	for _, c := range d.Constraints {
		if c.SchemaKey() == key {
			return nil, errors.New("constraint already exists: " + c.Name)
		}
	}
	con := &Constraint{Name: name, Kind: kind, Target: target, Entity: entity,
		Props: props, TypeSpec: typeSpec}
	d.Constraints = append(d.Constraints, con)
	return con, nil
}

// Applicable 返回作用于该实体的约束。
func (d *Database) Applicable(e *Entity) []*Constraint {
	out := []*Constraint{}
	for _, c := range d.Constraints {
		if e.Matches(c.Target, c.Entity) {
			out = append(out, c)
		}
	}
	return out
}

// Add 写入实体并逐条校验约束。
func (d *Database) Add(e *Entity) error {
	for _, c := range d.Applicable(e) {
		switch c.Kind {
		case Existence:
			if err := d.checkExists(c, e); err != nil {
				return err
			}
		case TypeCon:
			if err := d.checkType(c, e); err != nil {
				return err
			}
		case Uniqueness:
			if err := d.checkUnique(c, e); err != nil {
				return err
			}
		case KeyCon:
			// Key = 存在性 + 唯一性
			if err := d.checkExists(c, e); err != nil {
				return err
			}
			if err := d.checkUnique(c, e); err != nil {
				return err
			}
		}
	}
	d.Entities = append(d.Entities, e)
	return nil
}

func (d *Database) checkExists(c *Constraint, e *Entity) error {
	for _, p := range c.Props {
		if !e.Has(p) {
			return errors.New(c.Name + ": 缺少属性 " + p + "（或值为 null）")
		}
	}
	return nil
}

// checkType 属性类型约束。
//
// 口径：值缺失/null 时通过校验 —— 依据 Working with null 页「类型谓词表达式对
// null 一律返回 true」。
func (d *Database) checkType(c *Constraint, e *Entity) error {
	for _, p := range c.Props {
		v := e.Value(p)
		if v == nil {
			continue
		}
		if !ValueMatchesType(v, c.TypeSpec) {
			return errors.New(fmt.Sprintf("%s: %s 的值 %v 不满足类型 %s",
				c.Name, p, v, c.TypeSpec))
		}
	}
	return nil
}

// checkUnique 唯一性校验。
//
// 口径：官方文档本页未规定 null/缺失属性是否参与唯一性判定。本模型按「缺少任一
// 指定属性的实体不参与唯一性校验」实现。
func (d *Database) checkUnique(c *Constraint, e *Entity) error {
	for _, p := range c.Props {
		if !e.Has(p) {
			return nil
		}
	}
	key := make([]interface{}, 0, len(c.Props))
	for _, p := range c.Props {
		key = append(key, e.Props[p])
	}
	for _, o := range d.Entities {
		if o == e || !o.Matches(c.Target, c.Entity) {
			continue
		}
		skip := false
		for _, p := range c.Props {
			if !o.Has(p) {
				skip = true
				break
			}
		}
		if skip {
			continue
		}
		same := true
		for i, p := range c.Props {
			if o.Props[p] != key[i] {
				same = false
				break
			}
		}
		if same {
			return errors.New(fmt.Sprintf("%s: %v 与已有实体 %d 冲突",
				c.Name, key, o.ID))
		}
	}
	return nil
}

var primitives = map[string]string{
	"STRING": "str", "INTEGER": "int", "FLOAT": "float", "BOOLEAN": "bool",
	"MAP": "map", "DATE": "str", "DURATION": "str",
}

// ValueMatchesType 支持 T、LIST<T NOT NULL>、A | B、VECTOR<...> 四种形式。
func ValueMatchesType(v interface{}, spec string) bool {
	for _, alt := range strings.Split(spec, "|") {
		if oneMatches(v, strings.TrimSpace(alt)) {
			return true
		}
	}
	return false
}

func oneMatches(v interface{}, spec string) bool {
	if strings.HasPrefix(spec, "LIST<") && strings.HasSuffix(spec, ">") {
		inner := strings.TrimSpace(spec[5 : len(spec)-1])
		notNull := strings.HasSuffix(inner, " NOT NULL")
		if notNull {
			inner = strings.TrimSpace(inner[:len(inner)-len(" NOT NULL")])
		}
		lst, ok := v.([]interface{})
		if !ok {
			return false
		}
		if len(lst) == 0 && notNull {
			return true // 空列表里没有 null，满足 NOT NULL
		}
		for _, x := range lst {
			if !oneMatches(x, inner) {
				return false
			}
		}
		return true
	}
	if strings.HasPrefix(spec, "VECTOR<") {
		_, ok := v.([]interface{})
		return ok
	}
	switch primitives[spec] {
	case "str":
		_, ok := v.(string)
		return ok
	case "int":
		i, ok := v.(int)
		return ok && !isBool(v) && i == i
	case "float":
		_, ok := v.(float64)
		return ok
	case "bool":
		_, ok := v.(bool)
		return ok
	case "map":
		_, ok := v.(map[string]interface{})
		return ok
	}
	return false
}

func isBool(v interface{}) bool {
	_, ok := v.(bool)
	return ok
}

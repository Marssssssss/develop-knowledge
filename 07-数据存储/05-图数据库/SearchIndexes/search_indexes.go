// Package main 实现 Neo4j 搜索性能索引（search-performance indexes）最小模型。
//
// 依据 Neo4j Cypher Manual（实读）：四类搜索性能索引 Range（默认）/ Text /
// Point / Token lookup；Token lookup 只解决标签与关系类型谓词且建库时自带两个；
// Range 支持等值 / IN / 存在性 / 范围 / STARTS WITH；Text 支持 / ENDS WITH /
// CONTAINS 且采用 trigram 索引；Point 只解决 POINT 值谓词；索引名在索引与约束
// 之间唯一；CREATE INDEX 默认非幂等，IF NOT EXISTS 则只发通知；复合索引只收录
// 标签匹配且含全部指定属性的实体；planner 自动挑索引，USING 可强制指定。
package main

import (
	"errors"
	"fmt"
)

const (
	Range        = "RANGE"
	Text         = "TEXT"
	Point        = "POINT"
	Token        = "TOKEN_LOOKUP"
	NodeTarget   = "NODE"
	RelTarget    = "RELATIONSHIP"
)

// 每类索引可解决的谓词（官方 Supported predicates 表）。
var predsByType = map[string]map[string]bool{
	Range: {"eq": true, "in": true, "exists": true, "range": true,
		"starts_with": true},
	Text:  {"eq": true, "in": true, "starts_with": true, "ends_with": true,
		"contains": true},
	Point: {"point_eq": true, "within_bbox": true, "distance": true},
	Token: {"label": true, "rel_type": true},
}

// planner 的同分裁决口径：谓词只能被某类索引解决时无歧义；多类都能解决时优先
// Range（默认且最通用）。官方未公布优先级表，属本模型口径。
var preference = map[string][]string{
	"eq":          {Range, Text, Point},
	"in":          {Range, Text},
	"exists":      {Range},
	"range":       {Range},
	"starts_with": {Range, Text},
	"ends_with":   {Text},
	"contains":    {Text},
	"point_eq":    {Point},
	"within_bbox": {Point},
	"distance":    {Point},
	"label":       {Token},
	"rel_type":    {Token},
}

// Index 是一个索引条目。
type Index struct {
	Name      string
	Target    string
	Entity    string
	Props     []string
	Type      string
	Generated bool
}

// Key 是「同模式同类型」的判据。
func (i *Index) Key() string {
	return fmt.Sprintf("%s|%s|%v|%s", i.Target, i.Entity, i.Props, i.Type)
}

// Schema 是索引与约束共享的命名空间。
type Schema struct {
	Indexes      []*Index
	Constraints  []string
	Notification []string
}

// NewSchema 返回空 schema。
func NewSchema() *Schema { return &Schema{} }

// DefaultTokenIndexes 建库时自带的两个 token lookup 索引。
func (s *Schema) DefaultTokenIndexes() {
	s.Indexes = append(s.Indexes,
		&Index{Name: "__token_labels__", Target: NodeTarget, Entity: "*",
			Type: Token, Generated: true},
		&Index{Name: "__token_reltypes__", Target: RelTarget, Entity: "*",
			Type: Token, Generated: true})
}

func (s *Schema) nameTaken(name string) bool {
	for _, i := range s.Indexes {
		if i.Name == name {
			return true
		}
	}
	for _, c := range s.Constraints {
		if c == name {
			return true
		}
	}
	return false
}

// CreateIndex 建索引；itype 为空表示不指定类型（得到 Range）。
func (s *Schema) CreateIndex(name, target, entity string, props []string,
	itype string, ifNotExists bool) (*Index, error) {
	if itype == "" {
		itype = Range
	}
	if itype == Token && len(props) > 0 {
		return nil, errors.New("token lookup 索引不能带属性")
	}
	if target == NodeTarget && itype != Token && len(props) == 0 {
		return nil, errors.New("属性索引至少要指定一个属性")
	}
	// 与约束的冲突即使加了 IF NOT EXISTS 也会报错。
	for _, c := range s.Constraints {
		if c == name {
			return nil, errors.New("there is a constraint called " + name)
		}
	}
	key := (&Index{Target: target, Entity: entity, Props: props,
		Type: itype}).Key()
	if ifNotExists {
		for _, i := range s.Indexes {
			if i.Name == name || i.Key() == key {
				s.Notification = append(s.Notification,
					fmt.Sprintf("`CREATE INDEX %s` has no effect. `%s` already exists.",
						name, i.Name))
				return i, nil
			}
		}
	}
	if s.nameTaken(name) {
		return nil, errors.New("there already exists an index called " + name)
	}
	for _, i := range s.Indexes {
		if i.Key() == key {
			return nil, errors.New("index already exists with same schema and " +
				"index type: " + i.Name)
		}
	}
	idx := &Index{Name: name, Target: target, Entity: entity, Props: props,
		Type: itype}
	s.Indexes = append(s.Indexes, idx)
	return idx, nil
}

// CanSolve 判定索引能否解决该谓词。
func CanSolve(i *Index, pred, prop string) bool {
	if i.Type == Token {
		return predsByType[Token][pred]
	}
	if !predsByType[i.Type][pred] {
		return false
	}
	if pred == "label" || pred == "rel_type" {
		return false
	}
	if prop == "" {
		return true
	}
	for _, p := range i.Props {
		if p == prop {
			return true
		}
	}
	return false
}

// Candidates 返回能解决该谓词的所有候选索引。
func Candidates(indexes []*Index, pred, prop, target, entity string) []*Index {
	out := []*Index{}
	for _, i := range indexes {
		if target != "" && i.Target != target && i.Type != Token {
			continue
		}
		if entity != "" && i.Entity != entity && i.Type != Token {
			continue
		}
		if target == RelTarget && i.Type == Token {
			continue
		}
		if CanSolve(i, pred, prop) {
			out = append(out, i)
		}
	}
	return out
}

// Planner 按谓词类型偏好挑最合适的索引。
func Planner(indexes []*Index, pred, prop, target, entity string) *Index {
	cands := Candidates(indexes, pred, prop, target, entity)
	if len(cands) == 0 {
		return nil
	}
	for _, t := range preference[pred] {
		for _, i := range cands {
			if i.Type == t {
				return i
			}
		}
	}
	return cands[0]
}

// PlanWithHint 实现 USING INDEX 提示：强制使用指定索引。
func PlanWithHint(indexes []*Index, hint, pred, prop string) (*Index, error) {
	for _, i := range indexes {
		if i.Name == hint {
			return i, nil
		}
	}
	return nil, errors.New("no such index: " + hint)
}

// Trigrams 是 Text 索引的 trigram 切分：连续 3 个 Unicode 码点为一组。
// 官方例子："developer" → ["dev","eve","vel","elo","lop","ope","per"]。
func Trigrams(s string) []string {
	out := []string{}
	for i := 0; i+2 < len(s); i++ {
		out = append(out, s[i:i+3])
	}
	return out
}

// TextMatches 用 trigram 索引回答 CONTAINS：查询串的每个 trigram 都要命中。
// 口径：长度不足 3 的查询串切不出 trigram，官方未规定，本模型退化为子串判定。
func TextMatches(value, pattern string) bool {
	tg := Trigrams(pattern)
	if len(tg) == 0 {
		return len(pattern) <= len(value) && contains(value, pattern)
	}
	vt := Trigrams(value)
	for _, t := range tg {
		found := false
		for _, v := range vt {
			if v == t {
				found = true
				break
			}
		}
		if !found {
			return false
		}
	}
	return true
}

func contains(s, sub string) bool {
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return true
		}
	}
	return false
}

// IndexedMembers 是复合索引收录条件：标签匹配且含全部指定属性。
func IndexedMembers(nodeProps map[string]bool, required []string, label,
	wanted string) bool {
	if label != wanted {
		return false
	}
	for _, p := range required {
		if !nodeProps[p] {
			return false
		}
	}
	return true
}

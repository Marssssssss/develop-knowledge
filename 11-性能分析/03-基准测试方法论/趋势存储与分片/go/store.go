// Package main 对照两套基准趋势存储模型：Mozilla Perfherder 与 Chrome Perf (Catapult)。
// 口径来源：mozilla/treeherder 的 treeherder/etl/perf.py 与 treeherder/perf/models.py，
// catapult-project/catapult 的 dashboard/dashboard/models/graph_data.py。
package main

import (
	"crypto/sha1"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
)

const signatureHashLength = 40

// FieldLimits 是 models.py 里各字段的 max_length。
var FieldLimits = map[string]int{
	"suite": 80, "test": 80, "application": 10,
	"tags": 360, "extra_options": 422, "measurement_unit": 50,
}

// SeverityRank 对应 PerformanceSignature.SEVERITY_RANK。
var SeverityRank = map[string]int{"": 0, "normal": 1, "subcritical": 2, "critical": 3}

// OrderAndConcat = " ".join(sorted(words))。
func OrderAndConcat(words []string) string {
	s := make([]string, len(words))
	copy(s, words)
	sort.Strings(s)
	return strings.Join(s, " ")
}

// SignatureHash 复刻 _get_signature_hash：key 与 value 放进**同一个**列表一起排序。
// 因此键值角色可以互换而得到同一个 hash（见 README）。
func SignatureHash(props map[string]interface{}) string {
	bag := make([]string, 0, 2*len(props))
	for k := range props {
		bag = append(bag, k)
	}
	for _, v := range props {
		if s, ok := v.(string); ok {
			bag = append(bag, s)
		} else {
			b, _ := json.Marshal(v) // 只有 dict 的键会被排序，列表保持原序
			bag = append(bag, string(b))
		}
	}
	sort.Strings(bag)
	h := sha1.Sum([]byte(strings.Join(bag, "")))
	return hex.EncodeToString(h[:])
}

// SuiteIngest 复刻 _load_perf_datum 里 summary / subtest 两侧的属性构造。
type SuiteIngest struct {
	Tags                  string
	ExtraOptions          string
	LowerIsBetter         bool
	HasSubtests           bool
	SummarySignatureHash  string
	SubtestHashes         map[string]string
}

func ingestSuite(suite map[string]interface{}, reference map[string]interface{}) SuiteIngest {
	out := SuiteIngest{LowerIsBetter: true, SubtestHashes: map[string]string{}}
	if v, ok := suite["lowerIsBetter"].(bool); ok {
		out.LowerIsBetter = v
	}
	if tags, ok := suite["tags"].([]string); ok {
		out.Tags = OrderAndConcat(tags)
	}
	extra := map[string]interface{}{}
	if opts, ok := suite["extraOptions"].([]string); ok {
		sorted := append([]string{}, opts...)
		sort.Strings(sorted)
		extra["test_options"] = sorted
		out.ExtraOptions = OrderAndConcat(opts)
	}
	if subs, ok := suite["subtests"].([]map[string]string); ok {
		out.HasSubtests = len(subs) > 0
	}
	for k, v := range reference {
		extra[k] = v
	}

	// summary 只在 suite 有 value 时才建
	if suite["value"] != nil {
		props := map[string]interface{}{"suite": suite["name"]}
		for k, v := range extra {
			props[k] = v
		}
		out.SummarySignatureHash = SignatureHash(props)
	}
	if subs, ok := suite["subtests"].([]map[string]string); ok {
		for _, sub := range subs {
			props := map[string]interface{}{"suite": suite["name"], "test": sub["name"]}
			for k, v := range extra {
				props[k] = v
			}
			if out.SummarySignatureHash != "" {
				props["parent_signature"] = out.SummarySignatureHash
			}
			out.SubtestHashes[sub["name"]] = SignatureHash(props)
		}
	}
	return out
}

// PerfherderStore 只建模两条唯一约束与 last_updated 的单调性。
type PerfherderStore struct {
	Signatures map[[4]string]*Signature
	Data       map[[5]string]bool
}

type Signature struct {
	Repository, Framework, Application, Hash, Suite, Test, LastUpdated string
	HasSubtests                                                        bool
}

func (s *PerfherderStore) Upsert(sig *Signature, lastUpdated string) *Signature {
	key := [4]string{sig.Repository, sig.Framework, sig.Application, sig.Hash}
	if old, ok := s.Signatures[key]; ok {
		if old.LastUpdated > lastUpdated { // 只增不减
			lastUpdated = old.LastUpdated
		}
		old.LastUpdated = lastUpdated
		return old
	}
	sig.LastUpdated = lastUpdated
	s.Signatures[key] = sig
	return sig
}

func (s *PerfherderStore) AddDatum(repo, job, push, ts, hash string) bool {
	key := [5]string{repo, job, push, ts, hash}
	if s.Data[key] {
		return false
	}
	s.Data[key] = true
	return true
}

// ---- Catapult：路径即主键 ----

// TestMetadata 的主键 id 就是 master/bot/test/metric/page。
type TestMetadata struct {
	KeyID string
	Units string
}

func (t TestMetadata) Parts() []string { return strings.Split(t.KeyID, "/") }

// Bot 是 ComputedProperty：只有 3 段才是 test suite。
func (t TestMetadata) Bot() string {
	p := t.Parts()
	if len(p) != 3 {
		return ""
	}
	return p[0] + "/" + p[1]
}

// ParentTest 是 ComputedProperty：< 4 段为 None，否则去掉末段。
func (t TestMetadata) ParentTest() string {
	p := t.Parts()
	if len(p) < 4 {
		return ""
	}
	return strings.Join(p[:len(p)-1], "/")
}

func (t TestMetadata) Master() string { return t.Parts()[0] }

// Row 的主键是 (test path, revision)；revision = key.integer_id()。
type Row struct {
	ParentPath    string
	Revision      int64
	Value         float64
	Error         float64
	Supplemental  map[string]interface{}
}

// InvalidSupplemental 返回不合规的补充列名（必须带 d_ / r_ / a_ 前缀）。
func (r Row) InvalidSupplemental() []string {
	bad := []string{}
	for k := range r.Supplemental {
		if len(k) >= 2 {
			switch k[:2] {
			case "d_", "r_", "a_":
				continue
			}
		}
		bad = append(bad, k)
	}
	return bad
}

// Indexed 只有显式声明的属性才索引：value 索引、error 不索引。
func (r Row) Indexed(name string) bool {
	return name == "value" || name == "revision" || name == "timestamp"
}

// CatapultStore 中 LastAddedRevision 是独立实体（避免 datastore 写热点）。
type CatapultStore struct {
	Rows      map[[2]interface{}]*Row
	LastAdded map[string]int64
}

func (c *CatapultStore) AddPoint(row *Row) bool {
	key := [2]interface{}{row.ParentPath, row.Revision}
	_, existed := c.Rows[key]
	c.Rows[key] = row // 同一 revision 重复上报 ⇒ 覆盖
	if row.Revision > c.LastAdded[row.ParentPath] {
		c.LastAdded[row.ParentPath] = row.Revision
	}
	return !existed
}

func main() {
	h1 := SignatureHash(map[string]interface{}{"suite": "tp6", "test": "facebook"})
	h2 := SignatureHash(map[string]interface{}{"suite": "facebook", "test": "tp6"})
	fmt.Printf("Perfherder 键值互换: %s == %s ? %v\n", h1[:12], h2[:12], h1 == h2)
	fmt.Printf("键与值互换: %v\n",
		SignatureHash(map[string]interface{}{"a": "1"}) ==
			SignatureHash(map[string]interface{}{"1": "a"}))
	fmt.Printf("列表不排序会裂成两个系列: %v\n",
		SignatureHash(map[string]interface{}{"test_options": []string{"e10s", "webrender"}}) !=
			SignatureHash(map[string]interface{}{"test_options": []string{"webrender", "e10s"}}))

	suite := TestMetadata{"ChromiumPerf/linux-release/sunspider/Total", "ms"}
	top := TestMetadata{"ChromiumPerf/linux-release/sunspider", ""}
	fmt.Printf("Catapult 4 段: master=%s bot=%q parent=%s\n", suite.Master(), suite.Bot(), suite.ParentTest())
	fmt.Printf("Catapult 3 段: bot=%s parent=%q\n", top.Bot(), top.ParentTest())

	cs := &CatapultStore{Rows: map[[2]interface{}]*Row{}, LastAdded: map[string]int64{}}
	cs.AddPoint(&Row{"p", 100, 1.0, 0.1, nil})
	fmt.Printf("同 revision 再写: %v（值被覆盖成 2.0=%v）\n",
		cs.AddPoint(&Row{"p", 100, 2.0, 0.1, nil}), cs.Rows[[2]interface{}{"p", int64(100)}].Value)
}

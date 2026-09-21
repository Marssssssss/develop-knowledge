// Package main 转写 autovacuum.c 的 relation_needs_vacanalyze 与 varsup.c 的 XID 限位。
package main

const (
	vacBaseThresh      = 50
	vacScale           = 0.2
	vacInsBaseThresh   = 1000
	vacInsScale        = 0.2
	anlBaseThresh      = 50
	anlScale           = 0.1
	vacMaxThresh       = 100000000
	freezeMaxAge       = 200000000
	freezeMinAge       = 50000000
	maxTransactionID   = 0xFFFFFFFF
	halfOfXidSpace     = maxTransactionID >> 1 // 2147483647
	stopMargin         = 3000000
	warnMargin         = 100000000
)

// Thresholds 返回 (vacthresh, vacinsthresh, anlthresh)。
func Thresholds(reltuples float64, relpages, relallfrozen int) (float64, float64, float64) {
	vacthresh := vacBaseThresh + vacScale*reltuples
	if vacthresh > vacMaxThresh {
		vacthresh = vacMaxThresh
	}
	vacinsthresh := float64(vacInsBaseThresh) + vacInsScale*reltuples*UnfrozenRatio(relpages, relallfrozen)
	anlthresh := anlBaseThresh + anlScale*reltuples
	return vacthresh, vacinsthresh, anlthresh
}

// UnfrozenRatio 对应源码的 pcnt_unfrozen 计算(relallfrozen 被 clamp 到 relpages)。
func UnfrozenRatio(relpages, relallfrozen int) float64 {
	if relpages > 0 && relallfrozen > 0 {
		rf := relallfrozen
		if rf > relpages {
			rf = relpages
		}
		return 1.0 - float64(rf)/float64(relpages)
	}
	return 1.0
}

// Scores 是三个分量的比值: 实际值 / Max(阈值, 1)。
type Scores struct {
	Vac    float64
	VacIns float64
	Anl    float64
}

func newScores(dead, ins, mod, vt, vit, at float64) Scores {
	return Scores{Vac: dead / max1(vt), VacIns: ins / max1(vit), Anl: mod / max1(at)}
}

func max1(v float64) float64 {
	if v < 1 {
		return 1
	}
	return v
}

// Decision 是一次 relation_needs_vacanalyze 的输出。
type Decision struct {
	DoVacuum  bool
	DoAnalyze bool
	Wraparound bool
	Scores    Scores
	Thresh    [3]float64
}

type relOpts struct {
	Reltuples      float64
	Relpages       int
	RelAllFrozen   int
	DeadTuples     float64
	InsSinceVacuum float64
	ModSinceAnalyze float64
	AVEnabled      bool
	Relfrozenxid   int64
	RecentXid      int64
}

// NeedsVacanalyze 判定是否需要 vacuum / analyze。
func NeedsVacanalyze(r relOpts) Decision {
	vt, vit, at := Thresholds(r.Reltuples, r.Relpages, r.RelAllFrozen)
	sc := newScores(r.DeadTuples, r.InsSinceVacuum, r.ModSinceAnalyze, vt, vit, at)
	force := r.Relfrozenxid >= 0 && r.RecentXid >= 0 &&
		r.RecentXid-r.Relfrozenxid > freezeMaxAge
	d := Decision{Wraparound: force, DoVacuum: force, Scores: sc, Thresh: [3]float64{vt, vit, at}}
	if !r.AVEnabled {
		return d
	}
	if r.DeadTuples > vt {
		d.DoVacuum = true
	}
	if r.InsSinceVacuum > vit {
		d.DoVacuum = true
	}
	if r.ModSinceAnalyze > at {
		d.DoAnalyze = true
	}
	return d
}

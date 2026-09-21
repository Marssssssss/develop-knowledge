package main

// Limits 是 varsup.c SetTransactionIdLimit 算出的四条限位。
type Limits struct {
	Vac  int64
	Warn int64
	Stop int64
	Wrap int64
}

// XidLimits 返回四条防线: wrap/stop/warn/vac。
func XidLimits(oldestDatFrozenXid int64) Limits {
	wrap := oldestDatFrozenXid + halfOfXidSpace
	return Limits{
		Wrap: wrap,
		Stop: wrap - stopMargin,
		Warn: wrap - warnMargin,
		Vac:  oldestDatFrozenXid + freezeMaxAge,
	}
}

// RemainingPct 源码: (xidWrapLimit - xid) / (MaxTransactionId / 2) * 100。
func RemainingPct(curXid, wrap int64) float64 {
	return float64(wrap-curXid) / (float64(maxTransactionID) / 2) * 100
}

// Classify 判断当前 XID 落在哪一段。
func Classify(curXid int64, l Limits) string {
	switch {
	case curXid < l.Vac:
		return "normal"
	case curXid < l.Warn:
		return "vac"
	case curXid < l.Stop:
		return "warn"
	case curXid < l.Wrap:
		return "stop"
	}
	return "wrap"
}

// XidAge 是 age(): 当前 XID 与冻结点的差。
func XidAge(cur, relfrozen int64) int64 {
	return cur - relfrozen
}

// FreezeInterval 静态表被强制 vacuum 的间隔。
func FreezeInterval() int64 {
	return freezeMaxAge - freezeMinAge
}

package main

// OpenSLO v1 的 SLO 时间窗口与 ratioMetric 口径(与 python/openslo.py 同构)。

import (
	"fmt"
	"time"
)

// Duration 是 duration-shorthand。规范允许的后缀 m/h/d/w/M/Q/Y,其中
// M/Q/Y 是日历单位;规范原文明确不规定各后缀如何实现,本实现把 m/h/d/w
// 当固定时长、M/Q/Y 当日历月数,并在 README 标注该口径。
type Duration struct {
	Text       string
	Postfix    string
	Number     int
	Seconds    float64
	Months     int
	IsCalendar bool
}

var fixedSeconds = map[string]float64{
	"m": 60,
	"h": 3600,
	"d": 86400,
	"w": 604800,
}

var calMonths = map[string]int{"M": 1, "Q": 3, "Y": 12}

// ParseDuration 解析 shorthand,非法返回 error。
func ParseDuration(text string) (Duration, error) {
	d := Duration{Text: text}
	if len(text) < 2 {
		return d, fmt.Errorf("bad duration %q", text)
	}
	d.Postfix = text[len(text)-1:]
	n := 0
	if _, err := fmt.Sscanf(text[:len(text)-1], "%d", &n); err != nil {
		return d, fmt.Errorf("bad duration %q", text)
	}
	d.Number = n
	if n <= 0 {
		return d, fmt.Errorf("duration must be positive: %q", text)
	}
	if s, ok := fixedSeconds[d.Postfix]; ok {
		d.Seconds = float64(n) * s
		return d, nil
	}
	if m, ok := calMonths[d.Postfix]; ok {
		d.IsCalendar = true
		d.Months = n * m
		return d, nil
	}
	return d, fmt.Errorf("unknown postfix %q", d.Postfix)
}

func isLeap(y int) bool {
	return y%4 == 0 && (y%100 != 0 || y%400 == 0)
}

func daysInMonth(y, m int) int {
	last := []int{31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31}[m-1]
	if m == 2 && isLeap(y) {
		return 29
	}
	return last
}

// AddMonths 日历月份加法,日号超出目标月长度时钳到月末(1/31 + 1M = 2/28)。
func AddMonths(t time.Time, months int) time.Time {
	y := t.Year() + (t.Month()-1+time.Month(months))/12
	m := (t.Month()-1+time.Month(months))%12 + 1
	d := t.Day()
	if last := daysInMonth(int(y), int(m)); d > last {
		d = last
	}
	return time.Date(int(y), m, d, t.Hour(), t.Minute(), t.Second(), 0, t.Location())
}

// RollingWindow rolling 窗口 (now-duration, now],长度恒定。
func RollingWindow(now time.Time, d Duration) (time.Time, time.Time, error) {
	if d.IsCalendar {
		return now, now, fmt.Errorf("rolling window needs a fixed-length duration")
	}
	return now.Add(-time.Duration(d.Seconds * float64(time.Second))), now, nil
}

// CalendarWindow calendar-aligned 窗口,长度随日历变化(1M 在 2 月 28 天、7 月 31 天)。
func CalendarWindow(now, start time.Time, d Duration) (time.Time, time.Time, error) {
	if !d.IsCalendar {
		return now, now, fmt.Errorf("calendar window needs M/Q/Y")
	}
	lo := start
	for {
		hi := AddMonths(lo, d.Months)
		if !lo.After(now) && now.Before(hi) {
			return lo, hi, nil
		}
		lo = hi
	}
}

// SLIFromGood `{good, total}` 形态。
func SLIFromGood(good, total float64) (float64, bool) {
	if total == 0 {
		return 0, false
	}
	return good / total, true
}

// SLIFromBad `{bad, total}` 形态。
func SLIFromBad(bad, total float64) (float64, bool) {
	if total == 0 {
		return 0, false
	}
	return 1 - bad/total, true
}

// SLIFromRaw `raw` 形态:rawType 决定要不要取补。
// success → 存的就是 good/total;failure → 存的是 bad/total,必须取补。
func SLIFromRaw(raw float64, rawType string) (float64, error) {
	switch rawType {
	case "success":
		return raw, nil
	case "failure":
		return 1 - raw, nil
	}
	return 0, fmt.Errorf("rawType must be success or failure, got %q", rawType)
}

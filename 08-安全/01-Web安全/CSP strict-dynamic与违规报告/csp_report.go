package main

// Violation 是 CSP 违规对象（§2.4 / §5 的 body 字段集合）。
type Violation struct {
	DocumentURL        string
	Referrer           string
	BlockedURL         string
	EffectiveDirective string
	OriginalPolicy     string
	SourceFile         string
	Sample             string
	Disposition        string
	StatusCode         int
	LineNumber         int
	ColumnNumber       int
}

// AsReportBody 生成 application/reports+json 里 csp-violation 的 body。
func (v *Violation) AsReportBody() map[string]interface{} {
	body := map[string]interface{}{
		"documentURL":        v.DocumentURL,
		"referrer":           v.Referrer,
		"disposition":        v.Disposition,
		"effectiveDirective": v.EffectiveDirective,
		"originalPolicy":     v.OriginalPolicy,
		"statusCode":         v.StatusCode,
	}
	if v.BlockedURL != "" {
		body["blockedURL"] = v.BlockedURL
	}
	if v.SourceFile != "" {
		body["sourceFile"] = v.SourceFile
		body["lineNumber"] = v.LineNumber
		body["columnNumber"] = v.ColumnNumber
	}
	if v.Sample != "" {
		body["sample"] = v.Sample
	}
	return body
}

// Global 是 realm 的全局对象。
type Global struct {
	DocumentURL  string
	StatusCode   int
	Referrer     string
	SourceFile   string
	LineNumber   int
	ColumnNumber int
	Violations   []*Violation
}

// Policy 是一条 CSP。
type Policy struct {
	Serialized  string
	Disposition string
	Directives  map[string]string
}

// SourceList 取某指令的源列表。
func (p *Policy) SourceList(name string) []string {
	return ParseSourceList(p.Directives[name])
}

// ReportingEndpoints 返回报告端点；report-uri 标注为 deprecated。
func (p *Policy) ReportingEndpoints() []string {
	var eps []string
	if v := p.Directives["report-to"]; v != "" {
		eps = append(eps, v)
	}
	if v := p.Directives["report-uri"]; v != "" {
		eps = append(eps, "deprecated")
	}
	return eps
}

// CreateViolation 实现 §2.4.1 + §2.4：sample 取前 40 字符，
// 外部文件违规（source 为空）不带 sample。
func CreateViolation(g *Global, p *Policy, directive, source, blockedURL string) *Violation {
	sample := ""
	if len(source) > 40 {
		sample = source[:40]
	} else {
		sample = source
	}
	sourceFile, line, col := "", 0, 0
	if source != "" {
		sourceFile, line, col = g.SourceFile, g.LineNumber, g.ColumnNumber
	}
	v := &Violation{DocumentURL: g.DocumentURL, Referrer: g.Referrer,
		BlockedURL: blockedURL, EffectiveDirective: directive,
		OriginalPolicy: p.Serialized, SourceFile: sourceFile, Sample: sample,
		Disposition: p.Disposition, StatusCode: g.StatusCode,
		LineNumber: line, ColumnNumber: col}
	g.Violations = append(g.Violations, v)
	return v
}

// IsStrictCSP 实现 §8.5 的 Strict CSP 判据。
func IsStrictCSP(p *Policy) bool {
	src := p.SourceList("script-src")
	if len(src) == 0 {
		src = p.SourceList("default-src")
	}
	if len(src) == 0 {
		return false
	}
	hasDynamic, onlyNonceOrHash := false, true
	for _, e := range src {
		switch {
		case isKeyword(e, "strict-dynamic"):
			hasDynamic = true
		case isNonceSource(e) || isHashSource(e):
		default:
			onlyNonceOrHash = false
		}
	}
	base := p.SourceList("base-uri")
	baseOK := len(base) == 1 && (isKeyword(base[0], "self") || isKeyword(base[0], "none"))
	return hasDynamic && onlyNonceOrHash && baseOK
}

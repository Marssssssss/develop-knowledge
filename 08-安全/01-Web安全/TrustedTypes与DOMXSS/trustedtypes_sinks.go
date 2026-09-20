package main

import "strings"

// SetInnerHTML 是 Element.innerHTML 汇点。
func SetInnerHTML(g *Global, v interface{}) (string, error) {
	return GetTrustedTypeCompliantString(g, "TrustedHTML", v,
		"Element innerHTML", "script")
}

// SetScriptSrc 是 HTMLScriptElement.src 汇点。
func SetScriptSrc(g *Global, v interface{}) (string, error) {
	return GetTrustedTypeCompliantString(g, "TrustedScriptURL", v,
		"HTMLScriptElement src", "script")
}

// SetScriptText 是 HTMLScriptElement text 汇点。
func SetScriptText(g *Global, v interface{}) (string, error) {
	return GetTrustedTypeCompliantString(g, "TrustedScript", v,
		"HTMLScriptElement text", "script")
}

// GetTrustedTypeDataForAttribute 实现 §3.8 的属性表。
func GetTrustedTypeDataForAttribute(elementNS, tag, attr, attrNS string) (string, string, bool) {
	if attrNS == "" && (elementNS == NSHTML || elementNS == NSSVG || elementNS == NSMathML) &&
		strings.HasPrefix(attr, "on") && len(attr) > 2 {
		return "TrustedScript", "Element " + attr, true
	}
	if attrNS == "" {
		if tag == "iframe" && attr == "srcdoc" {
			return "TrustedHTML", "HTMLIFrameElement srcdoc", true
		}
		if tag == "script" && attr == "src" && elementNS == NSHTML {
			return "TrustedScriptURL", "HTMLScriptElement src", true
		}
		if tag == "script" && attr == "href" && elementNS == NSSVG {
			return "TrustedScriptURL", "SVGScriptElement href", true
		}
	}
	if attrNS == NSXLink && tag == "script" && attr == "href" {
		return "TrustedScriptURL", "SVGScriptElement href", true
	}
	return "", "", false
}

// SetAttribute 实现 §3.7 的属性赋值路径。
func SetAttribute(g *Global, elementNS, tag, attr string, v interface{},
	attrNS string) (string, error) {
	expected, sink, has := GetTrustedTypeDataForAttribute(elementNS, tag, attr, attrNS)
	if !has {
		return stringify(v), nil
	}
	return GetTrustedTypeCompliantString(g, expected, v, sink, "script")
}

// RequireTTPreNavigationCheck 实现 §4.2.1.1。
func RequireTTPreNavigationCheck(g *Global, url string) (string, string) {
	if !strings.HasPrefix(url, "javascript:") {
		return "Allowed", url
	}
	encoded := url[len("javascript:"):]
	converted, err := ProcessValueWithDefaultPolicy(g, "TrustedScript", encoded,
		"Location href")
	if err != nil || converted == nil || converted.TypeName != "TrustedScript" {
		return "Blocked", url
	}
	return "Allowed", "javascript:" + converted.Data
}

// ShouldSinkMismatchBeBlocked 实现 §4.2.4。
func ShouldSinkMismatchBeBlocked(g *Global, sink, sinkGroup, source string) string {
	result := "Allowed"
	sample := source
	if sink == "Function" {
		for _, prefix := range []string{"async function* anonymous",
			"async function anonymous", "function* anonymous", "function anonymous"} {
			if strings.HasPrefix(sample, prefix) {
				sample = sample[len(prefix):]
				break
			}
		}
	}
	for _, p := range g.CSPList {
		d, has := p.Directives["require-trusted-types-for"]
		if !has || !strings.Contains(d, sinkGroup) {
			continue
		}
		g.report(&Violation{Directive: "require-trusted-types-for",
			Disposition: p.Disposition, Resource: "trusted-types-sink",
			Sample: sink + "|" + truncate(sample, 40)})
		if p.Disposition == "enforce" {
			result = "Blocked"
		}
	}
	return result
}

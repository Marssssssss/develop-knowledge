// Go 版模板引擎 —— 用 stdlib text/template(等价于 Go html/template 安全行为)
// 等价于 Jinja2 的核心控制结构,但语法用 Go 风格 {{ .Var }} {{ range }} {{ if }}
//
// 来源:
// - Go 标准库 text/template (pkg.go.dev/text/template)
//   Action 语法:{{ pipeline }}, {{ range pipeline }}, {{ if pipeline }}, {{ with pipeline }}
// - "A template is ... text interspersed with actions ... delimited by {{ and }}"
// - 与 Jinja2 等价的语法对照:
//   Jinja2: {{ var }}      → Go: {{ .Var }}
//   Jinja2: {% for x in y %} → Go: {{ range .Y }}{{ .X }}{{ end }}
//   Jinja2: {% if cond %}   → Go: {{ if .Cond }}...{{ end }}
//   Jinja2: {{ x|upper }}   → Go: {{ .X | upper }} (text/template has func map)
//   Jinja2: {% set x = 1 %} → Go: {{ $x := 1 }}

package main

import (
	"bytes"
	"fmt"
	"strings"
	"text/template"
)

// -----------------------------------------------------------------------------
// 自定义函数映射(对应 Jinja 的 filter)
// -----------------------------------------------------------------------------

var funcMap = template.FuncMap{
	"upper": strings.ToUpper,
	"lower": strings.ToLower,
	"title": strings.Title,
	"default": func(arg, val interface{}) interface{} {
		// 类似 Jinja 的 |default('x')
		if val == nil || val == "" || val == 0 {
			return arg
		}
		return val
	},
	"replace": strings.ReplaceAll,
	"trim":    strings.TrimSpace,
}

type Persona struct {
	Title       string
	Role        string
	Env         string
	ShowSkills  bool
	Skills      []string
	Owner       string
}

const TPL = `# {{.Title}}

Hi, I'm {{.Role | default "anonymous" | title}}.

{{if .ShowSkills}}
Skills:
{{range .Skills -}}
- {{. | upper}}
{{end -}}
{{else -}}
(No public skills list.)
{{end -}}

Env: {{.Env}}.
Maintainer: {{.Owner}}
`

func main() {
	fmt.Println("=== 模板引擎 demo (Go stdlib text/template) ===\n")
	fmt.Println("--- Template ---")
	fmt.Println(TPL)

	data := Persona{
		Title:      "About me",
		Role:       "backend engineer",
		Env:        "prod",
		ShowSkills: true,
		Skills:     []string{"Python", "Go", "Rust"},
		Owner:      "mars",
	}

	t := template.Must(template.New("persona").Funcs(funcMap).Parse(TPL))
	var buf bytes.Buffer
	if err := t.Execute(&buf, data); err != nil {
		fmt.Println("render error:", err)
		return
	}
	fmt.Println("--- Rendered ---")
	fmt.Println(buf.String())

	// 双 demo:ShowSkills=false
	fmt.Println("--- with ShowSkills=false ---")
	data.ShowSkills = false
	buf.Reset()
	t.Execute(&buf, data)
	fmt.Println(buf.String())

	// 第三个 demo: 缺失 Role,触发 |default filter
	fmt.Println("--- with Role missing (filter default) ---")
	data.ShowSkills = true
	data.Role = ""
	buf.Reset()
	t.Execute(&buf, data)
	fmt.Println(buf.String())
}

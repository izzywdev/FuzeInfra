// Renders an Argo CD notifications template the way the notifications engine
// does: Go text/template over the Application as an unstructured map, with a
// sprig-compatible toJson. Used by tests/test_argo_alert.py to prove the
// argo-out-of-sync payload is valid JSON for real Application shapes.
//
// Usage: go run render_notification_template.go <template-file> <app.json>
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"text/template"
)

func toJson(v interface{}) string {
	b, err := json.Marshal(v)
	if err != nil {
		return ""
	}
	return string(b)
}

func main() {
	tmplText, err := os.ReadFile(os.Args[1])
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	raw, err := os.ReadFile(os.Args[2])
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	var app map[string]interface{}
	if err := json.Unmarshal(raw, &app); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	t, err := template.New("body").Funcs(template.FuncMap{"toJson": toJson}).Parse(string(tmplText))
	if err != nil {
		fmt.Fprintln(os.Stderr, "parse:", err)
		os.Exit(3)
	}
	if err := t.Execute(os.Stdout, map[string]interface{}{"app": app}); err != nil {
		fmt.Fprintln(os.Stderr, "execute:", err)
		os.Exit(4)
	}
}

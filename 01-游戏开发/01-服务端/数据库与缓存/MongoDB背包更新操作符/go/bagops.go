// bagops.go — 与 python/bagops.py 同语义的 Go 复刻(静态审查用)。
package main

import (
	"errors"
	"fmt"
	"strings"
)

func setPath(doc map[string]interface{}, path string, value interface{}) {
	parts := strings.Split(path, ".")
	cur := doc
	for _, p := range parts[:len(parts)-1] {
		next, ok := cur[p].(map[string]interface{})
		if !ok {
			next = map[string]interface{}{}
			cur[p] = next
		}
		cur = next
	}
	cur[parts[len(parts)-1]] = value
}

// IncPath:$inc 语义——null 报错;不存在则置为增量。
func IncPath(doc map[string]interface{}, path string, delta int) error {
	cur, ok := doc[path].(int)
	if !ok && doc[path] != nil {
		if _, exists := doc[path]; exists {
			return errors.New("$inc on null field: " + path)
		}
	}
	doc[path] = cur + delta
	return nil
}

type item struct {
	ID    string
	Count int
}

// PositionalFirst:查询匹配的**第一个**元素(位置 $ 语义)。
func PositionalFirst(items []item, condID string, newCount int) (*item, bool) {
	for i := range items {
		if items[i].ID == condID {
			items[i].Count = newCount
			return &items[i], true
		}
	}
	return nil, false
}

func main() {
	p := map[string]interface{}{}
	setPath(p, "bag.slots", 30)
	fmt.Println(p) // map[bag:map[slots:30]]

	if err := IncPath(p, "slots", 0); err != nil { // bag 不是 int → 视为非法
		fmt.Println("inc err:", err)
	}
	bag := []item{{"potion", 3}, {"potion", 2}, {"key", 1}}
	it, _ := PositionalFirst(bag, "potion", 99)
	fmt.Println(it, "second group untouched:", bag[1].Count) // 2
}

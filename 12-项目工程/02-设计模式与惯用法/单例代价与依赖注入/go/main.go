// main.go — 与 python/sidi.py 同语义的 Go 复刻(静态审查用)。
package main

import (
	"fmt"
	"sync"
)

type Config struct{ Source string }

var (
	instance  *Config
	initCalls int
	mu        sync.Mutex
)

// GetConfig:线程安全懒加载单例(锁内检查)。
func GetConfig() *Config {
	mu.Lock()
	defer mu.Unlock()
	if instance == nil {
		initCalls++
		instance = &Config{Source: "prod"}
	}
	return instance
}

// 定位器:每个使用者都依赖定位器本身。
type Locator struct{ services map[string]string }

func (l *Locator) Get(name string) string { return l.services[name] }

type ClientWithLocator struct {
	db string
}

func NewClientWithLocator(loc *Locator) *ClientWithLocator {
	return &ClientWithLocator{db: loc.Get("db")}
}

// 注入:构造签名即依赖清单,mock 直接当参数传。
type ClientWithDI struct{ db string }

func NewClientWithDI(db string) *ClientWithDI { return &ClientWithDI{db: db} }

func main() {
	a, b := GetConfig(), GetConfig()
	fmt.Println("same instance:", a == b, "initCalls:", initCalls)
	c := NewClientWithDI("TestDB")
	fmt.Println("DI db =", c.db, "(依赖可见于签名)")
}

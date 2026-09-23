package main

import "fmt"

const (
	// src/config.c:3650/3654 与 redis.conf:2358-2359
	zsetMaxListpackEntries = 128
	zsetMaxListpackValue   = 64

	encodingListpack = "listpack"
)

// ------------------------------------------------------- 编码选择与转换

// ZSetObject 只建模编码选择，不建模 listpack 字节布局。
type ZSetObject struct {
	Encoding string
	Members  map[string]float64
	Zsl      *ZSkipList
}

func newZSetObject(encoding string) *ZSetObject {
	o := &ZSetObject{Encoding: encoding, Members: map[string]float64{}}
	if encoding == encodingSkiplist {
		o.Zsl = newZSkipList()
	}
	return o
}

func (o *ZSetObject) Length() int {
	if o.Encoding == encodingListpack {
		return len(o.Members)
	}
	return o.Zsl.length
}

// ZsetTypeMaybeConvert 对应 t_zset.c:1428：只看 size_hint，不看 val_len_hint。
func ZsetTypeMaybeConvert(o *ZSetObject, sizeHint int) {
	if o.Encoding == encodingListpack && sizeHint > zsetMaxListpackEntries {
		o.Convert(encodingSkiplist)
	}
}

// Zadd 对应 t_zset.c:1650 的转换判定。safeToAdd 对应 lpSafeToAdd。
func Zadd(o *ZSetObject, ele string, score float64, safeToAdd bool) {
	if o.Encoding == encodingListpack {
		if len(o.Members)+1 > zsetMaxListpackEntries ||
			len(ele) > zsetMaxListpackValue || !safeToAdd {
			o.Convert(encodingSkiplist)
		} else {
			o.Members[ele] = score
			return
		}
	}
	if o.Encoding == encodingSkiplist {
		o.Zsl.InsertNode(newNode(1, score, ele))
	}
}

// ZsetConvertToListpackIfNeeded 对应 t_zset.c:1523：三个条件同时成立才反向转换。
func ZsetConvertToListpackIfNeeded(o *ZSetObject, maxEleLen int, safeToAdd bool) {
	if o.Encoding == encodingListpack {
		return
	}
	if o.Zsl.length <= zsetMaxListpackEntries && maxEleLen <= zsetMaxListpackValue && safeToAdd {
		o.Convert(encodingListpack)
	}
}

// Convert 在两种编码之间搬移数据。
func (o *ZSetObject) Convert(encoding string) {
	if o.Encoding == encoding {
		return
	}
	if encoding == encodingSkiplist {
		zsl := newZSkipList()
		keys := make([]string, 0, len(o.Members))
		for k := range o.Members {
			keys = append(keys, k)
		}
		for i := 0; i < len(keys); i++ {
			for j := i + 1; j < len(keys); j++ {
				a, b := keys[i], keys[j]
				if o.Members[a] > o.Members[b] || (o.Members[a] == o.Members[b] && a > b) {
					keys[i], keys[j] = keys[j], keys[i]
				}
			}
		}
		for _, k := range keys {
			zsl.InsertNode(newNode(1, o.Members[k], k))
		}
		o.Zsl = zsl
		o.Members = map[string]float64{}
		o.Encoding = encodingSkiplist
		return
	}
	members := map[string]float64{}
	for x := o.Zsl.header.level[0].forward; x != nil; x = x.level[0].forward {
		members[x.ele] = x.score
	}
	o.Members = members
	o.Zsl = nil
	o.Encoding = encodingListpack
}

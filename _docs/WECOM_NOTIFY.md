# WECOM_NOTIFY.md — 定时巡检后的企业微信通知规范

> 规则:每轮定时巡检(每 1 小时)完成后,把本轮"添加了什么知识"通过企业微信群机器人 webhook 推送给用户。
> 配置独立于 Git 同步:即使本轮无改动(被 `.gitignore` 过滤),通知也可能照常发送。

## 一、整体流程

```
┌──────────────────────────────────────────────────────────────┐
│ 定时巡检(automation)                                        │
│   1. 定位领域 → 2. 查权威 → 3. 写 3 个 demo → 4. 更新索引     │
│   5. 写 daily log → 6. git add + commit + push                │
│   7. 读取 _docs/.wecom_webhook → curl POST 群机器人            │
│         ↓ 失败                                                │
│      仅写一行 `notify_failed: <原因>` 到 SEARCH_PROGRESS.md,   │
│      不阻塞后续轮次。                                          │
└──────────────────────────────────────────────────────────────┘
```

## 二、仓库与凭据

| 项 | 值 |
|---|---|
| 本地路径 | `D:\开发研究\` |
| Webhook 凭据文件 | `_docs/.wecom_webhook`(**不进 git**,见 `.gitignore`) |
| 凭据格式 | JSON 单行,字段 `webhook_url`(必填)、`at_mobiles`(可选,数组)、`at_all`(可选,布尔) |
| 凭据缺失处理 | 文件不存在或 `webhook_url` 为空 → **跳过通知**,记日志 `notify_skipped: 凭据未配置`,**不重试**;等用户填好 URL 后下一轮自动启用 |
| 凭据错误处理 | HTTP 非 200 / 响应 `errcode != 0` → **跳过通知**,记 `notify_failed: <errcode>`,**不阻塞** |

## 三、webhook 文件格式

文件 `_docs/.wecom_webhook`(单行 JSON,UTF-8,**无 BOM**):

```json
{"webhook_url":"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx","at_mobiles":[],"at_all":false}
```

如何获取 webhook URL:

1. 在企业微信里打开目标群 → 右上角 `···` → **群机器人** → **添加** → 选「自定义」
2. 给机器人起名(如 `开发巡检`),可开启「@所有人」(若想让每条消息都 @ 全员,本文件 `at_all=true`)
3. 复制生成的 webhook URL,**只出现一次**,请妥善保存
4. 把 URL 粘贴到 `_docs/.wecom_webhook` 中替换占位

> ⚠️ webhook URL 等同于群消息发送权限,**不要提交到任何公开仓库**。本项目 `.gitignore` 已显式排除 `_docs/.wecom_webhook`。

## 四、消息体(简洁版)

企业微信群机器人支持 `text` / `markdown` / `markdown_v2` / `image` / `news` / `template_card` 等类型。本场景用 **markdown** —— 手机端可读、可加粗、链接可点。

```json
{
  "msgtype": "markdown",
  "markdown": {
    "content": "## 📚 巡检报告 2026-09-11 17:00\n> 领域:**01-游戏开发/服务端** · 推进 **+3**\n\n**新增 demo:**\n1. **IO 多路复用 · select** — `01-游戏开发/01-服务端/网络编程/IO多路复用/select/`\n   > 核心:fd_set 位图轮询,1024 上限,O(n) 扫描\n2. **IO 多路复用 · epoll(LT/ET)** — `01-游戏开发/01-服务端/网络编程/IO多路复用/epoll/`\n   > 核心:红黑树 + 就绪链表,O(1) 唤醒,ET 需非阻塞 + 循环读\n3. **IO 多路复用 · kqueue** — `01-游戏开发/01-服务端/网络编程/IO多路复用/kqueue/`\n   > 核心:BSD 事件驱动,kevent 注册/返回双工\n\n[查看进度 →](SEARCH_PROGRESS.md)"
  }
}
```

可选追加 `@` 提醒(读文件中的 `at_mobiles` / `at_all`):

```json
{
  "msgtype": "markdown",
  "markdown": { "content": "..." },
  "mentioned_list": ["13800138000"],
  "mentioned_mobile_list": ["13800138000"]
}
```

## 五、curl 模板

```bash
WEBHOOK_FILE="D:/开发研究/_docs/.wecom_webhook"
if [ ! -s "$WEBHOOK_FILE" ]; then
  echo "notify_skipped: 凭据未配置,跳过企业微信通知"
  exit 0
fi

# 1) 读 webhook_url(用 jq 提取;若系统无 jq,用 sed/awk 替代)
WEBHOOK_URL=$(jq -r '.webhook_url' "$WEBHOOK_FILE")
[ -z "$WEBHOOK_URL" ] || [ "$WEBHOOK_URL" = "null" ] && {
  echo "notify_skipped: webhook_url 为空"
  exit 0
}

# 2) 组装消息体(markdown 内容见第四节)
PAYLOAD=$(cat <<'EOF'
{"msgtype":"markdown","markdown":{"content":"## 📚 巡检报告 YYYY-MM-DD HH:MM\n> 领域:**XX** · 推进 +N\n\n**新增 demo:**\n1. **A** — `path/a`\n2. **B** — `path/b`\n3. **C** — `path/c`"}}
EOF
)

# 3) POST(超时 10 秒,失败不阻塞主流程)
HTTP_CODE=$(curl -sS -o /tmp/wecom_resp.json -w "%{http_code}" \
  -X POST "$WEBHOOK_URL" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d "$PAYLOAD" \
  --max-time 10 2>&1) || {
    echo "notify_failed: curl error"
    exit 0
}

# 4) 检查响应
if [ "$HTTP_CODE" = "200" ]; then
  ERRCODE=$(jq -r '.errcode' /tmp/wecom_resp.json 2>/dev/null || echo "-1")
  if [ "$ERRCODE" = "0" ]; then
    echo "notify_ok: HTTP 200, errcode=0"
  else
    echo "notify_failed: HTTP 200, errcode=$ERRCODE, errmsg=$(jq -r '.errmsg' /tmp/wecom_resp.json)"
  fi
else
  echo "notify_failed: HTTP $HTTP_CODE"
fi
```

## 六、内容模板(由 Agent 每轮填充)

`YYYY-MM-DD HH:MM` — 当前本地时间(GMT+8)。
`领域` — 对应 `_docs/SEARCH_PROGRESS.md` 索引表里的中文名(如「服务端」「渲染」)。
`+N` — 本轮新增 demo 数(0/1/2/3)。
`A/B/C` + 路径 + 1 句核心机制总结(≤ 40 字),从 README 简介提炼。

## 七、失败回退

| 场景 | 处理 |
|---|---|
| webhook 文件不存在或为空 | 跳过通知,记 `notify_skipped: 凭据未配置`,**不阻塞**主流程 |
| curl 失败 / 超时 | 跳过通知,记 `notify_failed: curl`,**不阻塞** |
| HTTP 非 200 | 跳过通知,记 `notify_failed: HTTP <code>` |
| HTTP 200 但 errcode ≠ 0 | 跳过通知,记 `notify_failed: errcode=<code>, errmsg=<msg>` |
| **连续 5 轮失败** | 在 SEARCH_PROGRESS 告警 `notify: 连续 5 轮失败,请检查 webhook 配置` |

原则:**通知失败永远不阻塞巡检主流程**;只允许「跳过 + 记日志」。

## 八、与其它规则文档的关系

- 索引/调度:`SEARCH_PROGRESS.md`
- 推进配额:`SCHEDULE_QUOTA.md`
- Git 同步:`GIT_SYNC.md`(通知是 Git 同步之后的第 7 步)
- 内容来源铁律:见 `DEMO_TEMPLATE.md` 第〇节 —— **即使是给企业微信发的摘要,也要基于实际查到的权威资料,不要凭空发挥**。

## 九、变更记录

| 日期 | 变更 |
|---|---|
| 2026-09-11 | 首版:每轮巡检完成后用群机器人 webhook 推送「领域 + 3 个 demo 路径 + 1 句核心机制」摘要 |
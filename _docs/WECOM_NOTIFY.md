# WECOM_NOTIFY.md — 定时巡检后的企业微信通知规范

> 规则:每个定点槽(全天 12 条任务:每日 `00:00 02:00 … 22:00` 各一次)巡检完成后,把本轮"添加了什么知识"通过企业微信群机器人 webhook 推送给用户。
> 配置独立于 Git 同步:即使本轮无改动(被 `.gitignore` 过滤),通知也可能照常发送。

## 一、整体流程

```
┌──────────────────────────────────────────────────────────────┐
│ 定时巡检(automation)                                        │
│   1. 定位领域 → 2. 查权威 → 3. 写 5 个 demo → 4. 更新索引     │
│   5. 写 daily log → 6. git add + commit + push                │
│   7. 读取 _docs/.wecom_webhook → curl POST 群机器人            │
│         ↓ 失败                                                │
│      仅写一行 `notify_failed: <原因>` 到 STATE.md「本轮状态」或 archive/schedule.md,   │
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

**格式铁律(2026-09-12 起)**:

- 列表项(`1./2./3.`)的**核心机制描述必须拼接在同行尾部**,用 ` · 核心:...` 分隔
- **禁止**用换行 + 子缩进引用(`\n   > 核心:...`)——企业微信 markdown 渲染器对"列表项内嵌引用块"跨客户端(Android/iOS/PC)表现不一致,会出现奇怪的二级缩进和空白
- **禁止**用子列表(`   -`)做核心机制描述,同理会触发嵌套渲染异常

```json
{
  "msgtype": "markdown",
  "markdown": {
    "content": "## 📚 巡检报告 2026-09-11 17:00\n> 领域:**01-游戏开发/服务端** · 推进 **+3**\n\n**新增 demo:**\n1. **IO 多路复用 · select** — `01-游戏开发/01-服务端/网络编程/IO多路复用/select/` · 核心:fd_set 位图轮询,1024 上限,O(n) 扫描\n2. **IO 多路复用 · epoll(LT/ET)** — `01-游戏开发/01-服务端/网络编程/IO多路复用/epoll/` · 核心:红黑树 + 就绪链表,O(1) 唤醒,ET 需非阻塞 + 循环读\n3. **IO 多路复用 · kqueue** — `01-游戏开发/01-服务端/网络编程/IO多路复用/kqueue/` · 核心:BSD 事件驱动,kevent 注册/返回双工\n\n[查看进度 →](STATE.md)"
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

> ⚠️ **2026-09-12 修订(防重复推送)**:旧模板用 `-o /tmp/wecom_resp.json` 写响应体,在 Git Bash 下 `/tmp` 写入有 quirk(curl exit 23),导致"服务端已收到、本地误判失败 → 重试 → 群里重复推送"。现改为**命令替换直接捕获响应体,不落任何临时文件**。

```bash
WEBHOOK_FILE="D:/开发研究/_docs/.wecom_webhook"
if [ ! -s "$WEBHOOK_FILE" ]; then
  echo "notify_skipped: 凭据未配置,跳过企业微信通知"
  exit 0
fi

# 1) 读 webhook_url(统一用 sed 提取;本机无 jq,2026-09-13 实测 jq 缺失会 exit 127)
WEBHOOK_URL=$(sed -n 's/.*"webhook_url":"\([^"]*\)".*/\1/p' "$WEBHOOK_FILE")
[ -z "$WEBHOOK_URL" ] || [ "$WEBHOOK_URL" = "null" ] && {
  echo "notify_skipped: webhook_url 为空"
  exit 0
}

# 2) 组装消息体(markdown 内容见第四节)
PAYLOAD=$(cat <<'EOF'
{"msgtype":"markdown","markdown":{"content":"## 📚 巡检报告 YYYY-MM-DD HH:MM\n> 领域:**XX** · 推进 +N\n\n**新增 demo:**\n1. **A** — `path/a`\n2. **B** — `path/b`\n3. **C** — `path/c`"}}
EOF
)

# 3) POST:响应体与 HTTP 状态码一起用命令替换捕获(禁止 -o 写文件!)
#    每轮 curl 只调用【一次】,任何结果都不重试(见 §七「重试禁令」)
RAW=$(curl -sS -w $'\n%{http_code}' \
  -X POST "$WEBHOOK_URL" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d "$PAYLOAD" \
  --max-time 10 2>&1)
CURL_EXIT=$?

if [ $CURL_EXIT -ne 0 ]; then
  echo "notify_failed: curl_error(exit=$CURL_EXIT)"
  exit 0
fi

# 4) 拆分:最后一行是 http_code,前面是响应体
HTTP_CODE=$(printf '%s' "$RAW" | tail -n1)
RESP_BODY=$(printf '%s' "$RAW" | sed '$d')

if [ "$HTTP_CODE" = "200" ]; then
  ERRCODE=$(printf '%s' "$RESP_BODY" | sed -n 's/.*"errcode":\([0-9]*\).*/\1/p')
  if [ "$ERRCODE" = "0" ]; then
    echo "notify_ok: HTTP 200, errcode=0"
  else
    echo "notify_failed: HTTP 200, errcode=$ERRCODE, body=$RESP_BODY"
  fi
else
  echo "notify_failed: HTTP $HTTP_CODE"
fi
```

## 六、内容模板(由 Agent 每轮填充)

`YYYY-MM-DD HH:MM` — 当前本地时间(GMT+8)。
`领域` — 对应 [`STATE.md`](./STATE.md) §1 索引表里的中文名(如「服务端」「渲染」)。
`+N` — 本轮新增 demo 数(0/1/2/3)。
`A/B/C` + 路径 + 1 句核心机制总结(≤ 40 字),从 README 简介提炼。

**拼接格式**:每个 demo 必须写成单行 `**标题** — \`路径\` · 核心:<一句话>`,**禁止**换行 + 子缩进(`\n   > ...` / `\n   - ...`)。完整示例与禁止写法见第四节。

## 七、失败回退

| 场景 | 处理 |
|---|---|
| webhook 文件不存在或为空 | 跳过通知,记 `notify_skipped: 凭据未配置`,**不阻塞**主流程 |
| curl 失败 / 超时 | 跳过通知,记 `notify_failed: curl`,**不阻塞** |
| HTTP 非 200 | 跳过通知,记 `notify_failed: HTTP <code>` |
| HTTP 200 但 errcode ≠ 0 | 跳过通知,记 `notify_failed: errcode=<code>, errmsg=<msg>` |
| **连续 5 轮失败** | 在 STATE.md「本轮状态」告警 `notify: 连续 5 轮失败,请检查 webhook 配置` |

原则:**通知失败永远不阻塞巡检主流程**;只允许「跳过 + 记日志」。

**重试禁令(2026-09-12 起强制)**:群机器人 webhook **没有幂等键**,重试 = 群里重复消息。因此每轮 curl **只调用一次**,无论结果是 curl 报错、超时、HTTP 非 200 还是 errcode ≠ 0,都**禁止重发**;宁可漏发一轮(记 `notify_failed`),不可重发一条。漏发由下一轮正常推送覆盖,不影响主流程。

## 八、与其它规则文档的关系

- 索引/调度:[`STATE.md`](./STATE.md)(主,≤ 5 KB)+ [`archive/schedule.md`](./archive/schedule.md)(日志全本)
- 推进配额:`SCHEDULE_QUOTA.md`
- Git 同步:`GIT_SYNC.md`(通知是 Git 同步之后的第 7 步)
- 内容来源铁律:见 `DEMO_TEMPLATE.md` 第〇节 —— **即使是给企业微信发的摘要,也要基于实际查到的权威资料,不要凭空发挥**。

## 九、变更记录

| 日期 | 变更 |
|---|---|
| 2026-09-11 | 首版:每轮巡检完成后用群机器人 webhook 推送「领域 + 3 个 demo 路径 + 1 句核心机制」摘要 |
| 2026-09-11 | 状态文件拆分同步:SEARCH_PROGRESS.md → STATE.md + archive/,本文档 4 处引用一并更新 |
| 2026-09-12 | 第十节「状态回写硬约束」:`notify` 字段改为三态枚举(`ok` / `failed <reason>` / `skipped <reason>`),禁止写「待发」 |
| 2026-09-12 | 第四节消息体格式铁律:核心机制从「列表项 + 子缩进引用(`\n   > 核心:...`)」改为「列表项尾部拼接(`· 核心:...`)」,消除企业微信 markdown 渲染器跨客户端(Android/iOS/PC)的二级缩进与空白异常 |
| 2026-09-12 | 配额上调 3→5 demo/轮:第三节流程图「写 3 个 demo」→「写 5 个 demo」;第四节示例消息体改为 5 demo;第六节「+N」范围 0/1/2/3 → 0-5;全文「每 1 小时」→「每 1.5 小时」 |
| 2026-09-13 | §五 模板去除 jq 依赖(本机 Git Bash 无 jq,实测 exit 127 致 WEBHOOK_URL 为空),统一 sed 提取 |
| 2026-09-12 | **防重复推送专项治理**(用户反馈"巡检报告重复发"):① §五 curl 模板废弃 `-o /tmp/...` 写文件(Git Bash /tmp quirk exit 23 致误判失败重试),改命令替换捕获响应体;② §七 新增「重试禁令」:webhook 无幂等键,curl 每轮仅一次;③ §10.5 修订:状态回写违规只补回写、禁止补推;④ 新增 §十一 幂等防线(发送前查 schedule.md 已有 `notify: ok` 则跳过 + `last_run` < 80 分钟本轮直接退出 + 开局即更新 `last_run` 当占位锁) |
| 2026-09-13 | 调度频率 **1.5h → 2h**(单任务化):文档首段 "每 1.5 小时" → "每 2 小时";**§十一 升级**:并发防护 → 轮间保险(单任务后已无双任务并发,但 80 分钟硬判保留作轮间/多源兜底,新增 2026-09-13 注);后续文档引用频率同步刷新 |
| 2026-09-14 | 调度模型 **单条 2h 滚动 → 12 条定点任务**(首段改为"每个定点槽"):**§11.2 阈值 80 → 45 分钟**(定点模型下 80 分钟会误杀正常槽:上一轮 16:58 结束 → 18:00 槽距它仅 62 分钟被判为重入,白丢一轮);§11.2 更名「轮间保险」并补充触发场景(重复派发/重启补偿/休眠后多槽同时补跑);§11.3 同步;流程定义抽出为 `AUTOMATION_PROMPT.md` |

## 十、状态回写硬约束(2026-09-12 起强制)

> 适用范围:每一轮定时巡检的 notify 步骤,**无例外**。这是与第七节「失败回退」并列的硬约束——第七节规定 curl 怎么处理,本节规定状态怎么记。

### 10.1 三态枚举(唯三合法值)

notify 字段在 `archive/schedule.md` 备注列尾和 `daily log`(`.workbuddy/memory/YYYY-MM-DD.md`)对应行的写法,**只能**是下列三种之一:

| 枚举值 | 格式 | 何时使用 |
|---|---|---|
| `notify: ok` | 固定字面量 | curl HTTP 200 且响应 `errcode == 0` |
| `notify: failed <reason>` | `<reason>` 必填,见 10.3 | curl 失败 / HTTP 非 200 / `errcode != 0` |
| `notify: skipped <reason>` | `<reason>` 必填,见 10.3 | 凭据文件缺失 / `webhook_url` 为空等明确跳过场景 |

### 10.2 禁止值(以下写法全部非法)

- ❌ `notify 待发` / `notify: pending` / `notify: TODO` —— 含糊占位符
- ❌ `notify:` 后留空
- ❌ `notify` 字段整段省略(漏写等同于「待发」)
- ❌ 任何不在 10.1 表内的字符串

### 10.3 `<reason>` 取值约定

`<reason>` 取自第七节失败回退表的实际原因,**保持短小可机读**:

| 触发条件 | `<reason>` 字面量 |
|---|---|
| `_docs/.wecom_webhook` 不存在 | `no_credential_file` |
| `webhook_url` 为空或 `null` | `empty_webhook_url` |
| curl 命令报错 / 超时 | `curl_error` 或 `curl_timeout` |
| HTTP 非 200 | `http_<code>`,如 `http_500` |
| HTTP 200 但 `errcode != 0` | `errcode_<code>`,如 `errcode_40001`;若响应有 `errmsg` 可追加 `; errmsg=<errmsg>` |

### 10.4 回写位置与时机

| 文件 | 位置 | 时机 |
|---|---|---|
| `_docs/archive/schedule.md` | 当轮新增行的「备注」列末尾(逗号或空格分隔) | notify 步骤结束**立即**(无论 ok/failed/skipped) |
| `.workbuddy/memory/YYYY-MM-DD.md` | 当轮日志行末尾 | 同上,与 schedule.md **同一轮**写入 |

### 10.5 违规处理(2026-09-12 修订:禁止补推)

- 任何一轮两文件中任一文件出现「待发」/空字段/枚举外值 → 该轮视为**状态回写违规**,下一轮首件事是**只补回写状态,禁止补推消息**——无法确认该轮是否真的没发出去时,一律按"已发过"处理,宁可群里少一条,不可多一条(重试禁令,见 §七)
- 连续 3 轮违规 → 在 STATE.md「本轮状态」告警 `notify_status: 连续 3 轮未回写,违反 §十 硬约束`
- 历史 backlog(09:39 / 11:04 / 12:15 / 13:20 四轮的 `notify 待发` 占位)已于 2026-09-12 确认实际均已推送成功,统一回填 `notify: ok`,**不再补推**

## 十一、防重复推送(幂等,2026-09-12 起强制)

> 背景:2026-09-12 群里出现同轮报告重复推送,根因有三——①双任务(A/B)并发重叠各自推了一遍;②curl `/tmp` 写入 quirk(exit 23)被误判失败引发重试;③旧 §10.5「补执行 notify」规则把"状态没回写"当成"没推送"又推了一遍。本节是硬性防线。

### 11.1 发送前检查(每轮 notify 步骤必做)

curl 之前,先 Grep `_docs/archive/schedule.md` 末尾:

- 若**本轮对应行**(时间 + 索引匹配)已含 `notify: ok` → 说明已由本任务或并发任务推送过 → **跳过 curl**,本轮 notify 步骤直接结束
- 若 schedule.md 末行是上一轮/上上几轮的行且已含 `notify: ok`,但内容与本轮 demo 完全一致(并发任务已代发) → 同样**跳过 curl**,本轮只补写自己的状态行

### 11.2 轮间保险(巡检任务开局必做,与 §十 并列)

> 2026-09-14 由「并发防护」更名并下调阈值:调度改为 12 条定点任务后,单槽间隔固定 120 分钟、单轮耗时 10~30 分钟,**80 分钟会误杀正常槽**(例:上一轮 16:58 结束写下 `last_run`,18:00 的正常槽距它只有 62 分钟 → 被当成重入而白丢一轮)。

每个定点槽起手读 `STATE.md` 第二节的 `last_run`:

- 距当前时间 **< 45 分钟** → 判定为**重复派发 / 重启补偿派发 / 休眠后多槽同时补跑**(正常槽不可能 45 分钟内再来一次)→ **本轮直接退出**,不写任何文件、不推送、不发任何通知
- ≥ 45 分钟 → 正常执行;并在第 1 步(定位领域)后**立即**把 `last_run` 更新为当前时间并 `git commit`(占位锁),让同批补跑的其它槽读到后退出
- 若执行中发现目标子目录已被上一槽部分/全部完成 → 不重复写 demo、**不再发通知**,只做索引衔接(S3),notify 记 `skipped concurrent_run`

### 11.3 三不原则(速记)

1. **不重试**:curl 每轮一次,失败只记日志(§七 重试禁令)
2. **不补推**:状态回写断了只补状态,不补消息(§10.5)
3. **不并发推**:`last_run` < 45 分钟直接退出;发送前查 schedule.md 已有 `notify: ok` 则跳过(§11.1/§11.2)
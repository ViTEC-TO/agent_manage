# Chat 多段历史对接（OpenClaw 2026.7.x）

这是 AgentManager 新增的主机侧只读能力，不是 OpenClaw 7 系列原生 Gateway 方法。
Server API 已支持转发任意 AgentManager 命令时，可以直接通过现有命令通道调用。
若前端采用点分方法名，代理按下表映射即可。用户、实例、Agent 的访问权限由现有 API 层先校验；主机侧再次校验物理段属于指定逻辑线程。

| 建议 WSS 方法 | AgentManager 命令 |
| --- | --- |
| `sessions.segments.history` | `sessions-segments-history` |
| `sessions.segments.message.get` | `sessions-segments-message-get` |
| `sessions.segments.media.get` | `sessions-segments-media-get` |

### 平台参数到 CLI 参数的映射

点分 WSS 协议的 `params` 使用驼峰字段；AgentManager 命令通道若按连字符键转 CLI，则 `parameters` 使用下表中间列。
`agent_manage` 只接收 CLI 参数，不解析平台的 `commandCode` 或 `parameters`；平台必须显式完成映射，不能只把 JSON 传给主机。

| WSS 字段 | 命令通道参数键 | CLI 参数 |
| --- | --- | --- |
| `sessionKey` | `session-k`（当前平台兼容）；`session-key`（标准） | `--session-k` 或 `--session-key` |
| `sessionId` | `session-id` | `--session-id` |
| `messageId` | `message-id` | `--message-id` |
| `edge` | `edge` | `--edge` |
| `limit` | `limit` | `--limit` |
| `view` | `view` | `--view compact`（默认）或 `--view full` |
| `cursor` | `cursor` | `--cursor` |
| `ref` | `ref` | `--ref` |

三个命令均要求逻辑线程参数（`session-k` 或 `session-key`）、`session-id`；消息补读还要求 `message-id`。
附件首次读取要求 `ref`，分块续读可仅传 `cursor`。历史的 `cursor` 与 `edge` 互斥。
2026-10-12 前端任务反馈：同值使用 `session-key` 时主机提示缺少该参数，改为 `session-k` 后线上旧段读取成功；短值也有同样差异。
这些结果说明问题与参数名有关，尚未证明是白名单或敏感键处理。Server API 源码不在本仓库。
三个只读命令现显式声明 `--session-k` 别名，不依赖 argparse 自动缩写；标准 `--session-key` 保留。实例、Agent 和会话归属检查不变。
当前平台转发建议使用 `session-k`；响应和点分 WSS 字段仍是 `sessionKey`。
例如平台请求：

```json
{
  "commandCode": "agent.sessions-segments-history",
  "parameters": {
    "session-k": "agent:example:dashboard:thread-key",
    "session-id": "00000000-0000-4000-8000-000000000001",
    "edge": "end",
    "limit": "30"
  }
}
```

应构造如下 argv（每个值保持为一个独立参数，不通过 shell 字符串拼接）：

```json
["sessions-segments-history", "--session-k", "agent:example:dashboard:thread-key", "--session-id", "00000000-0000-4000-8000-000000000001", "--edge", "end", "--limit", "30"]
```

## 分段索引

沿用 OpenClaw `sessions.usage`，参数 `key: <sessionKey>, groupBy: family, limit: 1`。
`currentSessionId` 是当前段，`includedSessionIds` 是已记录的段 ID；去重并把当前段移到最后。
主机读取器依据同一会话条目的 `sessionId` 和 `usageFamilySessionIds` 校验归属，不扫描其他线程来拼接来源。
索引未记录的旧 ID 不允许仅凭 UUID 读取。索引记录存在但文件已清理时返回 `SEGMENT_NOT_FOUND`。

## 历史分页

```bash
agent-manage sessions-segments-history \
  --session-key 'agent:example:dashboard:thread-key' \
  --session-id '00000000-0000-4000-8000-000000000001' \
  --edge end --limit 30
```

参数映射为 `sessionKey`, `sessionId`, `edge`, `limit`, `cursor`, `view`。
`sessionKey` 必须包含 `agent:<agentId>:`；`sessionId` 必填，不替换成当前段。
`edge` 默认为 `end`，也支持 `start`；`limit` 默认 30，范围 1–100。
传 `cursor` 时不要同时传 `edge`。向前、向后分页都用同一个命令：

```bash
agent-manage sessions-segments-history \
  --session-key 'agent:example:dashboard:thread-key' \
  --session-id '00000000-0000-4000-8000-000000000001' \
  --cursor '<olderCursor 或 newerCursor>' --limit 30
```

CLI 外层沿用 `{result, error, typeCode, message, serverTimeStamp, restartRequired}`。
API 代理可将 `result` 放入既有 WSS `payload`。以下为 `result`：

```json
{
  "sessionKey": "agent:example:dashboard:thread-key",
  "sessionId": "00000000-0000-4000-8000-000000000001",
  "view": "compact",
  "segment": {
    "startedAt": 1791766800000,
    "endedAt": 1791766801000,
    "isCurrent": false,
    "readable": true,
    "preview": ["最后一条用户消息", "最后一条助手消息"]
  },
  "messages": [{
    "id": "stable-entry-id",
    "role": "assistant",
    "timestamp": 1791766801000,
    "content": [{"type": "text", "text": "完整正文"}],
    "__openclaw": {"id": "stable-entry-id"}
  }],
  "olderCursor": "opaque-token",
  "newerCursor": null
}
```

每页按旧到新排列。段时间取本次快照中已存储记录的最早/最晚时间（毫秒），无时间则 `null`；当前段的 `endedAt` 是最后已存储记录时间，不代表对话已结束。
预览最多 180 个 Unicode 字符，正文不受这个限制，也不采用 OpenClaw `sessions.usage.logs` 或 `chat.history` 的文本截断规则。
保留已保存的思考和普通正文；默认精简工具记录，规则见下节。按 July transcript 树规则选择当前有效分支，不拼入已放弃分支。
无 ID 的旧记录以物理字节位置生成稳定 ID。不会重新生成未保存的思考或恢复已删除内容。

游标仅在同一实例、线程和物理段内有效，有效期一小时。
首次读取固定文件身份、已完成记录的字节边界和内容哈希；后续追加不进入这个快照。
文件正常重命名为 reset 归档仍可继续读；原文件被改写、替换、清理或引用失效返回 `CURSOR_EXPIRED`。
Gateway 认证配置变更也会使旧引用失效。页中保留内部消息，前端可自行隐藏；隐藏后即使没有显示行，也要继续处理返回的游标。
两侧游标均只在本段内推进。跨段顺序由前端的分段索引决定。

### 默认精简工具操作

新建页面默认 `view: compact`，原命令无需加参数。主机按固定规则处理，不调用模型做摘要：

- 用户消息、助手正文和已保存思考保留；图片、音频及媒体引用保留。
- `toolCall` / `tool_use` / `functionCall` 保留类型、ID、工具名，`arguments` 返回 `{}`。新字段 `summary` 是命令或文件路径的一行短预览，最多 180 字。没有命令/路径时显示工具名，不推测操作含义。
- `toolResult` / `tool` / `function` 保留工具名、调用关联、错误标记和附件；正文代码、日志、完整输出与任意 `details` 不传。`toolSummary` 返回 `{name, status}`，明确 `isError: true/false` 才返回 `error/success`，未记录时为 `unknown`。
- 被精简的消息带 `__openclaw.compacted: true, reason: "tool_details_omitted"`。省略的工具详情不带全文补读引用，前端无需补取，也不要把空 `arguments` 当成原始参数。

例如操作内容块为：

```json
{"type":"toolCall","id":"call-1","name":"exec","arguments":{},"summary":"grep -n 'auth' /app/extensions/openai/speech-providers.ts"}
```

结果消息为：

```json
{"id":"result-1","role":"toolResult","toolCallId":"call-1","toolName":"exec","isError":false,"content":[],"toolSummary":{"name":"exec","status":"success"},"__openclaw":{"id":"result-1","compacted":true,"reason":"tool_details_omitted"}}
```

前端用 `summary` 展示操作预览，用 `toolSummary` 展示结果，不再从完整参数和代码输出生成预览。
原先的完整模式仍可显式使用 `--view full`；这不是默认浏览流程，也不要求前端调用它。普通超大正文仍遵循原有输出预算和补读规则。
游标绑定首次页面的 `view`，后续可省略参数并继承；切换模式需重新打开该段，不能用原游标改模式。
升级前签发且未过期的页游标没有 `view`，继续沿用原完整模式；重新打开该段即用精简模式。

## 大消息补读

页面按输出字节预算控制大小，可能少于 `limit` 条。超大消息不静默截正文，而是返回：

```json
{
  "id": "large-entry-id",
  "role": "assistant",
  "timestamp": 1791766801000,
  "content": [],
  "__openclaw": {"id": "large-entry-id", "truncated": true, "reason": "full_message_reference"},
  "fullMessageRef": {
    "method": "sessions.segments.message.get",
    "params": {"sessionKey": "agent:example:dashboard:thread-key", "sessionId": "00000000-0000-4000-8000-000000000001", "messageId": "large-entry-id", "ref": "opaque-reference"}
  }
}
```

```bash
agent-manage sessions-segments-message-get \
  --session-key '<sessionKey>' --session-id '<sessionId>' \
  --message-id '<messageId>' --ref '<fullMessageRef.params.ref>'
```

`ref` 保持与历史页面相同的快照。也可以不传 `ref`，按指定物理段重新建立快照。
页面签发的 `ref` 也保留该页的 `view`；精简页面补齐超长正文/思考时，仍省略工具参数和输出，不会恢复这些工具原文。分块游标继续继承这个模式。
正常大小返回 `{sessionKey, sessionId, messageId, message}`。
特别大的消息返回如下分块结果：

```json
{
  "sessionKey": "agent:example:dashboard:thread-key",
  "sessionId": "00000000-0000-4000-8000-000000000001",
  "messageId": "large-entry-id",
  "contentType": "application/json",
  "encoding": "base64",
  "data": "base64-encoded-bytes",
  "offset": 0,
  "totalBytes": 900000,
  "sha256": "hash-of-the-complete-json-bytes",
  "nextCursor": "opaque-chunk-token"
}
```

继续调用同一补读命令，传 `--cursor <nextCursor>`；此时可以省略 `--ref`。
逐块 Base64 解码为字节，按 `offset` 拼接，直到 `nextCursor: null`。
检查长度和 SHA-256，再整体 UTF-8 解码并 JSON.parse。不要逐块直接解码文本，UTF-8 字符可能跨块。

## 图片、音频等附件

图片的内嵌 Base64 改为内容块上的 `mediaRef`，并在消息 `mediaRefs` 数组列出引用。
本地 `path` / `url` / `MEDIA:` 引用也会给出 `mediaRefs`，正文与原有路径及 `media` 字段仍保留。
引用结构为 `{method: "sessions.segments.media.get", params: {sessionKey, sessionId, ref}}`。

可读取附件的引用外层另带 `mediaId: "sha256:<附件原始字节哈希>"`。
内联 Base64 按解码后的字节计算；本地文件仅在既有 workspace/media 权限检查通过后计算，同文件在本次读取内复用哈希。
同一消息的 `mediaRefs` 按 `mediaId` 去重；内容块上的嵌套 `mediaRef` 仍保留，并携带相同 ID。
前端可按实例、逻辑线程、物理段和 `mediaId` 合并下载请求；下载后校验返回的 `sha256` 与 ID 匹配。
只有字节相同的附件会合并，字节不同的图片、缩略图和原图保留。
缺失、越界、符号链接或无法计算身份的附件引用没有 `mediaId`，不会据此合并或导致整条历史消息失败；真正读取时仍返回原有明确错误。
文件在历史读取后发生变化时，带身份的引用返回 `CURSOR_EXPIRED`，前端重新打开该段。
旧主机没有这个字段，前端应保留兼容；更新主机 AgentManager 后才有下载前身份和去重。

```bash
agent-manage sessions-segments-media-get \
  --session-key '<sessionKey>' --session-id '<sessionId>' --ref '<mediaRef.params.ref>'
```

返回格式与消息分块相同，`contentType` 为附件 MIME，`data` 为附件字节的 Base64。
传 `--cursor <nextCursor>` 继续取后续字节；所有块完成后校验 `sha256`，前端可构造 Blob/Object URL。
这样附件仍经过现有认证命令通道，不需要把主机文件目录公开为 HTTP 静态目录。
引用必须来自本段已存储消息，文件仅允许位于该 Agent workspace 或当前实例 `media` 目录；拒绝符号链接和目录越界。
原消息中的远程 HTTPS URL 保留原样，不由主机下载或绕过远端认证；链接过期或文件已清理无法恢复。
如代理另提供 HTTP 下载入口，可使用同一引用做鉴权读取，不改变此协议。

## 错误与只读保证

| `error.code` | 含义 |
| --- | --- |
| `FORBIDDEN` | 段不属于该线程、Agent 不属于实例、附件目录越界或符号链接 |
| `SEGMENT_NOT_FOUND` | 已知段的 transcript/归档不存在 |
| `CURSOR_EXPIRED` | 游标或引用失效、跨实例/线程/段、快照被替换或改写 |
| `MESSAGE_NOT_FOUND` | 指定消息不在该段有效分支/快照 |
| `MEDIA_NOT_FOUND` | 已引用附件不存在 |
| `SEGMENT_READ_FAILED` | 无法读取、索引或 transcript 格式损坏 |
| `VALIDATION_ERROR` / `INVALID_ARGUMENT` | 参数或命令格式错误 |

真正空段成功返回 `messages: []` 和两个 `null` 游标。失败不伪装空段，不退回当前段。
读取不调用模型，不修改 config、transcript、当前会话 ID、模型、标题、`updatedAt` 或排序。
Python 直接读取 July 的 JSON 会话索引和 JSONL transcript/reset 归档，不启动 Node、不加载 OpenClaw SDK，不使用截断的用量日志拼装消息。
线程 key 使用索引中的原始完整 key 做精确匹配，段必须在该条目的当前/历史 ID 集合中；不做跨 key 的大小写猜测。
保留轻量的归属、路径、文件身份和快照校验；不调用索引维护或更新函数。只扫描选中段，以字节位置读取当页消息，分页期间追加及 reset 不改变已签发快照。
每次请求仍需扫描选中 transcript 的快照边界；超大记录很多时，文件解析仍有成本。当前只针对 OpenClaw 2026.7.x 的 JSON 存储格式。

## 前端衔接

1. 进入线程：当前段继续走原有 `chat.history`，取 family 索引。
2. 悬停：目标段 `edge: end, limit: 1`，缓存时间与预览。
3. 进入旧段：`edge: end, limit: 30`。
4. 上滚：用 `olderCursor` 前插；为空则读取上一段末尾，保持原可见行位置。
5. 下滚：用 `newerCursor` 追加；为空则读取下一段开头。当前段也支持 `edge: start`。
6. 回到当前段最新位置时，用原生 `chat.history` 重新同步尾部，按稳定 ID 去重并恢复实时流；旧快照不会自动纳入后续追加的消息。
7. 切线程后丢弃迟到响应；引用失效时重新打开原目标段。

浏览旧段仅切换显示内容。发送消息仍走逻辑线程的当前段。

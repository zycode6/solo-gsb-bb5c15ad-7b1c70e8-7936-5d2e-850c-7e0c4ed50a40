# Webhook 事件接收与重放 API

基于 FastAPI + SQLite 的 Webhook 事件接收服务:按 `event_id` 幂等接收、记录首次接收顺序,支持时间范围分页查询与批量重放。

## 启动

```bash
docker compose up --build
```

服务监听 **8000** 端口(SQLite 数据持久化在 named volume `webhook-data`)。

不用 compose 也可以:

```bash
docker build -t webhook-replay . && docker run -p 8000:8000 webhook-replay
```

本地开发:`pip install -r requirements.txt && uvicorn app.main:app --port 8000`

## 示例 curl

### 1. 接收事件(幂等)

```bash
curl -X POST http://localhost:8000/events \
  -H 'Content-Type: application/json' \
  -d '{"event_id":"evt-1","occurred_at":"2026-09-18T09:00:00Z","payload":{"a":1}}'
# 首次: 201 {"status":"created","event_id":"evt-1"}
# 重复: 200 {"status":"duplicate","event_id":"evt-1"}  —— 不产生重复记录
```

### 2. 按时间范围分页查询

```bash
curl 'http://localhost:8000/events?from=2026-09-18T09:30:00Z&to=2026-09-18T11:30:00Z&page=1&page_size=50'
```

返回 `total / page / page_size / items`,每条含 `seq`(首次接收顺序号)、`event_id`、`occurred_at`、`payload`、`received_at`。`from`/`to` 均可省略。

### 3. 批量重放

```bash
curl -X POST http://localhost:8000/replay \
  -H 'Content-Type: application/json' \
  -d '{"event_ids":["evt-3","evt-1","evt-404"]}'
```

```json
{
  "replayed": 2,
  "skipped": 1,
  "results": [
    {"event_id": "evt-1", "status": "replayed", "event": {"seq": 1, "...": "..."}},
    {"event_id": "evt-3", "status": "replayed", "event": {"seq": 2, "...": "..."}},
    {"event_id": "evt-404", "status": "skipped", "reason": "not_found"}
  ]
}
```

命中的事件**按首次接收顺序(`seq`)输出**,与请求顺序无关;未命中的标记 `skipped`;请求中重复的 `event_id` 只输出一次。

### 4. 校验完整性(哈希链)

```bash
curl http://localhost:8000/integrity
```

```json
{"verified": true, "checked": 3, "first_break": null}
```

- `verified`:整条链是否通过校验(空库为 `true`,`checked` 为 0);
- `checked`:从最早记录起通过校验的记录数;
- `first_break`:首个断点(`seq` / `event_id` / `reason`),无断点时为 `null`。

发现断点时的示例:

```json
{
  "verified": false,
  "checked": 2,
  "first_break": {"seq": 3, "event_id": "evt-3", "reason": "record_hash_mismatch"}
}
```

`reason` 取值:`seq_not_continuous`(序号不连续)、`missing_hash_fields`(链字段缺失)、
`prev_hash_mismatch`(与上一条记录哈希不衔接)、`record_hash_mismatch`(事件字段被改动,哈希对不上)。

每条记录的哈希按固定规范计算,可独立复算:

```python
import hashlib, json

doc = {
    "seq": 1,                      # 首次接收顺序号
    "event_id": "evt-1",
    "occurred_at": "2026-09-18T09:00:00+00:00",
    "payload": {"a": 1},           # 解析后的 JSON(与存储键序无关)
    "received_at": "...",          # 首次接收时间
    "prev_hash": "0" * 64,         # 上一条记录哈希, 首条为 64 个 "0"
}
record_hash = hashlib.sha256(
    json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
).hexdigest()
```

## 设计要点

- **幂等**:`event_id` 上有 `UNIQUE` 约束,先查重再插入,重复投递返回 200 `duplicate`,**不产生新记录、不改变哈希链**(并发下由唯一约束兜底)。
- **接收顺序**:自增主键 `id` 即首次接收顺序,重放按它排序。
- **完整性哈希链**:每条记录写入时,在同一事务内取上一条记录的 `record_hash` 作为 `prev_hash`,按固定 JSON 规范(键排序 + 紧凑分隔符)计算本记录哈希一并落库;首条记录的 `prev_hash` 为 64 个 `0`。任何对历史事件字段、顺序或链接关系的篡改都会被 `GET /integrity` 定位到首个断点。
- **启动迁移**:服务启动时自动为旧库补充 `prev_hash` / `record_hash` 两列,并按接收顺序回填历史记录的哈希链(幂等,可重复执行),无需手工操作。
- **时间比较**:`occurred_at` 归一化为 UTC ISO8601 字符串存储,范围查询走字典序比较,并建有索引。
- **配置**:数据库路径由环境变量 `DB_PATH` 控制(容器内默认 `/data/events.db`)。

## API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/events` | 接收单个事件(幂等,写入哈希链) |
| GET | `/events` | 时间范围分页查询(`from`/`to`/`page`/`page_size`) |
| POST | `/replay` | 批量重放,按首次接收顺序输出 |
| GET | `/integrity` | 校验哈希链完整性(空库返回 `verified=true`) |
| GET | `/health` | 健康检查 |

交互式文档:启动后访问 `http://localhost:8000/docs`。

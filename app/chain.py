"""事件完整性哈希链: 固定 JSON 规范 + SHA-256。

每条记录哈希覆盖事件全部字段(seq/event_id/occurred_at/payload/received_at)
以及上一条记录的哈希(prev_hash), 形成可验证的链式结构。
"""
import hashlib
import json
from typing import Any

# 首条记录(创世)的 prev_hash: 64 个 "0", 与合法 SHA-256 十六进制输出同构
GENESIS_HASH = "0" * 64


def canonical_json(obj: Any) -> str:
    """固定 JSON 规范: 键按字典序排序、紧凑分隔符、非 ASCII 不转义。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_record_hash(
    seq: int,
    event_id: str,
    occurred_at: str,
    payload_text: str,
    received_at: str,
    prev_hash: str,
) -> str:
    """按固定规范计算单条记录哈希。

    payload_text 为库中存储的 JSON 字符串, 先解析再规范化,
    使哈希与存储时的键序/空白无关。
    """
    doc = {
        "seq": seq,
        "event_id": event_id,
        "occurred_at": occurred_at,
        "payload": json.loads(payload_text),
        "received_at": received_at,
        "prev_hash": prev_hash,
    }
    return hashlib.sha256(canonical_json(doc).encode("utf-8")).hexdigest()

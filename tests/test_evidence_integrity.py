"""Evidence store: the hash chain, manifests, and secret redaction (S12).

T5's acceptance criterion is specific: flipping one byte must make verify fail
and name the file. A chain that only detects wholesale deletion is not evidence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evidence import integrity
from evidence.store import EvidenceStore


@pytest.fixture()
def store(tmp_path: Path):
    s = EvidenceStore(tmp_path / "evidence.db")
    s.create_run("r1", label="pilot", plan_hash="plan123", git_commit="abc")
    s.start_session("s1", run_id="r1", persona="free", seed=1)
    yield s
    s.close()


@pytest.fixture()
def populated(store, tmp_path: Path):
    png = tmp_path / "shot.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n some bytes")
    store.add_screenshot("sh1", path=png, sha256=integrity.file_sha256(png))
    store.append_event("s1", type="session_start", payload={"persona": "free"})
    store.append_event("s1", type="agent_step", payload={"tool": "pick_door", "door": 2},
                       level_no=1)
    store.append_event("s1", type="offer_shown", payload={"price": 120}, level_no=1,
                       screenshot_id="sh1")
    store.end_session("s1", status="ok")
    manifests = tmp_path / "manifests"
    store.write_manifest("r1", manifests)
    return store, png, manifests


def test_a_clean_run_verifies(populated) -> None:
    store, _, manifests = populated
    manifest = store.verify("r1", manifests)
    assert manifest["session_final_hashes"]["s1"]
    assert manifest["screenshot_hashes"]["sh1"]


def test_the_chain_links_each_event_to_the_last(populated) -> None:
    store, _, _ = populated
    rows = store.conn.execute(
        "SELECT seq, prev_hash, row_hash FROM events WHERE session_id='s1' ORDER BY seq"
    ).fetchall()
    assert rows[0]["prev_hash"] == integrity.chain_start("s1")
    for earlier, later in zip(rows, rows[1:]):
        assert later["prev_hash"] == earlier["row_hash"]


def test_flipping_one_screenshot_byte_fails_and_names_the_file(populated) -> None:
    store, png, manifests = populated
    data = bytearray(png.read_bytes())
    data[-1] ^= 0x01
    png.write_bytes(bytes(data))

    with pytest.raises(integrity.IntegrityError) as excinfo:
        store.verify("r1", manifests)
    message = str(excinfo.value)
    assert "sh1" in message
    assert png.name in message


def test_a_missing_screenshot_is_caught(populated) -> None:
    store, png, manifests = populated
    png.unlink()
    with pytest.raises(integrity.IntegrityError, match="missing"):
        store.verify("r1", manifests)


def test_editing_an_event_payload_is_caught(populated) -> None:
    store, _, manifests = populated
    store.conn.execute("UPDATE events SET payload=? WHERE session_id='s1' AND seq=2",
                       ('{"door":0,"tool":"pick_door"}',))
    store.conn.commit()
    with pytest.raises(integrity.IntegrityError) as excinfo:
        store.verify("r1", manifests)
    assert "seq 2" in str(excinfo.value)


def test_deleting_an_event_is_caught(populated) -> None:
    store, _, manifests = populated
    store.conn.execute("DELETE FROM events WHERE session_id='s1' AND seq=2")
    store.conn.commit()
    with pytest.raises(integrity.IntegrityError):
        store.verify("r1", manifests)


def test_editing_the_manifest_itself_is_caught(populated) -> None:
    store, _, manifests = populated
    path = manifests / "r1.json"
    path.write_text(path.read_text(encoding="utf-8").replace('"pilot"', '"doctored"'),
                    encoding="utf-8")
    with pytest.raises(integrity.IntegrityError, match="altered"):
        store.verify("r1", manifests)


def test_canonical_json_is_order_independent() -> None:
    a = integrity.canonical_json({"b": 1, "a": 2})
    b = integrity.canonical_json({"a": 2, "b": 1})
    assert a == b == '{"a":2,"b":1}'


def test_spend_totals_add_up(store) -> None:
    store.record_spend({"run_id": "r1", "session_id": "s1", "model": "m",
                        "input_tokens": 1200, "output_tokens": 60, "usd": 0.00015})
    store.record_spend({"run_id": "r1", "session_id": "s1", "model": "m",
                        "input_tokens": 1200, "output_tokens": 60, "usd": 0.00015})
    assert store.total_spend_usd("r1") == pytest.approx(0.0003)
    assert store.total_spend_usd() == pytest.approx(0.0003)
    assert store.total_spend_usd("other") == 0.0


def test_a_missing_metric_is_absent_not_zero(store) -> None:
    store.set_metric("s1", "M1", None)
    store.set_metric("s1", "M2", 61.5)
    rows = dict(store.conn.execute("SELECT metric, value FROM metrics WHERE session_id='s1'"))
    assert "M1" not in rows, "a session with no losses must not record M1 as 0"
    assert rows["M2"] == pytest.approx(61.5)


# --- S12: secrets never reach the store --------------------------------------

@pytest.mark.parametrize("payload", [
    {"api_key": "sk-ant-api03-REALLOOKINGKEY123456"},
    {"headers": {"authorization": "Bearer sk-ant-api03-ANOTHERKEY9876"}},
    {"note": "export ANTHROPIC_API_KEY=sk-ant-api03-INLINEKEY4567890"},
    {"nested": [{"token": "sk-ant-deadbeefdeadbeef"}]},
])
def test_secrets_are_redacted_before_being_written(store, payload) -> None:
    store.append_event("s1", type="error", payload=payload)
    stored = store.conn.execute(
        "SELECT payload FROM events WHERE session_id='s1' ORDER BY seq DESC LIMIT 1"
    ).fetchone()[0]
    assert "sk-ant" not in stored
    assert "sk-" not in stored
    assert integrity.REDACTED in stored


def test_redaction_leaves_ordinary_data_alone() -> None:
    payload = {"tool": "pick_door", "door": 2, "reason": "best odds", "usd": 0.00015}
    assert integrity.redact(payload) == payload


def test_redaction_covers_keys_and_value_shapes() -> None:
    assert integrity.redact({"password": "hunter2"})["password"] == integrity.REDACTED
    assert "sk-" not in integrity.redact("prefix sk-ant-abcdefgh12345 suffix")
    assert integrity.redact(["sk-ant-abcdefgh12345"])[0] == integrity.REDACTED

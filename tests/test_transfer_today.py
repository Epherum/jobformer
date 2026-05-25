import json

import pytest

from jobscraper.transfer_today import TransferConfig, _chunk_rows_for_append, append_rows


def _row(i: int, *, payload: str = "x" * 300) -> list[str]:
    return [
        "source",
        "label",
        f"title-{i}",
        "company",
        "location",
        "2026-05-05",
        f"https://example.com/{i}",
        "",
        payload,
        "",
        "",
        "",
        "",
    ]


def test_chunk_rows_for_append_splits_large_payloads() -> None:
    cfg = TransferConfig(
        sheet_id="sheet",
        from_tabs=["Sales_Today", "Tech_Today"],
        append_batch_rows=120,
        max_values_json_bytes=25_000,
    )
    rows = [_row(i) for i in range(200)]

    chunks = _chunk_rows_for_append(cfg, rows)

    assert sum(len(chunk) for chunk in chunks) == len(rows)
    assert len(chunks) > 1
    for chunk in chunks:
        payload = json.dumps(chunk, ensure_ascii=False).encode("utf-8")
        assert len(payload) <= cfg.max_values_json_bytes


def test_append_rows_uses_multiple_gog_appends_for_large_batches(monkeypatch) -> None:
    cfg = TransferConfig(
        sheet_id="sheet",
        from_tabs=["Sales_Today", "Tech_Today"],
        append_batch_rows=120,
        max_values_json_bytes=25_000,
    )
    rows = [_row(i) for i in range(200)]
    calls: list[list[str]] = []

    def fake_run_gog(args: list[str]) -> str:
        calls.append(args)
        return ""

    monkeypatch.setattr("jobscraper.transfer_today._run_gog", fake_run_gog)

    moved = append_rows(cfg, "Jobs", rows)

    assert moved == len(rows)
    assert len(calls) > 1
    total_rows_sent = 0
    for args in calls:
        payload = args[args.index("--values-json") + 1]
        chunk = json.loads(payload)
        total_rows_sent += len(chunk)
        assert len(payload.encode("utf-8")) <= cfg.max_values_json_bytes
    assert total_rows_sent == len(rows)


def test_chunk_rows_for_append_raises_when_single_row_is_too_large() -> None:
    cfg = TransferConfig(
        sheet_id="sheet",
        from_tabs=["Sales_Today", "Tech_Today"],
        append_batch_rows=10,
        max_values_json_bytes=50,
    )

    with pytest.raises(ValueError, match="Single row payload too large"):
        _chunk_rows_for_append(cfg, [["x" * 500]])

"""Writable create_notes must stay actionable for agents."""

from __future__ import annotations

from manager_mcp.writable import WRITABLE


def test_receipt_create_notes_name_key_fields() -> None:
    notes = WRITABLE["receipts"].create_notes
    assert "ReceivedIn" in notes
    assert "Lines" in notes
    assert "FX" in notes or "foreign" in notes.casefold()
    assert "get_record" in notes


def test_payment_create_notes_mirror_workflow() -> None:
    notes = WRITABLE["payments"].create_notes
    assert "PaidFrom" in notes
    assert "get_record" in notes
    assert "Lines" in notes

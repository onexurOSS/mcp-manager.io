"""Pre-flight validation and persistence diff for hardened writable resources."""

from __future__ import annotations

from typing import Any

from manager_mcp.writable import WritableResource


def validate_write_body(
    resource: WritableResource,
    fields: dict[str, Any],
    *,
    creating: bool,
) -> None:
    """Raise ValueError when body is empty, has unknown keys, or bad types."""
    if not resource.known_keys:
        return
    if not fields:
        raise ValueError(
            f"{resource.name}: empty body rejected. Clone a template via "
            f"get_record then set required fields "
            f"{sorted(resource.required_keys) if resource.required_keys else '(see notes)'}."
        )
    unknown = sorted(set(fields) - resource.known_keys)
    if unknown:
        sample = ", ".join(sorted(resource.known_keys)[:12])
        raise ValueError(
            f"{resource.name}: unknown field(s) {unknown}. "
            f"Manager silently drops wrong names. Known keys include: {sample}, …"
        )
    if creating and resource.required_keys:
        missing = sorted(resource.required_keys - set(fields))
        if missing:
            raise ValueError(
                f"{resource.name}: missing required field(s) {missing}. "
                "Clone get_record template before create."
            )
    if "PaidBy" in fields and fields["PaidBy"] is not None:
        paid = fields["PaidBy"]
        if isinstance(paid, bool) or not isinstance(paid, int):
            raise ValueError(
                f"{resource.name}: PaidBy must be int (e.g. 1), not "
                f"{type(paid).__name__}."
            )
    lines = fields.get("Lines")
    if lines is None:
        return
    if not isinstance(lines, list):
        raise ValueError(f"{resource.name}: Lines must be a list.")
    if creating and len(lines) == 0:
        raise ValueError(
            f"{resource.name}: Lines must be non-empty on create "
            "(allocation / AR / AP / bank-charge lines)."
        )
    if not resource.known_line_keys:
        return
    for index, line in enumerate(lines):
        if not isinstance(line, dict):
            raise ValueError(f"{resource.name}: Lines[{index}] must be an object.")
        bad = sorted(set(line) - resource.known_line_keys)
        if bad:
            raise ValueError(
                f"{resource.name}: Lines[{index}] unknown field(s) {bad}. "
                f"Known line keys: {sorted(resource.known_line_keys)}."
            )


def diff_persisted(
    resource: WritableResource,
    submitted: dict[str, Any],
    persisted: dict[str, Any] | None,
) -> list[str]:
    """Return warnings when submitted known scalars/lines did not stick."""
    if not resource.known_keys or not isinstance(persisted, dict):
        return ["could not verify persistence (no form body returned)"]
    warnings: list[str] = []
    skip = {"Key", "id", "text", "UniqueName", "Lines", "CustomFields", "CustomFields2"}
    for key, value in submitted.items():
        if key in skip or key not in resource.known_keys:
            continue
        if key not in persisted:
            warnings.append(f"{key} missing after save (submitted value may have been dropped)")
            continue
        tracked = {"ReceivedIn", "PaidFrom", "Customer", "Supplier"}
        if key in tracked and persisted.get(key) != value:
            warnings.append(
                f"{key} differs after save "
                f"(submitted={value!r}, got={persisted.get(key)!r})"
            )
    if "Lines" in submitted and isinstance(submitted["Lines"], list):
        got = persisted.get("Lines")
        if not isinstance(got, list):
            warnings.append("Lines missing after save")
        elif len(got) != len(submitted["Lines"]):
            warnings.append(
                f"Lines count differs after save (submitted={len(submitted['Lines'])}, "
                f"got={len(got)})"
            )
    return warnings

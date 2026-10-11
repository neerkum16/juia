"""Check the DFSORT SORT/SUM field layout before generation."""

import re


def _fields(sysin, statement, width):
    match = re.search(rf"\b{statement}\s+FIELDS\s*=\s*\(([^)]*)\)", sysin, re.I)
    if not match:
        return []
    parts = [part.strip() for part in match.group(1).split(",")]
    if len(parts) % width:
        return []  # Complex or unsupported syntax belongs to the decoder.
    fields = []
    for offset in range(0, len(parts), width):
        position, length = parts[offset : offset + 2]
        if not position.isdigit() or not length.isdigit():
            return []
        fields.append((int(position), int(length)))
    return fields


def validate_sort_sum_layout(steps):
    """Reject a SUM field overlapping a SORT key in a simple DFSORT layout.

    Other control-card forms are left to the existing decoder and validator.
    """
    for step in steps:
        if step.get("utility_type") != "DFSORT":
            continue
        sysin = step.get("sysin_content", "")
        sort_fields = _fields(sysin, "SORT", 4)
        sum_fields = _fields(sysin, "SUM", 3)
        for sort_start, sort_length in sort_fields:
            for sum_start, sum_length in sum_fields:
                if max(sort_start, sum_start) < min(
                    sort_start + sort_length, sum_start + sum_length
                ):
                    raise ValueError(
                        f"{step['step_name']}: SORT field at {sort_start},{sort_length} "
                        f"overlaps SUM field at {sum_start},{sum_length}"
                    )

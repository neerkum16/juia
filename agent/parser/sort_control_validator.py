"""Catch a simple invalid DFSORT SORT/SUM layout before API calls.

For ordinary positional field lists, the parser extracts each field's start
and length and checks whether a SUM field overlaps a SORT key. Complex DFSORT
syntax is left to the existing decoder and validator instead of guessing.
"""

import re


def _fields(sysin, statement, width):
    """Extract (position, length) pairs from a simple SORT or SUM card.

    SORT entries have four comma-separated parts and SUM entries have three;
    width identifies the corresponding card shape.
    """
    # Match the card body first. A missing statement is valid for this guard
    # and produces no fields to compare.
    match = re.search(rf"\b{statement}\s+FIELDS\s*=\s*\(([^)]*)\)", sysin, re.I)
    if not match:
        return []
    # Split fixed-width tuples into their position and length components.
    parts = [part.strip() for part in match.group(1).split(",")]
    if len(parts) % width:
        return []  # Complex or unsupported syntax belongs to the decoder.
    fields = []
    for offset in range(0, len(parts), width):
        position, length = parts[offset : offset + 2]
        # Non-numeric expressions need full utility interpretation, so this
        # lightweight precheck deliberately declines to parse them.
        if not position.isdigit() or not length.isdigit():
            return []
        fields.append((int(position), int(length)))
    return fields


def validate_sort_sum_layout(steps):
    """Reject a SUM field overlapping a SORT key in a simple DFSORT layout.

    Other control-card forms are left to the existing decoder and validator.
    """
    # Only DFSORT cards have the SORT/SUM positional relationship checked here.
    for step in steps:
        if step.get("utility_type") != "DFSORT":
            continue
        sysin = step.get("sysin_content", "")
        sort_fields = _fields(sysin, "SORT", 4)
        sum_fields = _fields(sysin, "SUM", 3)
        # Each field occupies [start, start + length). Comparing the larger
        # start with the smaller end finds overlap without enumerating bytes.
        for sort_start, sort_length in sort_fields:
            for sum_start, sum_length in sum_fields:
                if max(sort_start, sum_start) < min(
                    sort_start + sort_length, sum_start + sum_length
                ):
                    raise ValueError(
                        f"{step['step_name']}: SORT field at {sort_start},{sort_length} "
                        f"overlaps SUM field at {sum_start},{sum_length}"
                    )

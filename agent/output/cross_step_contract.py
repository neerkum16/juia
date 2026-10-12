"""Compare explicit storage and key contracts across generated JCL steps.

This offline check reads Python source with ``ast``; it never imports or runs
generated PySpark. It reports only values that can be tied to a dataset named
in the original JCL. Dynamic paths and complete DataFrame schemas require a
separate Spark integration test, so they are reported as unverified.
"""

import ast
import re
from collections import defaultdict
from pathlib import Path

from agent.output.diagram_generator import get_step_files


def _literal(node, symbols):
    """Resolve plain string/list literals and references to earlier constants."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return symbols.get(node.id)
    if isinstance(node, (ast.List, ast.Tuple)):
        values = [_literal(item, symbols) for item in node.elts]
        return values if all(value is not None for value in values) else None
    return None


def _module_symbols(tree):
    symbols = {}
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target, value = statement.targets[0], statement.value
        elif isinstance(statement, ast.AnnAssign):
            target, value = statement.target, statement.value
        else:
            continue
        if isinstance(target, ast.Name) and value is not None:
            resolved = _literal(value, symbols)
            if resolved is not None:
                symbols[target.id] = resolved
    return symbols


def _code_contracts(tree, original):
    """Map a generated file's explicit constants to original JCL datasets."""
    symbols = _module_symbols(tree)
    inputs, outputs = get_step_files({}, original)
    inputs = {name.upper() for name in inputs}
    outputs = {name.upper() for name in outputs}
    contracts = defaultdict(dict)

    # DEFINE CLUSTER implementations often declare multiple targets in one
    # module. The cluster_name/table_path pair identifies each one exactly.
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            node = statement.value
        elif isinstance(statement, ast.AnnAssign):
            node = statement.value
        else:
            continue
        if not isinstance(node, ast.Call):
            continue
        keywords = {item.arg: item.value for item in node.keywords if item.arg}
        dataset = _literal(keywords["cluster_name"], symbols) if "cluster_name" in keywords else None
        if not isinstance(dataset, str) or dataset.upper() not in outputs:
            continue
        dataset = dataset.upper()
        for keyword, attribute in (("table_path", "storage_path"),
                                   ("table_name", "table_name"),
                                   ("key_column", "primary_key")):
            value = _literal(keywords[keyword], symbols) if keyword in keywords else None
            if isinstance(value, str):
                contracts[dataset][attribute] = value
        if isinstance(symbols.get("KEY_COLUMN"), str):
            contracts[dataset]["primary_key"] = symbols["KEY_COLUMN"]

    # A single input/output DD makes conventional path constants unambiguous.
    if len(inputs) == 1:
        dataset = next(iter(inputs))
        for name in ("SOURCE_PATH", "INPUT_PATH"):
            if isinstance(symbols.get(name), str):
                contracts[dataset]["storage_path"] = symbols[name]
                break
    if len(outputs) == 1:
        dataset = next(iter(outputs))
        for name in ("TARGET_PATH", "OUTPUT_PATH", "DESTINATION_PATH"):
            if isinstance(symbols.get(name), str):
                contracts[dataset]["storage_path"] = symbols[name]
                break
        key = symbols.get("KSDS_PRIMARY_KEY")
        if isinstance(key, list) and len(key) == 1 and isinstance(key[0], str):
            contracts[dataset]["primary_key"] = key[0]
        if isinstance(symbols.get("TARGET_TABLE"), str):
            contracts[dataset]["table_name"] = symbols["TARGET_TABLE"]
    return contracts


def _normalize(attribute, value):
    if attribute == "storage_path":
        return value.replace("\\", "/").rstrip("/")
    return value.lower()


def _placeholder(path):
    path = path.lower().replace("\\", "/")
    return path in ("s3://bucket", "/mnt/delta") or path.startswith(
        ("s3://bucket/", "path/to/", "/path/to/", "/mnt/delta/")
    )


def _placeholder_literals(tree):
    """Find placeholder strings used by code, excluding comments/docstrings."""
    docstring_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr) and
                    isinstance(first.value, ast.Constant) and
                    isinstance(first.value.value, str)):
                docstring_nodes.add(id(first.value))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docstring_nodes and _placeholder(node.value)):
            yield node.value, node.lineno


def _is_catalogue_only(original):
    """LISTCAT inspections inspect metadata, not the dataset's data path."""
    if str(original.get("utility_type", "")).upper() != "IDCAMS":
        return False
    cards = str(original.get("sysin_content") or "").upper()
    return bool(re.search(r"\bLISTCAT\b", cards)) and not bool(
        re.search(r"\b(?:REPRO|DEFINE|DELETE)\b", cards)
    )


def _semantic_issues(tree, original, step_name):
    """Flag two recognizable transformations that need stronger guarantees."""
    utility = str(original.get("utility_type", "")).upper()
    cards = str(original.get("sysin_content") or "")
    issues = []

    if utility == "DFSORT" and re.search(r"\bSUM\s+FIELDS\b", cards, re.I):
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and
                    isinstance(node.func, ast.Attribute) and
                    node.func.attr == "agg" and
                    isinstance(node.func.value, ast.Call) and
                    isinstance(node.func.value.func, ast.Attribute) and
                    node.func.value.func.attr in ("groupBy", "groupby")):
                continue
            carries_first = any(
                isinstance(child, ast.Call) and
                isinstance(child.func, ast.Attribute) and
                child.func.attr == "first" and
                isinstance(child.func.value, ast.Name) and
                child.func.value.id in ("F", "functions")
                for child in ast.walk(node)
            )
            if carries_first:
                issues.append({"code": "NONDETERMINISTIC_CARRY",
                               "severity": "error", "dataset": "",
                               "contract": "source_row_order", "steps": [step_name],
                               "message": f"{step_name}:{node.lineno} uses F.first inside "
                                          "groupBy aggregation for DFSORT SUM; "
                                          "source-row carry is not deterministic"})
                break

    if (utility == "IDCAMS" and re.search(r"\bDELETE\b", cards, re.I)
            and re.search(r"\bIF\s+LASTCC\b", cards, re.I)):
        for function in ast.walk(tree):
            if not (isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and any(word in function.name.lower() for word in ("drop", "delete"))):
                continue
            for handler in (node for node in ast.walk(function)
                            if isinstance(node, ast.ExceptHandler)):
                broad = (handler.type is None or
                         isinstance(handler.type, ast.Name) and
                         handler.type.id in ("Exception", "BaseException"))
                if broad and not any(isinstance(node, ast.Raise)
                                     for node in ast.walk(handler)):
                    issues.append({"code": "CONTROL_FLOW_UNVERIFIED",
                                   "severity": "warning", "dataset": "",
                                   "contract": "idcams_condition_code",
                                   "steps": [step_name],
                                   "message": f"{step_name}:{handler.lineno} suppresses "
                                              "all DROP/DELETE exceptions despite "
                                              "an IF LASTCC condition in SYSIN"})
                    return issues
    return issues


def check_step_source_contract(original_step, generated_code):
    """Return recognizable single-step hazards for the correction loop.

    The model validator can then request a correction even when its own
    review says PASS. Cross-step path and key comparisons still happen after
    every generated step has a final file.
    """
    tree = ast.parse(generated_code)
    step_name = original_step.get("step_name", "UNKNOWN")
    issues = _semantic_issues(tree, original_step, step_name)
    reported_paths = set()
    for value, line in _placeholder_literals(tree):
        if value in reported_paths:
            continue
        reported_paths.add(value)
        issues.append({"code": "PLACEHOLDER_PATH", "severity": "error",
                       "dataset": "", "contract": "placeholder_path",
                       "steps": [step_name],
                       "message": f"{step_name}:{line} uses unconfigured path {value!r}"})
    return issues


def check_cross_step_contracts(steps_data, code_paths):
    """Return FAIL for explicit conflicts and UNVERIFIED for unknown contracts.

    ``steps_data`` contains the original/decoded pairs used by output writers;
    ``code_paths`` maps generated step names to source files. The ``issues``
    list contains a severity, dataset, contract, steps, and diagnostic text.
    """
    issues = []
    bindings = defaultdict(lambda: defaultdict(list))
    uses = defaultdict(set)
    reported_placeholders = set()
    for step in steps_data:
        original = step.get("original", {})
        name = original.get("step_name") or step.get("decoded", {}).get("step_name")
        if name not in code_paths:
            continue
        # A pure LISTCAT step reads catalogue metadata. It is visible in the
        # lineage diagram, but no physical data path/key agreement is needed.
        inputs, outputs = ([], []) if _is_catalogue_only(original) else get_step_files({}, original)
        for dataset in inputs + outputs:
            uses[dataset.upper()].add(name)
        try:
            tree = ast.parse(Path(code_paths[name]).read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeError) as error:
            issues.append({"code": "SOURCE_UNREADABLE", "severity": "error",
                           "dataset": "", "contract": "source",
                           "steps": [name], "message": f"Cannot inspect generated code: {error}"})
            continue
        issues.extend(_semantic_issues(tree, original, name))
        for dataset, attributes in _code_contracts(tree, original).items():
            for attribute, value in attributes.items():
                bindings[dataset][attribute].append((name, value))
                if attribute == "storage_path" and _placeholder(value):
                    reported_placeholders.add((name, value))
                    issues.append({"code": "PLACEHOLDER_PATH", "severity": "error",
                                   "dataset": dataset,
                                   "contract": "placeholder_path", "steps": [name],
                                   "message": f"{name} uses unconfigured path {value!r}"})
        for value, line in _placeholder_literals(tree):
            if (name, value) in reported_placeholders:
                continue
            reported_placeholders.add((name, value))
            issues.append({"code": "PLACEHOLDER_PATH", "severity": "error",
                           "dataset": "", "contract": "placeholder_path",
                           "steps": [name],
                           "message": f"{name}:{line} uses unconfigured path {value!r}"})

    for dataset, step_names in sorted(uses.items()):
        if len(step_names) < 2:
            continue
        for attribute in ("storage_path", "primary_key", "table_name"):
            values = bindings[dataset][attribute]
            if len(values) < 2:
                if attribute == "storage_path" or (attribute == "primary_key" and values):
                    issues.append({"code": "CONTRACT_UNVERIFIED", "severity": "warning",
                                   "dataset": dataset,
                                   "contract": attribute, "steps": sorted(step_names),
                                   "message": f"{attribute} is not explicit in at least two generated steps"})
                continue
            if len({_normalize(attribute, value) for _, value in values}) > 1:
                detail = ", ".join(f"{step}={value!r}" for step, value in values)
                issues.append({"code": "CONTRACT_CONFLICT", "severity": "error",
                               "dataset": dataset,
                               "contract": attribute,
                               "steps": sorted({step for step, _ in values}),
                               "message": f"Conflicting {attribute} for {dataset}: {detail}"})

    status = ("FAIL" if any(item["severity"] == "error" for item in issues)
              else "UNVERIFIED" if issues else "PASS")
    return {"status": status, "issues": issues}

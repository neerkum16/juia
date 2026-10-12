"""Check generated Python source without importing Spark or API clients.

This preflight catches syntax errors and the missing ``sys`` import seen in a
saved STEP030 script. It does not execute the script or validate data logic.
"""

import ast
import builtins
import symtable


def check_python_source(code):
    """Return a concrete Python source error, or an empty string if clear."""

    try:
        tree = ast.parse(code)
    except SyntaxError as error:
        return f"Python syntax error at line {error.lineno}: {error.msg}"

    uses_sys_argv = any(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys" and node.attr == "argv"
        for node in ast.walk(tree)
    )
    imports_sys = any(
        isinstance(node, ast.Import)
        and any(alias.name == "sys" and (alias.asname is None or alias.asname == "sys")
                for alias in node.names)
        for node in ast.walk(tree)
    )
    if uses_sys_argv and not imports_sys:
        return "sys.argv is used without importing sys"

    # Resolve names that functions expect to find at module scope. This catches
    # omitted imports such as ``F.col(...)`` without requiring a lint package.
    # It is a conservative preflight: dynamic imports and runtime path/schema
    # behavior still require execution and human review.
    try:
        module_scope = symtable.symtable(code, "<generated>", "exec")
    except SyntaxError as error:
        return f"Python compile error at line {error.lineno}: {error.msg}"
    bound_names = {
        symbol.get_name() for symbol in module_scope.get_symbols()
        if symbol.is_assigned() or symbol.is_imported() or symbol.is_namespace()
    }
    known_names = set(dir(builtins)) | bound_names | {
        "__name__", "__file__", "__package__", "__spec__", "__loader__",
        "__builtins__", "__conditional_annotations__",
    }
    missing_names = set()

    def visit(scope):
        for symbol in scope.get_symbols():
            if (symbol.is_referenced() and symbol.is_global()
                    and symbol.get_name() not in known_names):
                missing_names.add(symbol.get_name())
        for child in scope.get_children():
            visit(child)

    visit(module_scope)
    if missing_names:
        return "Undefined global name(s): " + ", ".join(sorted(missing_names))
    return ""

import re

import sqlglot
from sqlglot import exp

BLOCKED_NODE_TYPES = (
    exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Alter,
    exp.Merge, exp.Command, exp.Grant, exp.Revoke, exp.TruncateTable,
)

BLOCKED_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|merge|copy|vacuum|analyze|refresh|call|do|execute)\b",
    re.IGNORECASE,
)


def validate_readonly_sql(sql: str) -> str:
    candidate = (sql or "").strip().rstrip(";")
    if not candidate:
        raise ValueError("La consulta SQL está vacía.")

    if BLOCKED_KEYWORDS.search(_strip_literals(candidate)):
        raise ValueError("Modo seguro: solo se permiten consultas de lectura.")

    try:
        statements = sqlglot.parse(candidate, read="postgres")
    except Exception as e:
        raise ValueError(f"SQL inválido: {e}") from e

    if len(statements) != 1:
        raise ValueError("Solo se permite una sentencia SQL por ejecución.")

    tree = statements[0]
    if any(tree.find(t) is not None for t in BLOCKED_NODE_TYPES):
        raise ValueError("La consulta contiene una operación de escritura o administración bloqueada.")

    if tree.find(exp.Select) is None:
        raise ValueError("Solo se permiten consultas que produzcan un SELECT.")

    return candidate


def apply_preview_limit(sql: str, row_limit: int) -> tuple[str, bool]:
    """
    Añade LIMIT al SELECT de nivel superior solo si no existe.
    El SQL que el usuario ve no se modifica; esto solo controla la ejecución de vista previa.
    """
    candidate = (sql or "").strip().rstrip(";")
    try:
        tree = sqlglot.parse_one(candidate, read="postgres")
        if tree is None or tree.find(exp.Select) is None:
            return candidate, False
        if tree.args.get("limit") is not None:
            return candidate, False
        limited = tree.limit(int(row_limit))
        return limited.sql(dialect="postgres"), True
    except Exception:
        # La validación ya ocurrió antes. Si sqlglot no puede reescribir, ejecutamos original.
        return candidate, False


def _strip_literals(sql: str) -> str:
    sql = re.sub(r"--.*?$", " ", sql, flags=re.MULTILINE)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"'(?:''|[^'])*'", "''", sql)
    return sql

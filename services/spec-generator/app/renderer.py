from __future__ import annotations
import re
from app.models import ExecutionPath

_SIDE_EFFECT_LABELS = {
    "db_read":    "DB READ",
    "db_write":   "DB WRITE",
    "cache_read": "CACHE READ",
    "cache_set":  "CACHE SET",
    "http_out":   "HTTP OUT",
    "fs_write":   "FS WRITE",
}

_SQL_KEYWORDS = {"SELECT", "INSERT", "UPDATE", "DELETE", "FROM", "INTO", "WHERE", "SET", "VALUES"}


def _runs_observed(path: ExecutionPath) -> int:
    """Approximate run count from timing (always at least 1)."""
    return max(1, round(path.timing_p50_ms)) if path.timing_p50_ms > 0 else 1


def _extract_table(detail: str) -> str:
    """Extract table name from a DB side effect detail.

    Strategy: look for the word immediately after FROM or INTO (covers SELECT/DELETE/INSERT).
    For UPDATE, the table is the word immediately after UPDATE.
    Fall back to the first non-SQL-keyword word if none of those patterns match.
    """
    words = re.split(r"\s+", detail.strip())
    upper_words = [w.upper().strip("(,);") for w in words]

    # Look for FROM or INTO followed by a table name
    for kw in ("FROM", "INTO"):
        if kw in upper_words:
            idx = upper_words.index(kw)
            if idx + 1 < len(words):
                candidate = words[idx + 1].strip("(,);")
                if candidate and candidate.upper() not in _SQL_KEYWORDS:
                    return candidate

    # For UPDATE: table is the word right after UPDATE
    if upper_words and upper_words[0] == "UPDATE" and len(words) > 1:
        candidate = words[1].strip("(,);")
        if candidate and candidate.upper() not in _SQL_KEYWORDS:
            return candidate

    # Fall back: first non-SQL-keyword word
    for word in words:
        clean = word.strip("(,);")
        if clean.upper() not in _SQL_KEYWORDS and clean:
            return clean

    return detail.split()[0] if detail.split() else detail


def _format_db_detail(detail: str) -> str:
    """Reformat a DB detail to omit the table name (shown in CHANGE_IMPACT instead).

    Returns a short description of the operation without repeating the table name,
    so that CHANGE_IMPACT is the single source of truth for table names in the output.

    Examples:
      "SELECT id FROM users WHERE id = $1"  -> "WHERE id = $1"
      "INSERT INTO audit_log VALUES (...)"   -> "INSERT"
      "UPDATE users SET active = false"      -> "SET active = false"
      "DELETE FROM sessions WHERE ..."       -> "WHERE ..."
    """
    table = _extract_table(detail)
    upper = detail.upper()

    # After FROM/INTO: grab everything after the table name
    for kw in ("FROM", "INTO"):
        kw_upper = kw + " " + table.upper()
        pos = upper.find(kw_upper)
        if pos != -1:
            after = detail[pos + len(kw_upper):].strip()
            if after:
                return after
            # No remainder — just return the leading SQL verb
            first_word = detail.split()[0].upper() if detail.split() else ""
            return first_word or detail

    # For UPDATE: everything after "UPDATE {table} "
    if upper.startswith("UPDATE "):
        after_table = detail[len("UPDATE ") + len(table):].strip()
        if after_table:
            return after_table
        return "UPDATE"

    # Fallback: return the leading SQL verb (INSERT, DELETE, etc.) or the raw detail
    first_word = detail.split()[0].upper() if detail.split() else ""
    return first_word or detail


def _extract_service(detail: str) -> str:
    """Extract hostname from an HTTP OUT detail like 'POST https://host/path'."""
    match = re.search(r"https?://([^/\s]+)", detail)
    if match:
        return match.group(1)
    parts = detail.split()
    return parts[-1] if parts else detail


def render_spec_text(path: ExecutionPath, entrypoint_name: str) -> str:
    runs = _runs_observed(path)
    lines: list[str] = []

    # PATH section
    lines.append(f"PATH {entrypoint_name}  ({runs} runs observed)")
    for i, item in enumerate(path.call_sequence, start=1):
        freq_str = f"{item.frequency_ratio:.2f}"
        ms_str = f"~{item.avg_ms:.1f}ms"
        lines.append(f"  {i}.  {item.name}    {freq_str}    {ms_str}")
    lines.append("")

    # SIDE_EFFECTS section
    if path.side_effects:
        lines.append("SIDE_EFFECTS:")
        for effect in path.side_effects:
            label = _SIDE_EFFECT_LABELS.get(effect.type, effect.type.upper().replace("_", " "))
            suffix = "   (conditional)" if effect.hop_depth > 1 else ""
            if effect.type in ("db_read", "db_write"):
                display = _format_db_detail(effect.detail)
            else:
                display = effect.detail
            lines.append(f"  {label}   {display}{suffix}")
        lines.append("")

    # CHANGE_IMPACT section
    tables: dict[str, list[str]] = {}  # table -> list of r/w
    services: list[str] = []
    for effect in path.side_effects:
        if effect.type == "db_read":
            t = _extract_table(effect.detail)
            tables.setdefault(t, [])
            if "r" not in tables[t]:
                tables[t].append("r")
        elif effect.type == "db_write":
            t = _extract_table(effect.detail)
            tables.setdefault(t, [])
            if "w" not in tables[t]:
                tables[t].append("w")
        elif effect.type == "http_out":
            svc = _extract_service(effect.detail)
            if svc not in services:
                services.append(svc)

    if tables or services:
        lines.append("CHANGE_IMPACT:")
        if tables:
            table_str = "  ".join(f"{t} ({'/'.join(rw)})" for t, rw in tables.items())
            lines.append(f"  tables affected    {table_str}")
        if services:
            cond_effects = {_extract_service(e.detail) for e in path.side_effects
                            if e.type == "http_out" and e.hop_depth > 1}
            svc_parts = []
            for svc in services:
                cond = " (conditional)" if svc in cond_effects else ""
                svc_parts.append(f"{svc}{cond}")
            lines.append(f"  external services  {'  '.join(svc_parts)}")

    return "\n".join(lines)

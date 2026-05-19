#!/usr/bin/env python3
"""Data analysis tools — describe and chart Excel / CSV / JSON / Parquet files.

These two tools are designed for the agent to onboard a tabular dataset
without burning context on raw rows. ``describe_dataset`` returns a compact
JSON profile (shape, dtypes, numeric summary, head sample, top missing
columns) suitable for routing the next planning step. ``plot_chart``
renders a matplotlib PNG to ``/opt/data/cache/charts/`` so adapters can
attach it back to the user.

Both tools enforce a strict path allowlist (only files under ``/opt/data``)
and a 50 MB size cap to keep load times predictable. They never mutate the
input file. Heavy slicing / aggregation should still go through
``execute_code`` — these tools are the on-ramp, not the analysis engine.
"""

from __future__ import annotations

import json
import logging
import math
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402  (must follow use("Agg"))
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

# Prefer fonts that include CJK glyphs so Chinese labels render instead of
# tofu boxes. The Debian base image ships WenQuanYi Zen Hei; fall back to
# matplotlib's default if none of these are installed.
plt.rcParams["font.sans-serif"] = [
    "WenQuanYi Zen Hei",
    "Noto Sans CJK SC",
    "Noto Sans CJK JP",
    "PingFang SC",
    "Microsoft YaHei",
    "DejaVu Sans",
]
plt.rcParams["axes.unicode_minus"] = False

from tools.registry import registry  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_ALLOWED_ROOT = Path("/opt/data").resolve()
_MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB hard ceiling
_DEFAULT_ROW_LIMIT = 10_000  # describe truncates to this many rows
_CHARTS_DIR = _ALLOWED_ROOT / "cache" / "charts"

_READERS = {
    ".xlsx": "excel",
    ".xls": "excel",
    ".csv": "csv",
    ".tsv": "csv",
    ".json": "json",
    ".jsonl": "json",
    ".parquet": "parquet",
}

_VALID_KINDS = {"line", "bar", "scatter", "hist", "box"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_path(path: str) -> Tuple[bool, str, Optional[Path]]:
    if not path or not isinstance(path, str):
        return False, "path must be a non-empty string", None
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        return False, f"could not resolve path: {exc}", None
    try:
        resolved.relative_to(_ALLOWED_ROOT)
    except ValueError:
        return False, f"path must be inside {_ALLOWED_ROOT}", None
    if not resolved.exists():
        return False, f"file not found: {resolved}", None
    if not resolved.is_file():
        return False, f"path is not a regular file: {resolved}", None
    size = resolved.stat().st_size
    if size > _MAX_FILE_BYTES:
        return (
            False,
            f"file too large: {size} bytes exceeds {_MAX_FILE_BYTES} cap",
            None,
        )
    suffix = resolved.suffix.lower()
    if suffix not in _READERS:
        return (
            False,
            f"unsupported extension '{suffix}'; supported: {sorted(_READERS)}",
            None,
        )
    return True, "", resolved


def _list_excel_sheets(path: Path) -> List[str]:
    try:
        with pd.ExcelFile(path) as xl:
            return list(xl.sheet_names)
    except Exception:
        return []


def _load_dataframe(
    path: Path,
    *,
    sheet: Optional[str] = None,
    nrows: Optional[int] = None,
) -> Tuple[pd.DataFrame, List[str]]:
    """Load a dataframe with format auto-detection. Returns (df, sheet_names)."""
    suffix = path.suffix.lower()
    fmt = _READERS[suffix]
    sheet_names: List[str] = []

    if fmt == "excel":
        sheet_names = _list_excel_sheets(path)
        target_sheet: Any = sheet if sheet else 0
        df = pd.read_excel(path, sheet_name=target_sheet, nrows=nrows)
    elif fmt == "csv":
        sep = "\t" if suffix == ".tsv" else ","
        df = pd.read_csv(path, sep=sep, nrows=nrows)
    elif fmt == "json":
        if suffix == ".jsonl":
            df = pd.read_json(path, lines=True, nrows=nrows)
        else:
            df = pd.read_json(path)
            if nrows is not None:
                df = df.head(nrows)
    elif fmt == "parquet":
        df = pd.read_parquet(path)
        if nrows is not None:
            df = df.head(nrows)
    else:  # pragma: no cover — defensive
        raise ValueError(f"unhandled format: {fmt}")

    return df, sheet_names


def _jsonable(value: Any) -> Any:
    """Coerce numpy / pandas scalars into JSON-safe primitives."""
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, pd.Timedelta)):
        return str(value)
    if isinstance(value, (np.ndarray,)):
        return [_jsonable(v) for v in value.tolist()]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    return value


def _numeric_summary(df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    numeric = df.select_dtypes(include=[np.number])
    if numeric.empty:
        return {}
    desc = numeric.describe().to_dict()
    return {col: _jsonable(stats) for col, stats in desc.items()}


def _top_missing(df: pd.DataFrame, top: int = 5) -> List[Dict[str, Any]]:
    if df.empty:
        return []
    missing = df.isna().mean().sort_values(ascending=False)
    missing = missing[missing > 0].head(top)
    return [
        {"column": str(col), "missing_pct": round(float(pct), 4)}
        for col, pct in missing.items()
    ]


# ---------------------------------------------------------------------------
# Tool: describe_dataset
# ---------------------------------------------------------------------------


def describe_dataset_tool(path: str, sheet: Optional[str] = None) -> Dict[str, Any]:
    ok, err, resolved = _validate_path(path)
    if not ok or resolved is None:
        return {"success": False, "error": err}

    try:
        df, sheet_names = _load_dataframe(
            resolved, sheet=sheet, nrows=_DEFAULT_ROW_LIMIT + 1
        )
    except Exception as exc:
        logger.exception("describe_dataset: failed to load %s", resolved)
        return {"success": False, "error": f"failed to load file: {exc}"}

    truncated = len(df) > _DEFAULT_ROW_LIMIT
    if truncated:
        df = df.head(_DEFAULT_ROW_LIMIT)

    try:
        head_records = df.head(5).to_dict(orient="records")
        head_records = [_jsonable(row) for row in head_records]
    except Exception as exc:
        logger.exception("describe_dataset: failed to serialize head")
        head_records = []
        return {"success": False, "error": f"failed to serialize sample rows: {exc}"}

    dtypes = {str(col): str(dt) for col, dt in df.dtypes.items()}

    result: Dict[str, Any] = {
        "success": True,
        "path": str(resolved),
        "shape": [int(df.shape[0]), int(df.shape[1])],
        "columns": [str(c) for c in df.columns.tolist()],
        "dtypes": dtypes,
        "numeric_summary": _numeric_summary(df),
        "sample_head": head_records,
        "missing_top5": _top_missing(df),
        "truncated": truncated,
        "row_limit_for_summary": _DEFAULT_ROW_LIMIT,
    }
    if sheet_names:
        result["sheets"] = sheet_names
        result["active_sheet"] = sheet if sheet else sheet_names[0]
    return result


# ---------------------------------------------------------------------------
# Tool: plot_chart
# ---------------------------------------------------------------------------


def _ensure_charts_dir() -> Path:
    _CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    return _CHARTS_DIR


def _resolve_out_path(out_path: Optional[str]) -> Tuple[bool, str, Optional[Path]]:
    if not out_path:
        return True, "", _ensure_charts_dir() / f"{uuid.uuid4().hex}.png"
    try:
        candidate = Path(out_path).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        return False, f"could not resolve out_path: {exc}", None
    try:
        candidate.relative_to(_ALLOWED_ROOT)
    except ValueError:
        return False, f"out_path must be inside {_ALLOWED_ROOT}", None
    if candidate.suffix.lower() != ".png":
        return False, "out_path must end with .png", None
    candidate.parent.mkdir(parents=True, exist_ok=True)
    return True, "", candidate


def plot_chart_tool(
    path: str,
    kind: str,
    x: str,
    y: Optional[str] = None,
    hue: Optional[str] = None,
    title: Optional[str] = None,
    out_path: Optional[str] = None,
) -> Dict[str, Any]:
    ok, err, resolved = _validate_path(path)
    if not ok or resolved is None:
        return {"success": False, "error": err}

    kind = (kind or "").lower().strip()
    if kind not in _VALID_KINDS:
        return {
            "success": False,
            "error": f"kind must be one of {sorted(_VALID_KINDS)}, got '{kind}'",
        }
    if kind != "hist" and not y:
        return {"success": False, "error": f"kind={kind} requires 'y' column"}

    ok2, err2, out_resolved = _resolve_out_path(out_path)
    if not ok2 or out_resolved is None:
        return {"success": False, "error": err2}

    try:
        df, _ = _load_dataframe(resolved)
    except Exception as exc:
        logger.exception("plot_chart: failed to load %s", resolved)
        return {"success": False, "error": f"failed to load file: {exc}"}

    missing_cols = [c for c in [x, y, hue] if c and c not in df.columns]
    if missing_cols:
        return {
            "success": False,
            "error": f"columns not found in dataset: {missing_cols}",
        }

    rows_used = int(len(df))

    try:
        fig, ax = plt.subplots(figsize=(10, 6), dpi=120)
        if kind == "hist":
            df[x].dropna().plot(kind="hist", ax=ax, bins=30)
            ax.set_xlabel(x)
        elif kind == "box":
            cols = [x] if not y else [x, y]
            df[cols].plot(kind="box", ax=ax)
        elif hue and kind in {"line", "bar", "scatter"}:
            for label, group in df.groupby(hue):
                group.plot(kind=kind, x=x, y=y, ax=ax, label=str(label))
        else:
            df.plot(kind=kind, x=x, y=y, ax=ax)
        if title:
            ax.set_title(title)
        plt.tight_layout()
        fig.savefig(out_resolved, format="png")
    except Exception as exc:
        logger.exception("plot_chart: failed to render")
        return {"success": False, "error": f"failed to render chart: {exc}"}
    finally:
        plt.close("all")

    file_size = out_resolved.stat().st_size if out_resolved.exists() else 0
    return {
        "success": True,
        "image_path": str(out_resolved),
        "kind": kind,
        "rows_used": rows_used,
        "file_size_bytes": int(file_size),
    }


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


DESCRIBE_DATASET_SCHEMA = {
    "name": "describe_dataset",
    "description": (
        "Profile a tabular data file (.xlsx, .xls, .csv, .tsv, .json, .jsonl, "
        ".parquet) and return a compact JSON summary: shape, columns, dtypes, "
        "numeric describe(), the first five rows as records, and the top five "
        "columns by missing-value ratio. Use this BEFORE running pandas via "
        "execute_code so you understand the schema. Files must live under "
        "/opt/data and be ≤ 50 MB."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Absolute path to the data file. Must be inside /opt/data. "
                    "Feishu attachments land under /opt/data/cache/."
                ),
            },
            "sheet": {
                "type": "string",
                "description": (
                    "Optional sheet name for Excel files. Omit to use the "
                    "first sheet. The response includes all sheet names."
                ),
            },
        },
        "required": ["path"],
    },
}


PLOT_CHART_SCHEMA = {
    "name": "plot_chart",
    "description": (
        "Render a matplotlib chart (line / bar / scatter / hist / box) from a "
        "tabular file and save it as PNG under /opt/data/cache/charts/. "
        "Returns the saved image path so the agent can attach it via "
        "send_message. Always call describe_dataset first to know which "
        "columns exist."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute path to the data file (under /opt/data).",
            },
            "kind": {
                "type": "string",
                "enum": sorted(_VALID_KINDS),
                "description": "Chart type.",
            },
            "x": {
                "type": "string",
                "description": (
                    "Column name for the x axis (or the value column when "
                    "kind=hist)."
                ),
            },
            "y": {
                "type": "string",
                "description": (
                    "Column name for the y axis. Required for every kind "
                    "except 'hist'."
                ),
            },
            "hue": {
                "type": "string",
                "description": (
                    "Optional grouping column — each unique value gets its "
                    "own series. Only honored for line / bar / scatter."
                ),
            },
            "title": {
                "type": "string",
                "description": "Optional chart title rendered above the axes.",
            },
            "out_path": {
                "type": "string",
                "description": (
                    "Optional output PNG path (must end with .png and live "
                    "inside /opt/data). Omit to auto-generate under "
                    "/opt/data/cache/charts/."
                ),
            },
        },
        "required": ["path", "kind", "x"],
    },
}


registry.register(
    name="describe_dataset",
    toolset="data",
    schema=DESCRIBE_DATASET_SCHEMA,
    handler=lambda args, **kw: describe_dataset_tool(
        path=args.get("path", ""),
        sheet=args.get("sheet"),
    ),
    emoji="📊",
    max_result_size_chars=20_000,
)

registry.register(
    name="plot_chart",
    toolset="data",
    schema=PLOT_CHART_SCHEMA,
    handler=lambda args, **kw: plot_chart_tool(
        path=args.get("path", ""),
        kind=args.get("kind", ""),
        x=args.get("x", ""),
        y=args.get("y"),
        hue=args.get("hue"),
        title=args.get("title"),
        out_path=args.get("out_path"),
    ),
    emoji="📈",
    max_result_size_chars=5_000,
)

"""Genpact F&A master-data tables (analytics / reference).

These hold the raw ingested workbook data — the full multi-year employee
master, benchmarks, currency table, job-posting/attrition data, and the
talent/AI/comp-outlook market-intelligence sheets. They are *analytics*
tables (bulk-loaded, read for reporting), separate from the transactional
JVRE-workspace tables the pay-review UI drives.

The tables are built as SQLAlchemy Core ``Table`` objects on
``Base.metadata`` from :mod:`scripts._genpact_spec` so Alembic autogenerate
picks them up and there is a single source of truth for the schema.
"""

from __future__ import annotations

import uuid

from scripts._genpact_spec import (
    BENCHMARK_TABLE,
    TABLES,
)
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base

_METADATA = Base.metadata


def _sa_type(kind: str, length_or_precision):
    if kind == "str":
        return String(length_or_precision)
    if kind == "text":
        return Text()
    if kind == "int":
        return Integer()
    if kind == "bigint":
        return BigInteger()
    if kind == "num":
        precision, scale = length_or_precision
        return Numeric(precision, scale)
    if kind == "date":
        return Date()
    if kind == "bool":
        return Boolean()
    raise ValueError(f"unknown kind: {kind}")


def _build_table(table_name: str, columns) -> Table:
    if table_name in _METADATA.tables:
        return _METADATA.tables[table_name]
    cols = [
        Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        Column("tenant_id", UUID(as_uuid=True), nullable=False, index=True),
    ]
    for attr, kind, lp in columns:
        cols.append(Column(attr, _sa_type(kind, lp), nullable=False))
    cols.append(
        Column(
            "created_at",
            DateTime(timezone=True),
            server_default=func.now(),
            nullable=False,
        )
    )
    return Table(table_name, _METADATA, *cols)


# Build every spec-declared table plus the unioned benchmark table.
# ``GENPACT_TABLES`` maps table_name -> Table for the seeder to use.
GENPACT_TABLES: dict[str, Table] = {}
for _sheet, (_tname, _hrow, _cols) in TABLES.items():
    GENPACT_TABLES[_tname] = _build_table(_tname, _cols)

_bench_name, _bench_hrow, _bench_cols = BENCHMARK_TABLE
GENPACT_TABLES[_bench_name] = _build_table(_bench_name, _bench_cols)


# ---------------------------------------------------------------------------
# ORM classes over the two tables compchat / iQuest AI reads.
# They reuse the Core ``Table`` objects above (no duplicate schema, no extra
# migration) so the service layer gets attribute access + ``select(Model)``.
# ---------------------------------------------------------------------------
class GenpactEmployeeMaster(Base):
    """Read model over ``genpact_employee_master`` for iQuest AI."""

    __table__ = GENPACT_TABLES["genpact_employee_master"]


class GenpactBenchmark(Base):
    """Read model over ``genpact_benchmark`` for iQuest AI."""

    __table__ = GENPACT_TABLES["genpact_benchmark"]

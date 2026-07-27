"""SQLAlchemy ORM models.

Every model module MUST be imported here. Alembic's autogenerate
compares ``Base.metadata`` to the live database, and a model only
appears in ``Base.metadata`` once its module has been imported.
``alembic/env.py`` imports this package, so re-exporting models from
here gives Alembic a single, reliable discovery point.
"""

from app.models.agent_audit import AgentPipelineRun, AgentRunLog, ToolRunLog
from app.models.audit_log import AuditLog
from app.models.budget_allocation import (
    BudgetAllocation,
    BudgetAllocationLine,
)
from app.models.compensation_cycle import CompensationCycle
from app.models.compensation_history import CompensationHistory
from app.models.department import Department
from app.models.genpact_master_data import (
    GENPACT_TABLES,
    GenpactBenchmark,
    GenpactEmployeeMaster,
)
from app.models.jvre_snapshot import JvreSnapshot
from app.models.market_benchmark import MarketBenchmark
from app.models.pay_recommendation import (
    PayRecommendation,
    PayRecommendationAnnotation,
    PayRecommendationComponent,
    PayRecommendationOverride,
)
from app.models.report_narrative import NarrativeGeneration
from app.models.reporting_relationship import ReportingRelationship
from app.models.role import Role
from app.models.tenant import Tenant
from app.models.user import User
from app.models.user_role import UserRole

__all__ = [
    "AgentPipelineRun",
    "AgentRunLog",
    "AuditLog",
    "BudgetAllocation",
    "BudgetAllocationLine",
    "CompensationCycle",
    "CompensationHistory",
    "Department",
    "GenpactBenchmark",
    "GenpactEmployeeMaster",
    "JvreSnapshot",
    "MarketBenchmark",
    "NarrativeGeneration",
    "PayRecommendation",
    "PayRecommendationAnnotation",
    "PayRecommendationComponent",
    "PayRecommendationOverride",
    "ReportingRelationship",
    "Role",
    "Tenant",
    "ToolRunLog",
    "User",
    "UserRole",
]

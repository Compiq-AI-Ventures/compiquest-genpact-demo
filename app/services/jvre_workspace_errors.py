"""Domain errors for the JVRE workspace feature.

Keeping these in their own module breaks the import coupling: routers,
tests, and any future middleware can catch or inspect these errors without
importing the entire service. Every class is a thin ``DomainError``
subclass — status_code and error_code control the global handler response.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import status

from app.core.exceptions import DomainError


class CycleNotFoundError(DomainError):
    status_code = status.HTTP_404_NOT_FOUND
    error_code = "CYCLE_NOT_FOUND"

    def __init__(self) -> None:
        super().__init__(message="Compensation cycle not found.")


class BudgetAllocationNotFoundError(DomainError):
    status_code = status.HTTP_404_NOT_FOUND
    error_code = "BUDGET_ALLOCATION_NOT_FOUND"

    def __init__(self) -> None:
        super().__init__(message="Budget allocation not found.")


class BudgetAllocationLineNotFoundError(DomainError):
    status_code = status.HTTP_404_NOT_FOUND
    error_code = "BUDGET_ALLOCATION_LINE_NOT_FOUND"

    def __init__(self) -> None:
        super().__init__(message="Budget allocation line not found on this allocation.")


class NotAllocationOwnerError(DomainError):
    """Caller tried to write to an allocation they don't own."""

    status_code = status.HTTP_403_FORBIDDEN
    error_code = "NOT_ALLOCATION_OWNER"

    def __init__(self) -> None:
        super().__init__(
            message=(
                "You can only modify your own budget allocation. Speak to "
                "the upstream owner if you need a change here."
            )
        )


class BudgetAllocationNotEditableError(DomainError):
    """Allocation is in a status that doesn't accept writes."""

    status_code = status.HTTP_400_BAD_REQUEST
    error_code = "BUDGET_ALLOCATION_NOT_EDITABLE"

    def __init__(self, current_status: str) -> None:
        super().__init__(
            message=(
                f"This budget allocation is in status {current_status!r} "
                "and can no longer be edited. Only PENDING allocations "
                "accept changes."
            ),
            details={"current_status": current_status},
        )


class StrategicReserveExceedsPoolError(DomainError):
    status_code = status.HTTP_400_BAD_REQUEST
    error_code = "STRATEGIC_RESERVE_EXCEEDS_POOL"

    def __init__(self, requested: Decimal, max_allowed: Decimal) -> None:
        super().__init__(
            message=f"Strategic reserve {requested} cannot exceed total pool {max_allowed}.",
            details={
                "requested_reserve": str(requested),
                "max_allowed": str(max_allowed),
            },
        )


class AllocationExceedsBudgetError(DomainError):
    """Sum of line allocated_amounts exceeds budget_for_allocation."""

    status_code = status.HTTP_400_BAD_REQUEST
    error_code = "ALLOCATION_EXCEEDS_BUDGET"

    def __init__(self, requested_total: Decimal, budget_for_allocation: Decimal) -> None:
        super().__init__(
            message=(
                f"Sum of line allocations {requested_total} exceeds the "
                f"budget for allocation {budget_for_allocation}. Reduce a "
                "line, increase the strategic reserve, or revise."
            ),
            details={
                "requested_total": str(requested_total),
                "budget_for_allocation": str(budget_for_allocation),
            },
        )


class MissingAllocationLinesError(DomainError):
    """Submit was called before lines exist for every direct report."""

    status_code = status.HTTP_400_BAD_REQUEST
    error_code = "MISSING_ALLOCATION_LINES"

    def __init__(self, missing_recipient_ids: list[uuid.UUID]) -> None:
        super().__init__(
            message=(
                "Allocation has no lines for some direct reports. Click "
                "'Allocate Budget' (or POST align-with-jvre) first."
            ),
            details={"missing_recipient_ids": [str(i) for i in missing_recipient_ids]},
        )


class SubjectNotInReportingChainError(DomainError):
    """Caller tried to read data for a subject who isn't their direct report."""

    status_code = status.HTTP_403_FORBIDDEN
    error_code = "SUBJECT_NOT_IN_REPORTING_CHAIN"

    def __init__(self) -> None:
        super().__init__(
            message=(
                "You can only access data for users in your direct-reporting "
                "chain for the active cycle."
            )
        )


class RecommendationNotFoundError(DomainError):
    status_code = status.HTTP_404_NOT_FOUND
    error_code = "RECOMMENDATION_NOT_FOUND"

    def __init__(self) -> None:
        super().__init__(message="Pay recommendation not found.")


class RecommendationNotEditableError(DomainError):
    """Recommendation is in a status that doesn't accept writes."""

    status_code = status.HTTP_400_BAD_REQUEST
    error_code = "RECOMMENDATION_NOT_EDITABLE"

    def __init__(self, current_status: str) -> None:
        super().__init__(
            message=(
                f"Recommendation is in status {current_status!r} and "
                "can't be edited by the original actor anymore. The "
                "MoM reviewer's edits go through a different path."
            ),
            details={"current_status": current_status},
        )


class InvalidPayComponentError(DomainError):
    status_code = status.HTTP_400_BAD_REQUEST
    error_code = "INVALID_PAY_COMPONENT"

    def __init__(self, component: str, valid_components: list[str]) -> None:
        super().__init__(
            message=(
                f"Unknown pay component {component!r}. Valid components: "
                f"{', '.join(valid_components)}."
            ),
            details={"component": component},
        )

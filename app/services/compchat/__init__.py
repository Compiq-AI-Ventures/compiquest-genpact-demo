"""CompChat — Employee Compensation Chat Assistant pipeline.

Implements the 11-layer agentic design from
``docs/specs/compchat_v1.md`` (Tessot CompChat Architecture Framework).

Core principle: **the LLM is a narrator, not a calculator, and never a
data accessor.** Every number reaching the manager was produced by a
deterministic tool, validated against its source record, and passed
through an access-control gate. The SLM only classifies intent (a
schema-constrained 6-way choice) and narrates from a minimal,
pre-validated context object.

Tool selection is deterministic: intent → static ``INTENT_TOOLS`` map.
The model never emits a tool call, so a TEAM_QUERY structurally cannot
reach the compensation tool.

Public entry point: :func:`pipeline.run`.
"""

"""Repositories: thin async data-access layer over SQLAlchemy.

Each module here owns the queries for a single aggregate (User, etc.) and
contains *only* persistence code — no hashing, no HTTP, no business rules.
That separation keeps services testable and lets us swap storage in the
future without touching the API or domain logic.
"""

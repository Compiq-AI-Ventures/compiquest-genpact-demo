"""HTTP routers — the FastAPI layer.

Routers translate HTTP into service calls and back. Keep them thin: parse
input, hand off to a service, map domain exceptions to HTTPException,
return a response model. No business logic lives here.
"""

"""Services: orchestration and business rules.

Routers stay thin and call into services; services compose repository
calls, hashing, and domain rules. Services raise *domain* exceptions
(e.g. :class:`~app.services.admin_user_service.EmailAlreadyExistsError`);
the router layer is responsible for translating those into HTTP
responses.
"""

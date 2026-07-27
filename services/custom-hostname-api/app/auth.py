"""Bearer-token authentication, resolving a caller to its route profile.

The token is not merely an authentication credential — it is the authorization
boundary. Each consumer's token maps to exactly one route profile, and a domain
can only ever be attached to the profile its token grants. That is what stops
one consumer from pointing a customer domain at another consumer's workload.
"""

from __future__ import annotations

from fastapi import Depends, Request

from .config import RouteProfile, Settings, get_settings
from .errors import forbidden, unauthorized


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise unauthorized()
    return token.strip()


def caller_profile(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> RouteProfile:
    """The route profile the caller's bearer token grants."""
    profile = settings.profile_for_token(_bearer(request))
    if profile is None:
        raise unauthorized()
    return profile


def resolve_profile(requested: str | None, granted: RouteProfile) -> RouteProfile:
    """Reconcile an explicitly requested profile against the granted one.

    Omitting `profile` is the normal case; naming a profile the token does not
    grant is a 403, never a silent fallback to the granted profile.
    """
    if requested is None or requested == granted.name:
        return granted
    raise forbidden(
        f"Token does not grant route profile {requested!r}.",
        f"This token is scoped to {granted.name!r}.",
    )

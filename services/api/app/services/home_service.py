"""Home lifecycle primitives (P0.9).

Until P0.9 the only path to a ``Home`` was :func:`app.services.auth_service.signup`,
which inlined the Home + HomeMembership creation. That was fine while signup was
the only caller; once ``POST /homes`` exists the primitive has to live in a
shared module so the two call sites cannot drift.

Today this module exposes a single function:

- :func:`create_home_for` — make a Home + OWNER HomeMembership for ``owner_id``.

Input validation (length, whitespace, ``extra="forbid"``) lives in the Pydantic
request schema (``app.schemas.home:HomeCreateRequest``); this layer only
normalizes (``str.strip`` on the name, ``Asia/Shanghai`` fallback on timezone)
and persists. Both signup and ``POST /homes`` therefore go through the same
write path, so ``test_signup_provisions_exactly_one_home`` keeps holding even
after the refactor.
"""
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import HomeRole
from app.models import Home, HomeMembership

#: Default timezone for a freshly-created Home when the caller did not name one.
#: Mirrors the model ``server_default`` so the same value lands whether we set it
#: explicitly here or let the DB fall back. Keep them in sync.
DEFAULT_HOME_TIMEZONE = "Asia/Shanghai"


async def create_home_for(
    db: AsyncSession,
    *,
    owner_id: uuid.UUID,
    name: str,
    timezone: str | None = None,
) -> Home:
    """Create a Home and make ``owner_id`` its OWNER.

    Single source of truth for the "new home" primitive — used by both
    :func:`app.services.auth_service.signup` and ``POST /homes``. Callers are
    responsible for *input* validation (length, extra fields, etc.); this layer
    only normalizes what it stores:

    - ``name`` is ``.strip()``'d. Empty-after-strip is the caller's job to
      reject *before* calling here (the request schema does that, surfacing
      400 via :class:`ValidationFailedError`).
    - ``timezone`` falls back to :data:`DEFAULT_HOME_TIMEZONE` when ``None`` or
      empty, matching the model's ``server_default``.

    The function does **not** call ``db.commit()`` — signup relies on the
    FastAPI session teardown to commit, and ``POST /homes`` follows the same
    pattern (one consistent transactional boundary per request, mirroring
    how every other write route in this app is written).
    """
    home = Home(
        id=uuid.uuid4(),
        name=name.strip(),
        owner_id=owner_id,
        timezone=(timezone or DEFAULT_HOME_TIMEZONE),
    )
    db.add(home)
    await db.flush()
    db.add(
        HomeMembership(
            home_id=home.id,
            user_id=owner_id,
            role=HomeRole.OWNER.value,
        )
    )
    await db.flush()
    return home


__all__ = ["DEFAULT_HOME_TIMEZONE", "create_home_for"]

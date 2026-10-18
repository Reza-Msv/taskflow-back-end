"""
Bootstrap Administrative CLI for TaskFlow.

Allows securely creating or promoting an administrator in local development
or deployment environments without exposing any public registration endpoints.
"""

import argparse
import asyncio
import sys

from sqlalchemy.ext.asyncio import AsyncSession

from taskflow.core.security import hash_password
from taskflow.db.session import AsyncSessionLocal
from taskflow.domains.identity.models import User
from taskflow.domains.identity.repository import UserRepository


async def create_or_promote_admin(
    email: str,
    password: str | None = None,
    full_name: str | None = None,
    session: AsyncSession | None = None,
) -> User:
    """Create a new superuser or promote an existing user to superuser."""
    clean_email = email.strip().lower()

    async def _execute(s: AsyncSession) -> User:
        user_repo = UserRepository(s)
        user = await user_repo.get_by_email(clean_email)

        if user is None:
            if not password:
                raise ValueError("Password is required when creating a new admin user.")
            if len(password) < 8:
                raise ValueError("Password must be at least 8 characters.")

            user = await user_repo.create_user(
                email=clean_email,
                full_name=(full_name or "Administrator").strip(),
                is_active=True,
                is_superuser=True,
            )
            pwd_hash = hash_password(password)
            await user_repo.create_credentials(
                user_id=user.id,
                password_hash=pwd_hash,
            )
            await s.commit()
            await s.refresh(user)
            print(f"Successfully created admin user '{clean_email}' (ID: {user.id}).")
        else:
            user.is_superuser = True
            user.is_active = True
            if full_name:
                user.full_name = full_name.strip()
            if password:
                if len(password) < 8:
                    raise ValueError("Password must be at least 8 characters.")
                pwd_hash = hash_password(password)
                if user.credentials:
                    user.credentials.password_hash = pwd_hash
                else:
                    await user_repo.create_credentials(
                        user_id=user.id,
                        password_hash=pwd_hash,
                    )
            await s.commit()
            await s.refresh(user)
            print(
                f"Successfully promoted existing user '{clean_email}' to administrator."
            )

        return user

    if session is not None:
        return await _execute(session)
    else:
        async with AsyncSessionLocal() as s:
            return await _execute(s)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="TaskFlow Administrator Provisioning Tool",
    )
    parser.add_argument(
        "--email",
        required=True,
        help="Email address of the admin user to create or promote",
    )
    parser.add_argument(
        "--password",
        required=False,
        help="Password for new user or password update (min 8 chars)",
    )
    parser.add_argument(
        "--full-name",
        required=False,
        default="Administrator",
        help="Display name for the administrator",
    )

    args = parser.parse_args()

    try:
        asyncio.run(
            create_or_promote_admin(
                email=args.email,
                password=args.password,
                full_name=args.full_name,
            )
        )
    except Exception as exc:
        print(f"Error provisioning administrator: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

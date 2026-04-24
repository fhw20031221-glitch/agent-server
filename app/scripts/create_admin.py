from __future__ import annotations

import argparse

from sqlalchemy import select

from app.core.config import settings
from app.core.security import hash_password
from app.db.models import User
from app.db.session import SessionLocal


def main() -> None:
    parser = argparse.ArgumentParser(description="创建或更新管理员账户")
    parser.add_argument("--username", default=settings.admin_username)
    parser.add_argument("--password", default=settings.admin_password)
    parser.add_argument("--email", default=settings.admin_email)
    args = parser.parse_args()

    username = str(args.username or "").strip()
    password = str(args.password or "")
    email = str(args.email or "").strip() or None
    if not username or not password:
        raise SystemExit("必须提供 --username 和 --password，或配置 AGENT_SERVER_ADMIN_USERNAME/AGENT_SERVER_ADMIN_PASSWORD")

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == username))
        if user is None:
            user = User(
                username=username,
                password_hash=hash_password(password),
                email=email,
                role="admin",
                status="active",
                monthly_token_limit=settings.default_monthly_token_limit,
            )
            db.add(user)
            action = "已创建"
        else:
            user.password_hash = hash_password(password)
            user.email = email
            user.role = "admin"
            user.status = "active"
            db.add(user)
            action = "已更新"
        db.commit()
        print(f"{action}管理员账户: {username}")


if __name__ == "__main__":
    main()

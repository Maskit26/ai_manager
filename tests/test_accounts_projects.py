import asyncio
import io
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from ai_seller_core.persistence.models import Account, AccountMembership, Project

ROOT = Path(__file__).resolve().parents[1]


def alembic_config(output: io.StringIO | None = None) -> Config:
    config = Config(str(ROOT / "alembic.ini"), output_buffer=output)
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return config


def test_accounts_projects_offline_migration() -> None:
    output = io.StringIO()
    command.upgrade(alembic_config(output), "head", sql=True)
    sql = output.getvalue()

    assert "CREATE TABLE accounts" in sql
    assert "CREATE TABLE account_memberships" in sql
    assert "CREATE TABLE projects" in sql
    assert "FOREIGN KEY(account_id) REFERENCES accounts (id) ON DELETE RESTRICT" in sql
    assert "uq_account_memberships_account_user" in sql
    assert "ix_projects_account_id" in sql


def test_accounts_projects_postgresql_migrations() -> None:
    database_url = os.environ.get("AI_SELLER_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set AI_SELLER_TEST_DATABASE_URL to an isolated PostgreSQL test database")

    async def verify() -> None:
        engine = create_async_engine(database_url)
        schema = f"test_accounts_{uuid4().hex}"
        schema_created = False
        try:
            async with engine.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            schema_created = True
            async with engine.begin() as connection:
                await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))

                def upgrade(sync_connection) -> None:
                    config = alembic_config()
                    config.attributes["connection"] = sync_connection
                    command.upgrade(config, "head")
                    command.upgrade(config, "head")

                await connection.run_sync(upgrade)
                assert await connection.scalar(text("SELECT count(*) FROM alembic_version")) == 1
                assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                    "20261118_accounts_projects"
                )

                project = {
                    "id": "project-1",
                    "account_id": "missing",
                    "locale": "ru",
                    "timezone": "UTC",
                    "settings": {},
                    "status": "active",
                }
                with pytest.raises(IntegrityError):
                    async with connection.begin_nested():
                        await connection.execute(insert(Project), project)

                await connection.execute(
                    insert(Account),
                    {"id": "account-1", "external_id": "external-1", "status": "active"},
                )
                await connection.execute(insert(Project), {**project, "account_id": "account-1"})
                await connection.execute(
                    insert(AccountMembership),
                    {
                        "id": "member-1",
                        "account_id": "account-1",
                        "user_id": "user-1",
                        "role": "owner",
                    },
                )
                for statement, values in (
                    (
                        insert(Account),
                        {"id": "account-2", "external_id": "external-1", "status": "active"},
                    ),
                    (
                        insert(AccountMembership),
                        {
                            "id": "member-2",
                            "account_id": "account-1",
                            "user_id": "user-1",
                            "role": "admin",
                        },
                    ),
                    (
                        insert(AccountMembership),
                        {
                            "id": "member-3",
                            "account_id": "account-1",
                            "user_id": "user-2",
                            "role": "invalid",
                        },
                    ),
                    (
                        insert(AccountMembership),
                        {
                            "id": "member-4",
                            "account_id": "missing",
                            "user_id": "user-2",
                            "role": "operator",
                        },
                    ),
                    (delete(Account).where(Account.id == "account-1"), {}),
                ):
                    with pytest.raises(IntegrityError):
                        async with connection.begin_nested():
                            await connection.execute(statement, values)
                assert await connection.scalar(select(Project.id)) == "project-1"
        finally:
            try:
                if schema_created:
                    async with engine.begin() as connection:
                        await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            finally:
                await engine.dispose()

    asyncio.run(verify())

"""Credentials stay in process configuration, never diagnostic state or rows."""

import os

from pydantic import BaseModel, ConfigDict, SecretStr, model_validator
from sqlalchemy.engine import make_url


class DatabaseSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    database_url: SecretStr
    checkpoint_database_url: SecretStr

    @model_validator(mode="after")
    def validate_urls(self):
        try:
            app = make_url(self.database_url.get_secret_value())
            checkpoint = make_url(self.checkpoint_database_url.get_secret_value())
            if app.drivername != "postgresql+asyncpg" or checkpoint.drivername != "postgresql":
                raise ValueError
            if not app.database or not checkpoint.database:
                raise ValueError
        except Exception:
            raise ValueError("Use PostgreSQL asyncpg application and PostgreSQL checkpoint URLs") from None
        return self

    @classmethod
    def from_env(cls):
        app, checkpoint = os.getenv("DATABASE_URL"), os.getenv("CHECKPOINT_DATABASE_URL")
        if not app or not checkpoint:
            raise ValueError("DATABASE_URL and CHECKPOINT_DATABASE_URL must be configured")
        return cls(database_url=app, checkpoint_database_url=checkpoint)

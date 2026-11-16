import os

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = (
    "postgresql+asyncpg://ai_seller:test@127.0.0.1:5432/ai_seller_test"
)

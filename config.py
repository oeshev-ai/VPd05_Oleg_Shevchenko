"""Загрузка и строгая валидация настроек бота из переменных окружения."""

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки приложения. Обязательные поля читаются из `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Токен Telegram-бота. Без него polling запустить нельзя.
    bot_token: SecretStr = Field(..., alias="BOT_TOKEN")

    # Ключ api.exchangerate.host. Уходит в запрос как access_key.
    api_key: SecretStr = Field(..., alias="API_KEY", min_length=1)

    # Файл локальной базы SQLite.
    db_path: str = Field(default="wallet.db", alias="DB_PATH")

    @field_validator("bot_token")
    @classmethod
    def validate_bot_token(cls, value: SecretStr) -> SecretStr:
        """Токен должен быть непустым и похожим на токен BotFather (`123:ABC`)."""
        token = value.get_secret_value().strip()
        if not token or token == "your_bot_token_here":
            raise ValueError(
                "BOT_TOKEN не задан. Укажите токен бота в файле .env."
            )
        if ":" not in token:
            raise ValueError(
                "BOT_TOKEN имеет неверный формат. Ожидается строка вида 123456:ABC..."
            )
        return SecretStr(token)

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr) -> SecretStr:
        """API_KEY обязателен: запросы без access_key запрещены."""
        key = value.get_secret_value().strip()
        if not key or key == "your_access_key_here":
            raise ValueError(
                "API_KEY не задан. Укажите access_key сервиса api.exchangerate.host в файле .env."
            )
        return SecretStr(key)

    @field_validator("db_path")
    @classmethod
    def validate_db_path(cls, value: str) -> str:
        """Путь к базе не должен быть пустым."""
        path = value.strip()
        if not path:
            raise ValueError("DB_PATH не может быть пустым.")
        return path


@lru_cache
def get_settings() -> Settings:
    """Возвращает единственный экземпляр настроек на процесс."""
    return Settings()

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    postgres_db: str
    postgres_user: str
    postgres_password: str
    postgres_host: str = "luka-postgres"
    postgres_port: int = 5432
    #mlx_server_url: str = "http://host.docker.internal:8181/luka"
    mlx_server_url: str = "http://192.168.0.90:8181/luka"
    jwt_secret: str = "cambia_esto_en_produccion"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7  # 7 días
    brevo_api_key: str
    brevo_from_email: str
    brevo_from_name: str = "LUKA"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()

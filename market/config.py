from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://market:market-local@127.0.0.1:55433/market"
    frontend_url: str = "http://localhost:3100"
    allowed_origins: str = "http://localhost:3100,http://127.0.0.1:3100"
    cookie_secure: bool = False
    minio_endpoint: str = "127.0.0.1:9100"
    minio_access_key: str = "market-local"
    minio_secret_key: str = "market-local-storage-password"
    minio_secure: bool = False
    minio_bucket: str = "market-products"
    max_image_bytes: int = 10 * 1024 * 1024
    max_image_pixels: int = 25_000_000
    max_product_images: int = 10
    claim_seconds: int = 180
    offer_hours: int = 24
    max_counteroffers: int = 3
    max_clarifications: int = 4
    max_conversation_turns: int = 12
    demo_password: str = "local-market-demo"
    demo_enabled: bool = False


settings = Settings()

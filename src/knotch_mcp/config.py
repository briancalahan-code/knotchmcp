from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    mcp_auth_token: str = ""
    apollo_api_key: str = ""
    clay_api_key: str = ""
    clay_webhook_url: str = ""
    clay_webhook_token: str = ""
    hubspot_private_app_token: str = ""
    hubspot_portal_id: str = "44523005"
    apollo_rate_limit: int = 45
    hubspot_max_retries: int = 3
    hubspot_retry_base_delay: float = 1.0
    hubspot_timeout: float = 15.0
    port: int = 8080

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

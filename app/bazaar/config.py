from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_APP_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_APP_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "dev"
    db_path: str = str(_APP_DIR / "bazaar.db")
    public_base_url: str = "https://r2-d2.xyz"
    rzp_key_id: str = ""
    rzp_key_secret: str = ""
    rzp_webhook_secret: str = ""
    llm_provider: str = "mock"  # mock | gemini | groq | nvidia
    llm_model: str = "gemini-3.6-flash"  # fast lane; pinned — flash-latest alias thinks >45 s (2026-08-24)
    llm_model_smart: str = "gemini-3.6-flash"    # slow lane (daily strategy)
    gemini_api_key: str = ""
    groq_api_key: str = ""
    nvidia_api_key: str = ""  # free NIM tier — break-glass backup provider
    nvidia_model: str = "meta/llama-3.3-70b-instruct"
    openrouter_api_key: str = ""  # OpenRouter — fast free-tier lane
    openrouter_model: str = "stealth/ox-alpha"
    mandate_secret: str = ""  # falls back to a derived key in dev

    # How consent envelopes are sealed.
    #
    #   hmac    (default) — symmetric. One shared key both seals and
    #           verifies. Simple, and fine while the merchant is the only
    #           party that ever mints an envelope.
    #
    #   ed25519 — asymmetric. The BUYER seals with a private key; the
    #           merchant verifies with the public key it publishes at
    #           /.well-known/bazaar-mandate-key. The merchant cannot forge
    #           a buyer's consent, which is the property that actually
    #           matters once a third party holds the envelope.
    #
    # The distinction is not academic: with hmac, "the merchant says the
    # buyer agreed" and "the buyer agreed" are the same statement, because
    # the merchant holds the only key. Ed25519 splits them.
    mandate_signing: str = "hmac"  # hmac | ed25519

    # base64 of the 32-byte Ed25519 private SEED (not the PKCS#8 blob).
    # Only read when mandate_signing=ed25519. The public key is derived
    # from it, so there is exactly one secret to manage and rotate.
    # Generate one with:  python scripts/mandate_keygen.py
    mandate_ed25519_seed: str = ""


settings = Settings()

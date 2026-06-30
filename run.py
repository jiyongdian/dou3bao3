import os

from app.config import ensure_config, load_settings


def resolve_port(default: int) -> int:
    for key in ("PORT", "WEB_PORT", "ZEA_WEB_PORT", "ZEABUR_WEB_PORT"):
        value = os.environ.get(key, "").strip()
        if value.isdigit():
            return int(value)
    return default


def main() -> None:
    import uvicorn

    ensure_config()
    settings = load_settings()
    uvicorn.run("app.main:app", host=settings.host, port=resolve_port(settings.port))


if __name__ == "__main__":
    main()

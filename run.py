from app.config import ensure_config, load_settings


def main() -> None:
    import uvicorn

    ensure_config()
    settings = load_settings()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()

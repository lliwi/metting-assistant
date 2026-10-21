from starlette.types import ASGIApp, Receive, Scope, Send


class RootPathMiddleware:
    """Fija el root_path del ASGI scope: el proxy publica la API bajo un prefijo (p. ej. /api)
    que ya ha recortado. Sirve para que OpenAPI y las URLs generadas lleven el prefijo."""

    def __init__(self, app: ASGIApp, root_path: str) -> None:
        self.app = app
        self.root_path = root_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            scope["root_path"] = self.root_path
        await self.app(scope, receive, send)

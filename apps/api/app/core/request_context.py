from dataclasses import dataclass

from fastapi import Request

from app.core.config import get_settings
from app.core.correlation import get_correlation_id
from app.core.security import pseudonymize_ip


@dataclass(frozen=True)
class ClientInfo:
    ip: str | None
    user_agent: str | None
    correlation_id: str | None

    @property
    def ip_pseudonym(self) -> str | None:
        return pseudonymize_ip(self.ip)


def get_client_info(request: Request) -> ClientInfo:
    ip = request.client.host if request.client else None
    if get_settings().trust_proxy_headers:
        ip = request.headers.get("x-real-ip") or ip
    user_agent = request.headers.get("user-agent")
    return ClientInfo(
        ip=ip,
        user_agent=user_agent[:512] if user_agent else None,
        correlation_id=get_correlation_id(),
    )

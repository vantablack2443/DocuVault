import secrets

from starlette.requests import Request

CLIENT_IP_HEADER = "x-docuvault-client-ip"
PROXY_SECRET_HEADER = "x-docuvault-proxy-secret"  # noqa: S105


def client_ip(request: Request, trusted_proxy_hops: int, proxy_secret: str | None = None) -> str:
    """Address of the caller, for rate limiting.

    Behind a reverse proxy the socket peer is the proxy, so every visitor would
    share one limit. Two ways to learn the real address, in this order:

    1. Our own edge proxy (the dashboard's Worker) states the visitor address in
       a header and proves who it is with a shared secret. Without the right
       secret the header is ignored, so a visitor cannot pick their own address.
    2. Each proxy appends the address it saw to `X-Forwarded-For`; with N trusted
       proxies in front, the Nth entry from the right was written by
       infrastructure we control. Entries further left can be forged.

    With no secret and `trusted_proxy_hops = 0` (local development) both headers
    are ignored.
    """
    if proxy_secret:
        presented = request.headers.get(PROXY_SECRET_HEADER, "")
        stated = request.headers.get(CLIENT_IP_HEADER, "").strip()
        if stated and secrets.compare_digest(presented.encode(), proxy_secret.encode()):
            return stated
    if trusted_proxy_hops > 0:
        forwarded = [
            part.strip()
            for part in request.headers.get("x-forwarded-for", "").split(",")
            if part.strip()
        ]
        if len(forwarded) >= trusted_proxy_hops:
            return forwarded[-trusted_proxy_hops]
    return request.client.host if request.client else "unknown"

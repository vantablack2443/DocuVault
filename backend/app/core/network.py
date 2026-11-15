from starlette.requests import Request


def client_ip(request: Request, trusted_proxy_hops: int) -> str:
    """Address of the caller, for rate limiting.

    Behind a reverse proxy the socket peer is the proxy, so every visitor would
    share one limit. Each proxy appends the address it saw to `X-Forwarded-For`;
    with N trusted proxies in front, the Nth entry from the right is the last
    one written by infrastructure we control. Entries further left are supplied
    by the client and can be forged, so they are never used.

    With `trusted_proxy_hops = 0` (direct exposure, local development) the
    header is ignored entirely.
    """
    if trusted_proxy_hops > 0:
        forwarded = [
            part.strip()
            for part in request.headers.get("x-forwarded-for", "").split(",")
            if part.strip()
        ]
        if len(forwarded) >= trusted_proxy_hops:
            return forwarded[-trusted_proxy_hops]
    return request.client.host if request.client else "unknown"

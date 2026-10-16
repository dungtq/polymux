def main() -> None:
    import asyncio
    import socket

    import uvicorn

    from pm.core.config import load_settings, setup_logging
    from pm.web.app import create_app

    setup_logging()
    s = load_settings()
    socks = []
    for host in s.binds:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, s.port))
        socks.append(sock)
    server = uvicorn.Server(uvicorn.Config(create_app(s), log_level="info", timeout_graceful_shutdown=3))
    asyncio.run(server.serve(sockets=socks))

"""Exit health probes used by the manual job and the periodic scheduler.

Node exits are checked with a TCP connect to their REALITY interconnect port.
External HTTP/SOCKS5 proxies are checked by tunnelling a request to
http://ifconfig.me/ip and (when configured) comparing the observed exit IP with
exit.expected_exit_ip.
"""

from __future__ import annotations

import asyncio
import time

CHECK_HOST = "ifconfig.me"
CHECK_PORT = 80
DEFAULT_TIMEOUT = 8.0


async def _probe_node(host: str, port: int, timeout: float) -> tuple[bool, int | None, str]:
    started = time.monotonic()
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (TimeoutError, OSError) as exc:
        return False, None, f"connect failed: {exc}"
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return True, int((time.monotonic() - started) * 1000), "tcp connect ok"


async def _read_http_response(reader: asyncio.StreamReader, limit: int = 8192) -> bytes:
    data = b""
    while len(data) < limit:
        chunk = await reader.read(2048)
        if not chunk:
            break
        data += chunk
        if b"\r\n\r\n" in data and len(data) > 64:
            break
    return data


async def _probe_http_proxy(host: str, port: int, timeout: float) -> tuple[bool, int | None, str, str | None]:
    started = time.monotonic()
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        request = (
            f"CONNECT {CHECK_HOST}:{CHECK_PORT} HTTP/1.1\r\n"
            f"Host: {CHECK_HOST}:{CHECK_PORT}\r\n"
            "Proxy-Connection: keep-alive\r\n\r\n"
        )
        writer.write(request.encode())
        await writer.drain()
        response = await asyncio.wait_for(_read_http_response(reader), timeout)
        if b" 200 " not in response.split(b"\r\n", 1)[0]:
            writer.close()
            return False, None, f"proxy CONNECT failed: {response[:120]!r}", None
        writer.write(f"GET /ip HTTP/1.1\r\nHost: {CHECK_HOST}\r\nConnection: close\r\n\r\n".encode())
        await writer.drain()
        body = await asyncio.wait_for(reader.read(4096), timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        text = body.decode("utf-8", "replace")
        observed = text.rsplit("\r\n\r\n", 1)[-1].strip() or None
        return True, int((time.monotonic() - started) * 1000), "http proxy ok", observed
    except (TimeoutError, OSError) as exc:
        return False, None, f"http proxy failed: {exc}", None


async def _probe_socks5(host: str, port: int, timeout: float) -> tuple[bool, int | None, str, str | None]:
    started = time.monotonic()
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        writer.write(b"\x05\x01\x00")
        await writer.drain()
        reply = await asyncio.wait_for(reader.readexactly(2), timeout)
        if reply != b"\x05\x00":
            writer.close()
            return False, None, f"socks5 auth rejected: {reply!r}", None
        host_bytes = CHECK_HOST.encode()
        writer.write(b"\x05\x01\x00\x03" + bytes([len(host_bytes)]) + host_bytes + CHECK_PORT.to_bytes(2, "big"))
        await writer.drain()
        header = await asyncio.wait_for(reader.readexactly(4), timeout)
        if header[0] != 5 or header[1] != 0:
            writer.close()
            return False, None, f"socks5 CONNECT failed: {header!r}", None
        atyp = header[3]
        if atyp == 1:
            await reader.readexactly(4 + 2)
        elif atyp == 3:
            length = await reader.readexactly(1)
            await reader.readexactly(length[0] + 2)
        elif atyp == 4:
            await reader.readexactly(16 + 2)
        writer.write(f"GET /ip HTTP/1.1\r\nHost: {CHECK_HOST}\r\nConnection: close\r\n\r\n".encode())
        await writer.drain()
        body = await asyncio.wait_for(reader.read(4096), timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        text = body.decode("utf-8", "replace")
        observed = text.rsplit("\r\n\r\n", 1)[-1].strip() or None
        return True, int((time.monotonic() - started) * 1000), "socks5 proxy ok", observed
    except (TimeoutError, OSError, asyncio.IncompleteReadError) as exc:
        return False, None, f"socks5 proxy failed: {exc}", None


async def check_exit(exit_row, timeout: float = DEFAULT_TIMEOUT) -> tuple[str, int | None, str]:
    """Return (health, delay_ms, detail) for an Exit ORM row."""
    if exit_row.kind == "node":
        ok, delay, detail = await _probe_node(exit_row.endpoint_host, exit_row.endpoint_port, timeout)
        return ("up" if ok else "down"), delay, detail
    if exit_row.protocol == "http":
        ok, delay, detail, observed = await _probe_http_proxy(
            exit_row.endpoint_host, exit_row.endpoint_port, timeout
        )
    else:
        ok, delay, detail, observed = await _probe_socks5(
            exit_row.endpoint_host, exit_row.endpoint_port, timeout
        )
    if ok and exit_row.expected_exit_ip:
        if observed != exit_row.expected_exit_ip:
            return "down", delay, f"exit IP mismatch: expected {exit_row.expected_exit_ip}, got {observed}"
        detail = f"exit IP {observed} ok"
    return ("up" if ok else "down"), delay, detail

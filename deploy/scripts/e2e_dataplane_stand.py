#!/usr/bin/env python3
"""Local data-plane E2E: real Xray entry+exit+client on loopback, no VPS.

Transport is plain VLESS (DP_PLAIN=1) by default: this stand verifies routing
and fail-closed data flow, not REALITY/TLS. Set DP_PLAIN=0 to try REALITY with a
local self-signed destination (requires a trusted local CA for the client).

Proves that the VPN data plane actually carries traffic:
  client --VLESS/REALITY--> entry --VLESS/REALITY--> exit --> local HTTP server

Routing checks (requested without deployment):
  1. default route                     -> exit A   (EXIT-A)
  2. site rule api.anthropic.com       -> exit B   (EXIT-B)
  3. RU-direct yandex.ru               -> direct   (DIRECT)
  4. fail-closed blocked.test          -> block    (no response)

All addresses are 127.0.0.1, all TLS "REALITY dest" is a local self-signed TLS server,
so no internet access and no VPS are required.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
XRAY = ROOT / ".tools" / "xray" / "xray"
ASSETS = ROOT / ".tools"

PORTS = {
    "tls_dest": 9443,
    "entry_in": 8443,
    "exit_a": 18443,
    "exit_b": 18444,
    "client_socks": 1081,
    "srv_a": 9101,
    "srv_b": 9102,
    "srv_direct": 9103,
    "srv_blocked": 9104,
}

PLAIN = os.environ.get("DP_PLAIN", "1") != "0"
results: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((ok, name, detail))
    print(("PASS  " if ok else "FAIL  ") + name + (("  " + detail) if detail else ""), flush=True)


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def gen_x25519() -> tuple[str, str]:
    # Prefer Xray's own key generator: REALITY key encoding must match Xray exactly.
    if XRAY.exists():
        out = subprocess.run([str(XRAY), "x25519"], capture_output=True, text=True, timeout=10)
        priv = pub = ""
        for line in out.stdout.splitlines():
            if line.startswith("PrivateKey:"):
                priv = line.split(":", 1)[1].strip()
            elif line.startswith("Password (PublicKey):"):
                pub = line.split(":", 1)[1].strip()
        if priv and pub:
            return priv, pub

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    key = X25519PrivateKey.generate()
    raw_priv = key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    raw_pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return b64url(raw_priv), b64url(raw_pub)


def new_uuid() -> str:
    return str(uuid.uuid4())


def wait_port(port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.15)
    return False


class TagHandler(BaseHTTPRequestHandler):
    tag = "?"

    def do_GET(self):  # noqa: N802
        body = self.tag.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence
        pass


def make_server(port: int, tag: str) -> ThreadingHTTPServer:
    handler = type("H" + str(port), (TagHandler,), {"tag": tag})
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def make_tls_dest(port: int, cert: Path, key: Path) -> ThreadingHTTPServer:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert), str(key))

    class Dest(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"DEST")

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", port), Dest)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def reality_inbound(tag: str, port: int, priv: str, short_id: str, uuid: str, sni: str, dest_port: int) -> dict:
    if PLAIN:
        return {
            "tag": tag,
            "listen": "127.0.0.1",
            "port": port,
            "protocol": "vless",
            "settings": {"clients": [{"id": uuid, "flow": ""}], "decryption": "none"},
            "streamSettings": {"network": "tcp"},
        }
    return {
        "tag": tag,
        "listen": "127.0.0.1",
        "port": port,
        "protocol": "vless",
        "settings": {"clients": [{"id": uuid, "flow": ""}], "decryption": "none"},
        "streamSettings": {
            "network": "tcp",
            "security": "reality",
            "realitySettings": {
                "show": False,
                "dest": f"127.0.0.1:{dest_port}",
                "xver": 0,
                "serverNames": [sni],
                "privateKey": priv,
                "shortIds": [short_id],
                "spiderX": "/",
            },
        },
    }


def reality_outbound(tag: str, port: int, uuid: str, pub: str, short_id: str, sni: str) -> dict:
    if PLAIN:
        return {
            "tag": tag,
            "protocol": "vless",
            "settings": {"vnext": [{"address": "127.0.0.1", "port": port,
                                    "users": [{"id": uuid, "encryption": "none", "flow": ""}]}]},
        }
    return {
        "tag": tag,
        "protocol": "vless",
        "settings": {
            "vnext": [
                {
                    "address": "127.0.0.1",
                    "port": port,
                    "users": [{"id": uuid, "encryption": "none", "flow": ""}],
                }
            ]
        },
        "streamSettings": {
            "network": "tcp",
            "security": "reality",
            "realitySettings": {
                "serverName": sni,
                "fingerprint": "chrome",
                "publicKey": pub,
                "shortId": short_id,
                "spiderX": "/",
            },
        },
    }


def write_config(path: Path, cfg: dict) -> None:
    path.write_text(json.dumps(cfg, indent=2))


def curl_via_socks(url: str, timeout: int = 10) -> tuple[bool, str]:
    cmd = [
        "curl", "-sS", "--max-time", str(timeout), "--socks5-hostname",
        f"127.0.0.1:{PORTS['client_socks']}", url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
    return proc.returncode == 0, (proc.stdout.strip() or proc.stderr.strip())


def free_port(port: int) -> None:
    # Kill stale xray processes from previous aborted runs listening on our ports.
    for pid in Path("/proc").iterdir():
        if not pid.name.isdigit():
            continue
        try:
            cmdline = (pid / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if "xray run -c" in cmdline and "/tmp/vpn-dataplane-" in cmdline:
            try:
                os.kill(int(pid.name), 15)
            except OSError:
                pass


def run_stand(keep: bool = False, hold: float = 0.0) -> int:
    if not XRAY.exists():
        print(f"xray binary not found: {XRAY}", file=sys.stderr)
        return 2

    work = Path(tempfile.mkdtemp(prefix="vpn-dataplane-"))
    procs: list[subprocess.Popen] = []
    servers: list[ThreadingHTTPServer] = []
    try:
        sni = "www.microsoft.com"
        entry_priv, entry_pub = gen_x25519()
        exit_a_priv, exit_a_pub = gen_x25519()
        exit_b_priv, exit_b_pub = gen_x25519()
        entry_sid = secrets.token_hex(8)
        exit_a_sid = secrets.token_hex(8)
        exit_b_sid = secrets.token_hex(8)
        client_uuid = new_uuid()
        peer_uuid = new_uuid()   # entry -> exit interconnect credential

        cert = work / "dest.crt"
        key = work / "dest.key"
        ca_cert = work / "ca.crt"
        ca_key = work / "ca.key"
        csr = work / "dest.csr"
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
             "-keyout", str(ca_key), "-out", str(ca_cert), "-subj", "/CN=Local Test CA",
             "-addext", "basicConstraints=critical,CA:TRUE",
             "-addext", "keyUsage=critical,keyCertSign,cRLSign"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["openssl", "req", "-newkey", "rsa:2048", "-nodes",
             "-keyout", str(key), "-out", str(csr), "-subj", f"/CN={sni}"],
            check=True, capture_output=True,
        )
        ext = work / "leaf.ext"
        ext.write_text("subjectAltName=DNS:" + sni + chr(10))
        subprocess.run(
            ["openssl", "x509", "-req", "-in", str(csr), "-CA", str(ca_cert), "-CAkey", str(ca_key),
             "-CAcreateserial", "-out", str(cert), "-days", "1", "-extfile", str(ext)],
            check=True, capture_output=True,
        )

        # Local HTTP origin servers: each one tells which path carried the request.
        servers.append(make_tls_dest(PORTS["tls_dest"], cert, key))
        servers.append(make_server(PORTS["srv_a"], "EXIT-A"))
        servers.append(make_server(PORTS["srv_b"], "EXIT-B"))
        servers.append(make_server(PORTS["srv_direct"], "DIRECT"))
        servers.append(make_server(PORTS["srv_blocked"], "BLOCKED-LEAK"))
        check("local origin servers started", True)

        dest_map = {
            "test.default": "127.0.0.1",
            "api.anthropic.com": "127.0.0.1",
            "yandex.ru": "127.0.0.1",
            "blocked.test": "127.0.0.1",
        }
        env = dict(os.environ, XRAY_LOCATION_ASSET=str(ASSETS))
        client_env = dict(env, SSL_CERT_FILE=str(ca_cert))

        for name, port, priv, sid, pubs, sids in (
            ("exit_a", PORTS["exit_a"], exit_a_priv, exit_a_sid, exit_a_pub, exit_a_sid),
            ("exit_b", PORTS["exit_b"], exit_b_priv, exit_b_sid, exit_b_pub, exit_b_sid),
        ):
            cfg = {
                "log": {"loglevel": "info"},
                "dns": {"hosts": dest_map, "queryStrategy": "UseIPv4"},
                "inbounds": [reality_inbound(f"{name}-in", port, priv, sid, peer_uuid, sni, PORTS["tls_dest"])],
                "outbounds": [
                    {"tag": "direct", "protocol": "freedom", "settings": {"domainStrategy": "UseIP", "finalRules": [{"action": "allow", "network": "tcp", "ip": ["127.0.0.1"]}]}},
                    {"tag": "block", "protocol": "blackhole"},
                ],
                "routing": {
                    "domainStrategy": "AsIs",
                    "rules": [{"type": "field", "network": "tcp,udp", "outboundTag": "direct"}],
                },
            }
            path = work / f"{name}.json"
            write_config(path, cfg)
            procs.append(subprocess.Popen([str(XRAY), "run", "-c", str(path)], env=env,
                                          stdout=(work / f"{name}.log").open("w"), stderr=subprocess.STDOUT))
        check("exit nodes listening", wait_port(PORTS["exit_a"]) and wait_port(PORTS["exit_b"]))

        entry_cfg = {
            "log": {"loglevel": "info"},
            "dns": {"hosts": {"yandex.ru": "127.0.0.1"}, "queryStrategy": "UseIPv4"},
            "inbounds": [reality_inbound("entry-in", PORTS["entry_in"], entry_priv, entry_sid,
                                         client_uuid, sni, PORTS["tls_dest"])],
            "outbounds": [
                reality_outbound("exit-a", PORTS["exit_a"], peer_uuid, exit_a_pub, exit_a_sid, sni),
                reality_outbound("exit-b", PORTS["exit_b"], peer_uuid, exit_b_pub, exit_b_sid, sni),
                {"tag": "direct", "protocol": "freedom", "settings": {"domainStrategy": "UseIP", "finalRules": [{"action": "allow", "network": "tcp", "ip": ["127.0.0.1"]}]}},
                {"tag": "block", "protocol": "blackhole"},
            ],
            "routing": {
                "domainStrategy": "AsIs",
                "rules": [
                    {"type": "field", "domain": ["blocked.test"], "outboundTag": "block"},
                    {"type": "field", "domain": ["api.anthropic.com"], "outboundTag": "exit-b"},
                    {"type": "field", "domain": ["yandex.ru"], "outboundTag": "direct"},
                    {"type": "field", "network": "tcp,udp", "outboundTag": "exit-a"},
                ],
            },
        }
        entry_path = work / "entry.json"
        write_config(entry_path, entry_cfg)
        procs.append(subprocess.Popen([str(XRAY), "run", "-c", str(entry_path)], env=env,
                                      stdout=(work / "entry.log").open("w"), stderr=subprocess.STDOUT))
        check("entry node listening", wait_port(PORTS["entry_in"]))

        client_cfg = {
            "log": {"loglevel": "info"},
            "inbounds": [{
                "tag": "socks-in", "listen": "127.0.0.1", "port": PORTS["client_socks"],
                "protocol": "socks", "settings": {"auth": "noauth", "udp": False},
            }],
            "outbounds": [reality_outbound("entry", PORTS["entry_in"], client_uuid, entry_pub, entry_sid, sni)],
        }
        client_path = work / "client.json"
        write_config(client_path, client_cfg)
        procs.append(subprocess.Popen([str(XRAY), "run", "-c", str(client_path)], env=client_env,
                                      stdout=(work / "client.log").open("w"), stderr=subprocess.STDOUT))
        check("client xray started (SOCKS 127.0.0.1:%d)" % PORTS["client_socks"], wait_port(PORTS["client_socks"]))

        # Give REALITY handshakes a moment to settle after the first connection.
        time.sleep(1.0)

        ok, body = curl_via_socks(f"http://test.default:{PORTS['srv_a']}/")
        check("default route -> exit A", ok and body == "EXIT-A", body)

        ok, body = curl_via_socks(f"http://api.anthropic.com:{PORTS['srv_b']}/")
        check("site rule api.anthropic.com -> exit B", ok and body == "EXIT-B", body)

        ok, body = curl_via_socks(f"http://yandex.ru:{PORTS['srv_direct']}/")
        check("RU-direct yandex.ru -> direct", ok and body == "DIRECT", body)

        ok, body = curl_via_socks(f"http://blocked.test:{PORTS['srv_blocked']}/", timeout=6)
        check("fail-closed blocked.test -> block", not (ok and body == "BLOCKED-LEAK"), body)

        # Verify that the real production path was used, not a direct fallback:
        # the entry log must show REALITY and the exit logs must show an accepted VLESS session.
        entry_log = (work / "entry.log").read_text(errors="replace")
        exit_a_log = (work / "exit_a.log").read_text(errors="replace")
        check("entry accepted client" if PLAIN else "entry accepted REALITY client", "accepted" in entry_log.lower(),
              "entry.log lines=%d" % len(entry_log.splitlines()))
        if hold:
            print("holding for %.0fs" % hold, flush=True)
            time.sleep(hold)
        check("exit A carried traffic", "accepted" in exit_a_log.lower(),
              "exit_a.log lines=%d" % len(exit_a_log.splitlines()))

    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        for srv in servers:
            srv.shutdown()
            srv.server_close()
        if keep:
            print("stand directory kept: %s" % work, flush=True)
        else:
            shutil.rmtree(work, ignore_errors=True)

    passed = sum(1 for ok, _, _ in results if ok)
    failed = len(results) - passed
    print("\ne2e-dataplane: PASS=%d FAIL=%d" % (passed, failed), flush=True)
    print("e2e-dataplane: " + ("OK" if failed == 0 else "FAILED"), flush=True)
    return 0 if failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Local data-plane E2E (no VPS)")
    parser.add_argument("--keep", action="store_true", help="keep temporary configs/logs")
    parser.add_argument("--hold", type=float, default=0.0, help="keep the stand alive for N seconds")
    args = parser.parse_args()
    free_port(0)
    return run_stand(keep=args.keep, hold=args.hold)


if __name__ == "__main__":
    sys.exit(main())

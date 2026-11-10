"""Connection transport variants: one VLESS+REALITY endpoint per supported transport.

The list is the single source of truth shared by the entry compiler (extra inbound
builders), link generation and the connection API. Ports are fixed by design: the
agent/firewall provisioning opens exactly these ports.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TransportVariant:
    id: str
    label: str
    description: str
    network: str  # "tcp" | "xhttp" | "grpc"
    port: int
    flow: str = ""
    path: str | None = None  # xhttp only
    service_name: str | None = None  # grpc only


VARIANTS: tuple[TransportVariant, ...] = (
    TransportVariant(
        id="tcp-vision",
        label="TCP + Vision",
        description="Основной профиль: VLESS TCP REALITY с XTLS Vision (максимальная скорость).",
        network="tcp",
        port=443,
        flow="xtls-rprx-vision",
    ),
    TransportVariant(
        id="tcp",
        label="TCP",
        description="VLESS TCP REALITY без XTLS Vision — для клиентов без поддержки Vision.",
        network="tcp",
        port=2096,
    ),
    TransportVariant(
        id="xhttp",
        label="XHTTP",
        description="VLESS XHTTP REALITY — современный транспорт, поддерживается Incy и sing-box.",
        network="xhttp",
        port=2053,
        path="/vpn-xhttp",
    ),
    TransportVariant(
        id="grpc",
        label="gRPC",
        description="VLESS gRPC REALITY — запасной вариант для клиентов с gRPC.",
        network="grpc",
        port=8443,
        service_name="vpn-grpc",
    ),
)

MAIN_VARIANT = VARIANTS[0]
EXTRA_VARIANTS: tuple[TransportVariant, ...] = tuple(v for v in VARIANTS if v.id != MAIN_VARIANT.id)
VARIANT_PORTS: tuple[int, ...] = (443, 18443, *(v.port for v in EXTRA_VARIANTS))


def variant_by_id(variant_id: str) -> TransportVariant | None:
    return next((v for v in VARIANTS if v.id == variant_id), None)

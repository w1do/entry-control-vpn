#!/usr/bin/env python3
"""Ротация ENCRYPTION_KEY: перешифрование секретов в БД новым ключом.

Запускается ОДНОРАЗОВО внутри образа приложения (см. docs/RUNBOOK.md,
раздел «Ротация секретов»). Приложение должно быть остановлено.

Порядок:
1. Сгенерируйте новый Fernet-ключ и впишите его в deploy/.env / Dokploy.
2. Положите старый ключ в файл (по умолчанию /run/secrets/old.key, 0600).
3. Остановите app.
4. docker compose -f deploy/docker-compose.yml --env-file deploy/.env run --rm --no-deps \
       --entrypoint python app /scripts/rotate-encryption-key.py
   (каталог deploy/scripts монтируется в /scripts, old.key — в /run/secrets).
5. Удалите файл старого ключа, запустите app, проверьте вход и список серверов.

Скрипт меняет только зашифрованные поля:
  servers.secret_enc,
  server_secrets.reality_private_key_enc, server_secrets.interconnect_uuid_enc,
  exits.secret_enc.
Транзакция одна: при любой ошибке (в т.ч. неверном старом ключе) изменения
не коммитятся. Значения ключей в stdout/stderr не выводятся.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.core.crypto import SecretBox
from app.core.db import dispose_engine, get_session_factory, init_engine
from app.models.routing import Exit
from app.models.servers import Server, ServerSecret


def _fail(message: str) -> "None":
    print(f"rotate-encryption-key: ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def _read_old_key() -> str:
    path = Path(os.environ.get("OLD_ENCRYPTION_KEY_FILE", "/run/secrets/old.key"))
    if not path.is_file():
        _fail(f"old key file not found: {path} (см. OLD_ENCRYPTION_KEY_FILE)")
    key = path.read_text(encoding="utf-8").strip()
    if not key:
        _fail(f"old key file is empty: {path}")
    if key == os.environ.get("ENCRYPTION_KEY", "").strip():
        _fail("old и новый ENCRYPTION_KEY совпадают — сначала обновите deploy/.env")
    return key


async def _rotate(old_key: str, new_key: str) -> int:
    init_engine(get_settings())
    try:
        factory = get_session_factory()
        async with factory() as db:
            changed = 0
            old_box = SecretBox(old_key)
            new_box = SecretBox(new_key)

            for server in (await db.scalars(select(Server))).all():
                if server.secret_enc:
                    server.secret_enc = new_box.encrypt(old_box.decrypt(server.secret_enc))
                    changed += 1

            for secret in (await db.scalars(select(ServerSecret))).all():
                for field in ("reality_private_key_enc", "interconnect_uuid_enc"):
                    token = getattr(secret, field)
                    if token:
                        setattr(secret, field, new_box.encrypt(old_box.decrypt(token)))
                        changed += 1

            for exit_ in (await db.scalars(select(Exit))).all():
                if exit_.secret_enc:
                    exit_.secret_enc = new_box.encrypt(old_box.decrypt(exit_.secret_enc))
                    changed += 1

            await db.commit()
            return changed
    finally:
        await dispose_engine()


def main() -> None:
    old_key = _read_old_key()
    settings = get_settings()
    new_key = settings.encryption_key
    changed = asyncio.run(_rotate(old_key, new_key))
    print(f"rotate-encryption-key: OK, re-encrypted fields: {changed}")
    print("Не забудьте удалить файл старого ключа и запустить приложение.")


if __name__ == "__main__":
    main()

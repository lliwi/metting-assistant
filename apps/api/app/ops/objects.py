"""Copia del almacenamiento de objetos como tar por stdin/stdout (para scripts/backup.sh).

    python -m app.ops.objects export > objects.tar     # todo el bucket
    python -m app.ops.objects import < objects.tar     # sube (sobrescribe) cada objeto
    python -m app.ops.objects count                    # nº de objetos y bytes

Se ejecuta en el contenedor de la API: usa sus credenciales y no requiere exponer el S3.
Los objetos se copian en streaming, sin cargarlos enteros en memoria.
"""

import sys
import tarfile
import time
from collections.abc import Iterator
from typing import IO, Any

from app.storage import ObjectStorage, get_storage


def iter_objects(storage: ObjectStorage) -> Iterator[dict[str, Any]]:
    paginator = storage.client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=storage.bucket):
        yield from page.get("Contents", [])


def export_tar(storage: ObjectStorage, out: IO[bytes]) -> tuple[int, int]:
    """(objetos, bytes). El tar se escribe como flujo (no necesita salida con seek)."""
    count = size = 0
    with tarfile.open(fileobj=out, mode="w|") as tar:
        for obj in iter_objects(storage):
            body = storage.client.get_object(Bucket=storage.bucket, Key=obj["Key"])["Body"]
            info = tarfile.TarInfo(obj["Key"])
            info.size = int(obj["Size"])
            info.mtime = int(obj["LastModified"].timestamp()) if "LastModified" in obj else 0
            info.mode = 0o600
            try:
                tar.addfile(info, body)
            finally:
                body.close()
            count += 1
            size += info.size
    return count, size


def import_tar(storage: ObjectStorage, source: IO[bytes]) -> tuple[int, int]:
    count = size = 0
    extra = {"ServerSideEncryption": storage.sse} if storage.sse else {}
    with tarfile.open(fileobj=source, mode="r|") as tar:
        for member in tar:
            if not member.isfile():
                continue
            data = tar.extractfile(member)
            if data is None:
                continue
            # upload_fileobj sube por partes: válido para audios grandes.
            storage.client.upload_fileobj(data, storage.bucket, member.name, ExtraArgs=extra)
            count += 1
            size += member.size
    return count, size


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in ("export", "import", "count"):
        print(__doc__, file=sys.stderr)
        return 2
    storage = get_storage()
    started = time.monotonic()
    if argv[0] == "export":
        count, size = export_tar(storage, sys.stdout.buffer)
    elif argv[0] == "import":
        count, size = import_tar(storage, sys.stdin.buffer)
    else:
        objects = list(iter_objects(storage))
        count, size = len(objects), sum(int(o["Size"]) for o in objects)
        print(f"{count} {size}")
        return 0
    # Resumen por stderr: stdout es el propio tar.
    elapsed = time.monotonic() - started
    print(f"{argv[0]}: {count} objetos, {size / 1e6:.1f} MB en {elapsed:.1f} s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

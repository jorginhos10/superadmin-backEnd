import os

import pg8000.native as pg8000


def get_connection() -> pg8000.Connection:
    return pg8000.Connection(
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", "5432")),
        database=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )


def get_tenant_connection() -> pg8000.Connection:
    """Access to the shared chefcontrol database, where each restaurant is an
    owner account (usuarios.propietario = true)."""
    return pg8000.Connection(
        host=os.environ["TENANT_DB_HOST"],
        port=int(os.environ.get("TENANT_DB_PORT", "5432")),
        database=os.environ["TENANT_DB_NAME"],
        user=os.environ["TENANT_DB_USER"],
        password=os.environ["TENANT_DB_PASSWORD"],
    )

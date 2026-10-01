import os
import threading
from queue import Empty, Queue

import pg8000.native as pg8000

# Máximo de conexiones inactivas que se guardan por base de datos. Cada conexión nueva paga el
# apretón de manos TCP + autenticación de Postgres (~50 ms en local); reutilizarlas evita ese costo
# en cada petición.
_MAX_POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "10"))


class _PooledConnection(pg8000.Connection):
    """Una conexión de pg8000 que, al llamar close(), se devuelve a su pool en vez de cerrar el
    socket. El resto del código no cambia: sigue siendo `conn = get_connection(); ...; conn.close()`."""

    def close(self) -> None:
        self._pool.release(self)

    def close_real(self) -> None:
        pg8000.Connection.close(self)


class _ConnectionPool:
    def __init__(self, params: dict):
        self._params = params
        self._idle: Queue[_PooledConnection] = Queue(maxsize=_MAX_POOL_SIZE)
        self._lock = threading.Lock()

    def acquire(self) -> _PooledConnection:
        while True:
            try:
                conn = self._idle.get_nowait()
            except Empty:
                return self._create()
            if self._alive(conn):
                return conn
            # La base la cerró (reinicio, tiempo de inactividad): se descarta y se prueba otra.
            try:
                conn.close_real()
            except Exception:
                pass

    def release(self, conn: _PooledConnection) -> None:
        try:
            self._idle.put_nowait(conn)
        except Exception:
            # El pool ya está lleno: esta conexión de más sí se cierra de verdad.
            try:
                conn.close_real()
            except Exception:
                pass

    def _create(self) -> _PooledConnection:
        conn = _PooledConnection(**self._params)
        conn._pool = self
        return conn

    @staticmethod
    def _alive(conn: _PooledConnection) -> bool:
        try:
            conn.run("SELECT 1")
            return True
        except Exception:
            return False


_pools: dict[str, _ConnectionPool] = {}
_pools_lock = threading.Lock()


def _pool_for(key: str, params: dict) -> _ConnectionPool:
    pool = _pools.get(key)
    if pool is None:
        with _pools_lock:
            pool = _pools.get(key)
            if pool is None:
                pool = _ConnectionPool(params)
                _pools[key] = pool
    return pool


def get_connection() -> pg8000.Connection:
    return _pool_for(
        "main",
        dict(
            host=os.environ["DB_HOST"],
            port=int(os.environ.get("DB_PORT", "5432")),
            database=os.environ["DB_NAME"],
            user=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"],
        ),
    ).acquire()


def get_tenant_connection() -> pg8000.Connection:
    """Access to the shared chefcontrol database, where each restaurant is an
    owner account (usuarios.propietario = true)."""
    return _pool_for(
        "tenant",
        dict(
            host=os.environ["TENANT_DB_HOST"],
            port=int(os.environ.get("TENANT_DB_PORT", "5432")),
            database=os.environ["TENANT_DB_NAME"],
            user=os.environ["TENANT_DB_USER"],
            password=os.environ["TENANT_DB_PASSWORD"],
        ),
    ).acquire()

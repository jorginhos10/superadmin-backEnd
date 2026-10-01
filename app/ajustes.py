from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth import get_current_user
from app.database import get_connection
from app.schemas import UserOut

router = APIRouter(prefix="/superadmin/ajustes", tags=["ajustes"])


class CategoriaRegistrosOut(BaseModel):
    store: bool
    restobar: bool


class CategoriaRegistrosIn(BaseModel):
    store: bool
    restobar: bool


def _leer_categoria_registros(conn) -> CategoriaRegistrosOut:
    rows = conn.run("SELECT tipo_store_habilitado, tipo_restobar_habilitado FROM ajustes_generales WHERE id = 1")
    if not rows:
        return CategoriaRegistrosOut(store=True, restobar=True)
    return CategoriaRegistrosOut(store=bool(rows[0][0]), restobar=bool(rows[0][1]))


@router.get("/categoria-registros", response_model=CategoriaRegistrosOut)
def obtener_categoria_registros(current_user: UserOut = Depends(get_current_user)):
    """Qué tipos de comercio (Store / Restobar) se pueden elegir al registrarse."""
    conn = get_connection()
    try:
        return _leer_categoria_registros(conn)
    finally:
        conn.close()


@router.put("/categoria-registros", response_model=CategoriaRegistrosOut)
def guardar_categoria_registros(payload: CategoriaRegistrosIn, current_user: UserOut = Depends(get_current_user)):
    if not payload.store and not payload.restobar:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Debes dejar al menos un tipo de comercio habilitado",
        )
    conn = get_connection()
    try:
        conn.run(
            "INSERT INTO ajustes_generales (id, tipo_store_habilitado, tipo_restobar_habilitado) VALUES (1, :s, :r) "
            "ON CONFLICT (id) DO UPDATE SET tipo_store_habilitado = EXCLUDED.tipo_store_habilitado, "
            "tipo_restobar_habilitado = EXCLUDED.tipo_restobar_habilitado, updated_at = now()",
            s=payload.store, r=payload.restobar,
        )
        return CategoriaRegistrosOut(store=payload.store, restobar=payload.restobar)
    finally:
        conn.close()

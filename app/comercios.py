import os
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr

from app.auth import get_current_user
from app.database import get_tenant_connection
from app.schemas import UserOut

router = APIRouter(prefix="/superadmin/comercios", tags=["comercios"])

COMERCIO_COLUMNS = [
    "id", "nombre", "username", "email", "activo", "verificado", "fecha_creacion", "ultimo_login",
]


class ComercioOut(BaseModel):
    id: int
    nombre: str
    username: str
    email: str
    activo: bool
    verificado: bool
    fecha_creacion: datetime
    ultimo_login: Optional[datetime] = None
    total_staff: int


class ComercioEditIn(BaseModel):
    nombre: str
    email: EmailStr


class ImpersonarOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    frontend_url: str


def _get_comercio(conn, comercio_id: int) -> dict:
    rows = conn.run(
        f"""
        SELECT {', '.join('p.' + c for c in COMERCIO_COLUMNS)},
            (SELECT COUNT(*) FROM usuarios s WHERE s.propietario_id = p.id) AS total_staff
        FROM usuarios p
        WHERE p.id = :id AND p.propietario = true
        """,
        id=comercio_id,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comercio no encontrado")
    return dict(zip(COMERCIO_COLUMNS + ["total_staff"], rows[0]))


@router.get("", response_model=list[ComercioOut])
def listar_comercios(current_user: UserOut = Depends(get_current_user)):
    conn = get_tenant_connection()
    try:
        rows = conn.run(
            f"""
            SELECT {', '.join('p.' + c for c in COMERCIO_COLUMNS)},
                (SELECT COUNT(*) FROM usuarios s WHERE s.propietario_id = p.id) AS total_staff
            FROM usuarios p
            WHERE p.propietario = true
            ORDER BY p.fecha_creacion DESC
            """
        )
        columns = COMERCIO_COLUMNS + ["total_staff"]
        return [ComercioOut(**dict(zip(columns, row))) for row in rows]
    finally:
        conn.close()


@router.patch("/{comercio_id}", response_model=ComercioOut)
def editar_comercio(comercio_id: int, payload: ComercioEditIn, current_user: UserOut = Depends(get_current_user)):
    conn = get_tenant_connection()
    try:
        _get_comercio(conn, comercio_id)
        conn.run(
            "UPDATE usuarios SET nombre = :nombre, email = :email WHERE id = :id",
            nombre=payload.nombre, email=payload.email, id=comercio_id,
        )
        return ComercioOut(**_get_comercio(conn, comercio_id))
    finally:
        conn.close()


@router.post("/{comercio_id}/toggle-activo", response_model=ComercioOut)
def toggle_activo(comercio_id: int, current_user: UserOut = Depends(get_current_user)):
    conn = get_tenant_connection()
    try:
        _get_comercio(conn, comercio_id)
        conn.run("UPDATE usuarios SET activo = NOT activo WHERE id = :id", id=comercio_id)
        return ComercioOut(**_get_comercio(conn, comercio_id))
    finally:
        conn.close()


@router.post("/{comercio_id}/verificar", response_model=ComercioOut)
def toggle_verificado(comercio_id: int, current_user: UserOut = Depends(get_current_user)):
    conn = get_tenant_connection()
    try:
        _get_comercio(conn, comercio_id)
        conn.run("UPDATE usuarios SET verificado = NOT verificado WHERE id = :id", id=comercio_id)
        return ComercioOut(**_get_comercio(conn, comercio_id))
    finally:
        conn.close()


@router.post("/{comercio_id}/impersonar", response_model=ImpersonarOut)
def impersonar(comercio_id: int, current_user: UserOut = Depends(get_current_user)):
    conn = get_tenant_connection()
    try:
        _get_comercio(conn, comercio_id)
    finally:
        conn.close()

    chefcontrol_api = os.environ["CHEFCONTROL_API_URL"]
    service_secret = os.environ["CHEFCONTROL_SERVICE_SECRET"]

    try:
        resp = httpx.post(
            f"{chefcontrol_api}/auth/impersonate",
            json={"user_id": comercio_id},
            headers={"X-Service-Secret": service_secret},
            timeout=10,
        )
    except httpx.RequestError:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo contactar el backend de chefcontrol")

    if resp.status_code != 200:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo iniciar sesión como este comercio")

    data = resp.json()
    return ImpersonarOut(
        access_token=data["access_token"],
        frontend_url=os.environ["CHEFCONTROL_FRONTEND_URL"],
    )

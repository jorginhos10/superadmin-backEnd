from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth import get_current_user
from app.database import get_connection, get_tenant_connection
from app.schemas import UserOut

router = APIRouter(prefix="/superadmin/pagos-nequi", tags=["pagos-nequi"])

PENDIENTE = "pendiente_verificacion_nequi"


class PagoNequiOut(BaseModel):
    id: int
    usuario_id: int
    comercio_nombre: str
    plan_id: int
    plan_nombre: str
    monto: float
    codigo: str
    estado: str
    created_at: datetime


def _nombre_plan(plan_id: int) -> str:
    conn = get_connection()
    try:
        rows = conn.run("SELECT nombre FROM planes WHERE id = :id", id=plan_id)
        return rows[0][0] if rows else "(plan eliminado)"
    finally:
        conn.close()


def _row_to_pago(r) -> PagoNequiOut:
    codigo = r[4].removeprefix("nqi-")
    return PagoNequiOut(
        id=r[0], usuario_id=r[1], comercio_nombre=r[2] or "", plan_id=r[3], plan_nombre=_nombre_plan(r[3]),
        monto=float(r[5]), codigo=codigo, estado=r[6], created_at=r[7],
    )


@router.get("", response_model=list[PagoNequiOut])
def listar(estado: str = PENDIENTE, current_user: UserOut = Depends(get_current_user)):
    """Por defecto solo los pendientes de confirmar; pasa estado='' para ver todos."""
    conn = get_tenant_connection()
    try:
        where = "sp.metodo = 'nequi'"
        params: dict = {}
        if estado:
            where += " AND sp.estado = :estado"
            params["estado"] = estado
        rows = conn.run(
            f"""
            SELECT sp.id, sp.usuario_id, u.nombre, sp.plan_id, sp.wompi_reference, sp.monto, sp.estado, sp.created_at
            FROM suscripcion_pagos sp
            JOIN usuarios u ON u.id = sp.usuario_id
            WHERE {where}
            ORDER BY sp.created_at DESC
            """,
            **params,
        )
        return [_row_to_pago(r) for r in rows]
    finally:
        conn.close()


@router.post("/{pago_id}/aprobar", response_model=PagoNequiOut)
def aprobar(pago_id: int, current_user: UserOut = Depends(get_current_user)):
    conn = get_tenant_connection()
    try:
        rows = conn.run(
            "SELECT usuario_id, plan_id, estado FROM suscripcion_pagos WHERE id = :id AND metodo = 'nequi'",
            id=pago_id,
        )
        if not rows:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pago no encontrado")
        usuario_id, plan_id, estado_actual = rows[0]
        if estado_actual != PENDIENTE:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Este pago ya fue procesado")

        conn.run(
            "UPDATE suscripcion_pagos SET estado = 'pagado', updated_at = now() WHERE id = :id",
            id=pago_id,
        )
        conn.run(
            "UPDATE usuarios SET plan_id = :pid, plan_actualizado_en = now() WHERE id = :id",
            pid=plan_id, id=usuario_id,
        )

        r = conn.run(
            """
            SELECT sp.id, sp.usuario_id, u.nombre, sp.plan_id, sp.wompi_reference, sp.monto, sp.estado, sp.created_at
            FROM suscripcion_pagos sp JOIN usuarios u ON u.id = sp.usuario_id
            WHERE sp.id = :id
            """,
            id=pago_id,
        )[0]
        return _row_to_pago(r)
    finally:
        conn.close()


@router.post("/{pago_id}/rechazar", response_model=PagoNequiOut)
def rechazar(pago_id: int, current_user: UserOut = Depends(get_current_user)):
    conn = get_tenant_connection()
    try:
        rows = conn.run(
            "SELECT estado FROM suscripcion_pagos WHERE id = :id AND metodo = 'nequi'", id=pago_id,
        )
        if not rows:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pago no encontrado")
        if rows[0][0] != PENDIENTE:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Este pago ya fue procesado")

        conn.run(
            "UPDATE suscripcion_pagos SET estado = 'rechazado', updated_at = now() WHERE id = :id",
            id=pago_id,
        )
        r = conn.run(
            """
            SELECT sp.id, sp.usuario_id, u.nombre, sp.plan_id, sp.wompi_reference, sp.monto, sp.estado, sp.created_at
            FROM suscripcion_pagos sp JOIN usuarios u ON u.id = sp.usuario_id
            WHERE sp.id = :id
            """,
            id=pago_id,
        )[0]
        return _row_to_pago(r)
    finally:
        conn.close()

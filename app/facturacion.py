from datetime import date, datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.auth import get_current_user
from app.database import get_connection, get_tenant_connection
from app.schemas import UserOut

router = APIRouter(prefix="/superadmin/facturacion", tags=["facturacion"])

NEQUI_PENDIENTE = "pendiente_verificacion_nequi"
MAX_FILAS = 500


class PagoFacturacionOut(BaseModel):
    id: int
    usuario_id: int
    comercio_nombre: str
    plan_nombre: str
    monto: float
    metodo: str
    estado: str
    referencia: str
    transaccion_id: str | None = None
    created_at: datetime
    updated_at: datetime


class ResumenFacturacionOut(BaseModel):
    cobrado: float
    pagos_aprobados: int
    por_confirmar: int
    monto_por_confirmar: float
    sin_completar: int
    fallidos: int


class FacturacionOut(BaseModel):
    resumen: ResumenFacturacionOut
    pagos: list[PagoFacturacionOut]
    total_filas: int


def _nombres_planes(ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    conn = get_connection()
    try:
        rows = conn.run("SELECT id, nombre FROM planes WHERE id = ANY(:ids)", ids=list(ids))
        return {r[0]: r[1] for r in rows}
    finally:
        conn.close()


@router.get("", response_model=FacturacionOut)
def listar_facturacion(
    estado: str = Query(default=""),
    metodo: str = Query(default=""),
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    q: str = Query(default=""),
    current_user: UserOut = Depends(get_current_user),
):
    """Todos los pagos de suscripción (Wompi y Nequi) de todos los comercios. El resumen sale de los filtros
    de fecha, método y búsqueda; el filtro de estado solo recorta la lista."""
    where = ["1 = 1"]
    params: dict = {}
    if metodo in ("wompi", "nequi"):
        where.append("sp.metodo = :metodo")
        params["metodo"] = metodo
    if desde:
        where.append("sp.created_at::date >= :desde")
        params["desde"] = desde
    if hasta:
        where.append("sp.created_at::date <= :hasta")
        params["hasta"] = hasta
    termino = q.strip().lower()
    if termino:
        where.append("(lower(u.nombre) LIKE :q OR lower(sp.wompi_reference) LIKE :q)")
        params["q"] = f"%{termino}%"

    conn = get_tenant_connection()
    try:
        rows = conn.run(
            f"""
            SELECT sp.id, sp.usuario_id, u.nombre, sp.plan_id, sp.monto, sp.metodo, sp.estado,
                   sp.wompi_reference, sp.wompi_transaction_id, sp.created_at, sp.updated_at
            FROM suscripcion_pagos sp
            JOIN usuarios u ON u.id = sp.usuario_id
            WHERE {' AND '.join(where)}
            ORDER BY sp.created_at DESC
            LIMIT 5000
            """,
            **params,
        )
    finally:
        conn.close()

    cobrado = 0.0
    aprobados = por_confirmar = sin_completar = fallidos = 0
    monto_por_confirmar = 0.0
    for r in rows:
        monto = float(r[4])
        if r[6] == "pagado":
            cobrado += monto
            aprobados += 1
        elif r[6] == NEQUI_PENDIENTE:
            por_confirmar += 1
            monto_por_confirmar += monto
        elif r[6] == "pendiente_pago":
            sin_completar += 1
        else:
            fallidos += 1

    visibles = [r for r in rows if not estado or r[6] == estado]
    nombres = _nombres_planes({r[3] for r in visibles[:MAX_FILAS]})
    pagos = [
        PagoFacturacionOut(
            id=r[0], usuario_id=r[1], comercio_nombre=r[2] or "", plan_nombre=nombres.get(r[3], "(plan eliminado)"),
            monto=float(r[4]), metodo=r[5], estado=r[6],
            referencia=r[7].removeprefix("nqi-").removeprefix("sus-"), transaccion_id=r[8],
            created_at=r[9], updated_at=r[10],
        )
        for r in visibles[:MAX_FILAS]
    ]
    return FacturacionOut(
        resumen=ResumenFacturacionOut(
            cobrado=cobrado, pagos_aprobados=aprobados, por_confirmar=por_confirmar,
            monto_por_confirmar=monto_por_confirmar, sin_completar=sin_completar, fallidos=fallidos,
        ),
        pagos=pagos,
        total_filas=len(visibles),
    )

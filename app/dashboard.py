from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth import get_current_user
from app.database import get_tenant_connection
from app.schemas import UserOut

router = APIRouter(prefix="/superadmin/dashboard", tags=["dashboard"])


class SerieMes(BaseModel):
    mes: str
    label: str
    total: int


class ComercioReciente(BaseModel):
    id: int
    nombre: str
    email: str
    activo: bool
    fecha_creacion: datetime


class DashboardStats(BaseModel):
    total_comercios: int
    activos: int
    inactivos: int
    total_staff: int
    nuevos_30d: int
    serie_mensual: list[SerieMes]
    recientes: list[ComercioReciente]


@router.get("/stats", response_model=DashboardStats)
def stats(current_user: UserOut = Depends(get_current_user)):
    conn = get_tenant_connection()
    try:
        totales = conn.run(
            """
            SELECT
                COUNT(*) FILTER (WHERE propietario = true) AS total_comercios,
                COUNT(*) FILTER (WHERE propietario = true AND activo = true) AS activos,
                COUNT(*) FILTER (WHERE propietario = true AND activo = false) AS inactivos,
                COUNT(*) FILTER (WHERE propietario = false) AS total_staff,
                COUNT(*) FILTER (WHERE propietario = true AND fecha_creacion >= now() - interval '30 days') AS nuevos_30d
            FROM usuarios
            """
        )[0]

        serie_rows = conn.run(
            """
            SELECT to_char(gs, 'YYYY-MM') AS mes, to_char(gs, 'Mon') AS label, COUNT(u.id) AS total
            FROM generate_series(date_trunc('month', now()) - interval '5 months', date_trunc('month', now()), interval '1 month') gs
            LEFT JOIN usuarios u ON u.propietario = true AND date_trunc('month', u.fecha_creacion) = gs
            GROUP BY gs
            ORDER BY gs
            """
        )

        recientes_rows = conn.run(
            """
            SELECT id, nombre, email, activo, fecha_creacion
            FROM usuarios
            WHERE propietario = true
            ORDER BY fecha_creacion DESC
            LIMIT 5
            """
        )

        return DashboardStats(
            total_comercios=totales[0],
            activos=totales[1],
            inactivos=totales[2],
            total_staff=totales[3],
            nuevos_30d=totales[4],
            serie_mensual=[SerieMes(mes=r[0], label=r[1], total=r[2]) for r in serie_rows],
            recientes=[
                ComercioReciente(id=r[0], nombre=r[1], email=r[2], activo=r[3], fecha_creacion=r[4])
                for r in recientes_rows
            ],
        )
    finally:
        conn.close()

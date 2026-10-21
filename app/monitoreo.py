import calendar
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.auth import get_current_user
from app.database import get_tenant_connection
from app.schemas import UserOut

router = APIRouter(prefix="/superadmin/monitoreo", tags=["monitoreo"])


class MonitoreoDiaOut(BaseModel):
    fecha: str
    visitas: int
    ips_unicas: int


class MonitoreoResumenOut(BaseModel):
    # Totales de siempre, como contexto.
    total_visitas: int
    ips_unicas_total: int
    # Métricas del día elegido y del día anterior (para ver si subió o bajó).
    fecha: date
    visitas: int
    ips_unicas: int
    ips_nuevas: int
    ips_recurrentes: int
    visitas_anterior: int
    ips_unicas_anterior: int
    # Todos los días del mes del día elegido (hasta hoy), con ceros en los días sin visitas.
    dias: list[MonitoreoDiaOut]
    # Meses con datos, del más antiguo al actual (AAAA-MM), para el selector.
    meses: list[str]


def _metricas_dia(conn, dia: date) -> tuple[int, int, int]:
    """Visitas, IPs distintas e IPs que visitaron por primera vez ese día."""
    visitas, ips = conn.run(
        "SELECT COUNT(*), COUNT(DISTINCT ip) FROM visitas_ip WHERE created_at::date = :d", d=dia,
    )[0]
    nuevas = conn.run(
        "SELECT COUNT(*) FROM (SELECT ip FROM visitas_ip GROUP BY ip HAVING MIN(created_at)::date = :d) t", d=dia,
    )[0][0]
    return int(visitas), int(ips), int(nuevas)


@router.get("/resumen", response_model=MonitoreoResumenOut)
def resumen(fecha: date | None = Query(default=None), current_user: UserOut = Depends(get_current_user)):
    """Visitas al front-client (violet.cloud-control.co), contadas por IP distinta, para un día concreto.
    visitas_ip vive en la base de datos de los tenants (no en la del SuperAdmin)."""
    conn = get_tenant_connection()
    try:
        dia = fecha or conn.run("SELECT CURRENT_DATE")[0][0]
        total_visitas, ips_unicas_total = conn.run("SELECT COUNT(*), COUNT(DISTINCT ip) FROM visitas_ip")[0]
        visitas, ips, nuevas = _metricas_dia(conn, dia)
        visitas_ant, ips_ant, _ = _metricas_dia(conn, dia - timedelta(days=1))
        hoy = conn.run("SELECT CURRENT_DATE")[0][0]
        primero = dia.replace(day=1)
        fin_mes = dia.replace(day=calendar.monthrange(dia.year, dia.month)[1])
        dias_rows = conn.run(
            "SELECT g.d::date, COUNT(v.ip), COUNT(DISTINCT v.ip) "
            "FROM generate_series(CAST(:ini AS timestamp), CAST(:fin AS timestamp), interval '1 day') AS g(d) "
            "LEFT JOIN visitas_ip v ON v.created_at >= g.d AND v.created_at < g.d + interval '1 day' "
            "GROUP BY g.d ORDER BY g.d",
            ini=primero, fin=min(fin_mes, max(hoy, dia)),
        )
        primera = conn.run("SELECT MIN(created_at)::date FROM visitas_ip")[0][0] or hoy
        meses, m = [], primera.replace(day=1)
        ultimo = max(hoy, dia).replace(day=1)
        while m <= ultimo:
            meses.append(m.strftime("%Y-%m"))
            m = (m + timedelta(days=32)).replace(day=1)
        return MonitoreoResumenOut(
            total_visitas=total_visitas,
            ips_unicas_total=ips_unicas_total,
            fecha=dia,
            visitas=visitas,
            ips_unicas=ips,
            ips_nuevas=nuevas,
            ips_recurrentes=max(ips - nuevas, 0),
            visitas_anterior=visitas_ant,
            ips_unicas_anterior=ips_ant,
            dias=[MonitoreoDiaOut(fecha=str(r[0]), visitas=int(r[1]), ips_unicas=int(r[2])) for r in dias_rows],
            meses=meses,
        )
    finally:
        conn.close()

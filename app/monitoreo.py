from fastapi import APIRouter, Depends
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
    total_visitas: int
    ips_unicas_total: int
    visitas_hoy: int
    ips_unicas_hoy: int
    dias: list[MonitoreoDiaOut]


@router.get("/resumen", response_model=MonitoreoResumenOut)
def resumen(current_user: UserOut = Depends(get_current_user)):
    """Visitas al front-client (violet.cloud-control.co), contadas por IP distinta.
    visitas_ip vive en la base de datos de los tenants (no en la del SuperAdmin)."""
    conn = get_tenant_connection()
    try:
        total_visitas, ips_unicas_total = conn.run(
            "SELECT COUNT(*), COUNT(DISTINCT ip) FROM visitas_ip"
        )[0]
        hoy_visitas, hoy_ips = conn.run(
            "SELECT COUNT(*), COUNT(DISTINCT ip) FROM visitas_ip WHERE created_at::date = CURRENT_DATE"
        )[0]
        dias_rows = conn.run(
            "SELECT created_at::date AS dia, COUNT(*), COUNT(DISTINCT ip) FROM visitas_ip "
            "WHERE created_at >= CURRENT_DATE - INTERVAL '29 days' GROUP BY dia ORDER BY dia"
        )
        dias = [MonitoreoDiaOut(fecha=str(r[0]), visitas=r[1], ips_unicas=r[2]) for r in dias_rows]
        return MonitoreoResumenOut(
            total_visitas=total_visitas,
            ips_unicas_total=ips_unicas_total,
            visitas_hoy=hoy_visitas,
            ips_unicas_hoy=hoy_ips,
            dias=dias,
        )
    finally:
        conn.close()

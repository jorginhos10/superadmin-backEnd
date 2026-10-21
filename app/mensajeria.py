from datetime import date, datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.auth import get_current_user
from app.database import get_tenant_connection
from app.schemas import UserOut

router = APIRouter(prefix="/superadmin/mensajeria", tags=["mensajeria"])


class SerieDiaOut(BaseModel):
    fecha: date
    enviados: int
    fallidos: int


class DesgloseOut(BaseModel):
    clave: str
    enviados: int
    fallidos: int


class EnvioRecienteOut(BaseModel):
    id: int
    fecha: datetime
    telefono: str
    proposito: str
    estado: str
    error: str
    comercio: str


class MensajeriaOut(BaseModel):
    dias: int
    enviados: int
    fallidos: int
    tasa_exito: float
    codigos_emitidos: int
    verificados: int
    tasa_verificacion: float
    ultimo_envio: datetime | None
    por_dia: list[SerieDiaOut]
    por_pais: list[DesgloseOut]
    por_proposito: list[DesgloseOut]
    recientes: list[EnvioRecienteOut]


def _enmascarar(telefono: str) -> str:
    """El SuperAdmin ve el país y el final del número, no el teléfono completo de cada persona."""
    return f"{telefono[:4]}***{telefono[-2:]}" if len(telefono) > 8 else "***"


def _desglose(conn, columna: str, desde: date) -> list[DesgloseOut]:
    # `columna` sale de una lista fija en el código, nunca del usuario.
    rows = conn.run(
        f"SELECT COALESCE(NULLIF({columna}, ''), 'Sin dato'), "
        f"COUNT(*) FILTER (WHERE estado = 'enviado'), COUNT(*) FILTER (WHERE estado <> 'enviado') "
        f"FROM sms_envios WHERE created_at >= :d GROUP BY 1 ORDER BY 2 DESC, 1",
        d=desde,
    )
    return [DesgloseOut(clave=r[0], enviados=int(r[1]), fallidos=int(r[2])) for r in rows]


@router.get("", response_model=MensajeriaOut)
def metricas_mensajeria(dias: int = Query(default=30, ge=1, le=365), current_user: UserOut = Depends(get_current_user)):
    """Métricas de los SMS que envía la plataforma: cuántos salieron, cuántos fallaron y cuántos códigos se verificaron."""
    conn = get_tenant_connection()
    try:
        desde_row = conn.run("SELECT (current_date - CAST(:n AS integer) + 1)", n=dias)[0][0]
        desde = desde_row if isinstance(desde_row, date) else date.fromisoformat(str(desde_row))

        tot = conn.run(
            "SELECT COUNT(*) FILTER (WHERE estado = 'enviado'), COUNT(*) FILTER (WHERE estado <> 'enviado'), MAX(created_at) "
            "FROM sms_envios WHERE created_at >= :d",
            d=desde,
        )[0]
        enviados, fallidos, ultimo = int(tot[0]), int(tot[1]), tot[2]
        total = enviados + fallidos

        ver = conn.run(
            "SELECT COUNT(*), COUNT(*) FILTER (WHERE verificado_en IS NOT NULL) FROM verificaciones_telefono WHERE created_at >= :d",
            d=desde,
        )[0]
        emitidos, verificados = int(ver[0]), int(ver[1])

        serie = conn.run(
            "SELECT g.d::date, COUNT(s.id) FILTER (WHERE s.estado = 'enviado'), COUNT(s.id) FILTER (WHERE s.estado <> 'enviado') "
            "FROM generate_series(CAST(:d AS timestamp), CAST(current_date AS timestamp), interval '1 day') AS g(d) "
            "LEFT JOIN sms_envios s ON s.created_at >= g.d AND s.created_at < g.d + interval '1 day' "
            "GROUP BY g.d ORDER BY g.d",
            d=desde,
        )

        recientes = conn.run(
            "SELECT s.id, s.created_at, s.telefono, s.proposito, s.estado, s.error, COALESCE(u.nombre, '') "
            "FROM sms_envios s LEFT JOIN usuarios u ON u.id = s.usuario_id ORDER BY s.created_at DESC LIMIT 25"
        )

        return MensajeriaOut(
            dias=dias,
            enviados=enviados,
            fallidos=fallidos,
            tasa_exito=round(enviados / total * 100, 1) if total else 0.0,
            codigos_emitidos=emitidos,
            verificados=verificados,
            tasa_verificacion=round(verificados / emitidos * 100, 1) if emitidos else 0.0,
            ultimo_envio=ultimo,
            por_dia=[SerieDiaOut(fecha=r[0], enviados=int(r[1]), fallidos=int(r[2])) for r in serie],
            por_pais=_desglose(conn, "pais", desde),
            por_proposito=_desglose(conn, "proposito", desde),
            recientes=[
                EnvioRecienteOut(
                    id=r[0], fecha=r[1], telefono=_enmascarar(r[2]), proposito=r[3], estado=r[4], error=r[5], comercio=r[6],
                )
                for r in recientes
            ],
        )
    finally:
        conn.close()

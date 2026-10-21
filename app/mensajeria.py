import ipaddress
import json
import re
from datetime import date, datetime
from typing import Literal, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app import sms_envio
from app.auth import get_current_user
from app.database import get_connection, get_tenant_connection
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


# --- Conexión con el proveedor de SMS (la tuerca de la pantalla) -----------------------------------------

CONFIG_COLUMNAS = [
    "proveedor", "activo", "url", "metodo", "auth_tipo", "auth_usuario", "auth_secreto", "auth_header_nombre",
    "remitente", "content_type", "cuerpo_plantilla", "exito_contiene",
]


class SmsConfigOut(BaseModel):
    proveedor: str
    activo: bool
    url: str
    metodo: str
    auth_tipo: str
    auth_usuario: str
    # La clave nunca vuelve al navegador: solo se indica si ya hay una guardada.
    tiene_secreto: bool
    auth_header_nombre: str
    remitente: str
    content_type: str
    cuerpo_plantilla: str
    exito_contiene: str


class SmsConfigIn(BaseModel):
    proveedor: str = Field(default="inalambria", max_length=60)
    activo: bool = False
    url: str = Field(default="", max_length=500)
    metodo: Literal["POST", "GET"] = "POST"
    auth_tipo: Literal["basic", "bearer", "header", "ninguna"] = "basic"
    auth_usuario: str = Field(default="", max_length=200)
    # Vacío = conservar la clave que ya está guardada.
    auth_secreto: Optional[str] = Field(default=None, max_length=1000)
    auth_header_nombre: str = Field(default="", max_length=80)
    remitente: str = Field(default="", max_length=60)
    content_type: Literal["json", "form"] = "json"
    cuerpo_plantilla: str = Field(default="", max_length=4000)
    exito_contiene: str = Field(default="", max_length=200)


class ProbarSmsIn(BaseModel):
    telefono: str = Field(min_length=7, max_length=30)


class ProbarSmsOut(BaseModel):
    ok: bool
    detalle: str


def _leer_config(conn) -> dict:
    rows = conn.run(f"SELECT {', '.join(CONFIG_COLUMNAS)} FROM sms_config WHERE id = 1")
    if not rows:
        conn.run("INSERT INTO sms_config (id) VALUES (1) ON CONFLICT DO NOTHING")
        rows = conn.run(f"SELECT {', '.join(CONFIG_COLUMNAS)} FROM sms_config WHERE id = 1")
    return dict(zip(CONFIG_COLUMNAS, rows[0]))


def _config_out(cfg: dict) -> SmsConfigOut:
    visible = {k: v for k, v in cfg.items() if k != "auth_secreto"}
    return SmsConfigOut(**visible, tiene_secreto=bool(cfg["auth_secreto"]))


def _validar_url(url: str) -> None:
    partes = urlparse(url)
    if partes.scheme != "https" or not partes.hostname:
        raise HTTPException(status_code=422, detail="La dirección debe empezar por https://")
    host = partes.hostname.lower()
    try:
        ip = ipaddress.ip_address(host)
        privada = ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        privada = host == "localhost" or host.endswith(".local") or host.endswith(".internal")
    if privada:
        raise HTTPException(status_code=422, detail="La dirección no puede apuntar a una red interna")


def _validar_plantilla(cfg: dict) -> None:
    plantilla = cfg["cuerpo_plantilla"]
    if "{telefono}" not in plantilla or "{mensaje}" not in plantilla:
        raise HTTPException(status_code=422, detail="El cuerpo debe incluir {telefono} y {mensaje}")
    if cfg["content_type"] == "json":
        ejemplo = sms_envio._cuerpo_json(plantilla, sms_envio.valores_de(cfg, "573001234567", "Hola"))
        try:
            json.loads(ejemplo)
        except ValueError:
            raise HTTPException(status_code=422, detail="El cuerpo no es un JSON válido (revisa comillas y comas)")
    elif not sms_envio._campos_form(plantilla, sms_envio.valores_de(cfg, "1", "x")):
        raise HTTPException(status_code=422, detail="Escribe un campo por línea, con la forma campo={valor}")


@router.get("/config", response_model=SmsConfigOut)
def obtener_config(current_user: UserOut = Depends(get_current_user)):
    conn = get_connection()
    try:
        return _config_out(_leer_config(conn))
    finally:
        conn.close()


@router.put("/config", response_model=SmsConfigOut)
def guardar_config(payload: SmsConfigIn, current_user: UserOut = Depends(get_current_user)):
    conn = get_connection()
    try:
        actual = _leer_config(conn)
        nuevo = payload.model_dump()
        # Al pegar una clave es fácil arrastrar un espacio, un salto de línea o comillas; con eso el
        # encabezado de autorización queda inválido y el envío falla antes de salir.
        secreto = (payload.auth_secreto or "").strip().strip("\"'").strip()
        if secreto and not re.fullmatch(r"[!-~]+", secreto):
            raise HTTPException(status_code=422, detail="La clave tiene espacios o caracteres no válidos. Cópiala de nuevo, sin comillas.")
        nuevo["auth_secreto"] = secreto if secreto else actual["auth_secreto"]
        for campo in ("proveedor", "url", "auth_usuario", "auth_header_nombre", "remitente", "cuerpo_plantilla", "exito_contiene"):
            nuevo[campo] = nuevo[campo].strip()

        if nuevo["url"]:
            _validar_url(nuevo["url"])
        if nuevo["activo"]:
            if not nuevo["url"]:
                raise HTTPException(status_code=422, detail="Para activarlo falta la dirección (URL) de la API")
            if nuevo["auth_tipo"] != "ninguna" and not nuevo["auth_secreto"]:
                raise HTTPException(status_code=422, detail="Para activarlo falta la clave o el token de la API")
            if nuevo["auth_tipo"] == "basic" and not nuevo["auth_usuario"]:
                raise HTTPException(status_code=422, detail="Con autenticación básica falta el usuario")
            _validar_plantilla(nuevo)
        elif nuevo["cuerpo_plantilla"]:
            _validar_plantilla(nuevo)

        conn.run(
            "UPDATE sms_config SET proveedor = :proveedor, activo = :activo, url = :url, metodo = :metodo, "
            "auth_tipo = :auth_tipo, auth_usuario = :auth_usuario, auth_secreto = :auth_secreto, "
            "auth_header_nombre = :auth_header_nombre, remitente = :remitente, content_type = :content_type, "
            "cuerpo_plantilla = :cuerpo_plantilla, exito_contiene = :exito_contiene, actualizado_en = now() WHERE id = 1",
            **{k: nuevo[k] for k in CONFIG_COLUMNAS},
        )
        return _config_out(_leer_config(conn))
    finally:
        conn.close()


def _a_internacional(telefono: str) -> str:
    d = re.sub(r"\D", "", telefono)
    if d.startswith("00"):
        d = d[2:]
    return "57" + d if len(d) == 10 and d.startswith("3") else d


@router.post("/config/probar", response_model=ProbarSmsOut)
def probar_envio(payload: ProbarSmsIn, current_user: UserOut = Depends(get_current_user)):
    """Manda un SMS real de prueba con la configuración guardada (cuenta en las métricas como motivo «prueba»)."""
    conn = get_connection()
    try:
        cfg = _leer_config(conn)
    finally:
        conn.close()
    if not cfg["url"]:
        raise HTTPException(status_code=422, detail="Guarda primero la dirección (URL) de la API")

    destino = _a_internacional(payload.telefono)
    estado, detalle = "enviado", ""
    try:
        detalle = sms_envio.enviar(cfg, destino, "Violet: mensaje de prueba de la conexión de SMS.")
    except sms_envio.EnvioFallido as exc:
        estado, detalle = "fallido", str(exc)

    tconn = get_tenant_connection()
    try:
        tconn.run(
            "INSERT INTO sms_envios (usuario_id, telefono, proposito, estado, proveedor, error) "
            "VALUES (NULL, :t, 'prueba', :e, :p, :er)",
            t=destino, e=estado, p=cfg["proveedor"], er="" if estado == "enviado" else detalle[:500],
        )
    finally:
        tconn.close()
    return ProbarSmsOut(ok=estado == "enviado", detalle=detalle)

import logging
import os
from datetime import datetime
from typing import Optional
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr, Field

from app.auth import get_current_user
from app.database import get_connection, get_tenant_connection
from app.schemas import UserOut

router = APIRouter(prefix="/superadmin/comercios", tags=["comercios"])
logger = logging.getLogger("uvicorn.error")

COMERCIO_COLUMNS = [
    "id", "nombre", "username", "email", "activo", "verificado", "fecha_creacion", "ultimo_login", "plan_id", "pais",
    "estado_aprobacion",
]
COMERCIO_SELECT = (
    f"SELECT {', '.join('p.' + c for c in COMERCIO_COLUMNS)}, "
    "(SELECT COUNT(*) FROM usuarios s WHERE s.propietario_id = p.id) AS total_staff, "
    "(SELECT n.nombre FROM negocios n WHERE n.usuario_id = p.id) AS negocio_nombre, "
    "(SELECT n.tipo FROM negocios n WHERE n.usuario_id = p.id) AS negocio_tipo "
    "FROM usuarios p"
)
COMERCIO_ROW_KEYS = COMERCIO_COLUMNS + ["total_staff", "negocio_nombre", "negocio_tipo"]


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
    pais: Optional[str] = None
    estado_aprobacion: str
    negocio_nombre: Optional[str] = None
    negocio_tipo: Optional[str] = None


def _planes_pais_map() -> dict[int, str]:
    """plan_id -> codigo_pais, read from our own (SuperAdmin) database."""
    conn = get_connection()
    try:
        rows = conn.run("SELECT id, pais FROM planes")
        return {plan_id: pais for plan_id, pais in rows}
    finally:
        conn.close()


def _con_pais(data: dict, planes_pais: dict[int, str]) -> dict:
    """Prefer the country captured at signup (by IP); fall back to the
    country of the assigned plan for older accounts that predate that."""
    pais_registro = data.pop("pais")
    plan_id = data.pop("plan_id")
    data["pais"] = pais_registro or planes_pais.get(plan_id)
    return data


class ComercioEditIn(BaseModel):
    nombre: str
    email: EmailStr


class ImpersonarOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    frontend_url: str


def _get_comercio(conn, comercio_id: int) -> dict:
    rows = conn.run(f"{COMERCIO_SELECT} WHERE p.id = :id AND p.propietario = true", id=comercio_id)
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comercio no encontrado")
    data = dict(zip(COMERCIO_ROW_KEYS, rows[0]))
    return _con_pais(data, _planes_pais_map())


@router.get("", response_model=list[ComercioOut])
def listar_comercios(current_user: UserOut = Depends(get_current_user)):
    conn = get_tenant_connection()
    try:
        rows = conn.run(f"{COMERCIO_SELECT} WHERE p.propietario = true ORDER BY p.fecha_creacion DESC")
        planes_pais = _planes_pais_map()
        return [ComercioOut(**_con_pais(dict(zip(COMERCIO_ROW_KEYS, row)), planes_pais)) for row in rows]
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


PALABRA_CONFIRMACION = "confirmo"


class ConfirmacionIn(BaseModel):
    confirmacion: str = Field(default="", max_length=50)


def _exigir_confirmacion(payload: Optional[ConfirmacionIn], accion: str) -> None:
    """Destructive actions must be confirmed by typing the word; enforced here too so a
    direct API call can't skip the dialog."""
    if payload is None or payload.confirmacion.strip().lower() != PALABRA_CONFIRMACION:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f'Escribe "{PALABRA_CONFIRMACION}" para {accion}',
        )


@router.post("/{comercio_id}/toggle-activo", response_model=ComercioOut)
def toggle_activo(
    comercio_id: int,
    payload: Optional[ConfirmacionIn] = None,
    current_user: UserOut = Depends(get_current_user),
):
    conn = get_tenant_connection()
    try:
        comercio = _get_comercio(conn, comercio_id)
        if comercio["activo"]:
            _exigir_confirmacion(payload, "suspender el comercio")
        conn.run("UPDATE usuarios SET activo = NOT activo WHERE id = :id", id=comercio_id)
        return ComercioOut(**_get_comercio(conn, comercio_id))
    finally:
        conn.close()


class RetiroVerificacionIn(BaseModel):
    motivo: str = Field(default="", max_length=500)


@router.post("/{comercio_id}/verificar", response_model=ComercioOut)
def toggle_verificado(
    comercio_id: int,
    payload: Optional[RetiroVerificacionIn] = None,
    current_user: UserOut = Depends(get_current_user),
):
    """Marks an approved comercio as verified. Removing the verification sends the comercio back to
    the documentation stage: it is locked out again until it resubmits, and sees the reason."""
    conn = get_tenant_connection()
    try:
        comercio = _get_comercio(conn, comercio_id)
        if comercio["verificado"]:
            motivo = (payload.motivo if payload else "").strip()
            if len(motivo) < 3:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Escribe el motivo para retirar la verificación: el comercio deberá volver a enviar su documentación",
                )
            conn.run(
                "UPDATE usuarios SET verificado = false, estado_aprobacion = 'rechazado', motivo_rechazo = :m WHERE id = :id",
                m=f"Se retiró la verificación de tu comercio. {motivo}", id=comercio_id,
            )
        else:
            if comercio["estado_aprobacion"] != "aprobado":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Solo se puede verificar un comercio aprobado",
                )
            conn.run("UPDATE usuarios SET verificado = true WHERE id = :id", id=comercio_id)
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


DOC_COLUMNS = ["id", "tipo", "nombre_archivo", "content_type", "tamano", "created_at", "estado", "motivo_rechazo"]


class DocumentoOut(BaseModel):
    id: int
    tipo: str
    nombre_archivo: str
    content_type: str
    tamano: int
    created_at: datetime
    estado: str = "pendiente"
    motivo_rechazo: str = ""


class PlanSolicitadoOut(BaseModel):
    id: int
    nombre: str
    precio: float
    periodo: str
    moneda: str


class SolicitudOut(BaseModel):
    estado_aprobacion: str
    motivo_rechazo: str
    numero_documento: str
    telefono: str
    nombre_negocio: str
    tipo_negocio: str
    rut: str
    direccion: str
    ciudad: str
    sitio_web: str
    logo_url: Optional[str] = None
    plan: Optional[PlanSolicitadoOut] = None
    documentos: list[DocumentoOut]


class RechazoIn(BaseModel):
    # Optional when at least one document was rejected with its own reason.
    motivo: str = Field(default="", max_length=500)


class RechazoDocumentoIn(BaseModel):
    motivo: str = Field(min_length=3, max_length=500)


def _plan_por_id(plan_id: Optional[int]) -> Optional[PlanSolicitadoOut]:
    """Plans live in the SuperAdmin's own database."""
    if plan_id is None:
        return None
    conn = get_connection()
    try:
        rows = conn.run("SELECT id, nombre, precio, periodo, moneda FROM planes WHERE id = :id", id=plan_id)
        if not rows:
            return None
        r = rows[0]
        return PlanSolicitadoOut(id=r[0], nombre=r[1], precio=float(r[2]), periodo=r[3], moneda=r[4])
    finally:
        conn.close()


@router.get("/{comercio_id}/solicitud", response_model=SolicitudOut)
def ver_solicitud(comercio_id: int, current_user: UserOut = Depends(get_current_user)):
    conn = get_tenant_connection()
    try:
        _get_comercio(conn, comercio_id)
        row = conn.run(
            """
            SELECT u.estado_aprobacion, u.motivo_rechazo, u.numero_documento, u.telefono, u.plan_solicitado_id,
                   n.nombre, n.tipo, n.rut, n.direccion, n.ciudad, n.sitio_web, n.logo_url
            FROM usuarios u LEFT JOIN negocios n ON n.usuario_id = u.id
            WHERE u.id = :id
            """,
            id=comercio_id,
        )[0]
        documentos = conn.run(
            f"SELECT {', '.join(DOC_COLUMNS)} FROM comercio_documentos WHERE usuario_id = :id ORDER BY created_at",
            id=comercio_id,
        )
        return SolicitudOut(
            estado_aprobacion=row[0], motivo_rechazo=row[1], numero_documento=row[2], telefono=row[3],
            plan=_plan_por_id(row[4]),
            nombre_negocio=row[5] or "", tipo_negocio=row[6] or "", rut=row[7] or "", direccion=row[8] or "",
            ciudad=row[9] or "", sitio_web=row[10] or "", logo_url=row[11],
            documentos=[dict(zip(DOC_COLUMNS, d)) for d in documentos],
        )
    finally:
        conn.close()


@router.get("/{comercio_id}/documentos/{documento_id}")
def descargar_documento(comercio_id: int, documento_id: int, current_user: UserOut = Depends(get_current_user)):
    conn = get_tenant_connection()
    try:
        rows = conn.run(
            "SELECT nombre_archivo, content_type, contenido FROM comercio_documentos WHERE id = :d AND usuario_id = :u",
            d=documento_id, u=comercio_id,
        )
        if not rows:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Documento no encontrado")
        nombre, content_type, contenido = rows[0]
        return Response(
            content=bytes(contenido),
            media_type=content_type,
            headers={"Content-Disposition": "inline; filename*=UTF-8''" + quote(nombre)},
        )
    finally:
        conn.close()


@router.post("/{comercio_id}/aprobar", response_model=ComercioOut)
def aprobar(comercio_id: int, current_user: UserOut = Depends(get_current_user)):
    conn = get_tenant_connection()
    try:
        comercio = _get_comercio(conn, comercio_id)
        if comercio["estado_aprobacion"] not in ("pendiente_aprobacion", "rechazado"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="El comercio no tiene una solicitud pendiente de revisión",
            )
        if _hay_documentos_rechazados(conn, comercio_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Hay documentos rechazados: deshaz el rechazo o devuelve la solicitud al comercio",
            )
        conn.run(
            "UPDATE usuarios SET estado_aprobacion = 'aprobado', motivo_rechazo = '', verificado = true WHERE id = :id",
            id=comercio_id,
        )
        # A free plan requested during signup is applied right away; paid plans still go
        # through the normal Suscripción checkout, so they are never granted without payment.
        solicitado = conn.run("SELECT plan_solicitado_id FROM usuarios WHERE id = :id", id=comercio_id)[0][0]
        plan = _plan_por_id(solicitado)
        if plan and plan.precio <= 0:
            conn.run(
                "UPDATE usuarios SET plan_id = :pid, plan_actualizado_en = now() WHERE id = :id",
                pid=plan.id, id=comercio_id,
            )
        return ComercioOut(**_get_comercio(conn, comercio_id))
    finally:
        conn.close()


@router.post("/{comercio_id}/rechazar", response_model=ComercioOut)
def rechazar(comercio_id: int, payload: RechazoIn, current_user: UserOut = Depends(get_current_user)):
    conn = get_tenant_connection()
    try:
        comercio = _get_comercio(conn, comercio_id)
        if comercio["estado_aprobacion"] != "pendiente_aprobacion":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Solo se pueden rechazar solicitudes pendientes de revisión",
            )
        motivo = payload.motivo.strip()
        if len(motivo) < 3 and not _hay_documentos_rechazados(conn, comercio_id):
            raise HTTPException(status_code=422, detail="Escribe el motivo del rechazo para que el comercio pueda corregirlo")
        conn.run(
            "UPDATE usuarios SET estado_aprobacion = 'rechazado', motivo_rechazo = :motivo WHERE id = :id",
            motivo=motivo, id=comercio_id,
        )
        return ComercioOut(**_get_comercio(conn, comercio_id))
    finally:
        conn.close()


def _hay_documentos_rechazados(conn, comercio_id: int) -> bool:
    return bool(
        conn.run(
            "SELECT 1 FROM comercio_documentos WHERE usuario_id = :u AND estado = 'rechazado' LIMIT 1", u=comercio_id,
        )
    )


def _documento_actualizado(conn, comercio_id: int, documento_id: int) -> DocumentoOut:
    rows = conn.run(
        f"SELECT {', '.join(DOC_COLUMNS)} FROM comercio_documentos WHERE id = :d AND usuario_id = :u",
        d=documento_id, u=comercio_id,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Documento no encontrado")
    return DocumentoOut(**dict(zip(DOC_COLUMNS, rows[0])))


class RevisionDocumentoOut(BaseModel):
    documento: DocumentoOut
    comercio: ComercioOut


def _requerir_en_revision(conn, comercio_id: int) -> None:
    if _get_comercio(conn, comercio_id)["estado_aprobacion"] not in ("pendiente_aprobacion", "rechazado"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Solo se pueden revisar documentos de solicitudes en revisión o devueltas al comercio",
        )


@router.post("/{comercio_id}/documentos/{documento_id}/rechazar", response_model=RevisionDocumentoOut)
def rechazar_documento(
    comercio_id: int, documento_id: int, payload: RechazoDocumentoIn, current_user: UserOut = Depends(get_current_user),
):
    """Rejects one document with the reason the merchant will see and sends the application
    straight back to them, so they can fix it (replace the document) and resubmit."""
    conn = get_tenant_connection()
    try:
        _requerir_en_revision(conn, comercio_id)
        _documento_actualizado(conn, comercio_id, documento_id)
        conn.run(
            "UPDATE comercio_documentos SET estado = 'rechazado', motivo_rechazo = :m WHERE id = :d AND usuario_id = :u",
            m=payload.motivo.strip(), d=documento_id, u=comercio_id,
        )
        conn.run("UPDATE usuarios SET estado_aprobacion = 'rechazado' WHERE id = :u", u=comercio_id)
        return RevisionDocumentoOut(
            documento=_documento_actualizado(conn, comercio_id, documento_id),
            comercio=ComercioOut(**_get_comercio(conn, comercio_id)),
        )
    finally:
        conn.close()


@router.post("/{comercio_id}/documentos/{documento_id}/restablecer", response_model=RevisionDocumentoOut)
def restablecer_documento(comercio_id: int, documento_id: int, current_user: UserOut = Depends(get_current_user)):
    conn = get_tenant_connection()
    try:
        _requerir_en_revision(conn, comercio_id)
        _documento_actualizado(conn, comercio_id, documento_id)
        conn.run(
            "UPDATE comercio_documentos SET estado = 'pendiente', motivo_rechazo = '' WHERE id = :d AND usuario_id = :u",
            d=documento_id, u=comercio_id,
        )
        # If the only thing that had sent it back was document rejections, there is nothing left
        # for the merchant to fix: put it back in the review queue.
        motivo_general = conn.run("SELECT motivo_rechazo FROM usuarios WHERE id = :u", u=comercio_id)[0][0]
        if not motivo_general.strip() and not _hay_documentos_rechazados(conn, comercio_id):
            conn.run(
                "UPDATE usuarios SET estado_aprobacion = 'pendiente_aprobacion' WHERE id = :u AND estado_aprobacion = 'rechazado'",
                u=comercio_id,
            )
        return RevisionDocumentoOut(
            documento=_documento_actualizado(conn, comercio_id, documento_id),
            comercio=ComercioOut(**_get_comercio(conn, comercio_id)),
        )
    finally:
        conn.close()


# ---------- Eliminación definitiva de un comercio ----------

# Single-column foreign keys of the shared chefcontrol database, read from the catalog so the
# deletion keeps working when tables are added.
FK_QUERY = """
SELECT conrelid::regclass::text, a.attname, confrelid::regclass::text, af.attname
FROM pg_constraint k
JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = k.conkey[1]
JOIN pg_attribute af ON af.attrelid = k.confrelid AND af.attnum = k.confkey[1]
WHERE k.contype = 'f' AND k.connamespace = 'public'::regnamespace AND array_length(k.conkey, 1) = 1
"""


def _hijos_por_tabla(conn) -> dict[str, list[tuple[str, str, str]]]:
    hijos: dict[str, list[tuple[str, str, str]]] = {}
    for hijo, col_hijo, padre, col_padre in conn.run(FK_QUERY):
        if hijo != padre:  # staff -> owner self reference: removed by the same DELETE
            hijos.setdefault(padre, []).append((hijo, col_hijo, col_padre))
    return hijos


def _borrar_filas(conn, hijos: dict, tabla: str, condicion: str, camino: tuple[str, ...] = ()) -> None:
    """Deletes the rows of `tabla` matching `condicion`, first deleting (recursively) every row
    that references them — most foreign keys are ON DELETE NO ACTION."""
    if tabla in camino:
        return
    for hijo, col_hijo, col_padre in hijos.get(tabla, []):
        _borrar_filas(
            conn, hijos, hijo, f"{col_hijo} IN (SELECT {col_padre} FROM {tabla} WHERE {condicion})", camino + (tabla,),
        )
    conn.run(f"DELETE FROM {tabla} WHERE {condicion}")


def _limpiar_datos_en_superadmin(comercio_id: int) -> None:
    """What a comercio leaves in the SuperAdmin's own database (no foreign keys across databases)."""
    conn = get_connection()
    try:
        conn.run("BEGIN")
        try:
            conn.run("DELETE FROM plan_comercios WHERE comercio_id = :id", id=comercio_id)
            conn.run("DELETE FROM chat_soporte_mensajes WHERE comercio_id = :id", id=comercio_id)
            conn.run("DELETE FROM soporte_tickets WHERE comercio_id = :id", id=comercio_id)
            conn.run(
                "DELETE FROM marketplace_productos WHERE tienda_id IN "
                "(SELECT id FROM tiendas WHERE propietario_comercio_id = :id)",
                id=comercio_id,
            )
            conn.run("DELETE FROM tiendas WHERE propietario_comercio_id = :id", id=comercio_id)
            conn.run("COMMIT")
        except Exception:
            conn.run("ROLLBACK")
            raise
    except Exception:
        # The comercio is already gone; leftovers here are harmless orphans, so don't fail the request.
        logger.exception("No se pudieron limpiar los datos del comercio %s en la base del superadmin", comercio_id)
    finally:
        conn.close()


@router.post("/{comercio_id}/eliminar", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_comercio(
    comercio_id: int,
    payload: Optional[ConfirmacionIn] = None,
    current_user: UserOut = Depends(get_current_user),
):
    """Permanently deletes the comercio, its staff accounts and ALL of their data, in one transaction."""
    _exigir_confirmacion(payload, "eliminar el comercio")
    conn = get_tenant_connection()
    try:
        _get_comercio(conn, comercio_id)
        ids = [comercio_id] + [r[0] for r in conn.run("SELECT id FROM usuarios WHERE propietario_id = :id", id=comercio_id)]
        lista = ", ".join(str(i) for i in ids)
        hijos = _hijos_por_tabla(conn)

        conn.run("BEGIN")
        try:
            # marketplace_pedidos points at the user without a foreign key, so it is not found by the graph.
            _borrar_filas(conn, hijos, "marketplace_pedidos", f"usuario_id IN ({lista})")
            _borrar_filas(conn, hijos, "usuarios", f"id IN ({lista})")
            conn.run("COMMIT")
        except Exception:
            conn.run("ROLLBACK")
            raise
    finally:
        conn.close()

    _limpiar_datos_en_superadmin(comercio_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

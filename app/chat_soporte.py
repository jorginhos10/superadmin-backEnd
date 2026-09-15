from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth import get_current_user
from app.database import get_connection, get_tenant_connection
from app.schemas import UserOut

router = APIRouter(prefix="/superadmin/chat-soporte", tags=["chat-soporte"])

MENSAJE_COLUMNS = ["id", "comercio_id", "de", "mensaje", "created_at"]


class ChatSoporteResumenOut(BaseModel):
    comercio_id: int
    nombre_comercio: str
    ultimo_mensaje: Optional[str] = None
    ultimo_mensaje_de: Optional[str] = None
    ultimo_mensaje_at: Optional[datetime] = None
    no_leidos: int


class ChatSoporteMensajeOut(BaseModel):
    id: int
    comercio_id: int
    de: str
    mensaje: str
    created_at: datetime


class ChatSoporteMensajeIn(BaseModel):
    mensaje: str


@router.get("/resumen", response_model=list[ChatSoporteResumenOut])
def resumen(current_user: UserOut = Depends(get_current_user)):
    tconn = get_tenant_connection()
    try:
        comercios = tconn.run(
            "SELECT id, nombre FROM usuarios WHERE propietario = true ORDER BY nombre"
        )
    finally:
        tconn.close()

    conn = get_connection()
    try:
        rows = conn.run(
            "SELECT DISTINCT ON (comercio_id) comercio_id, mensaje, de, created_at "
            "FROM chat_soporte_mensajes ORDER BY comercio_id, created_at DESC"
        )
        ultimos = {r[0]: {"mensaje": r[1], "de": r[2], "at": r[3]} for r in rows}

        no_leidos_rows = conn.run(
            "SELECT comercio_id, COUNT(*) FROM chat_soporte_mensajes "
            "WHERE de = 'comercio' AND leido_superadmin = false GROUP BY comercio_id"
        )
        no_leidos = {r[0]: r[1] for r in no_leidos_rows}
    finally:
        conn.close()

    items = [
        ChatSoporteResumenOut(
            comercio_id=c[0],
            nombre_comercio=c[1],
            ultimo_mensaje=ultimos.get(c[0], {}).get("mensaje"),
            ultimo_mensaje_de=ultimos.get(c[0], {}).get("de"),
            ultimo_mensaje_at=ultimos.get(c[0], {}).get("at"),
            no_leidos=no_leidos.get(c[0], 0),
        )
        for c in comercios
    ]
    items.sort(key=lambda i: i.ultimo_mensaje_at or datetime.min.replace(tzinfo=None), reverse=True)
    return items


@router.get("/{comercio_id}/mensajes", response_model=list[ChatSoporteMensajeOut])
def listar_mensajes(comercio_id: int, current_user: UserOut = Depends(get_current_user)):
    conn = get_connection()
    try:
        conn.run(
            "UPDATE chat_soporte_mensajes SET leido_superadmin = true "
            "WHERE comercio_id = :cid AND de = 'comercio'",
            cid=comercio_id,
        )
        rows = conn.run(
            f"SELECT {', '.join(MENSAJE_COLUMNS)} FROM chat_soporte_mensajes "
            "WHERE comercio_id = :cid ORDER BY created_at",
            cid=comercio_id,
        )
        return [ChatSoporteMensajeOut(**dict(zip(MENSAJE_COLUMNS, r))) for r in rows]
    finally:
        conn.close()


@router.post("/{comercio_id}/mensajes", response_model=ChatSoporteMensajeOut, status_code=201)
def enviar_mensaje(comercio_id: int, payload: ChatSoporteMensajeIn, current_user: UserOut = Depends(get_current_user)):
    texto = payload.mensaje.strip()
    conn = get_connection()
    try:
        rows = conn.run(
            f"INSERT INTO chat_soporte_mensajes (comercio_id, de, mensaje, leido_comercio) "
            f"VALUES (:cid, 'superadmin', :msg, false) RETURNING {', '.join(MENSAJE_COLUMNS)}",
            cid=comercio_id, msg=texto,
        )
        return ChatSoporteMensajeOut(**dict(zip(MENSAJE_COLUMNS, rows[0])))
    finally:
        conn.close()

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel


from app.auth import get_current_user
from app.database import get_connection
from app.schemas import UserOut

router = APIRouter(prefix="/superadmin/soporte", tags=["soporte"])

ESTADOS_VALIDOS = {"abierto", "en_progreso", "cerrado"}


class SoporteTicketOut(BaseModel):
    id: int
    comercio_id: int
    asunto: str
    estado: str
    no_leidos_superadmin: int
    created_at: datetime
    updated_at: datetime


class SoporteMensajeOut(BaseModel):
    id: int
    ticket_id: int
    de: str
    mensaje: str
    imagen_url: Optional[str] = None
    created_at: datetime


class SoporteMensajeIn(BaseModel):
    mensaje: str


class SoporteEstadoIn(BaseModel):
    estado: str


TICKET_COLUMNS = [
    "id", "comercio_id", "asunto", "estado", "no_leidos_superadmin", "created_at", "updated_at",
]
MENSAJE_COLUMNS = ["id", "ticket_id", "de", "mensaje", "imagen_url", "created_at"]


def _get_ticket_or_404(conn, ticket_id: int) -> dict:
    rows = conn.run(
        f"SELECT {', '.join(TICKET_COLUMNS)} FROM soporte_tickets WHERE id = :id", id=ticket_id
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket no encontrado")
    return dict(zip(TICKET_COLUMNS, rows[0]))


@router.get("/tickets", response_model=list[SoporteTicketOut])
def listar_tickets(current_user: UserOut = Depends(get_current_user)):
    conn = get_connection()
    try:
        rows = conn.run(
            f"SELECT {', '.join(TICKET_COLUMNS)} FROM soporte_tickets ORDER BY updated_at DESC"
        )
        return [SoporteTicketOut(**dict(zip(TICKET_COLUMNS, r))) for r in rows]
    finally:
        conn.close()


@router.get("/tickets/{ticket_id}/mensajes", response_model=list[SoporteMensajeOut])
def listar_mensajes(ticket_id: int, current_user: UserOut = Depends(get_current_user)):
    conn = get_connection()
    try:
        _get_ticket_or_404(conn, ticket_id)
        conn.run(
            "UPDATE soporte_tickets SET no_leidos_superadmin = 0 WHERE id = :id", id=ticket_id
        )
        rows = conn.run(
            f"SELECT {', '.join(MENSAJE_COLUMNS)} FROM soporte_mensajes "
            "WHERE ticket_id = :id ORDER BY created_at",
            id=ticket_id,
        )
        return [SoporteMensajeOut(**dict(zip(MENSAJE_COLUMNS, r))) for r in rows]
    finally:
        conn.close()


@router.post("/tickets/{ticket_id}/mensajes", response_model=SoporteMensajeOut, status_code=status.HTTP_201_CREATED)
def enviar_mensaje(ticket_id: int, payload: SoporteMensajeIn, current_user: UserOut = Depends(get_current_user)):
    texto = payload.mensaje.strip()
    if not texto:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El mensaje no puede estar vacío")

    conn = get_connection()
    try:
        _get_ticket_or_404(conn, ticket_id)
        rows = conn.run(
            f"INSERT INTO soporte_mensajes (ticket_id, de, mensaje) VALUES (:tid, 'superadmin', :msg) "
            f"RETURNING {', '.join(MENSAJE_COLUMNS)}",
            tid=ticket_id, msg=texto,
        )
        conn.run(
            "UPDATE soporte_tickets SET updated_at = now(), no_leidos_comercio = no_leidos_comercio + 1, "
            "estado = CASE WHEN estado = 'abierto' THEN 'en_progreso' ELSE estado END WHERE id = :id",
            id=ticket_id,
        )
        return SoporteMensajeOut(**dict(zip(MENSAJE_COLUMNS, rows[0])))
    finally:
        conn.close()


@router.patch("/tickets/{ticket_id}/estado", response_model=SoporteTicketOut)
def cambiar_estado(ticket_id: int, payload: SoporteEstadoIn, current_user: UserOut = Depends(get_current_user)):
    if payload.estado not in ESTADOS_VALIDOS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Estado inválido")

    conn = get_connection()
    try:
        _get_ticket_or_404(conn, ticket_id)
        conn.run(
            "UPDATE soporte_tickets SET estado = :estado, updated_at = now() WHERE id = :id",
            estado=payload.estado, id=ticket_id,
        )
        return SoporteTicketOut(**_get_ticket_or_404(conn, ticket_id))
    finally:
        conn.close()

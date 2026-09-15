import json
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pg8000.exceptions import DatabaseError
from pydantic import BaseModel, Field

from app.auth import get_current_user
from app.database import get_connection
from app.schemas import UserOut

router = APIRouter(prefix="/superadmin/planes", tags=["planes"])

UNIQUE_VIOLATION = "23505"

PLAN_COLUMNS = [
    "id", "nombre", "slug", "descripcion", "precio", "periodo", "color",
    "caracteristicas", "modulos", "destacado", "predeterminado", "activo", "orden", "visibilidad",
    "pais", "moneda", "fecha_creacion",
]


class PlanIn(BaseModel):
    nombre: str = Field(min_length=1, max_length=150)
    slug: str = Field(min_length=1, max_length=80)
    descripcion: Optional[str] = None
    precio: float = 0
    periodo: str = "mensual"
    color: str = "#6366f1"
    caracteristicas: list[str] = []
    modulos: list[str] = []
    destacado: bool = False
    orden: int = 0
    visibilidad: str = "publico"
    comercios: list[int] = []
    pais: str = "CO"
    moneda: str = "COP"


class PlanOut(BaseModel):
    id: int
    nombre: str
    slug: str
    descripcion: Optional[str] = None
    precio: float
    periodo: str
    color: str
    caracteristicas: list[str]
    modulos: list[str]
    destacado: bool
    predeterminado: bool
    activo: bool
    orden: int
    visibilidad: str
    pais: str
    moneda: str
    fecha_creacion: datetime
    comercios: list[int] = []


def _slugify(raw: str) -> str:
    slug = raw.strip().lower().replace(" ", "-")
    return re.sub(r"[^a-z0-9\-]", "", slug)


def _row_to_dict(row) -> dict:
    return dict(zip(PLAN_COLUMNS, row))


def _fetch_plan(conn, plan_id: int) -> PlanOut:
    rows = conn.run(f"SELECT {', '.join(PLAN_COLUMNS)} FROM planes WHERE id = :id", id=plan_id)
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan no encontrado")
    data = _row_to_dict(rows[0])
    comercios = conn.run("SELECT comercio_id FROM plan_comercios WHERE plan_id = :id", id=plan_id)
    data["comercios"] = [c[0] for c in comercios]
    return PlanOut(**data)


def _guardar_comercios(conn, plan_id: int, visibilidad: str, comercios: list[int]) -> None:
    conn.run("DELETE FROM plan_comercios WHERE plan_id = :id", id=plan_id)
    if visibilidad != "privado" or not comercios:
        return
    for comercio_id in set(comercios):
        conn.run(
            "INSERT INTO plan_comercios (plan_id, comercio_id) VALUES (:plan_id, :comercio_id) ON CONFLICT DO NOTHING",
            plan_id=plan_id, comercio_id=comercio_id,
        )


@router.get("", response_model=list[PlanOut])
def listar_planes(current_user: UserOut = Depends(get_current_user)):
    conn = get_connection()
    try:
        rows = conn.run(f"SELECT {', '.join(PLAN_COLUMNS)} FROM planes ORDER BY orden ASC, id ASC")
        planes = [_row_to_dict(row) for row in rows]

        comercios_rows = conn.run("SELECT plan_id, comercio_id FROM plan_comercios")
        por_plan: dict[int, list[int]] = {}
        for plan_id, comercio_id in comercios_rows:
            por_plan.setdefault(plan_id, []).append(comercio_id)

        for plan in planes:
            plan["comercios"] = por_plan.get(plan["id"], [])

        return [PlanOut(**plan) for plan in planes]
    finally:
        conn.close()


@router.post("", response_model=PlanOut, status_code=status.HTTP_201_CREATED)
def crear_plan(payload: PlanIn, current_user: UserOut = Depends(get_current_user)):
    slug = _slugify(payload.slug)
    if not payload.nombre.strip() or not slug:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nombre y slug son obligatorios")
    visibilidad = "privado" if payload.visibilidad == "privado" else "publico"

    conn = get_connection()
    try:
        try:
            rows = conn.run(
                """
                INSERT INTO planes (nombre, slug, descripcion, precio, periodo, color, caracteristicas, modulos, destacado, orden, visibilidad, pais, moneda)
                VALUES (:nombre, :slug, :descripcion, :precio, :periodo, :color, :caracteristicas, :modulos, :destacado, :orden, :visibilidad, :pais, :moneda)
                RETURNING id
                """,
                nombre=payload.nombre.strip(), slug=slug, descripcion=payload.descripcion,
                precio=payload.precio, periodo=payload.periodo, color=payload.color,
                caracteristicas=json.dumps([c.strip() for c in payload.caracteristicas if c.strip()]),
                modulos=json.dumps(payload.modulos), destacado=payload.destacado, orden=payload.orden,
                visibilidad=visibilidad, pais=payload.pais, moneda=payload.moneda,
            )
        except DatabaseError as exc:
            if exc.args and exc.args[0].get("C") == UNIQUE_VIOLATION:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"El slug '{slug}' ya existe")
            raise

        plan_id = rows[0][0]
        if payload.destacado:
            conn.run("UPDATE planes SET destacado = false WHERE id != :id", id=plan_id)
        _guardar_comercios(conn, plan_id, visibilidad, payload.comercios)
        return _fetch_plan(conn, plan_id)
    finally:
        conn.close()


@router.patch("/{plan_id}", response_model=PlanOut)
def editar_plan(plan_id: int, payload: PlanIn, current_user: UserOut = Depends(get_current_user)):
    slug = _slugify(payload.slug)
    if not payload.nombre.strip() or not slug:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nombre y slug son obligatorios")
    visibilidad = "privado" if payload.visibilidad == "privado" else "publico"

    conn = get_connection()
    try:
        _fetch_plan(conn, plan_id)
        try:
            conn.run(
                """
                UPDATE planes SET nombre=:nombre, slug=:slug, descripcion=:descripcion, precio=:precio,
                    periodo=:periodo, color=:color, caracteristicas=:caracteristicas, modulos=:modulos,
                    destacado=:destacado, orden=:orden, visibilidad=:visibilidad, pais=:pais, moneda=:moneda
                WHERE id=:id
                """,
                nombre=payload.nombre.strip(), slug=slug, descripcion=payload.descripcion,
                precio=payload.precio, periodo=payload.periodo, color=payload.color,
                caracteristicas=json.dumps([c.strip() for c in payload.caracteristicas if c.strip()]),
                modulos=json.dumps(payload.modulos), destacado=payload.destacado, orden=payload.orden,
                visibilidad=visibilidad, pais=payload.pais, moneda=payload.moneda, id=plan_id,
            )
        except DatabaseError as exc:
            if exc.args and exc.args[0].get("C") == UNIQUE_VIOLATION:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"El slug '{slug}' ya existe")
            raise

        if payload.destacado:
            conn.run("UPDATE planes SET destacado = false WHERE id != :id", id=plan_id)
        _guardar_comercios(conn, plan_id, visibilidad, payload.comercios)
        return _fetch_plan(conn, plan_id)
    finally:
        conn.close()


@router.delete("/{plan_id}")
def eliminar_plan(plan_id: int, current_user: UserOut = Depends(get_current_user)):
    conn = get_connection()
    try:
        _fetch_plan(conn, plan_id)
        conn.run("DELETE FROM planes WHERE id = :id", id=plan_id)
        return {"ok": True}
    finally:
        conn.close()


@router.post("/{plan_id}/toggle-activo", response_model=PlanOut)
def toggle_activo(plan_id: int, current_user: UserOut = Depends(get_current_user)):
    conn = get_connection()
    try:
        _fetch_plan(conn, plan_id)
        conn.run("UPDATE planes SET activo = NOT activo WHERE id = :id", id=plan_id)
        return _fetch_plan(conn, plan_id)
    finally:
        conn.close()


@router.post("/{plan_id}/destacar", response_model=PlanOut)
def destacar(plan_id: int, current_user: UserOut = Depends(get_current_user)):
    """Only one plan can be featured at a time."""
    conn = get_connection()
    try:
        _fetch_plan(conn, plan_id)
        conn.run("UPDATE planes SET destacado = false")
        conn.run("UPDATE planes SET destacado = true WHERE id = :id", id=plan_id)
        return _fetch_plan(conn, plan_id)
    finally:
        conn.close()


@router.post("/{plan_id}/predeterminado", response_model=PlanOut)
def marcar_predeterminado(plan_id: int, current_user: UserOut = Depends(get_current_user)):
    """Only one plan can be the automatic default assigned on signup."""
    conn = get_connection()
    try:
        _fetch_plan(conn, plan_id)
        conn.run("UPDATE planes SET predeterminado = false")
        conn.run("UPDATE planes SET predeterminado = true WHERE id = :id", id=plan_id)
        return _fetch_plan(conn, plan_id)
    finally:
        conn.close()

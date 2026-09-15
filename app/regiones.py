from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pg8000.exceptions import DatabaseError
from pydantic import BaseModel, Field

from app.auth import get_current_user
from app.database import get_connection
from app.schemas import UserOut

router = APIRouter(prefix="/superadmin/regiones", tags=["regiones"])

UNIQUE_VIOLATION = "23505"

REGION_COLUMNS = ["id", "pais", "codigo_pais", "moneda", "codigo_moneda", "activo", "orden", "fecha_creacion"]


class RegionIn(BaseModel):
    pais: str = Field(min_length=1, max_length=100)
    codigo_pais: str = Field(min_length=2, max_length=2)
    moneda: str = Field(min_length=1, max_length=100)
    codigo_moneda: str = Field(min_length=3, max_length=3)
    orden: int = 0


class RegionOut(BaseModel):
    id: int
    pais: str
    codigo_pais: str
    moneda: str
    codigo_moneda: str
    activo: bool
    orden: int
    fecha_creacion: datetime


def _row_to_region(row) -> RegionOut:
    return RegionOut(**dict(zip(REGION_COLUMNS, row)))


def _get_region_or_404(conn, region_id: int) -> RegionOut:
    rows = conn.run(f"SELECT {', '.join(REGION_COLUMNS)} FROM regiones WHERE id = :id", id=region_id)
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Región no encontrada")
    return _row_to_region(rows[0])


@router.get("", response_model=list[RegionOut])
def listar_regiones(current_user: UserOut = Depends(get_current_user)):
    conn = get_connection()
    try:
        rows = conn.run(f"SELECT {', '.join(REGION_COLUMNS)} FROM regiones ORDER BY orden ASC, id ASC")
        return [_row_to_region(row) for row in rows]
    finally:
        conn.close()


@router.post("", response_model=RegionOut, status_code=status.HTTP_201_CREATED)
def crear_region(payload: RegionIn, current_user: UserOut = Depends(get_current_user)):
    conn = get_connection()
    try:
        try:
            rows = conn.run(
                f"""
                INSERT INTO regiones (pais, codigo_pais, moneda, codigo_moneda, orden)
                VALUES (:pais, :codigo_pais, :moneda, :codigo_moneda, :orden)
                RETURNING {', '.join(REGION_COLUMNS)}
                """,
                pais=payload.pais.strip(), codigo_pais=payload.codigo_pais.strip().upper(),
                moneda=payload.moneda.strip(), codigo_moneda=payload.codigo_moneda.strip().upper(),
                orden=payload.orden,
            )
        except DatabaseError as exc:
            if exc.args and exc.args[0].get("C") == UNIQUE_VIOLATION:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ese código de país ya existe")
            raise
        return _row_to_region(rows[0])
    finally:
        conn.close()


@router.patch("/{region_id}", response_model=RegionOut)
def editar_region(region_id: int, payload: RegionIn, current_user: UserOut = Depends(get_current_user)):
    conn = get_connection()
    try:
        _get_region_or_404(conn, region_id)
        try:
            conn.run(
                """
                UPDATE regiones SET pais=:pais, codigo_pais=:codigo_pais, moneda=:moneda,
                    codigo_moneda=:codigo_moneda, orden=:orden
                WHERE id=:id
                """,
                pais=payload.pais.strip(), codigo_pais=payload.codigo_pais.strip().upper(),
                moneda=payload.moneda.strip(), codigo_moneda=payload.codigo_moneda.strip().upper(),
                orden=payload.orden, id=region_id,
            )
        except DatabaseError as exc:
            if exc.args and exc.args[0].get("C") == UNIQUE_VIOLATION:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ese código de país ya existe")
            raise
        return _get_region_or_404(conn, region_id)
    finally:
        conn.close()


@router.post("/{region_id}/toggle-activo", response_model=RegionOut)
def toggle_activo(region_id: int, current_user: UserOut = Depends(get_current_user)):
    conn = get_connection()
    try:
        _get_region_or_404(conn, region_id)
        conn.run("UPDATE regiones SET activo = NOT activo WHERE id = :id", id=region_id)
        return _get_region_or_404(conn, region_id)
    finally:
        conn.close()


@router.delete("/{region_id}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_region(region_id: int, current_user: UserOut = Depends(get_current_user)):
    conn = get_connection()
    try:
        _get_region_or_404(conn, region_id)
        conn.run("DELETE FROM regiones WHERE id = :id", id=region_id)
    finally:
        conn.close()

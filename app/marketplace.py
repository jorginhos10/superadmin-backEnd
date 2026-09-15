from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import get_current_user
from app.database import get_connection
from app.schemas import UserOut

router = APIRouter(prefix="/superadmin/marketplace", tags=["marketplace"])

PRODUCTO_COLUMNS = [
    "id", "nombre", "categoria", "precio", "unidad", "descripcion", "activo", "fecha_creacion", "tienda_id",
]


class ProductoIn(BaseModel):
    nombre: str = Field(min_length=1, max_length=150)
    categoria: str = Field(min_length=1, max_length=80)
    precio: float = 0
    unidad: str = "unidad"
    descripcion: Optional[str] = None


class ProductoOut(BaseModel):
    id: int
    nombre: str
    categoria: str
    precio: float
    unidad: str
    descripcion: Optional[str] = None
    activo: bool
    fecha_creacion: datetime
    tienda_id: int


def _row_to_producto(row) -> ProductoOut:
    return ProductoOut(**dict(zip(PRODUCTO_COLUMNS, row)))


def _fetch_producto(conn, producto_id: int) -> ProductoOut:
    rows = conn.run(
        f"SELECT {', '.join(PRODUCTO_COLUMNS)} FROM marketplace_productos WHERE id = :id", id=producto_id
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Producto no encontrado")
    return _row_to_producto(rows[0])


def _chefcontrol_tienda_id(conn) -> int:
    """All products created here belong to the platform's own store. When
    comercio-owned stores exist (future), product creation will take a
    tienda_id instead of assuming this one."""
    rows = conn.run("SELECT id FROM tiendas WHERE slug = 'chefcontrol'")
    if not rows:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Tienda ChefControl no existe")
    return rows[0][0]


@router.get("", response_model=list[ProductoOut])
def listar_productos(current_user: UserOut = Depends(get_current_user)):
    conn = get_connection()
    try:
        rows = conn.run(
            f"SELECT {', '.join(PRODUCTO_COLUMNS)} FROM marketplace_productos ORDER BY categoria, nombre"
        )
        return [_row_to_producto(row) for row in rows]
    finally:
        conn.close()


@router.post("", response_model=ProductoOut, status_code=status.HTTP_201_CREATED)
def crear_producto(payload: ProductoIn, current_user: UserOut = Depends(get_current_user)):
    conn = get_connection()
    try:
        rows = conn.run(
            """
            INSERT INTO marketplace_productos (nombre, categoria, precio, unidad, descripcion, tienda_id)
            VALUES (:nombre, :categoria, :precio, :unidad, :descripcion, :tienda_id)
            RETURNING id
            """,
            nombre=payload.nombre.strip(), categoria=payload.categoria.strip(),
            precio=payload.precio, unidad=payload.unidad.strip() or "unidad",
            descripcion=payload.descripcion, tienda_id=_chefcontrol_tienda_id(conn),
        )
        return _fetch_producto(conn, rows[0][0])
    finally:
        conn.close()


@router.patch("/{producto_id}", response_model=ProductoOut)
def editar_producto(producto_id: int, payload: ProductoIn, current_user: UserOut = Depends(get_current_user)):
    conn = get_connection()
    try:
        _fetch_producto(conn, producto_id)
        conn.run(
            """
            UPDATE marketplace_productos
            SET nombre=:nombre, categoria=:categoria, precio=:precio, unidad=:unidad, descripcion=:descripcion
            WHERE id=:id
            """,
            nombre=payload.nombre.strip(), categoria=payload.categoria.strip(),
            precio=payload.precio, unidad=payload.unidad.strip() or "unidad",
            descripcion=payload.descripcion, id=producto_id,
        )
        return _fetch_producto(conn, producto_id)
    finally:
        conn.close()


@router.delete("/{producto_id}")
def eliminar_producto(producto_id: int, current_user: UserOut = Depends(get_current_user)):
    conn = get_connection()
    try:
        _fetch_producto(conn, producto_id)
        conn.run("DELETE FROM marketplace_productos WHERE id = :id", id=producto_id)
        return {"ok": True}
    finally:
        conn.close()


@router.post("/{producto_id}/toggle-activo", response_model=ProductoOut)
def toggle_activo(producto_id: int, current_user: UserOut = Depends(get_current_user)):
    conn = get_connection()
    try:
        _fetch_producto(conn, producto_id)
        conn.run("UPDATE marketplace_productos SET activo = NOT activo WHERE id = :id", id=producto_id)
        return _fetch_producto(conn, producto_id)
    finally:
        conn.close()

import json
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.auth import get_current_user
from app.database import get_connection, get_tenant_connection
from app.schemas import UserOut

router = APIRouter(prefix="/superadmin/marketplace", tags=["marketplace"])

PRODUCTO_COLUMNS = [
    "id", "nombre", "categoria", "precio", "unidad", "descripcion", "activo", "fecha_creacion", "tienda_id", "precio_envio", "imagenes",
]

MAX_IMAGENES = 5
MAX_CHARS_IMAGEN = 1_500_000  # ~1 MB once decoded; the panel shrinks images before sending
IMAGEN_DATA_URL = re.compile(r"^data:image/(png|jpe?g|webp|gif);base64,[A-Za-z0-9+/=]+$")


class ProductoIn(BaseModel):
    nombre: str = Field(min_length=1, max_length=150)
    categoria: str = Field(min_length=1, max_length=80)
    precio: float = 0
    # Shipping charged once per product line of an order, whatever the quantity.
    precio_envio: float = Field(default=0, ge=0)
    unidad: str = "unidad"
    descripcion: Optional[str] = None
    imagenes: list[str] = Field(default_factory=list, max_length=MAX_IMAGENES)

    @field_validator("imagenes")
    @classmethod
    def _imagenes_validas(cls, valor: list[str]) -> list[str]:
        for imagen in valor:
            if len(imagen) > MAX_CHARS_IMAGEN or not IMAGEN_DATA_URL.match(imagen):
                raise ValueError("Cada imagen debe ser PNG, JPG, WEBP o GIF y pesar máximo 1 MB")
        return valor


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
    precio_envio: float = 0
    imagenes: list[str] = []


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
            INSERT INTO marketplace_productos (nombre, categoria, precio, precio_envio, unidad, descripcion, tienda_id, imagenes)
            VALUES (:nombre, :categoria, :precio, :precio_envio, :unidad, :descripcion, :tienda_id, :imagenes)
            RETURNING id
            """,
            nombre=payload.nombre.strip(), categoria=payload.categoria.strip(),
            precio=payload.precio, precio_envio=payload.precio_envio, unidad=payload.unidad.strip() or "unidad",
            descripcion=payload.descripcion, tienda_id=_chefcontrol_tienda_id(conn), imagenes=json.dumps(payload.imagenes),
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
            SET nombre=:nombre, categoria=:categoria, precio=:precio, precio_envio=:precio_envio, unidad=:unidad,
                descripcion=:descripcion, imagenes=:imagenes
            WHERE id=:id
            """,
            nombre=payload.nombre.strip(), categoria=payload.categoria.strip(),
            precio=payload.precio, precio_envio=payload.precio_envio, unidad=payload.unidad.strip() or "unidad",
            descripcion=payload.descripcion, imagenes=json.dumps(payload.imagenes), id=producto_id,
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


# ---------- Órdenes de los comercios (pedidos al marketplace de la plataforma) ----------
# A comercio's order starts as pendiente_pago and becomes pagado when Wompi confirms it. From there
# the SuperAdmin fulfils it: pagado -> en_preparacion -> en_camino -> entregado.

ESTADOS_ORDENES = ("pagado", "en_preparacion", "en_camino")
ESTADOS_HISTORIAL = ("entregado", "fallido", "cancelado")
SIGUIENTE_ESTADO = {"pagado": "en_preparacion", "en_preparacion": "en_camino", "en_camino": "entregado"}

PEDIDO_SELECT = """
    SELECT p.id, p.usuario_id, u.nombre, u.email, n.nombre, p.tienda_nombre, p.subtotal, p.descuento, p.envio, p.total,
           p.cupon_codigo, p.estado, p.wompi_reference, p.created_at, p.updated_at
    FROM marketplace_pedidos p
    JOIN usuarios u ON u.id = p.usuario_id
    LEFT JOIN negocios n ON n.usuario_id = u.id
"""
PEDIDO_KEYS = [
    "id", "comercio_id", "comercio", "comercio_email", "negocio", "tienda_nombre", "subtotal", "descuento", "envio", "total",
    "cupon_codigo", "estado", "wompi_reference", "created_at", "updated_at",
]


class PedidoItemOut(BaseModel):
    id: int
    nombre: str
    categoria: str
    precio_unitario: float
    cantidad: float
    subtotal: float


class PedidoOut(BaseModel):
    id: int
    radicado: str = ""
    comercio_id: int
    comercio: str
    comercio_email: str
    negocio: Optional[str] = None
    tienda_nombre: str
    subtotal: float
    descuento: float
    envio: float = 0
    total: float
    cupon_codigo: Optional[str] = None
    estado: str
    wompi_reference: str
    created_at: datetime
    updated_at: datetime
    items: list[PedidoItemOut] = []


def _tienda_de_la_plataforma() -> int:
    sconn = get_connection()
    try:
        return _chefcontrol_tienda_id(sconn)
    finally:
        sconn.close()


def radicado_de(pedido_id: int, creado: datetime) -> str:
    """Filing number shown to both the merchant and the SuperAdmin (same format on both sides)."""
    return f"MK-{creado.year}-{pedido_id:06d}"


def _row_a_pedido(row) -> dict:
    datos = dict(zip(PEDIDO_KEYS, row))
    for clave in ("subtotal", "descuento", "envio", "total"):
        datos[clave] = float(datos[clave])
    datos["radicado"] = radicado_de(datos["id"], datos["created_at"])
    return datos


@router.get("/pedidos", response_model=list[PedidoOut])
def listar_pedidos(vista: str = "ordenes", current_user: UserOut = Depends(get_current_user)):
    """vista=ordenes: paid orders still to fulfil (oldest first). vista=historial: finished ones (newest first)."""
    if vista not in ("ordenes", "historial"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Vista no válida")
    estados = ESTADOS_ORDENES if vista == "ordenes" else ESTADOS_HISTORIAL
    orden = "ASC" if vista == "ordenes" else "DESC"
    tienda_id = _tienda_de_la_plataforma()

    conn = get_tenant_connection()
    try:
        marcadores = ", ".join(f"'{e}'" for e in estados)
        filas = conn.run(
            f"{PEDIDO_SELECT} WHERE p.tienda_id = :t AND p.estado IN ({marcadores}) ORDER BY p.created_at {orden}",
            t=tienda_id,
        )
        return [PedidoOut(**_row_a_pedido(f)) for f in filas]
    finally:
        conn.close()


def _pedido_con_items(conn, pedido_id: int) -> PedidoOut:
    filas = conn.run(f"{PEDIDO_SELECT} WHERE p.id = :id AND p.tienda_id = :t", id=pedido_id, t=_tienda_de_la_plataforma())
    if not filas:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Orden no encontrada")
    items = conn.run(
        "SELECT id, nombre, categoria, precio_unitario, cantidad, subtotal FROM marketplace_pedido_items "
        "WHERE pedido_id = :id ORDER BY id",
        id=pedido_id,
    )
    datos = _row_a_pedido(filas[0])
    datos["items"] = [
        PedidoItemOut(id=i[0], nombre=i[1], categoria=i[2], precio_unitario=float(i[3]), cantidad=float(i[4]), subtotal=float(i[5]))
        for i in items
    ]
    return PedidoOut(**datos)


@router.get("/pedidos/{pedido_id}", response_model=PedidoOut)
def ver_pedido(pedido_id: int, current_user: UserOut = Depends(get_current_user)):
    conn = get_tenant_connection()
    try:
        return _pedido_con_items(conn, pedido_id)
    finally:
        conn.close()


@router.post("/pedidos/{pedido_id}/avanzar", response_model=PedidoOut)
def avanzar_pedido(pedido_id: int, current_user: UserOut = Depends(get_current_user)):
    """Moves an order to its next fulfilment step."""
    conn = get_tenant_connection()
    try:
        pedido = _pedido_con_items(conn, pedido_id)
        siguiente = SIGUIENTE_ESTADO.get(pedido.estado)
        if siguiente is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Esta orden ya no tiene un siguiente paso")
        conn.run(
            "UPDATE marketplace_pedidos SET estado = :e, updated_at = now() WHERE id = :id AND estado = :actual",
            e=siguiente, id=pedido_id, actual=pedido.estado,
        )
        return _pedido_con_items(conn, pedido_id)
    finally:
        conn.close()

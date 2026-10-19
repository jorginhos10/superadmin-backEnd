import json
import logging
import re
import time
from datetime import datetime
from typing import Literal, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
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
    "pais", "moneda", "dias_gratis", "tipo_comercio", "fecha_creacion",
]


TipoComercio = Literal["restobar", "store"]


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
    # Free time gifted on subscribing; it postpones the first billing date. Only meaningful for paid plans.
    dias_gratis: int = Field(default=0, ge=0, le=365)
    # Which kind of commerce the plan is offered to (each type has its own list of plans).
    tipo_comercio: TipoComercio = "restobar"


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
    dias_gratis: int = 0
    tipo_comercio: str = "restobar"
    fecha_creacion: datetime
    comercios: list[int] = []


def _dias_gratis(payload: PlanIn) -> int:
    """A free plan has nothing to postpone, so free time only applies to paid plans."""
    return payload.dias_gratis if payload.precio > 0 else 0


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
def listar_planes(tipo: Optional[TipoComercio] = None, current_user: UserOut = Depends(get_current_user)):
    conn = get_connection()
    try:
        if tipo:
            rows = conn.run(
                f"SELECT {', '.join(PLAN_COLUMNS)} FROM planes WHERE tipo_comercio = :tipo ORDER BY orden ASC, id ASC",
                tipo=tipo,
            )
        else:
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
                INSERT INTO planes (nombre, slug, descripcion, precio, periodo, color, caracteristicas, modulos, destacado, orden, visibilidad, pais, moneda, dias_gratis, tipo_comercio)
                VALUES (:nombre, :slug, :descripcion, :precio, :periodo, :color, :caracteristicas, :modulos, :destacado, :orden, :visibilidad, :pais, :moneda, :dias_gratis, :tipo)
                RETURNING id
                """,
                nombre=payload.nombre.strip(), slug=slug, descripcion=payload.descripcion,
                precio=payload.precio, periodo=payload.periodo, color=payload.color,
                caracteristicas=json.dumps([c.strip() for c in payload.caracteristicas if c.strip()]),
                modulos=json.dumps(payload.modulos), destacado=payload.destacado, orden=payload.orden,
                visibilidad=visibilidad, pais=payload.pais, moneda=payload.moneda, dias_gratis=_dias_gratis(payload),
                tipo=payload.tipo_comercio,
            )
        except DatabaseError as exc:
            if exc.args and exc.args[0].get("C") == UNIQUE_VIOLATION:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"El slug '{slug}' ya existe")
            raise

        plan_id = rows[0][0]
        if payload.destacado:
            conn.run(
                "UPDATE planes SET destacado = false WHERE pais = :pais AND tipo_comercio = :tipo AND id != :id",
                pais=payload.pais, tipo=payload.tipo_comercio, id=plan_id,
            )
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
                    destacado=:destacado, orden=:orden, visibilidad=:visibilidad, pais=:pais, moneda=:moneda,
                    dias_gratis=:dias_gratis, tipo_comercio=:tipo
                WHERE id=:id
                """,
                nombre=payload.nombre.strip(), slug=slug, descripcion=payload.descripcion,
                precio=payload.precio, periodo=payload.periodo, color=payload.color,
                caracteristicas=json.dumps([c.strip() for c in payload.caracteristicas if c.strip()]),
                modulos=json.dumps(payload.modulos), destacado=payload.destacado, orden=payload.orden,
                visibilidad=visibilidad, pais=payload.pais, moneda=payload.moneda, dias_gratis=_dias_gratis(payload),
                tipo=payload.tipo_comercio, id=plan_id,
            )
        except DatabaseError as exc:
            if exc.args and exc.args[0].get("C") == UNIQUE_VIOLATION:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"El slug '{slug}' ya existe")
            raise

        if payload.destacado:
            conn.run(
                "UPDATE planes SET destacado = false WHERE pais = :pais AND tipo_comercio = :tipo AND id != :id",
                pais=payload.pais, tipo=payload.tipo_comercio, id=plan_id,
            )
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
    """Only one plan per país and type of commerce can be featured at a time."""
    conn = get_connection()
    try:
        plan = _fetch_plan(conn, plan_id)
        conn.run(
            "UPDATE planes SET destacado = false WHERE pais = :pais AND tipo_comercio = :tipo",
            pais=plan.pais, tipo=plan.tipo_comercio,
        )
        conn.run("UPDATE planes SET destacado = true WHERE id = :id", id=plan_id)
        return _fetch_plan(conn, plan_id)
    finally:
        conn.close()


@router.post("/{plan_id}/predeterminado", response_model=PlanOut)
def marcar_predeterminado(plan_id: int, current_user: UserOut = Depends(get_current_user)):
    """Only one plan per país can be the automatic default assigned on signup."""
    conn = get_connection()
    try:
        plan = _fetch_plan(conn, plan_id)
        conn.run("UPDATE planes SET predeterminado = false WHERE pais = :pais", pais=plan.pais)
        conn.run("UPDATE planes SET predeterminado = true WHERE id = :id", id=plan_id)
        return _fetch_plan(conn, plan_id)
    finally:
        conn.close()


# --- Duplicar un plan a otro país, convirtiendo el precio a la moneda local de esa región ----------------

logger = logging.getLogger("planes")

# Tasas de cambio gratuitas y sin llave; si una falla se prueba la siguiente.
_PROVEEDORES_TASAS = [
    ("https://open.er-api.com/v6/latest/{de}", lambda d, de, a: d.get("rates", {}).get(a) if d.get("result") == "success" else None),
    (
        "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/{de_min}.json",
        lambda d, de, a: d.get(de.lower(), {}).get(a.lower()),
    ),
]
_CACHE_TASAS: dict[tuple[str, str], tuple[float, float]] = {}
_TTL_TASAS_S = 3600


def _tasa_de_cambio(de: str, a: str) -> Optional[float]:
    """Cuántas unidades de `a` vale una unidad de `de`; None si ningún proveedor responde."""
    if de == a:
        return 1.0
    en_cache = _CACHE_TASAS.get((de, a))
    if en_cache and time.time() - en_cache[1] < _TTL_TASAS_S:
        return en_cache[0]
    for url, extraer in _PROVEEDORES_TASAS:
        try:
            resp = httpx.get(url.format(de=de, de_min=de.lower()), timeout=4.0)
            if resp.status_code != 200:
                continue
            tasa = extraer(resp.json(), de, a)
            if isinstance(tasa, (int, float)) and tasa > 0:
                _CACHE_TASAS[(de, a)] = (float(tasa), time.time())
                return float(tasa)
        except (httpx.HTTPError, ValueError):
            logger.warning("No se pudo consultar la tasa %s->%s en %s", de, a, url.split("/")[2])
    return None


def _redondear_precio(valor: float) -> float:
    """Redondeo comercial: a mayor valor, menos decimales (3.000 COP -> 15 MXN, no 14,87)."""
    if valor >= 1000:
        return float(round(valor / 100) * 100)
    if valor >= 100:
        return float(round(valor / 5) * 5)
    if valor >= 10:
        return float(round(valor))
    return round(valor, 2)


def _region_activa(conn, codigo_pais: str):
    rows = conn.run(
        "SELECT pais, codigo_moneda, moneda FROM regiones WHERE codigo_pais = :c AND activo = true",
        c=codigo_pais.strip().upper(),
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ese país no está registrado (o está inactivo) en Regiones")
    return rows[0]


class ConversionOut(BaseModel):
    pais: str
    moneda_origen: str
    moneda_destino: str
    nombre_moneda_destino: str
    precio_origen: float
    tasa: Optional[float] = None
    precio_convertido: Optional[float] = None


@router.get("/{plan_id}/conversion", response_model=ConversionOut)
def conversion_para_duplicar(plan_id: int, pais: str = Query(min_length=2, max_length=2), current_user: UserOut = Depends(get_current_user)):
    """Precio del plan expresado en la moneda local del país destino (la registrada en Regiones)."""
    conn = get_connection()
    try:
        plan = _fetch_plan(conn, plan_id)
        nombre_pais, moneda_destino, nombre_moneda = _region_activa(conn, pais)
    finally:
        conn.close()
    tasa = _tasa_de_cambio(plan.moneda, moneda_destino)
    convertido = _redondear_precio(plan.precio * tasa) if tasa is not None and plan.precio > 0 else (0.0 if plan.precio <= 0 else None)
    return ConversionOut(
        pais=nombre_pais, moneda_origen=plan.moneda, moneda_destino=moneda_destino, nombre_moneda_destino=nombre_moneda,
        precio_origen=plan.precio, tasa=tasa, precio_convertido=convertido,
    )


class DuplicarIn(BaseModel):
    pais: str = Field(min_length=2, max_length=2)
    nombre: Optional[str] = Field(default=None, max_length=150)
    # Si no se indica, se convierte con la tasa del día.
    precio: Optional[float] = Field(default=None, ge=0)


@router.post("/{plan_id}/duplicar", response_model=PlanOut, status_code=status.HTTP_201_CREATED)
def duplicar_plan(plan_id: int, payload: DuplicarIn, current_user: UserOut = Depends(get_current_user)):
    conn = get_connection()
    try:
        origen = _fetch_plan(conn, plan_id)
        _, moneda_destino, _ = _region_activa(conn, payload.pais)
        pais = payload.pais.strip().upper()

        precio = payload.precio
        if precio is None:
            tasa = _tasa_de_cambio(origen.moneda, moneda_destino)
            if tasa is None and origen.precio > 0:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="No se pudo consultar la tasa de cambio; escribe el precio a mano",
                )
            precio = _redondear_precio(origen.precio * tasa) if origen.precio > 0 else 0.0

        nombre = (payload.nombre or origen.nombre).strip()
        base_slug = _slugify(f"{origen.slug}-{pais.lower()}" if pais != origen.pais else f"{origen.slug}-copia")
        slug, n = base_slug, 1
        while conn.run("SELECT 1 FROM planes WHERE slug = :s", s=slug):
            n += 1
            slug = f"{base_slug}-{n}"

        rows = conn.run(
            """
            INSERT INTO planes (nombre, slug, descripcion, precio, periodo, color, caracteristicas, modulos, destacado, orden,
                                visibilidad, pais, moneda, dias_gratis, tipo_comercio, activo)
            VALUES (:nombre, :slug, :descripcion, :precio, :periodo, :color, :caracteristicas, :modulos, false, :orden,
                    :visibilidad, :pais, :moneda, :dias_gratis, :tipo, :activo)
            RETURNING id
            """,
            nombre=nombre, slug=slug, descripcion=origen.descripcion, precio=precio, periodo=origen.periodo, color=origen.color,
            caracteristicas=json.dumps(origen.caracteristicas), modulos=json.dumps(origen.modulos), orden=origen.orden,
            visibilidad=origen.visibilidad, pais=pais, moneda=moneda_destino,
            dias_gratis=origen.dias_gratis if precio > 0 else 0, tipo=origen.tipo_comercio, activo=origen.activo,
        )
        nuevo_id = rows[0][0]
        _guardar_comercios(conn, nuevo_id, origen.visibilidad, origen.comercios)
        return _fetch_plan(conn, nuevo_id)
    finally:
        conn.close()

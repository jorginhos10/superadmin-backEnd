import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.auth import get_current_user
from app.database import get_connection
from app.schemas import UserOut

router = APIRouter(prefix="/superadmin/apariencias", tags=["apariencias"])

# Debe reflejar exactamente los AparienciaId que existen en el front del cliente.
APARIENCIAS_CONOCIDAS = [
    {"key": "violet-original", "nombre": "Violet Original"},
    {"key": "violet-tableta", "nombre": "Violet Original — Modo Tableta"},
    {"key": "halloween", "nombre": "Halloween"},
    {"key": "navidad", "nombre": "Navidad"},
    {"key": "modo-nocturno", "nombre": "Modo Nocturno"},
]
CLAVES_VALIDAS = {a["key"] for a in APARIENCIAS_CONOCIDAS}
VISIBILIDADES_VALIDAS = {"todos", "nadie", "todos_menos", "solo"}

# Apariencias que se pueden elegir para la pantalla de login (no todas tienen sentido antes de iniciar sesión).
LOGIN_CLAVES = ["violet-original", "halloween", "navidad"]
LOGIN_APARIENCIAS = [a for a in APARIENCIAS_CONOCIDAS if a["key"] in LOGIN_CLAVES]


MAX_CHARS_IMAGEN = 900_000  # el panel la reduce antes de enviarla (unos 100-300 KB)
IMAGEN_DATA_URL = re.compile(r"^data:image/(png|jpe?g|webp);base64,[A-Za-z0-9+/=]+$")


class AparienciaReglaOut(BaseModel):
    key: str
    nombre: str
    visibilidad: str
    comercios: list[int] = []
    imagen: str | None = None


class AparienciaImagenIn(BaseModel):
    imagen: str

    @field_validator("imagen")
    @classmethod
    def _imagen_valida(cls, valor: str) -> str:
        if len(valor) > MAX_CHARS_IMAGEN or not IMAGEN_DATA_URL.match(valor):
            raise ValueError("La imagen debe ser PNG, JPG o WEBP y pesar menos de 600 KB")
        return valor


class AparienciaReglaIn(BaseModel):
    visibilidad: str = Field(pattern="^(todos|nadie|todos_menos|solo)$")
    comercios: list[int] = []


@router.get("", response_model=list[AparienciaReglaOut])
def listar(current_user: UserOut = Depends(get_current_user)):
    conn = get_connection()
    try:
        reglas = dict(conn.run("SELECT apariencia_key, visibilidad FROM apariencia_reglas"))
        comercios_por_key: dict[str, list[int]] = {}
        for key, comercio_id in conn.run("SELECT apariencia_key, comercio_id FROM apariencia_comercios"):
            comercios_por_key.setdefault(key, []).append(comercio_id)

        imagenes = dict(conn.run("SELECT apariencia_key, imagen FROM apariencia_imagenes"))

        return [
            AparienciaReglaOut(
                key=a["key"],
                nombre=a["nombre"],
                visibilidad=reglas.get(a["key"], "todos"),
                comercios=comercios_por_key.get(a["key"], []),
                imagen=imagenes.get(a["key"]),
            )
            for a in APARIENCIAS_CONOCIDAS
        ]
    finally:
        conn.close()


class AparienciaLoginOut(BaseModel):
    apariencia: str
    opciones: list[dict]


class AparienciaLoginIn(BaseModel):
    apariencia: str


def _leer_login(conn) -> str:
    rows = conn.run("SELECT apariencia_key FROM apariencia_login WHERE id = 1")
    return rows[0][0] if rows and rows[0][0] in LOGIN_CLAVES else "violet-original"


def _login_out(conn, actual: str) -> AparienciaLoginOut:
    fotos = dict(conn.run("SELECT apariencia_key, imagen FROM apariencia_imagenes WHERE apariencia_key LIKE 'login:%'"))
    opciones = [{**o, "imagen": fotos.get("login:" + o["key"])} for o in LOGIN_APARIENCIAS]
    return AparienciaLoginOut(apariencia=actual, opciones=opciones)


@router.get("/login", response_model=AparienciaLoginOut)
def obtener_login(current_user: UserOut = Depends(get_current_user)):
    """Apariencia de la pantalla de inicio de sesión de los comercios."""
    conn = get_connection()
    try:
        return _login_out(conn, _leer_login(conn))
    finally:
        conn.close()


@router.put("/login", response_model=AparienciaLoginOut)
def guardar_login(payload: AparienciaLoginIn, current_user: UserOut = Depends(get_current_user)):
    if payload.apariencia not in LOGIN_CLAVES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Apariencia no disponible para el login")
    conn = get_connection()
    try:
        conn.run(
            "INSERT INTO apariencia_login (id, apariencia_key) VALUES (1, :k) "
            "ON CONFLICT (id) DO UPDATE SET apariencia_key = EXCLUDED.apariencia_key, updated_at = now()",
            k=payload.apariencia,
        )
        return _login_out(conn, payload.apariencia)
    finally:
        conn.close()


@router.put("/login/{key}/imagen", response_model=AparienciaLoginOut)
def guardar_foto_login(key: str, payload: AparienciaImagenIn, current_user: UserOut = Depends(get_current_user)):
    """Foto de la pantalla de login con esta apariencia, para que el SuperAdmin la vea al elegir."""
    if key not in LOGIN_CLAVES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Apariencia no disponible para el login")
    conn = get_connection()
    try:
        conn.run(
            "INSERT INTO apariencia_imagenes (apariencia_key, imagen) VALUES (:k, :i) "
            "ON CONFLICT (apariencia_key) DO UPDATE SET imagen = EXCLUDED.imagen, updated_at = now()",
            k="login:" + key, i=payload.imagen,
        )
        return _login_out(conn, _leer_login(conn))
    finally:
        conn.close()


@router.delete("/login/{key}/imagen", response_model=AparienciaLoginOut)
def quitar_foto_login(key: str, current_user: UserOut = Depends(get_current_user)):
    if key not in LOGIN_CLAVES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Apariencia no disponible para el login")
    conn = get_connection()
    try:
        conn.run("DELETE FROM apariencia_imagenes WHERE apariencia_key = :k", k="login:" + key)
        return _login_out(conn, _leer_login(conn))
    finally:
        conn.close()


@router.put("/{key}/imagen", response_model=AparienciaReglaOut)
def guardar_imagen(key: str, payload: AparienciaImagenIn, current_user: UserOut = Depends(get_current_user)):
    """Imagen que el comercio ve como vista previa de esta apariencia."""
    if key not in CLAVES_VALIDAS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Apariencia no reconocida")
    conn = get_connection()
    try:
        conn.run(
            "INSERT INTO apariencia_imagenes (apariencia_key, imagen) VALUES (:k, :i) "
            "ON CONFLICT (apariencia_key) DO UPDATE SET imagen = EXCLUDED.imagen, updated_at = now()",
            k=key, i=payload.imagen,
        )
        return _regla(conn, key)
    finally:
        conn.close()


@router.delete("/{key}/imagen", response_model=AparienciaReglaOut)
def quitar_imagen(key: str, current_user: UserOut = Depends(get_current_user)):
    if key not in CLAVES_VALIDAS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Apariencia no reconocida")
    conn = get_connection()
    try:
        conn.run("DELETE FROM apariencia_imagenes WHERE apariencia_key = :k", k=key)
        return _regla(conn, key)
    finally:
        conn.close()


def _regla(conn, key: str) -> AparienciaReglaOut:
    vis = conn.run("SELECT visibilidad FROM apariencia_reglas WHERE apariencia_key = :k", k=key)
    comercios = [r[0] for r in conn.run("SELECT comercio_id FROM apariencia_comercios WHERE apariencia_key = :k", k=key)]
    img = conn.run("SELECT imagen FROM apariencia_imagenes WHERE apariencia_key = :k", k=key)
    nombre = next(a["nombre"] for a in APARIENCIAS_CONOCIDAS if a["key"] == key)
    return AparienciaReglaOut(
        key=key, nombre=nombre, visibilidad=vis[0][0] if vis else "todos", comercios=comercios,
        imagen=img[0][0] if img else None,
    )


@router.put("/{key}", response_model=AparienciaReglaOut)
def actualizar(key: str, payload: AparienciaReglaIn, current_user: UserOut = Depends(get_current_user)):
    if key not in CLAVES_VALIDAS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Apariencia no reconocida")

    conn = get_connection()
    try:
        conn.run(
            "INSERT INTO apariencia_reglas (apariencia_key, visibilidad) VALUES (:key, :vis) "
            "ON CONFLICT (apariencia_key) DO UPDATE SET visibilidad = EXCLUDED.visibilidad, updated_at = now()",
            key=key, vis=payload.visibilidad,
        )
        conn.run("DELETE FROM apariencia_comercios WHERE apariencia_key = :key", key=key)

        comercios: list[int] = []
        if payload.visibilidad in ("todos_menos", "solo"):
            comercios = sorted(set(payload.comercios))
            for comercio_id in comercios:
                conn.run(
                    "INSERT INTO apariencia_comercios (apariencia_key, comercio_id) VALUES (:key, :cid) "
                    "ON CONFLICT DO NOTHING",
                    key=key, cid=comercio_id,
                )

        nombre = next(a["nombre"] for a in APARIENCIAS_CONOCIDAS if a["key"] == key)
        img = conn.run("SELECT imagen FROM apariencia_imagenes WHERE apariencia_key = :k", k=key)
        return AparienciaReglaOut(
            key=key, nombre=nombre, visibilidad=payload.visibilidad, comercios=comercios, imagen=img[0][0] if img else None
        )
    finally:
        conn.close()

import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.auth import get_current_user
from app.database import get_connection
from app.schemas import UserOut

router = APIRouter(prefix="/superadmin/apps", tags=["apps"])

COLUMNS = ["id", "nombre", "plataforma", "dimensiones", "tamano_bytes", "imagen", "created_at"]
MAX_ICONOS = 60
MAX_CHARS_IMAGEN = 2_800_000  # ~2 MB una vez decodificada
IMAGEN_DATA_URL = re.compile(r"^data:image/(png|jpe?g|webp|svg\+xml|x-icon|vnd\.microsoft\.icon);base64,[A-Za-z0-9+/=]+$")


class IconoIn(BaseModel):
    nombre: str = Field(min_length=1, max_length=120)
    plataforma: str = Field(default="todas", pattern="^(todas|android|ios)$")
    dimensiones: str = Field(default="", max_length=20)
    imagen: str

    @field_validator("imagen")
    @classmethod
    def _imagen_valida(cls, valor: str) -> str:
        if len(valor) > MAX_CHARS_IMAGEN or not IMAGEN_DATA_URL.match(valor):
            raise ValueError("El icono debe ser PNG, JPG, WEBP, SVG o ICO y pesar máximo 2 MB")
        return valor


class IconoOut(BaseModel):
    id: int
    nombre: str
    plataforma: str
    dimensiones: str
    tamano_bytes: int
    imagen: str
    created_at: datetime


def _to_out(row) -> IconoOut:
    return IconoOut(**dict(zip(COLUMNS, row)))


@router.get("/iconos", response_model=list[IconoOut])
def listar(current_user: UserOut = Depends(get_current_user)):
    conn = get_connection()
    try:
        rows = conn.run(f"SELECT {', '.join(COLUMNS)} FROM app_iconos ORDER BY created_at DESC, id DESC")
        return [_to_out(r) for r in rows]
    finally:
        conn.close()


@router.post("/iconos", response_model=IconoOut, status_code=status.HTTP_201_CREATED)
def crear(payload: IconoIn, current_user: UserOut = Depends(get_current_user)):
    conn = get_connection()
    try:
        total = conn.run("SELECT COUNT(*) FROM app_iconos")[0][0]
        if total >= MAX_ICONOS:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Máximo {MAX_ICONOS} iconos. Elimina alguno para cargar otro.")
        # Tamaño real del archivo: 3 de cada 4 caracteres de un base64.
        base64 = payload.imagen.split(",", 1)[1]
        tamano = (len(base64) * 3) // 4 - base64.count("=")
        rows = conn.run(
            f"INSERT INTO app_iconos (nombre, plataforma, dimensiones, tamano_bytes, imagen) "
            f"VALUES (:n, :p, :d, :t, :i) RETURNING {', '.join(COLUMNS)}",
            n=payload.nombre.strip(), p=payload.plataforma, d=payload.dimensiones.strip(), t=tamano, i=payload.imagen,
        )
        return _to_out(rows[0])
    finally:
        conn.close()


@router.delete("/iconos/{icono_id}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar(icono_id: int, current_user: UserOut = Depends(get_current_user)):
    conn = get_connection()
    try:
        rows = conn.run("DELETE FROM app_iconos WHERE id = :id RETURNING id", id=icono_id)
        if not rows:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Icono no encontrado")
    finally:
        conn.close()

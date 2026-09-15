from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.database import get_connection
from app.schemas import LoginIn, TokenOut, UserOut
from app.security import create_access_token, decode_access_token, verify_password

router = APIRouter(prefix="/superadmin/auth", tags=["auth"])
bearer_scheme = HTTPBearer()

USER_COLUMNS = ["id", "username", "nombre", "email", "activo", "ultimo_login"]


def _row_to_user(row: dict) -> UserOut:
    return UserOut(**{col: row[col] for col in USER_COLUMNS})


@router.post("/login", response_model=TokenOut)
def login(payload: LoginIn):
    conn = get_connection()
    try:
        rows = conn.run(
            f"SELECT {', '.join(USER_COLUMNS)}, password_hash FROM usuarios WHERE email = :e",
            e=payload.email,
        )
        if not rows:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciales inválidas")

        row = dict(zip(USER_COLUMNS + ["password_hash"], rows[0]))

        if not verify_password(payload.password, row["password_hash"]):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciales inválidas")

        if not row["activo"]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cuenta desactivada")

        updated = conn.run(
            f"UPDATE usuarios SET ultimo_login = now() WHERE id = :id RETURNING {', '.join(USER_COLUMNS)}",
            id=row["id"],
        )
        user = _row_to_user(dict(zip(USER_COLUMNS, updated[0])))
        token = create_access_token(user_id=user.id, email=user.email)
        return TokenOut(access_token=token, user=user)
    finally:
        conn.close()


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> UserOut:
    try:
        payload = decode_access_token(credentials.credentials)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido o expirado")

    conn = get_connection()
    try:
        rows = conn.run(
            f"SELECT {', '.join(USER_COLUMNS)} FROM usuarios WHERE id = :id",
            id=int(payload["sub"]),
        )
        if not rows:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario no encontrado")
        row = dict(zip(USER_COLUMNS, rows[0]))
        if not row["activo"]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cuenta desactivada")
        return _row_to_user(row)
    finally:
        conn.close()


@router.get("/me", response_model=UserOut)
def me(current_user: UserOut = Depends(get_current_user)):
    return current_user

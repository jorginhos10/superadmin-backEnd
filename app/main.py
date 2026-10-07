import os

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.ajustes import router as ajustes_router
from app.apariencias import router as apariencias_router
from app.apps import router as apps_router
from app.auth import router as auth_router
from app.chat_soporte import router as chat_soporte_router
from app.comercios import router as comercios_router
from app.dashboard import router as dashboard_router
from app.database import get_connection
from app.marketplace import router as marketplace_router
from app.pagos_nequi import router as pagos_nequi_router
from app.planes import router as planes_router
from app.regiones import router as regiones_router
from app.soporte import router as soporte_router

app = FastAPI(title="ChefControl SuperAdmin API")

origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(ajustes_router)
app.include_router(apariencias_router)
app.include_router(apps_router)
app.include_router(chat_soporte_router)
app.include_router(comercios_router)
app.include_router(dashboard_router)
app.include_router(planes_router)
app.include_router(regiones_router)
app.include_router(marketplace_router)
app.include_router(pagos_nequi_router)
app.include_router(soporte_router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/db-check")
def db_check():
    conn = get_connection()
    try:
        conn.run("SELECT 1")
        return {"database": "ok"}
    finally:
        conn.close()

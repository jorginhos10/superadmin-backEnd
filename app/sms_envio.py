"""Envío de un SMS por la API HTTP del proveedor, según la configuración guardada en sms_config.

Hay una copia de esta lógica en el backend de los comercios (app/sms.py). Si cambias una, cambia la otra.
"""

import json
import re

import httpx

PLACEHOLDERS = ("telefono", "mensaje", "remitente", "usuario")
_PATRON = re.compile(r"\{(" + "|".join(PLACEHOLDERS) + r")\}")
TIMEOUT_S = 15.0


class EnvioFallido(Exception):
    pass


def valores_de(cfg: dict, destino: str, texto: str) -> dict:
    return {"telefono": destino, "mensaje": texto, "remitente": cfg["remitente"], "usuario": cfg["auth_usuario"]}


def _cuerpo_json(plantilla: str, valores: dict) -> str:
    # Cada valor se escapa como texto JSON, así un mensaje con comillas o saltos de línea no rompe el cuerpo.
    return _PATRON.sub(lambda m: json.dumps(valores[m.group(1)], ensure_ascii=False)[1:-1], plantilla)


def _campos_form(plantilla: str, valores: dict) -> dict:
    campos = {}
    for linea in plantilla.splitlines():
        if "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        campos[clave.strip()] = _PATRON.sub(lambda m: valores[m.group(1)], valor.strip())
    return campos


def enviar(cfg: dict, destino: str, texto: str) -> str:
    """Hace la llamada al proveedor. Devuelve un resumen de su respuesta o lanza EnvioFallido."""
    headers: dict[str, str] = {}
    auth = None
    tipo = cfg["auth_tipo"]
    secreto = (cfg["auth_secreto"] or "").strip().strip("\"'").strip()
    if tipo != "ninguna" and not secreto:
        raise EnvioFallido("Falta la clave o el token de la API: pégalo en la tuerca de Mensajería y guarda")
    if tipo == "basic":
        auth = (cfg["auth_usuario"].strip(), secreto)
    elif tipo == "bearer":
        headers["Authorization"] = f"Bearer {secreto}"
    elif tipo == "header":
        headers[(cfg["auth_header_nombre"] or "X-API-Key").strip()] = secreto

    valores = valores_de(cfg, destino, texto)
    kwargs: dict = {}
    if cfg["content_type"] == "json":
        headers["Content-Type"] = "application/json"
        if cfg["metodo"] == "GET":
            kwargs["params"] = json.loads(_cuerpo_json(cfg["cuerpo_plantilla"], valores))
        else:
            kwargs["content"] = _cuerpo_json(cfg["cuerpo_plantilla"], valores).encode("utf-8")
    else:
        campos = _campos_form(cfg["cuerpo_plantilla"], valores)
        kwargs["params" if cfg["metodo"] == "GET" else "data"] = campos

    try:
        resp = httpx.request(cfg["metodo"], cfg["url"], headers=headers, auth=auth, timeout=TIMEOUT_S, **kwargs)
    except httpx.ReadTimeout:
        return "Sin confirmación del proveedor (tiempo agotado): el mensaje pudo haber llegado"
    except (httpx.HTTPError, ValueError) as exc:
        raise EnvioFallido(f"No se pudo conectar con el proveedor: {type(exc).__name__} {str(exc)[:120]}".strip()) from exc

    cuerpo = resp.text[:300].replace("\n", " ")
    if not 200 <= resp.status_code < 300:
        raise EnvioFallido(f"El proveedor respondió HTTP {resp.status_code}: {cuerpo}")
    if cfg["exito_contiene"] and cfg["exito_contiene"] not in resp.text:
        raise EnvioFallido(f"La respuesta no contiene «{cfg['exito_contiene']}»: {cuerpo}")
    return f"HTTP {resp.status_code}: {cuerpo}"

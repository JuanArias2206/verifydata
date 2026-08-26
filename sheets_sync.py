"""
sheets_sync.py — Sincronización con Google Sheets.

Permite sincronizar datos de VerifyData con Google Sheets para:
  - Exportar solicitudes de crédito a una hoja de cálculo
  - Importar datos de clientes desde Google Sheets
  - Mantener historial de aprobaciones sincronizado

Requiere variables de entorno:
  GOOGLE_SHEETS_ID: ID del documento de Google Sheets
  GOOGLE_SHEETS_CREDENTIALS: Ruta al archivo de credenciales JSON (service account)
"""
from __future__ import annotations
import os
import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("verifydata.sheets")

# ═══════════════════════════════════════════════════════════════════
#  Configuración
# ═══════════════════════════════════════════════════════════════════
SHEET_ID = os.environ.get("GOOGLE_SHEETS_ID", "")
CREDENTIALS_PATH = os.environ.get("GOOGLE_SHEETS_CREDENTIALS", "")


def is_configured() -> bool:
    """Verifica si Google Sheets está configurado."""
    return bool(SHEET_ID and CREDENTIALS_PATH)


def _get_credentials():
    """Obtiene las credenciales desde env var (JSON string) o archivo."""
    import tempfile
    
    # Si CREDENTIALS_PATH es un archivo que existe, usarlo
    if Path(CREDENTIALS_PATH).exists():
        return CREDENTIALS_PATH
    
    # Si no, asumir que es el JSON string directo
    if CREDENTIALS_PATH.startswith("{"):
        # Es un JSON string, escribirlo a un archivo temporal
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write(CREDENTIALS_PATH)
            return f.name
    
    return CREDENTIALS_PATH


# ═══════════════════════════════════════════════════════════════════
#  Estructura de hojas esperada
# ═══════════════════════════════════════════════════════════════════
SHEET_STRUCTURE = {
    "Solicitudes": {
        "columns": [
            "ID", "Cédula", "Nombre", "Tipo Solicitud", "Ejecutivo",
            "Estado", "Monto Solicitado", "Crédito Actual", "Cupo Inicial",
            "Promedio Compras", "Calificación", "Score", "Nivel Riesgo",
            "Aprobado Por", "Fecha Aprobación", "Observaciones", "Fecha Creación"
        ],
        "description": "Historial completo de solicitudes de crédito"
    },
    "Aprobaciones": {
        "columns": [
            "ID Solicitud", "Acción", "Ejecutivo", "Motivo", "Fecha"
        ],
        "description": "Historial de aprobaciones y rechazos"
    },
    "Clientes": {
        "columns": [
            "Cédula", "Nombre", "Tipo Solicitud", "Ejecutivo",
            "Crédito Actual", "Monto Solicitado", "Cupo Inicial",
            "Promedio Compras", "Calificación", "Estado", "Observaciones"
        ],
        "description": "Datos maestros de clientes"
    }
}


# ═══════════════════════════════════════════════════════════════════
#  Funciones de sincronización
# ═══════════════════════════════════════════════════════════════════
def get_sheets_service():
    """Obtiene el servicio de Google Sheets API."""
    if not is_configured():
        raise RuntimeError("Google Sheets no está configurado. "
                         "Define GOOGLE_SHEETS_ID y GOOGLE_SHEETS_CREDENTIALS")

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError:
        raise RuntimeError("Instala las dependencias: pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib")

    scopes = ['https://www.googleapis.com/auth/spreadsheets']
    creds_path = _get_credentials()
    credentials = service_account.Credentials.from_service_account_file(
        creds_path, scopes=scopes)
    return build('sheets', 'v4', credentials=credentials)


def export_credit_requests(solicitudes: list[dict]) -> dict:
    """Exporta solicitudes de crédito a la hoja 'Solicitudes'."""
    if not is_configured():
        return {"ok": False, "error": "Google Sheets no configurado"}

    try:
        service = get_sheets_service()

        # Preparar datos
        rows = []
        for s in solicitudes:
            rows.append([
                str(s.get("id", "")),
                str(s.get("cedula", "")),
                str(s.get("nombre", "")),
                str(s.get("tipo_solicitud", "")),
                str(s.get("ejecutivo", "")),
                str(s.get("estado", "")),
                str(s.get("monto_solicitado", 0)),
                str(s.get("credito_actual", 0)),
                str(s.get("cupo_inicial", 0)),
                str(s.get("promedio_compras", 0)),
                str(s.get("calificacion", 0)),
                str(s.get("score", 0)),
                str(s.get("nivel_riesgo", "")),
                str(s.get("aprobado_por", "")),
                str(s.get("fecha_aprobacion", "")),
                str(s.get("observaciones", "")),
                str(s.get("created_at", "")),
            ])

        # Agregar headers si la hoja está vacía
        sheet_name = "Hoja 1"  # Usar la hoja existente
        headers = ["ID", "Cédula", "Nombre", "Tipo", "Ejecutivo", "Estado",
                   "Monto Solicitado", "Crédito Actual", "Cupo Inicial",
                   "Promedio Compras", "Calificación", "Score", "Nivel Riesgo",
                   "Aprobado Por", "Fecha Aprobación", "Observaciones", "Fecha Creación"]

        # Verificar si la hoja tiene datos
        try:
            existing = service.spreadsheets().values().get(
                spreadsheetId=SHEET_ID,
                range=f'{sheet_name}!A1:A1'
            ).execute()
            if not existing.get("values"):
                # Hoja vacía, agregar headers
                rows.insert(0, headers)
        except Exception:
            rows.insert(0, headers)

        # Escribir datos
        service.spreadsheets().values().update(
            spreadsheetId=SHEET_ID,
            range=f'{sheet_name}!A1',
            valueInputOption="RAW",
            body={"values": rows}
        ).execute()

        log.info("Exportadas %d solicitudes a Google Sheets", len(solicitudes))
        return {"ok": True, "exported": len(solicitudes)}

    except Exception as e:
        log.error("Error exportando a Google Sheets: %s", e)
        return {"ok": False, "error": str(e)}


def export_approval_history(history: list[dict]) -> dict:
    """Exporta historial de aprobaciones a la hoja 'Aprobaciones'."""
    if not is_configured():
        return {"ok": False, "error": "Google Sheets no configurado"}

    try:
        service = get_sheets_service()

        rows = []
        for h in history:
            rows.append([
                str(h.get("request_id", "")),
                str(h.get("accion", "")),
                str(h.get("ejecutivo", "")),
                str(h.get("motivo", "")),
                str(h.get("created_at", "")),
            ])

        # Usar la misma hoja "Hoja 1" pero en columnas diferentes
        sheet_name = "Hoja 1"
        headers = ["ID Solicitud", "Acción", "Ejecutivo", "Motivo", "Fecha"]

        # Verificar si hay datos en las columnas Q:U (historial)
        try:
            existing = service.spreadsheets().values().get(
                spreadsheetId=SHEET_ID,
                range=f'{sheet_name}!Q1:Q1'
            ).execute()
            if not existing.get("values"):
                # Agregar headers en columnas Q-U
                service.spreadsheets().values().update(
                    spreadsheetId=SHEET_ID,
                    range=f'{sheet_name}!Q1',
                    valueInputOption="RAW",
                    body={"values": [headers]}
                ).execute()
        except Exception:
            pass

        # Escribir datos en columnas Q-U
        if rows:
            service.spreadsheets().values().update(
                spreadsheetId=SHEET_ID,
                range=f'{sheet_name}!Q2',
                valueInputOption="RAW",
                body={"values": rows}
            ).execute()

        log.info("Exportadas %d aprobaciones a Google Sheets", len(rows))
        return {"ok": True, "exported": len(rows)}

    except Exception as e:
        log.error("Error exportando historial: %s", e)
        return {"ok": False, "error": str(e)}


def import_clients() -> dict:
    """Importa clientes desde la hoja 'Clientes' de Google Sheets."""
    if not is_configured():
        return {"ok": False, "error": "Google Sheets no configurado"}

    try:
        service = get_sheets_service()

        result = service.spreadsheets().values().get(
            spreadsheetId=SHEET_ID,
            range="Clientes!A:K"
        ).execute()

        values = result.get("values", [])
        if not values:
            return {"ok": True, "clients": [], "message": "Hoja vacía"}

        # Primera fila son headers
        headers = values[0]
        clients = []
        for row in values[1:]:
            client = {}
            for i, h in enumerate(headers):
                if i < len(row):
                    client[h.lower().replace(" ", "_")] = row[i]
                else:
                    client[h.lower().replace(" ", "_")] = ""
            clients.append(client)

        log.info("Importados %d clientes desde Google Sheets", len(clients))
        return {"ok": True, "clients": clients, "count": len(clients)}

    except Exception as e:
        log.error("Error importando desde Google Sheets: %s", e)
        return {"ok": False, "error": str(e)}


def sync_all(solicitudes: list[dict], history: list[dict]) -> dict:
    """Sincroniza todo con Google Sheets."""
    results = {}

    # Exportar solicitudes
    r1 = export_credit_requests(solicitudes)
    results["solicitudes"] = r1

    # Exportar historial
    r2 = export_approval_history(history)
    results["historial"] = r2

    return {
        "ok": r1.get("ok") and r2.get("ok"),
        "results": results
    }


# ═══════════════════════════════════════════════════════════════════
#  Endpoints API
# ═══════════════════════════════════════════════════════════════════
def register_sheets_routes(app):
    """Registra las rutas de sincronización con Google Sheets."""

    @app.route("/api/sheets/sync", methods=["POST"])
    def api_sheets_sync():
        """Sincroniza datos con Google Sheets."""
        from flask import jsonify
        from db import credit_request_get_all, approval_history_get

        if not is_configured():
            return jsonify({"ok": False, "error": "Google Sheets no configurado. "
                          "Define GOOGLE_SHEETS_ID y GOOGLE_SHEETS_CREDENTIALS"})

        solicitudes = credit_request_get_all()
        # Obtener historial de todas las solicitudes
        all_history = []
        for s in solicitudes:
            h = approval_history_get(s.get("id", 0))
            all_history.extend(h)

        result = sync_all(solicitudes, all_history)
        return jsonify(result)

    @app.route("/api/sheets/import")
    def api_sheets_import():
        """Importa clientes desde Google Sheets."""
        from flask import jsonify
        result = import_clients()
        return jsonify(result)

    @app.route("/api/sheets/status")
    def api_sheets_status():
        """Estado de la conexión con Google Sheets."""
        from flask import jsonify
        return jsonify({
            "configured": is_configured(),
            "sheet_id": SHEET_ID[:10] + "..." if SHEET_ID else "",
            "has_credentials": bool(CREDENTIALS_PATH and Path(CREDENTIALS_PATH).exists())
        })

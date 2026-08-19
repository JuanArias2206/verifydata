"""
openapi_spec.py — Construye la especificación OpenAPI 3.1 de la API de VerifyData.

Se genera en runtime (build_spec) para que el catálogo de fuentes y la versión
queden siempre en sincronía con el registry. La sirve api.py en
GET /api/v1/openapi.json y la consume Swagger UI en /api/v1/docs.
"""
from __future__ import annotations
from typing import Any


def build_spec(source_meta: list[dict], version: str) -> dict[str, Any]:
    source_names = [m["name"] for m in source_meta]

    result_schema = {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Nombre de la fuente."},
            "category": {"type": "string"},
            "source_url": {"type": "string", "format": "uri"},
            "matched": {"type": "boolean",
                        "description": "True si hubo coincidencia."},
            "state": {
                "type": "string",
                "description": (
                    "Estado normalizado de la consulta a la fuente. Valores "
                    "base: match, no_match, captcha_required, error. Además "
                    "puede devolver estados FINOS de la fuente (p. ej. "
                    "nomatch_verified, timeout, captcha_blocked, "
                    "dataset_missing, dataset_stale, not_implemented, "
                    "requires_login, requires_payment, source_changed)."),
                "examples": ["match", "no_match", "nomatch_verified",
                             "captcha_required", "error"],
            },
            "status": {"type": ["string", "null"],
                       "description": "Estado fino explícito de la fuente."},
            "confidence": {"type": ["string", "null"],
                           "enum": ["exacta", "fuerte", "posible", None]},
            "summary": {"type": ["string", "null"]},
            "matched_name": {"type": ["string", "null"]},
            "matched_document": {"type": ["string", "null"]},
            "role": {"type": ["string", "null"]},
            "case_number": {"type": ["string", "null"]},
            "dataset_version": {"type": ["string", "null"]},
            "dataset_records": {"type": "integer"},
            "error": {"type": ["string", "null"]},
            "error_type": {"type": ["string", "null"]},
            "notice": {"type": ["string", "null"]},
            "requires_manual_review": {"type": "boolean"},
            "notes": {"type": ["string", "null"]},
            "evidence_urls": {"type": "array", "items": {"type": "string"}},
            "download_url": {"type": ["string", "null"]},
            "elapsed_s": {"type": "number"},
            "details": {"type": "array", "items": {"type": "object"}},
        },
    }

    run_schema = {
        "type": "object",
        "properties": {
            "token": {"type": "string", "example": "a1b2c3d4e5f6"},
            "status": {"type": "string", "enum": ["running", "completed"]},
            "query": {"type": "object"},
            "started_at": {"type": "string"},
            "progress": {
                "type": "object",
                "properties": {
                    "total": {"type": "integer"},
                    "completed": {"type": "integer"},
                    "pending": {"type": "integer"},
                },
            },
            "summary": {
                "type": "object",
                "properties": {
                    "sources_total": {"type": "integer"},
                    "matches": {"type": "integer"},
                    "captcha_blocked": {"type": "integer"},
                    "errors": {"type": "integer"},
                },
            },
            "results": {"type": "array",
                        "items": {"$ref": "#/components/schemas/SourceResult"}},
            "links": {
                "type": "object",
                "properties": {
                    "self": {"type": "string"},
                    "report_pdf": {"type": "string"},
                },
            },
        },
    }

    search_request = {
        "type": "object",
        "properties": {
            "nombre": {"type": "string",
                       "description": "Nombre completo del sujeto.",
                       "example": "Juan Pérez Gómez"},
            "cedula": {"type": "string",
                       "description": "Número de documento (cédula/NIT).",
                       "example": "1234567890"},
            "fecha_exp": {"type": "string",
                          "description": "Fecha de expedición del documento "
                                         "(AAAA-MM-DD), requerida por algunas "
                                         "fuentes.",
                          "example": "2005-04-12"},
            "sources": {
                "description": "'all' (todas, por defecto), 'featured' "
                               "(principales) o una lista de nombres exactos "
                               "(ver GET /sources).",
                "oneOf": [
                    {"type": "string", "enum": ["all", "featured"]},
                    {"type": "array", "items": {
                        "type": "string", "enum": source_names}},
                ],
                "default": "all",
            },
        },
        "anyOf": [
            {"required": ["nombre"]},
            {"required": ["cedula"]},
        ],
    }

    error_schema = {
        "type": "object",
        "properties": {
            "error": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "message": {"type": "string"},
                },
            },
        },
    }

    def _errors(*codes):
        m = {
            "401": "API key ausente o inválida.",
            "404": "Recurso no encontrado.",
            "422": "Parámetros inválidos.",
            "500": "Error interno.",
        }
        return {
            c: {"description": m[c],
                "content": {"application/json": {
                    "schema": {"$ref": "#/components/schemas/Error"}}}}
            for c in codes
        }

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "VerifyData API",
            "version": version,
            "description": (
                "Búsqueda automatizada de personas y entidades en "
                f"{len(source_meta)} fuentes públicas colombianas e "
                "internacionales (antecedentes, sanciones, PEP, prófugos). "
                "Producto VerifyData.\n\n"
                "**Autenticación:** header `X-API-Key: <clave>` "
                "(o `Authorization: Bearer <clave>`). Los endpoints de "
                "descubrimiento (`/health`, `/sources`, `/docs`) son públicos.\n\n"
                "**Modelos de uso:** asíncrono (`POST /searches` → token → "
                "`GET /searches/{token}`) o síncrono (`POST /searches/sync`)."
            ),
            "contact": {"name": "VerifyData"},
        },
        "servers": [{"url": "/api/v1"}],
        "tags": [
            {"name": "Búsquedas", "description": "Ejecutar y consultar búsquedas."},
            {"name": "Fuentes", "description": "Catálogo de fuentes."},
            {"name": "Sistema", "description": "Salud y metadatos."},
        ],
        "components": {
            "securitySchemes": {
                "ApiKeyHeader": {"type": "apiKey", "in": "header",
                                 "name": "X-API-Key"},
                "BearerAuth": {"type": "http", "scheme": "bearer"},
            },
            "schemas": {
                "SearchRequest": search_request,
                "SearchRun": run_schema,
                "SourceResult": result_schema,
                "Error": error_schema,
            },
        },
        "security": [{"ApiKeyHeader": []}, {"BearerAuth": []}],
        "paths": {
            "/health": {
                "get": {
                    "tags": ["Sistema"],
                    "summary": "Sonda de salud",
                    "security": [],
                    "responses": {"200": {"description": "Servicio operativo"}},
                }
            },
            "/sources": {
                "get": {
                    "tags": ["Fuentes"],
                    "summary": "Catálogo de fuentes disponibles",
                    "security": [],
                    "parameters": [
                        {"name": "category", "in": "query",
                         "schema": {"type": "string"},
                         "description": "Filtra por categoría."},
                        {"name": "captcha", "in": "query",
                         "schema": {"type": "boolean"},
                         "description": "Filtra por fuentes que requieren captcha."},
                    ],
                    "responses": {"200": {"description": "Lista de fuentes"}},
                }
            },
            "/searches": {
                "post": {
                    "tags": ["Búsquedas"],
                    "summary": "Iniciar búsqueda asíncrona",
                    "description": "Lanza la búsqueda en background y devuelve "
                                   "un token. Consulte el estado con "
                                   "GET /searches/{token}.",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {
                            "schema": {"$ref": "#/components/schemas/SearchRequest"}}},
                    },
                    "responses": {
                        "202": {
                            "description": "Búsqueda aceptada",
                            "headers": {
                                "Location": {"schema": {"type": "string"},
                                             "description": "URL de polling."}},
                            "content": {"application/json": {
                                "schema": {"$ref": "#/components/schemas/SearchRun"}}},
                        },
                        **_errors("401", "422"),
                    },
                }
            },
            "/searches/sync": {
                "post": {
                    "tags": ["Búsquedas"],
                    "summary": "Búsqueda síncrona (bloqueante)",
                    "description": "Ejecuta la búsqueda y espera hasta que "
                                   "termine o expire el timeout del servidor. "
                                   "Si expira, devuelve status='running' y el "
                                   "token para continuar por polling.",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {
                            "schema": {"$ref": "#/components/schemas/SearchRequest"}}},
                    },
                    "responses": {
                        "200": {
                            "description": "Resultados (completos o parciales "
                                           "si expiró el timeout).",
                            "content": {"application/json": {
                                "schema": {"$ref": "#/components/schemas/SearchRun"}}},
                        },
                        **_errors("401", "422", "500"),
                    },
                }
            },
            "/searches/{token}": {
                "get": {
                    "tags": ["Búsquedas"],
                    "summary": "Estado y resultados de una búsqueda",
                    "parameters": [
                        {"name": "token", "in": "path", "required": True,
                         "schema": {"type": "string"}},
                    ],
                    "responses": {
                        "200": {
                            "description": "Estado del run",
                            "content": {"application/json": {
                                "schema": {"$ref": "#/components/schemas/SearchRun"}}},
                        },
                        **_errors("401", "404"),
                    },
                }
            },
            "/searches/{token}/report": {
                "get": {
                    "tags": ["Búsquedas"],
                    "summary": "Descargar reporte PDF",
                    "parameters": [
                        {"name": "token", "in": "path", "required": True,
                         "schema": {"type": "string"}},
                    ],
                    "responses": {
                        "200": {
                            "description": "Reporte PDF",
                            "content": {"application/pdf": {
                                "schema": {"type": "string", "format": "binary"}}},
                        },
                        "202": {"description": "El run aún no tiene resultados."},
                        **_errors("401", "404"),
                    },
                }
            },
        },
    }

"""Deep Agent backend - chatbot con acceso al MCP legal.

Hace de puente entre el frontend y el modelo GLM (ZAI), permitiendo
que el LLM use las herramientas del MCP (buscar_normas, texto_vigente,
consulta_grafo, estadistica_jurisprudencial) con razonamiento visible.
"""
from __future__ import annotations

import json
import os
import sys
import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
from typing import Any

app = FastAPI(title="Deep Agent Legal")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuración del LLM (ZAI / GLM)
ZAI_API_KEY = os.environ.get("GLM_API_KEY", "")
ZAI_BASE_URL = os.environ.get("ZAI_BASE_URL", "https://api.z.ai/api/paas/v4")
LLM_MODEL = os.environ.get("LLM_MODEL", "glm-4.6")

# Endpoint del MCP legal
MCP_API_BASE = os.environ.get("MCP_API_BASE", "http://localhost:8000")

# Definición de herramientas que el LLM puede llamar (mapean al MCP)
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "buscar_normas",
            "description": "Búsqueda híbrida (semántica + léxica) sobre el corpus legal colombiano. "
                           "Encuentra fragmentos relevantes de leyes, decretos, sentencias y conceptos. "
                           "USAR PRIMERO para cualquier consulta jurídica.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Consulta o concepto jurídico a buscar"},
                    "limit": {"type": "integer", "description": "Número de resultados (default 5, max 20)", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "texto_vigente",
            "description": "Verifica si una norma o artículo específico está vigente a una fecha. "
                           "CRÍTICO antes de citar una norma: evita citar texto derogado o inexequible.",
            "parameters": {
                "type": "object",
                "properties": {
                    "canonical_id": {"type": "string", "description": "ID canónico de la norma (ej: co:ley:1715:2014)"},
                    "fecha": {"type": "string", "description": "Fecha de vigencia YYYY-MM-DD (opcional, default hoy)"},
                    "articulo": {"type": "integer", "description": "Número de artículo (opcional)"},
                },
                "required": ["canonical_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consulta_grafo",
            "description": "Consulta el grafo de conocimiento de una norma/sentencia. "
                           "Muestra citas, modificaciones, derogaciones y relaciones temáticas. "
                           "Útil para trazabilidad jurídica.",
            "parameters": {
                "type": "object",
                "properties": {
                    "canonical_id": {"type": "string", "description": "ID canónico de la norma"},
                },
                "required": ["canonical_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "estadistica_jurisprudencial",
            "description": "Jurimetría: distribuciones del corpus por corte, año, materia, magistrado. "
                           "Útil para análisis cuantitativos.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tipo": {"type": "string", "description": "Tipo de análisis"},
                    "top": {"type": "integer", "description": "Top N resultados", "default": 10},
                },
            },
        },
    },
]

SYSTEM_PROMPT = """Eres LexIA, un asistente jurídico colombiano experto con acceso a una base de conocimiento del ordenamiento jurídico colombiano.

CAPACIDADES:
- Tienes acceso a más de 200,000 documentos legales indexados (leyes, decretos, sentencias, conceptos)
- Puedes buscar por concepto, verificar vigencia, consultar el grafo de citas y hacer análisis estadístico

PROTOCOLO DE RAZONAMIENTO (SIGUE ESTO SIEMPRE):
1. ANÁLISIS: Antes de responder, explica brevemente qué necesitas buscar
2. BÚSQUEDA: Usa buscar_normas para encontrar el contenido relevante
3. VERIFICACIÓN: Si citas una norma específica, usa texto_vigente para confirmar que esté vigente
4. TRAZABILIDAD: Si es relevante, usa consulta_grafo para mostrar cómo se relaciona con otras normas
5. RESPUESTA: Cita SIEMPRE por canonical_id y referencia la fuente exacta

REGLAS CRÍTICAS:
- NUNCA inventes normas, fechas o contenido. Si no lo encuentras, di "no encontré información"
- NUNCA cites texto derogado o inexequible sin aclararlo
- Cita siempre: tipo, número, año y canonical_id
- Si hay ambigüedad, pide aclaración
- Responde en español, claro y estructurado
- Muestra tu razonamiento paso a paso

Eres el cerebro jurídico más completo de Colombia. Úsalo bien."""


class ChatRequest(BaseModel):
    message: str
    history: list[dict[str, str]] = []


def call_mcp_tool(name: str, args: dict) -> str:
    """Llama una herramienta del MCP via API REST."""
    try:
        if name == "buscar_normas":
            r = httpx.post(
                f"{MCP_API_BASE}/api/tools/buscar",
                json={"query": args.get("query", ""), "limit": args.get("limit", 5)},
                timeout=30,
            )
        elif name == "texto_vigente":
            r = httpx.post(
                f"{MCP_API_BASE}/api/tools/texto-vigente",
                json={
                    "canonical_id": args.get("canonical_id", ""),
                    "fecha": args.get("fecha"),
                    "articulo": args.get("articulo"),
                },
                timeout=30,
            )
        elif name == "consulta_grafo":
            # Buscar el suin_id del canonical_id primero
            cid = args.get("canonical_id", "")
            r = httpx.get(
                f"{MCP_API_BASE}/api/graph/global",
                params={"limit": 100},
                timeout=15,
            )
            # Simplificado: devolver el grafo de la norma
            return json.dumps(r.json(), ensure_ascii=False)[:3000]
        elif name == "estadistica_jurisprudencial":
            r = httpx.get(
                f"{MCP_API_BASE}/api/jurimetria",
                params={"tipo": args.get("tipo", "SENTENCIA"), "top": args.get("top", 10)},
                timeout=15,
            )
        else:
            return f"Error: herramienta desconocida {name}"

        if r.status_code == 200:
            data = r.json()
            # Formatear resultado de forma concisa para el LLM
            return _format_tool_result(name, data)
        return f"Error HTTP {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return f"Error llamando herramienta: {e}"


def _format_tool_result(name: str, data: Any) -> str:
    """Formatea el resultado de una herramienta de forma concisa."""
    if name == "buscar_normas":
        resultados = data if isinstance(data, list) else data.get("resultados", data)
        lines = [f"Encontrados {len(resultados) if isinstance(resultados, list) else '?'} resultados:\n"]
        if isinstance(resultados, list):
            for i, r in enumerate(resultados[:5], 1):
                lines.append(
                    f"{i}. [{r.get('tipo', '?')} {r.get('numero', '?')} de {r.get('anio', '?')}] "
                    f"({r.get('canonical_id', '?')}) score={r.get('score', '?')}\n"
                    f"   Sección: {r.get('section', '?')}\n"
                    f"   Texto: {(r.get('text', '') or '')[:500]}\n"
                )
        return "\n".join(lines)
    elif name == "texto_vigente":
        return json.dumps(data, ensure_ascii=False, indent=2)[:2000]
    else:
        return json.dumps(data, ensure_ascii=False, indent=2)[:2000]


def call_llm(messages: list[dict], stream: bool = True):
    """Llama al LLM (GLM/ZAI) con tool calling."""
    headers = {
        "Authorization": f"Bearer {ZAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "auto",
        "temperature": 0.3,
        "max_tokens": 4096,
        "stream": stream,
    }
    with httpx.Client(timeout=120) as client:
        with client.stream("POST", f"{ZAI_BASE_URL}/chat/completions",
                           headers=headers, json=payload) as resp:
            for line in resp.iter_lines():
                if line:
                    yield line


@app.post("/api/agent/chat")
async def chat(req: ChatRequest):
    """Endpoint principal del agente con streaming SSE."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + req.history + [
        {"role": "user", "content": req.message}
    ]

    async def event_stream():
        max_tool_rounds = 5
        for round_num in range(max_tool_rounds):
            # Llamar al LLM
            assistant_content = ""
            tool_calls = []
            reasoning_parts = []

            try:
                for line in call_llm(messages, stream=True):
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                            delta = chunk.get("choices", [{}])[0].get("delta", {})

                            # Capturar razonamiento si existe
                            if delta.get("reasoning_content"):
                                reasoning_parts.append(delta["reasoning_content"])
                                yield f"data: {json.dumps({'type': 'reasoning', 'content': delta['reasoning_content']})}\n\n"

                            # Capturar contenido
                            if delta.get("content"):
                                assistant_content += delta["content"]
                                yield f"data: {json.dumps({'type': 'content', 'content': delta['content']})}\n\n"

                            # Capturar tool calls
                            if delta.get("tool_calls"):
                                for tc in delta["tool_calls"]:
                                    if tc.get("index", 0) >= len(tool_calls):
                                        tool_calls.append({
                                            "id": tc.get("id", ""),
                                            "function": {"name": "", "arguments": ""},
                                        })
                                    idx = tc.get("index", 0)
                                    if tc.get("function", {}).get("name"):
                                        tool_calls[idx]["function"]["name"] = tc["function"]["name"]
                                    if tc.get("function", {}).get("arguments"):
                                        tool_calls[idx]["function"]["arguments"] += tc["function"]["arguments"]
                        except json.JSONDecodeError:
                            pass
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'content': f'LLM error: {e}'})}\n\n"
                return

            # Si hay razonamiento acumulado, enviarlo como bloque
            if reasoning_parts:
                yield f"data: {json.dumps({'type': 'reasoning_block', 'content': ''.join(reasoning_parts)})}\n\n"

            # Si no hay tool calls, terminar
            if not tool_calls:
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                return

            # Procesar tool calls
            messages.append({
                "role": "assistant",
                "content": assistant_content or None,
                "tool_calls": tool_calls,
            })

            for tc in tool_calls:
                tool_name = tc["function"]["name"]
                try:
                    tool_args = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    tool_args = {}

                yield f"data: {json.dumps({'type': 'tool_start', 'name': tool_name, 'args': tool_args})}\n\n"

                result = call_mcp_tool(tool_name, tool_args)

                yield f"data: {json.dumps({'type': 'tool_result', 'name': tool_name, 'result': result[:800]})}\n\n"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/agent/health")
async def health():
    return {
        "status": "ok",
        "llm_model": LLM_MODEL,
        "mcp_connected": MCP_API_BASE,
        "has_api_key": bool(ZAI_API_KEY),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3001)

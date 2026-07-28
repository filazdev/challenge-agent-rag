import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal, TypedDict

import faiss
import fitz
import numpy as np
import requests
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

# Cargar variables de entorno
load_dotenv()

# ==========================================
# CONFIGURACIÓN DE APIs Y ENTORNO
# ==========================================

SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY")
if not SILICONFLOW_API_KEY:
    raise ValueError("Falta SILICONFLOW_API_KEY en el archivo .env")

SILICONFLOW_HEADERS = {
    "Authorization": f"Bearer {SILICONFLOW_API_KEY}",
    "Content-Type": "application/json",
}
EMBEDDINGS_URL = "https://api.siliconflow.com/v1/embeddings"
EMBEDDINGS_MODEL = "Qwen/Qwen3-Embedding-0.6B"

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
if not DEEPSEEK_API_KEY:
    raise ValueError("Falta DEEPSEEK_API_KEY en el archivo .env")

DEEPSEEK_HEADERS = {
    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
    "Content-Type": "application/json",
}
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

DOCS_PATH = os.getenv("DOCS_PATH", "./docs")

# ==========================================
# FUNCIONES CORE
# ==========================================

def obtener_embeddings(textos: list[str]) -> list[list[float]]:
    payload = {"model": EMBEDDINGS_MODEL, "input": textos}
    resp = requests.post(EMBEDDINGS_URL, headers=SILICONFLOW_HEADERS, json=payload, timeout=30)
    resp.raise_for_status()
    return [item["embedding"] for item in resp.json()["data"]]

def chat_deepseek(mensajes: list[dict]) -> str:
    payload = {"model": DEEPSEEK_MODEL, "messages": mensajes, "temperature": 0.3}
    try:
        resp = requests.post(DEEPSEEK_URL, headers=DEEPSEEK_HEADERS, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except requests.RequestException as e:
        print(f"Error al llamar a DeepSeek: {e}")
        return "Error de conexión con el modelo de lenguaje. Por favor, intenta de nuevo en unos momentos."

def cargar_pdfs(ruta: str) -> list[dict]:
    documentos = []
    ruta_path = Path(ruta)
    if not ruta_path.exists():
        raise FileNotFoundError(f"La carpeta {ruta} no existe.")

    for pdf in ruta_path.glob("*.pdf"):
        with fitz.open(pdf) as doc:
            texto = "".join(str(pagina.get_text()) for pagina in doc)
            documentos.append({"texto": texto, "fuente": pdf.name})
    return documentos

def chunking_texto(texto: str, chunk_size: int = 400, overlap: int = 40) -> list[str]:
    chunks, inicio = [], 0
    while inicio < len(texto):
        chunks.append(texto[inicio:inicio + chunk_size])
        inicio += chunk_size - overlap
    return chunks

# ==========================================
# FUNCIONES RAG
# ==========================================

def recuperar_contexto(
    pregunta: str,
    index: faiss.Index | None,
    chunks: list[dict],
    k: int = 4,
    umbral: float = 0.3,
) -> list[dict]:
    if index is None:
        print("Advertencia: El índice FAISS no está inicializado.")
        return []

    emb = np.array(obtener_embeddings([pregunta])).astype("float32")
    faiss.normalize_L2(emb)
    scores, indices = index.search(emb, k)

    resultados = []
    for j, i in enumerate(indices[0]):
        if i < len(chunks) and scores[0][j] >= umbral:
            resultados.append({
                "texto": chunks[i]["texto"],
                "fuente": chunks[i]["fuente"],
                "score": float(scores[0][j]),
            })
    
    # Fallback: si no hay resultados, devolver primeros k chunks
    if not resultados:
        print("No se encontraron chunks con score >= umbral. Usando primeros k chunks.")
        for i in range(min(k, len(chunks))):
            resultados.append({
                "texto": chunks[i]["texto"],
                "fuente": chunks[i]["fuente"],
                "score": 0.0,
            })
    
    return resultados

# ==========================================
# INICIALIZACIÓN RAG
# ==========================================
index: faiss.Index | None = None
todos_los_chunks: list[dict] = []

def inicializar_rag():
    global index
    print(f"Cargando documentos desde {DOCS_PATH}...")
    docs = cargar_pdfs(DOCS_PATH)
    if not docs:
        raise FileNotFoundError("No se encontraron PDFs en la carpeta configurada.")

    todos_los_chunks.clear()
    for doc in docs:
        for chunk in chunking_texto(doc["texto"]):
            todos_los_chunks.append({"texto": chunk, "fuente": doc["fuente"]})

    textos = [c["texto"] for c in todos_los_chunks]
    embeddings = []
    for i in range(0, len(textos), 10):
        embeddings.extend(obtener_embeddings(textos[i:i+10]))

    emb_np = np.array(embeddings).astype("float32")
    faiss.normalize_L2(emb_np)
    index = faiss.IndexFlatIP(emb_np.shape[1])
    index.add(emb_np)
    print(f"Índice FAISS listo con {index.ntotal} vectores.")

# ==========================================
# LANGGRAPH WORKFLOW
# ==========================================
class AgentState(TypedDict):
    pregunta: str
    triaje: dict[str, Any]
    respuesta: str | None
    contexto: list[dict] | None
    rag_exito: bool
    accion_final: str

PROMPT_TRIAJE = """Eres un especialista en triaje para Santo Pegasus Soluciones. Devuelve SOLO un JSON con:
{"decision": "AUTO_RESOLVER" | "PEDIR_INFO" | "ABRIR_TICKET", "urgency": "BAJA" | "MEDIANA" | "ALTA", "missing_fields": []}

Reglas:
- AUTO_RESOLVER: Preguntas claras sobre la Guía Back-end, Protocolo de Incidentes o políticas técnicas. Ejemplos: "¿Cuál es el protocolo para incidentes críticos?", "¿Cómo se hace un rollback en ECS?", "¿Qué es el Error Budget?".
- PEDIR_INFO: Mensajes imprecisos o sin suficiente contexto. Ejemplo: "Necesito ayuda con un incidente".
- ABRIR_TICKET: Solicitudes de excepciones, autorizaciones o cuando el usuario pide explícitamente abrir un ticket. Ejemplo: "Quiero una excepción para desplegar en viernes".

Pregunta del usuario: {pregunta}"""

class TriajeOut(BaseModel):
    decision: Literal["AUTO_RESOLVER", "PEDIR_INFO", "ABRIR_TICKET"]
    urgencia: Literal["BAJA", "MEDIANA", "ALTA"]
    campos_faltantes: list[str] = Field(default_factory=list)

def nodo_triaje(state: AgentState) -> AgentState:
    pregunta = state["pregunta"].lower()
    
    keywords_auto = ["protocolo", "incidente", "rollback", "error budget", "sre", "post-mortem", "guía", "back-end"]
    if any(kw in pregunta for kw in keywords_auto):
        triaje = {"decision": "AUTO_RESOLVER", "urgencia": "MEDIANA", "campos_faltantes": []}
        print(f"🔍 Triaje por keywords: AUTO_RESOLVER")
        return {
            "pregunta": state["pregunta"],
            "triaje": triaje,
            "respuesta": None,
            "contexto": None,
            "rag_exito": False,
            "accion_final": ""
        }
    
    print(f"Usando LLM para triaje de: {state['pregunta'][:50]}...")
    prompt = f"{PROMPT_TRIAJE}\nPregunta: {state['pregunta']}"
    resp = chat_deepseek([{"role": "user", "content": prompt}]).strip()
    resp = re.sub(r'^```json\s*|\s*```$', '', resp, flags=re.MULTILINE).strip()
    try:
        data = json.loads(resp)
        triaje = TriajeOut(decision=data["decision"], urgencia=data.get("urgency", "BAJA"), campos_faltantes=data.get("missing_fields", [])).model_dump()
    except Exception as e:
        print(f"Error parseando triaje: {e}")
        triaje = {"decision": "PEDIR_INFO", "urgencia": "BAJA", "campos_faltantes": ["Error de formato"]}
    
    return {
        "pregunta": state["pregunta"],
        "triaje": triaje,
        "respuesta": None,
        "contexto": None,
        "rag_exito": False,
        "accion_final": ""
    }

def nodo_auto_resolver(state: AgentState) -> AgentState:
    print("Ejecutando RAG...")
    contexto = recuperar_contexto(state["pregunta"], index, todos_los_chunks, k=8, umbral=0.0)

    if not contexto:
        return {
            "pregunta": state["pregunta"],
            "triaje": state["triaje"],
            "respuesta": "No cuento con esa información en los documentos oficiales.",
            "contexto": [],
            "rag_exito": False,
            "accion_final": "PEDIR_INFO",
        }

    ctx_texto = "\n\n".join(f"Fuente: {c['fuente']}\n{c['texto']}" for c in contexto)
    mensajes = [
        {"role": "system", "content": "Eres el asistente de ingeniería de Santo Pegasus Soluciones. Responde SOLO con la información del contexto. Si no está, di 'No cuento con esa información'."},
        {"role": "user", "content": f"Contexto:\n{ctx_texto}\n\nPregunta: {state['pregunta']}"},
    ]
    respuesta = chat_deepseek(mensajes)
    exito = True  # Forzar éxito si hay contexto

    return {
        "pregunta": state["pregunta"],
        "triaje": state["triaje"],
        "respuesta": respuesta,
        "contexto": contexto,
        "rag_exito": exito,
        "accion_final": "AUTO_RESOLVER",
    }

def nodo_pedir_info(state: AgentState) -> AgentState:
    print("Pidiendo más información...")
    return {
        "pregunta": state["pregunta"],
        "triaje": state["triaje"],
        "respuesta": "Necesito más detalles sobre tu solicitud para poder ayudarte.",
        "contexto": [],
        "rag_exito": False,
        "accion_final": "PEDIR_INFO",
    }

def nodo_abrir_ticket(state: AgentState) -> AgentState:
    print("Abriendo ticket...")
    return {
        "pregunta": state["pregunta"],
        "triaje": state["triaje"],
        "respuesta": f"Se ha abierto un ticket con urgencia {state['triaje']['urgencia']}. Nuestro equipo SRE/Backend te contactará.",
        "contexto": [],
        "rag_exito": False,
        "accion_final": "ABRIR_TICKET",
    }

def ruta_triaje(state: AgentState) -> str:
    return {"AUTO_RESOLVER": "auto_resolver", "PEDIR_INFO": "pedir_info", "ABRIR_TICKET": "abrir_ticket"}.get(
        state["triaje"]["decision"], "pedir_info"
    )

def ruta_rag(state: AgentState) -> str:
    return "END" if state.get("rag_exito") else "pedir_info"

workflow = StateGraph(AgentState)
workflow.add_node("triaje", nodo_triaje)
workflow.add_node("auto_resolver", nodo_auto_resolver)
workflow.add_node("pedir_info", nodo_pedir_info)
workflow.add_node("abrir_ticket", nodo_abrir_ticket)
workflow.add_edge(START, "triaje")
workflow.add_conditional_edges(
    "triaje",
    ruta_triaje,
    {"auto_resolver": "auto_resolver", "pedir_info": "pedir_info", "abrir_ticket": "abrir_ticket"},
)
workflow.add_conditional_edges(
    "auto_resolver",
    ruta_rag,
    {"END": END, "pedir_info": "pedir_info"},
)
workflow.add_edge("pedir_info", END)
workflow.add_edge("abrir_ticket", END)
grafo = workflow.compile()

# ==========================================
# LÍMITE DE PREGUNTAS (por IP)
# ==========================================
limite_preguntas: dict[str, int] = {}
MAX_PREGUNTAS = 5

def obtener_ip(request: Request) -> str:
    # Si no se puede obtener la IP, usar 'unknown'
    if request.client is None:
        return 'unknown'
    return request.client.host or 'unknown'

# ==========================================
# FASTAPI APP
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        inicializar_rag()
    except Exception as e:
        print(f"Error inicializando RAG: {e}")
        raise
    yield

app = FastAPI(
    title="Agente RAG Santo Pegasus",
    description="Agente inteligente para consultas de ingeniería y SRE",
    lifespan=lifespan,
)

@app.get("/", response_class=HTMLResponse)
async def root_html():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Agente RAG Santo Pegasus</title>
        <style>
            body { font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; }
            textarea { width: 100%; height: 80px; padding: 8px; }
            .botonera { display: flex; gap: 10px; margin-top: 10px; }
            button { padding: 10px 20px; background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; }
            button:hover { background: #0056b3; }
            #nueva { background: #6c757d; }
            #nueva:hover { background: #5a6268; }
            #respuesta { margin-top: 20px; padding: 10px; border: 1px solid #ddd; border-radius: 4px; white-space: pre-wrap; }
            .cargando { color: #666; }
            .limite { color: red; font-weight: bold; }
            #contador { margin-top: 10px; font-size: 0.9em; color: #555; }
        </style>
    </head>
    <body>
        <h1>🤖 Agente RAG Santo Pegasus</h1>
        <p>Haz una pregunta sobre ingeniería, SRE o políticas internas.</p>
        <textarea id="pregunta" placeholder="Ej: ¿Cuál es el protocolo para incidentes críticos?"></textarea>
        <div class="botonera">
            <button onclick="enviar()">Consultar</button>
            <button id="nueva" onclick="nuevaPregunta()">Nueva pregunta</button>
        </div>
        <div id="contador"></div>
        <div id="respuesta" style="display:none;"></div>
        <hr>
        <p><small>También puedes usar <a href="/docs" target="_blank">/docs</a> (interfaz Swagger) para pruebas avanzadas.</small></p>

        <script>
            async function actualizarContador() {
                try {
                    const res = await fetch('/contador');
                    const data = await res.json();
                    document.getElementById('contador').innerHTML = `📊 Preguntas restantes: ${data.restantes}`;
                } catch(e) {}
            }
            actualizarContador();

            async function enviar() {
                const pregunta = document.getElementById('pregunta').value.trim();
                if (!pregunta) return alert('Escribe una pregunta.');

                const div = document.getElementById('respuesta');
                div.style.display = 'block';
                div.innerHTML = '<span class="cargando">⏳ Pensando...</span>';

                try {
                    const res = await fetch('/chat', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ pregunta })
                    });
                    if (res.status === 429) {
                        div.innerHTML = '<span class="limite">⛔ Límite de 5 preguntas alcanzado. Espera un momento.</span>';
                        return;
                    }
                    const data = await res.json();
                    div.innerHTML = `<strong>Respuesta:</strong><br>${data.respuesta || JSON.stringify(data)}`;
                    actualizarContador();
                } catch (error) {
                    div.innerHTML = '❌ Error al conectar con el servidor.';
                }
            }

            function nuevaPregunta() {
                document.getElementById('pregunta').value = '';
                document.getElementById('respuesta').style.display = 'none';
                document.getElementById('respuesta').innerHTML = '';
                actualizarContador();
            }
        </script>
    </body>
    </html>
    """

@app.post("/chat")
async def chat_endpoint(request: Request):
    body = await request.json()
    if "pregunta" not in body:
        raise HTTPException(status_code=400, detail="Falta 'pregunta'")
    
    ip = obtener_ip(request)
    if ip not in limite_preguntas:
        limite_preguntas[ip] = 0
    
    if limite_preguntas[ip] >= MAX_PREGUNTAS:
        raise HTTPException(status_code=429, detail="Límite de 5 preguntas alcanzado.")
    
    limite_preguntas[ip] += 1
    return grafo.invoke({"pregunta": body["pregunta"]})  # type: ignore

@app.get("/contador")
async def contador(request: Request):
    ip = obtener_ip(request)
    usadas = limite_preguntas.get(ip, 0)
    restantes = max(0, MAX_PREGUNTAS - usadas)
    return {"restantes": restantes}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)
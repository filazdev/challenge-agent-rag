# 🤖 Agente RAG Santo Pegasus

Agente de inteligencia artificial que responde preguntas sobre ingeniería back-end y SRE (Site Reliability Engineering) utilizando la arquitectura RAG (Retrieval-Augmented Generation).

El agente lee documentos PDF (Guía de Back-end y Protocolo de Incidentes) y genera respuestas basadas exclusivamente en su contenido.

---

## 📌 Alcance del agente

Este agente solo responde preguntas sobre:

- Ingeniería back-end (Java, Spring Boot, arquitectura, principios SOLID, pruebas, CI/CD)
- SRE y gestión de incidentes (protocolos, SEV-1, rollback, Error Budget, Post-Mortems)
- Políticas técnicas de Santo Pegasus Soluciones

No responde sobre: front-end, recursos humanos, beneficios, onboarding, o temas no técnicos. Si se le pregunta sobre estos temas, el agente responderá "Necesito más detalles sobre tu solicitud" o "No cuento con esa información".

---

## 📐 Arquitectura de la solución

Usuario (Interfaz HTML)
        │
        ▼
   FastAPI (API REST)
        │
        ▼
  LangGraph (Workflow)
        │
        ├── Triaje (keywords o LLM)
        │       ├── AUTO_RESOLVER → RAG
        │       ├── PEDIR_INFO → respuesta genérica
        │       └── ABRIR_TICKET → mensaje de ticket
        │
        ▼
   RAG Pipeline
        │
        ├── Carga de PDFs (PyMuPDF)
        ├── Chunking (400 caracteres, overlap 40)
        ├── Embeddings (SiliconFlow / Qwen3-Embedding-0.6B)
        ├── Índice FAISS (búsqueda de similitud)
        └── Generación de respuesta (DeepSeek Chat)

---

## 🛠️ Tecnologías y herramientas

| Categoría | Tecnología |
|-----------|------------|
| Lenguaje | Python 3.14+ |
| Framework API | FastAPI + Uvicorn |
| Orquestación | LangGraph (flujo de trabajo) |
| Embeddings | SiliconFlow API (Qwen3-Embedding-0.6B) |
| LLM | DeepSeek Chat (API oficial) |
| Búsqueda vectorial | FAISS (CPU) |
| Extracción de PDF | PyMuPDF (fitz) |
| Frontend | HTML + CSS + JavaScript |
| Gestión de entorno | python-dotenv |

---

## 🚀 Instrucciones para ejecutar localmente

### 1. Clonar el repositorio
git clone <URL_DEL_REPO>
cd challenge_agente_rag

### 2. Crear y activar entorno virtual (con uv)
uv venv
source .venv/bin/activate   # Linux/Mac
# .venv\Scripts\activate    # Windows

### 3. Instalar dependencias
uv pip install -r requirements.txt

### 4. Configurar variables de entorno
Crea un archivo .env en la raíz del proyecto:
SILICONFLOW_API_KEY=tu_api_key_de_siliconflow
DEEPSEEK_API_KEY=tu_api_key_de_deepseek
DOCS_PATH=./docs

### 5. Colocar los documentos PDF
Crea una carpeta docs/ y coloca dentro los PDFs (ej. Guia_Backend.pdf, Protocolo_Incidentes.pdf).

### 6. Ejecutar la aplicación
python main.py

La aplicación estará disponible en http://localhost:8080

---

## 📝 Preguntas de ejemplo (funcionan con los PDFs actuales)

| Pregunta | Respuesta esperada |
|----------|---------------------|
| ¿Cuál es el protocolo para incidentes críticos? | Checklist SEV-1 con pasos: reconocer alerta, declarar, asignar roles, abrir War Room, rollback, etc. |
| ¿Qué es el Error Budget? | Definición: tiempo máximo de indisponibilidad permitido. Ejemplo: 43 minutos/mes para 99.9% SLO. |
| ¿Cómo se hace un rollback en AWS ECS? | Comando aws ecs update-service con Task Definition anterior. |
| ¿Qué principios SOLID se aplican en Santo Pegasus? | SRP, OCP, LSP, ISP, DIP. |
| ¿Cómo se gestionan las credenciales? | Uso de Vault o AWS Secrets Manager, variables de entorno, rotación periódica. |
| ¿Qué es un Post-Mortem? | Documento blameless con timeline, 5 Whys, acciones correctivas. Plazos según severidad. |
| ¿Qué estrategias de deploy se recomiendan? | Blue-Green o Canary Releases. |
| ¿Qué cobertura de pruebas se exige? | Mínimo 80% en pruebas unitarias. |

---

## 💬 Ejemplos de respuestas generadas

Pregunta: ¿Cuál es el protocolo para incidentes críticos?

Respuesta:
El protocolo para incidentes críticos (SEV-1) incluye un checklist rápido que debe seguirse:
1. Reconocer alerta en PagerDuty (< 5 min)
2. Confirmar el incidente en Datadog/Prometheus
3. Declarar el incidente en #incidents
4. IC asigna roles (TL, CL, SMEs)
5. Abrir #sev1-war-room en Slack
6. Abrir documento de timeline
7. CL publica primera actualización en #incidents
8. TL inicia diagnóstico (último deploy, RDS, ECS)
9. IC aprueba acción de mitigación
10. TL ejecuta mitigación / rollback
11. Monitorear métricas por 10 minutos
12. IC declara resolución
13. CL publica comunicado de resolución
14. IC abre ticket de Post-Mortem (< 48h)

Pregunta: ¿Qué es el Error Budget?

Respuesta:
El Error Budget (presupuesto de errores) es la cantidad máxima de indisponibilidad o degradación que Santo Pegasus puede "gastar" en un período determinado (normalmente mensual) sin violar el SLO establecido. Es el mecanismo que define el equilibrio entre la velocidad de entrega de nuevas funcionalidades y la necesidad de mantener la estabilidad del sistema.

---

## ☁️ Despliegue

### Render (recomendado)
1. Crear cuenta en render.com
2. Conectar repositorio de GitHub
3. Crear Web Service con:
   - Build Command: pip install -r requirements.txt
   - Start Command: uvicorn main:app --host 0.0.0.0 --port 10000
4. Añadir variables de entorno en el panel de Render

### Alternativas
- fly.io: fly launch y fly deploy
- Railway: similar a Render

---

## 📁 Estructura del proyecto

challenge_agente_rag/
├── docs/               # PDFs fuente
├── main.py             # Código principal
├── requirements.txt    # Dependencias
├── .env                # Variables (no subir)
├── .gitignore          # Ignorar .env, __pycache__, .venv
└── README.md           # Este archivo

---

## ⚙️ Requisitos del sistema

- Python 3.12+
- Conexión a Internet (APIs externas)
- PDFs en carpeta docs/

---

**Proyecto desarrollado para el Challenge Alura**
# BharatGen - NCERT Question Generator

BharatGen is an interactive study tool that generates NCERT/CBSE-aligned questions across Math, Physics, Chemistry, and Biology using multiple LLM backends and an optional RAG pipeline built on NCERT PDFs.

## Features

- Modern study UI with Tailwind CSS
- FastAPI backend with multiple LLM support
- RAG pipeline for context-aware question generation
- Support for cloud models (Gemini, GPT-4o) and local models (Llama, Qwen, Granite, Param)
- Configurable question types and cognitive depth levels

## Project Structure

```
bharatgen-ibm-yojaka-llm-board/
├── backend/
│   ├── Dockerfile          # Backend container definition
│   ├── app.py              # FastAPI app entry point (debug mode)
│   ├── main.py             # FastAPI routes and LLM integration
│   ├── requirements.txt    # Python dependencies
│   └── ncert_rag_pipe/     # RAG pipeline module
│       ├── main.py         # RAG retriever
│       └── ingest.py       # PDF ingestion script
├── frontend/
│   └── index.html          # Single-page UI
├── docker-compose.yml      # Multi-container orchestration
├── .env                    # Environment variables (gitignored)
├── data/                   # NCERT PDFs (gitignored)
├── indexes/                # Vector indexes (gitignored)
│   ├── vector_db.index
│   └── chunks_metadata.pkl
└── vector_store/           # Vector store (gitignored)
```

## Prerequisites

- Python 3.9+
- **Redis** (required for Celery task queue)
- (Optional) CUDA-capable GPU for local models
- API keys for cloud models (Gemini, OpenAI)
- Ollama installed if using local Ollama models

## Installation

```bash
cd bharatgen-ibm-yojaka-llmquestion-board
pip install -r backend/requirements.txt
```

## Configuration

Create a `.env` file in the project root:

```bash
GEMINI_API_KEY_21=your_gemini_key_here
OPENAI_API_KEY=your_openai_key_here

# Path to frontend (relative to backend/)
FRONTEND_RELATIVE_PATH=../frontend/index.html

# Path to Param-1-7B-MoE model (absolute or relative path)
PARAM1_7B_MOE_PATH=/home/jashwanth/Param-1-7B-MoE

# Optional: Param-1-2.9B-Instruct (HuggingFace). Default: bharatgenai/Param-1-2.9B-Instruct
# PARAM1_2_9B_INSTRUCT_MODEL=bharatgenai/Param-1-2.9B-Instruct
# Optional: Use 4-bit quantization for 2.9B to save VRAM
# PARAM_2_9B_4BIT=1

# Celery / Redis (defaults shown)
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
```

## Running the Application

### Option 1: Docker (Recommended)

```bash
# Build and start containers
docker compose up --build

# Or run in detached mode
docker compose up -d

# View logs
docker compose logs -f backend

# Stop containers
docker compose down
```

- Backend available at `http://localhost:8005/`
- Frontend served at `http://localhost:8080/`

### Option 2: Local Development

You need **3 things running** to use the full application:

#### 1. Redis (message broker)

```bash
# Check if Redis is already running
redis-cli ping        # Should print PONG

# If not running, start it
redis-server --daemonize yes
```

#### 2. FastAPI Backend (web server)

```bash
cd backend
python app.py
```

The application will be available at `http://localhost:8000/`

#### 3. Celery Workers (background question generation)

Bulk question generation runs asynchronously via Celery. You **must** start at least one worker for generation to work.

**Start workers (from the `backend/` directory):**

```bash
# Start 3 background workers (adjust the number based on your GPU VRAM)
cd backend
celery -A tasks multi start worker1 worker2 worker3 --pool=solo --loglevel=info --pidfile="./%n.pid" --logfile="./%n.log"
```

**Stop workers:**

```bash
cd backend
celery -A tasks multi stop worker1 worker2 worker3 --pidfile="./%n.pid" --logfile="./%n.log"
```

**Restart workers (after code changes):**

```bash
cd backend
celery -A tasks multi restart worker1 worker2 worker3 --pool=solo --loglevel=info --pidfile="./%n.pid" --logfile="./%n.log"
```

**View worker logs:**

```bash
# Live-tail a specific worker's log
tail -f backend/worker1.log
```

> **Note:** We use `--pool=solo` because PyTorch/CUDA cannot be forked safely.
> Each worker is an independent process. More workers = more parallel generations,
> but each one loads models into GPU memory. Adjust the number to fit your VRAM.

## RAG Pipeline Setup

1. Place NCERT PDFs in the `data/` folder at the project root
2. Run the ingestion script:

```bash
python backend/ncert_rag_pipe/ingest.py
```

This will:
- Extract text from all PDFs in `data/`
- Chunk and embed the content
- Create `vector_db.index` and `chunks_metadata.pkl` in the `indexes/` folder

## Available Models

### Cloud Models
- `gemini` - Google Gemini 3 Flash
- `chatgpt` - OpenAI GPT-4o
- `groq-llama-8b` - Groq Llama 3.1 8B Instant
- `groq-llama-70b` - Groq Llama 3.3 70B Versatile
- `groq-llama-guard` - Groq Llama Guard 4 12B
- `groq-gpt-oss-120b` - Groq GPT OSS 120B
- `groq-gpt-oss-20b` - Groq GPT OSS 20B

### Local Models (via Ollama)
- `local-llama` - Local Llama 3
- `qwen` - Qwen 2.5
- `granite3.3:8b` - IBM Granite 3

### Param Models
- `param-1-7b-moe` - Param 1 7B MoE model (requires PARAM1_7B_MOE_PATH)
- `param-1-2.9b-instruct` - [Param 1 2.9B Instruct](https://huggingface.co/bharatgenai/Param-1-2.9B-Instruct) (loads from HuggingFace; optional PARAM1_2_9B_INSTRUCT_MODEL, PARAM_2_9B_4BIT)
- `rag-piped-param-moe` - Param 1 7B MoE with RAG context

### RAG-Enhanced Models
- `rag-piped-llama` - Llama 3 with RAG context

## Usage

1. Select a subject (Math, Physics, Chemistry, Biology)
2. Choose a chapter from the dropdown
3. Select a theme (cricket, IPL, football, or Interstellar movie)
4. Set cognitive depth level (DOK 1-4)
5. Configure question distribution by type
6. Click "GENERATE SESSION" to create questions

## API Documentation

Once the application is running, access the interactive API docs:

| Endpoint | Description |
|----------|-------------|
| `/docs`  | Swagger UI - Interactive API explorer |
| `/redoc` | ReDoc - Alternative API documentation |



## License

[Add your license here]

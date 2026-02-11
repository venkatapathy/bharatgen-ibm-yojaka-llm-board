# import os
# import json # Added to parse the string response
# import uvicorn
# import ollama
# import re
# from fastapi import FastAPI, HTTPException
# from fastapi.responses import FileResponse
# from pydantic import BaseModel
# from dotenv import load_dotenv
# from google import genai
# from openai import OpenAI
# from pathlib import Path
# import ncert_rag_pipe.main as ncert_rag

# BASE_DIR = Path(__file__).resolve().parent

# load_dotenv()
# app = FastAPI()

# # Clients
# gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY_1"))
# openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# class QueryRequest(BaseModel):
#     model_id: str
#     depth: str
#     theme: str
#     topic: str
#     qType: str

# def parse_ai_output(raw_text):
#     # 1. Check if raw_text is actually a string
#     if raw_text is None:
#         return {"question": "Error: Model returned no data.", "answer": "Check local server status."}
    
#     # 2. Extract tags
#     question_match = re.search(r'<Question>(.*?)</Question>', raw_text, re.DOTALL)
#     answer_match = re.search(r'<Answer>(.*?)</Answer>', raw_text, re.DOTALL)

#     # 3. Fallback: If tags aren't found, give the user the raw output in the question box
#     # instead of just crashing or saying "Not Found"
#     question = question_match.group(1).strip() if question_match else raw_text.strip()
#     answer = answer_match.group(1).strip() if answer_match else "No tags detected. Logic may be embedded in text above."

#     return {
#         "question": question,
#         "answer": answer
#     }

# @app.get("/")
# async def serve_index():
#     return FileResponse(BASE_DIR / "../frontend/index.html")

# @app.post("/ask")
# async def ask_llm(req: QueryRequest):
#     # The strictly defined prompt for diverse questions
#     # Refined implementation for your code
#     topic_chunk, theme_chunk  = ncert_rag.main(req.theme, req.topic)
#     prompt = (
#         "### ROLE\n"
#         "Act as an expert Academic Assessment Designer specializing in curriculum development.\n\n"
        
#         "### TASK\n"
#         f"Generate a high-quality question based on:\n"
#         f"- QUESTION TYPE: {req.qType}\n"
#         f"- TOPIC: {req.topic}\n"
#         f"- THEME: {req.theme}\n"
#         f"- DEPTH: {req.depth}\n\n"
        
#         "### CONSTRAINTS\n"
#         "1. If depth is 'High' or 'Application', include a situational scenario.\n"
#         "2. Ensure distractors are plausible and logically related.\n"
#         "3. Use professional academic language.\n\n"
        
#         "### OUTPUT FORMAT\n"
#         "Strictly wrap the content in these tags:\n"
#         "<Question> [Question text and options] </Question>\n"
#         "<Answer> [Correct answer] </Answer>"
#     )
#     prompt2 = (
#         "### ROLE\n"
#         "Act as an expert Academic Assessment Designer specializing in psychometrics and Evidence-Centered Design. "
#         "Your goal is to synthesize specific reference materials into a high-validity assessment item.\n\n"
        
#         "### INPUT PARAMETERS\n"
#         f"- QUESTION TYPE: {req.qType}\n"
#         f"- TOPIC: {req.topic}\n"
#         f"- THEME: {req.theme}\n"
#         f"- COGNITIVE DEPTH: {req.depth}\n\n"
        
#         "### REFERENCE MATERIAL (RAG CONTEXT)\n"
#         "The following chunks are provided as potential source material. "
#         "**Crucial:** Evaluate these chunks for relevance to the TOPIC and THEME. "
#         "If a chunk is relevant, prioritize its details. If a chunk is irrelevant or contains 'noise,' ignore it and rely on your internal expert knowledge to maintain academic accuracy.\n\n"
#         f"--- [TOPIC CHUNK] ---\n{topic_chunk}\n--- [END TOPIC CHUNK] ---\n\n"
#         f"--- [THEME CHUNK] ---\n{theme_chunk}\n--- [END THEME CHUNK] ---\n\n"
        
#         "### DESIGN CONSTRAINTS\n"
#         "1. **Relevance Assessment:** Before generating, determine if [TOPIC CHUNK] contains the core facts for the question and if [THEME CHUNK] provides a usable narrative setting. "
#         "If the chunks are not helpful for the requested {req.topic}, prioritize factual correctness over context adherence.\n"
#         "2. **Context Integration:** Where relevant, the question should assess the [TOPIC CHUNK] principles while using the [THEME CHUNK] as the situational backdrop.\n"
#         "3. **Depth Handling:**\n"
#         "   - If depth is 'Low/Recall': Focus on defining terms or facts. Use the [TOPIC CHUNK] if it contains the definitions; otherwise, use standard academic definitions.\n"
#         "   - If depth is 'High/Application': Create a situational scenario. Use the [THEME CHUNK] to build the 'story' of the scenario ONLY if it aligns with the {req.topic}.\n"
#         "4. **Distractor Quality:** Distractors must be 'plausible distractors'—common misconceptions related to the topic, not random or obviously wrong choices.\n"
#         "5. **Professionalism:** Use formal academic language and ensure the question is clear and unambiguous.\n\n"
        
#         "### OUTPUT FORMAT\n"
#         "Strictly wrap the content in these tags. Do not include introductory text, reasoning, or meta-comments on context relevance:\n"
#         "<Question>\n"
#         "[Question Stem/Scenario]\n"
#         "[A. Option]\n"
#         "[B. Option]\n"
#         "[C. Option]\n"
#         "[D. Option]\n"
#         "</Question>\n"
#         "<Answer> [Correct Answer Letter and Text] </Answer>"
#     )
#     try:
#         raw_output = ""
        
#         if req.model_id == "gemini":
#             response = gemini_client.models.generate_content(
#                 model="gemini-3-flash-preview", 
#                 contents=prompt
#             )
#             raw_output = response.text
        
#         elif req.model_id == "local-llama":
#             response = ollama.chat(model='llama3', messages=[
#                 {
#                     'role': 'user',
#                     'content': prompt,
#                 },
#             ])
#             raw_output = response['message']['content']

#         elif req.model_id == "qwen":
#             response = ollama.chat(model='qwen3:8b', messages=[
#                 {'role': 'user', 'content': prompt}
#             ])
#             raw_output = response['message']['content']

#         elif req.model_id == "rag-piped-llama":
#             response = ollama.chat(model='llama3', messages=[
#                 {
#                     'role': 'user',
#                     'content': prompt2,
#                 },
#             ])
#             raw_output = response['message']['content']

#         elif req.model_id == "granite3.3:8b":
#             response = ollama.chat(model='granite3.3:8b', messages=[
#                 {'role': 'user', 'content': prompt}
#             ])
#             raw_output = response['message']['content']
        
#         else:
#             raw_output = "<Question> Local Mode Question <Question> <Answer> Local Answer <Answer>"

#         # Return the raw text directly in the 'question' key
#         # return {"question": raw_output}
#         formatted_response = parse_ai_output(raw_output)
#         return formatted_response

#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))
from prompt import GUARDRAILS_PROMPT
from GEval import GEval
import os
import re
import traceback
import json
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, HTTPException, Form, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pathlib import Path
from dotenv import load_dotenv
import ncert_rag_pipe.main as ncert_rag
from typing import List, Optional
from model_runner import run_model, needs_rag, get_rag_context
from council import run_council_flow
from db import save_question, QuestionDB
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib.enums import TA_LEFT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
import io
import re

def latex_to_text(s: str) -> str:
    if not s:
        return ""

    replacements = {
        r"\\frac\{([^}]+)\}\{([^}]+)\}": r"(\1/\2)",
        r"\\cdot": "×",
        r"\\times": "×",
        r"\\Delta": "Δ",
        r"\\neq": "≠",
        r"\\leq": "≤",
        r"\\geq": "≥",
        r"\\text\{([^}]+)\}": r"\1",
        r"\$": "",
    }

    for pattern, repl in replacements.items():
        s = re.sub(pattern, repl, s)

    return s


load_dotenv()

 # FastAPI app with Swagger documentation
app = FastAPI(
    title="BharatGen LLM Board API",
    description="""
## BharatGen IBM Yojaka LLM Board API - Sovereign AI Stack

A comprehensive API for generating NCERT/CBSE-aligned academic assessment questions using sovereign AI models.

### 🎯 Core Features

| Feature | Description |
|---------|-------------|
| **Question Generation** | Generate MCQs, Short/Long Answer questions using LLMs |
| **Council/Board Mode** | Multi-model orchestration for higher quality questions |
| **RAG Pipeline** | NCERT textbook-grounded Retrieval Augmented Generation |
| **GEval Scoring** | Automatic quality scoring (NCERT alignment, Bloom's taxonomy, Guardrails) |
| **Explore Mode** | Interactive PDF-based tutoring with source grounding |

### 🤖 Available Models
| Model ID | Description |
|----------|-------------|
| `groq-llama-8b` | Llama 3.1 8B via Groq (fast) |
| `groq-llama-70b` | Llama 3.3 70B via Groq (versatile) |
| `rag-piped-groq-70b` | Llama 70B with RAG context |

### 📊 Cognitive Depth Levels (DOK/Bloom's)
- **DOK 1**: Recall/Remember - Facts, terms, definitions
- **DOK 2**: Skills & Concepts - Classify, describe, solve routine problems  
- **DOK 3**: Strategic Thinking - Analyze, evaluate, non-routine problems
- **DOK 4**: Extended Thinking - Create, synthesize, cross-chapter connections

### 🔗 API Endpoints Summary
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/ask` | POST | Generate questions (single model or council mode) |
| `/chapters` | GET | List chapters by subject and language |
| `/explore/chat` | POST | Chat with PDF context grounding |
| `/api/pdf` | GET | Serve PDF files for exploration |
| `/health` | GET | Health check endpoint |

### 📖 Documentation
- **Swagger UI**: `/docs`
- **ReDoc**: `/redoc`
    """,
    version="2.0.0",
    contact={
        "name": "BharatGen Team - IBM Yojaka",
    },
    license_info={
        "name": "MIT",
    },
    openapi_tags=[
        {
            "name": "Question Generation",
            "description": "Generate NCERT-aligned academic questions using LLM models with optional council orchestration"
        },
        {
            "name": "Chapters",
            "description": "Retrieve available chapters and subjects from the NCERT curriculum"
        },
        {
            "name": "Explore",
            "description": "Interactive PDF exploration and tutoring with RAG-grounded responses"
        },
        {
            "name": "Health",
            "description": "System health and status endpoints"
        }
    ]
)

BASE_DIR = Path(__file__).resolve().parent
DATABASE_URL = "sqlite:////tmp/bharatgen_questions.db"

# ---------------- Static files (frontend assets) ----------------
PROJECT_ROOT = BASE_DIR.parent          # bharatgen-ibm-yojaka-llm-board/
FRONTEND_STATIC = PROJECT_ROOT / "frontend" / "static"

app.mount(
    "/static",
    StaticFiles(directory=FRONTEND_STATIC),
    name="static"
)
# ----------------------------------------------------------------

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(bind=engine)

# Initialize Groq client only
groq_api_key = os.getenv("GROQ_API_KEY")
print(groq_api_key)
groq_client = None
try:
    from groq import Groq
    if groq_api_key:
        try:
            groq_client = Groq(api_key=groq_api_key)
            print("Groq client initialized successfully")
        except Exception as e:
            print(f"Warning: Failed to initialize Groq client: {e}")
            groq_client = None
    else:
        print("Warning: GROQ_API_KEY not set. Groq models will not work.")
except ImportError:
    print("Warning: Groq library not installed. Install with: pip install groq")
    groq_client = None

# GEval instances for alignment scoring - use Groq by default
_geval_model = os.getenv("GEVAL_MODEL", " https://model-serve-qwen3-32b.impactsummit.nxtgen.cloud/v1/chat/completions")
_geval_model_2 = os.getenv("GEVAL_MODEL_2", " https://model-serve-qwen3-32b.impactsummit.nxtgen.cloud/v1/chat/completions")
# _geval_model = os.getenv("GEVAL_MODEL", "groq-qwen-32b")
# _geval_model_2 = os.getenv("GEVAL_MODEL_2", "groq-qwen-32b")
# https://param5b.impactsummit.nxtgen.cloud/v1/chat/completions
param_ncert = GEval(model=_geval_model, groq_api_key=groq_api_key or "", likert_scale=[1, 2, 3, 4, 5])
llama_bloom = GEval(model=_geval_model_2, groq_api_key=groq_api_key or "", likert_scale=[1, 2, 3, 4, 5])
guardrails_qwen = GEval(model=_geval_model, groq_api_key=groq_api_key or "", likert_scale=[1, 2])
verification_llama = GEval(model=_geval_model_2, groq_api_key=groq_api_key or "", likert_scale=[1, 2])

# Share Groq client with model_runner
from model_runner import set_clients
set_clients(groq_client=groq_client)



# class QueryRequest(BaseModel):
#     model_id: str
#     depth: str
#     subject: str
#     chapter: str
#     topic: str
#     qType: str

# def parse_ai_output(raw_text):
#     if not raw_text:
#         return {"question": "Error: No data.", "answer": "N/A"}
#     question_match = re.search(r'<Question>(.*?)</Question>', raw_text, re.DOTALL)
#     answer_match = re.search(r'<Answer>(.*?)</Answer>', raw_text, re.DOTALL)
#     return {
#         "question": question_match.group(1).strip() if question_match else raw_text.strip(),
#         "answer": answer_match.group(1).strip() if answer_match else "Logic embedded in text."
#     }

# @app.get("/")
# async def serve_index():
#     return FileResponse(BASE_DIR / "../frontend/index.html")

# @app.post("/ask")
# async def ask_llm(req: QueryRequest):
#     # RAG Context Retrieval
    
    
#     # Standard Prompt
#     prompt = f"### ROLE: NCERT {req.subject} Expert. TASK: Generate a {req.qType} for {req.topic} (Chapter: {req.chapter}). DOK: {req.depth}. OUTPUT: <Question>...</Question><Answer>...</Answer>"
    
#     try:
#         raw_output = ""
#         if req.model_id == "gemini":
#             response = gemini_client.models.generate_content(model="gemini-3-flash-preview", contents=prompt)
#             raw_output = response.text
#         elif req.model_id == "chatgpt":
#             response = openai_client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt}])
#             raw_output = response.choices[0].message.content
#         elif req.model_id == "local-llama":
#             response = ollama.chat(model='llama3', messages=[{'role': 'user', 'content': prompt}])
#             raw_output = response['message']['content']
#         elif req.model_id == "qwen":
#             response = ollama.chat(model='qwen3:8b', messages=[{'role': 'user', 'content': prompt}])
#             raw_output = response['message']['content']
#         elif req.model_id == "granite3.3:8b":
#             response = ollama.chat(model='granite3.3:8b', messages=[{'role': 'user', 'content': prompt}])
#             raw_output = response['message']['content']
#         elif req.model_id == "rag-piped-llama":
#             topic_chunk, theme_chunk = ncert_rag.main(req.chapter, req.topic)
#             # RAG-Specific Prompt (for rag-piped-llama)
#             prompt_rag = f"### RAG CONTEXT:\n{topic_chunk}\n\n### TASK: Generate {req.qType} for {req.topic} using context. DOK: {req.depth}. OUTPUT: <Question>...</Question><Answer>...</Answer>"
#             response = ollama.chat(model='llama3', messages=[{'role': 'user', 'content': prompt_rag}])
#             raw_output = response['message']['content']
#         else:
#             raw_output = "<Question>Model not found.</Question><Answer>N/A</Answer>"

#         return parse_ai_output(raw_output)
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))
    
#     # ... (existing imports)

class BoardConfig(BaseModel):
    chairman_model_id: str
    member_model_ids: List[str]

class QueryRequest(BaseModel):
    # For backward compatibility, model_id is optional if board is provided
    model_id: Optional[str] = None
    language: str = "en"
    depth: str
    subject: str
    chapter: str
    standard: str
    theme: str = "general"
    qType: str
    num_questions: int
    # Board configuration (required for new flow)
    board: Optional[BoardConfig] = None

import re

def parse_ai_output(raw_text):
    if not raw_text:
        print("[DEBUG] parse_ai_output: raw_text is empty")
        return []

    print(f"[DEBUG] parse_ai_output: Input length: {len(raw_text)} characters")
    print(f"[DEBUG] parse_ai_output: First 500 chars: {raw_text}")

    # More lenient patterns - try multiple variations
    # Pattern 1: Standard <Question>...</Question> format
    q_pattern1 = r'<(?:[Qq]uestion)>(.*?)(?:</[Qq]uestion>|(?=<[Qq]uestion>|<[Aa]nswer>|$))'
    a_pattern1 = r'<(?:[Aa]nswer)>(.*?)(?:</[Aa]nswer>|(?=<[Qq]uestion>|<[Aa]nswer>|$))'
    
    # Pattern 2: Without closing tags
    q_pattern2 = r'<(?:[Qq]uestion)>(.*?)(?=<[Qq]uestion>|<[Aa]nswer>|$)'
    a_pattern2 = r'<(?:[Aa]nswer)>(.*?)(?=<[Qq]uestion>|<[Aa]nswer>|$)'

    questions = re.findall(q_pattern1, raw_text, re.DOTALL)
    answers = re.findall(a_pattern1, raw_text, re.DOTALL)
    
    # If no matches, try pattern 2
    if not questions:
        questions = re.findall(q_pattern2, raw_text, re.DOTALL)
        answers = re.findall(a_pattern2, raw_text, re.DOTALL)

    # Fallback: markdown-style ### QUESTION / ### ANSWER or "Answer:" / "ANSWER"
    if not questions:
        md_answer = re.search(
            r'(?i)(?:###\s*)?(?:ANSWER|Answer)\s*\n\s*(.*)',
            raw_text,
            re.DOTALL
        )
        md_question = re.search(
            r'(?i)(?:###\s*)?(?:QUESTION|Question)\s*\n\s*(.*?)(?=(?:###\s*)?(?:ANSWER|Answer)\s*\n|\Z)',
            raw_text,
            re.DOTALL
        )
        if md_answer:
            a_text = md_answer.group(1).strip()[:2000]
            if md_question:
                q_text = md_question.group(1).strip()
            else:
                before = raw_text[:md_answer.start()].strip()
                q_text = before if len(before) < 2000 else before[:1997] + "..."
            if not q_text:
                q_text = "Question (format not parsed; see synthesis output)"
            questions = [q_text]
            answers = [a_text] if a_text else ["No answer provided."]
        elif re.search(r'(?i)(?:###\s*)?(?:ANSWER|Answer)\s*', raw_text):
            lines = raw_text.strip().split('\n')
            for i, line in enumerate(lines):
                if re.match(r'(?i)^(?:###\s*)?(?:ANSWER|Answer)\s*$', line.strip()) and i + 1 < len(lines):
                    questions = ["Question (see synthesis output)"]
                    answers = ['\n'.join(lines[i + 1:]).strip()[:2000]]
                    break
    
    print(f"[DEBUG] parse_ai_output: Found {len(questions)} questions, {len(answers)} answers")

    results = []
    
    # We loop based on the number of questions found
    for i in range(len(questions)):
        q_raw = questions[i]
        a_raw = answers[i] if i < len(answers) else "No answer provided."

        # CLEANUP: Remove any stray closing tags the AI might have actually included
        q_clean = re.sub(r'</?[Qq]uestion/?>', '', q_raw).strip()
        a_clean = re.sub(r'</?[Aa]nswer/?>', '', a_raw).strip()

        # CLEANUP: Remove AI artifacts like "**Question 1**" or "Note:"
        q_clean = re.sub(r'(?i)(\*\*Question\s*\d+\*\*|Question\s*\d+:|###.*?\n)', '', q_clean).strip()
        a_clean = re.sub(r'(?i)(\*\*Answer\*\*|Answer:|Note:.*$)', '', a_clean).strip()

        if q_clean:  # Only add if question is not empty
            results.append({
                "question": q_clean,
                "answer": a_clean
            })

    print(f"[DEBUG] parse_ai_output: Returning {len(results)} parsed results")
    return results


@app.get("/")
async def serve_index():
    return FileResponse(BASE_DIR / os.getenv("FRONTEND_RELATIVE_PATH", "../frontend/index.html"))


@app.get("/explore.html")
async def serve_explore():
    return FileResponse(BASE_DIR / "../frontend/explore.html")

def _get_books_root():
    """Books root for PDF serving; must match ingest BOOKS_ROOT.
    Uses BHARATGEN_BOOKS_PATH if set; else project/books if it exists; else project/data.
    """
    env_path = os.getenv("BHARATGEN_BOOKS_PATH")
    if env_path:
        return Path(env_path).resolve()
    project_root = BASE_DIR.parent
    books_dir = project_root / "books"
    if books_dir.is_dir():
        return books_dir.resolve()
    return (project_root / "data").resolve()


@app.get("/api/pdf", tags=["Explore"])
async def serve_pdf(path: str):
    """
    Serve a PDF file for the Explore mode.
    
    - **path**: Relative path to PDF (e.g., `English/Biology/Class-11/file.pdf`)
    
    Returns the PDF file for viewing in the Explore interface.
    """
    if not path or ".." in path or path.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid path")
    books_root = _get_books_root()
    full = (books_root / path).resolve()
    try:
        if not full.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        if os.path.commonpath([str(full), str(books_root)]) != str(books_root):
            raise HTTPException(status_code=403, detail="Access denied")
    except HTTPException:
        raise
    return FileResponse(full, media_type="application/pdf", filename=full.name)


class ExploreChatRequest(BaseModel):
    chunk_text: str
    pdf_path: Optional[str] = None
    page: Optional[int] = None
    messages: List[dict]  # [{"role": "user"|"assistant", "content": str}]


@app.post("/explore/chat", tags=["Explore"])
async def explore_chat(body: ExploreChatRequest):
    """
    Interactive chat grounded in PDF source material.
    
    The AI tutor answers questions using ONLY the provided source chunk text,
    ensuring factually accurate, curriculum-aligned responses.
    
    - **chunk_text**: The source material to ground responses
    - **pdf_path**: Optional path to the PDF being viewed
    - **page**: Optional current page number
    - **messages**: Conversation history as list of {role, content} objects
    
    Returns the AI tutor's response grounded in the source material.
    """
    try:
        system = (
            "You are a helpful tutor. Answer ONLY using the following source material. "
            "Do not add information from outside the source. If the source does not contain enough information, say so. "
            "Keep answers concise and educational.\n\n### Source material:\n"
        ) + body.chunk_text
        if body.pdf_path and body.page is not None:
            system += f"\n\n(The student may be viewing the PDF at page {body.page}.)"
        prompt = f"{system}\n\n---\n\nConversation:\n"
        for m in body.messages:
            prompt += f"{m.get('role', 'user').capitalize()}: {m.get('content', '')}\n"
        prompt += "\nAssistant:"
        explore_model = os.getenv("EXPLORE_CHAT_MODEL", "groq-llama-8b")
        reply = await run_model(explore_model, prompt, None)
        if not reply:
            reply = "I couldn't generate a response. Please try again."
        return {"reply": reply.strip()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class ReferenceRequest(BaseModel):
    prompt: str
    num_chunks_post_rrf: int = 20
    num_docs_reranker: int = 1
    use_reranker: bool = True
    language: str
    subject: str
    class_level: str

class ChatCompletionRequest(BaseModel):
    messages: List[dict]
    model: str = "ibm-granite/granite-3.3-8b-instruct"
    system_context: Optional[str] = None


@app.post("/api/reference", tags=["User Chat"])
async def get_reference(body: ReferenceRequest):
    """
    Get reference context/chunk from RAG backend.

    This endpoint retrieves relevant educational content based on the provided parameters.
    """
    import httpx

    reference_url = os.getenv("REFERENCE_API_URL", "https://edu-rag-bkend.impactsummit.nxtgen.cloud/reference")

    try:
        async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
            response = await client.post(
                reference_url,
                json={
                    "prompt": body.prompt,
                    "num_chunks_post_rrf": body.num_chunks_post_rrf,
                    "num_docs_reranker": body.num_docs_reranker,
                    "use_reranker": body.use_reranker,
                    "language": body.language,
                    "subject": body.subject,
                    "class_level": body.class_level
                },
                headers={
                    "Content-Type": "application/json"
                }
            )
            response.raise_for_status()
            return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Reference API error: {str(e)}")


@app.post("/api/chat-completions", tags=["User Chat"])
async def chat_completions(body: ChatCompletionRequest):
    """
    Chat completions endpoint for user conversation.

    This endpoint forwards chat requests to the model serve endpoint with optional system context.
    """
    import httpx

    chat_url = os.getenv("CHAT_COMPLETION_URL", "https://model-serve-chat.impactsummit.nxtgen.cloud/v1/chat/completions")

    try:
        messages = []

        # Add system context if provided
        if body.system_context:
            messages.append({
                "role": "system",
                "content": body.system_context
            })

        # Add conversation messages
        messages.extend(body.messages)

        async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
            response = await client.post(
                chat_url,
                json={
                    "model": body.model,
                    "messages": messages
                },
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Dummy"
                }
            )
            response.raise_for_status()
            return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chat completion error: {str(e)}")

@app.get("/user-chat")
async def serve_user_chat():
    return FileResponse(BASE_DIR / "../frontend/user-chat.html")

@app.post("/user-chat", tags=["User Chat"])
async def receive_user_chat_form(
    request: Request,
    subject: str = Form(...),
    language: str = Form(...),
    class_level: str = Form(...),
    chapter: str = Form(...),
    context: str = Form(...)
):
    """
    POST endpoint to receive form data and serve user-chat page with embedded data.

    This allows users to submit quiz/session data and transition to the chat interface.
    """
    from fastapi.responses import HTMLResponse
    import json

    # Read the HTML template
    html_path = BASE_DIR / "../frontend/user-chat.html"
    with open(html_path, 'r', encoding='utf-8') as f:
        html_content = f.read()

    # Inject form data as a script tag before </body>
    print(class_level,type(class_level))
    if(class_level=='10' ):
        if(subject!='Math'):
            subject='Science'
        else:
            subject='Maths'
    form_data = {
        'subject': subject,
        'language': language,
        'class': class_level,
        'chapter': chapter,
        'context': context
    }

    injection_script = f"""
    <script>
        window.FORM_DATA = {json.dumps(form_data)};
    </script>
    </body>"""

    html_content = html_content.replace('</body>', injection_script)

    return HTMLResponse(content=html_content)

@app.get("/test-form")
async def serve_test_form():
    return FileResponse(BASE_DIR / "../frontend/test-form.html")


@app.get("/chapters", tags=["Chapters"])
async def list_chapters(subject: str, language: str = "en"):
    """
    Get available chapters for a subject.
    
    Returns chapters from the NCERT curriculum manifest.
    
    - **subject**: Subject name (e.g., `Science`, `Mathematics`, `Physics`)
    - **language**: Language code - `en` (English) or `hi` (Hindi)
    
    Example response:
    ```json
    {"chapters": ["Units and Measurements", "Motion in a Straight Line", ...]}
    ```
    """
    language = (language or "en").lower()
    if language not in ("en", "hi"):
        raise HTTPException(status_code=400, detail="language must be 'en' or 'hi'")

    manifest_path = (BASE_DIR.parent / "indexes" / language / "chapters_manifest.json").resolve()
    if not manifest_path.exists():
        return {"chapters": []}
    print(manifest_path)
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        chapters = data.get(subject, [])
        if not isinstance(chapters, list):
            chapters = []
        return {"chapters": chapters}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read chapters manifest: {e}")


@app.get("/health", tags=["Health"])
async def health_check():
    """
    System health check.
    
    Returns the status of critical components:
    - **ok**: `true` if the Groq client is initialized and ready
    - **message**: Current operational mode
    
    Example response:
    ```json
    {"ok": true, "message": "Groq-only mode"}
    ```
    """
    return {"ok": groq_client is not None, "message": "Groq-only mode"}

@app.get("/api/questions")
def viewer_questions(offset: int = 0, limit: int = 25):
    db = SessionLocal()

    rows = db.query(QuestionDB)\
        .order_by(QuestionDB.created_at.desc())\
        .offset(offset)\
        .limit(limit)\
        .all()

    db.close()

    return [{
        "id": r.id,
        "question": r.question or "",
        "alignment_score": r.alignment_score or 0,
        "model_id": r.model_id
    } for r in rows]


@app.get("/api/question/{qid}")
def viewer_single(qid: str):
    db = SessionLocal()
    q = db.query(QuestionDB).filter(QuestionDB.id == qid).first()
    db.close()

    if not q:
        raise HTTPException(status_code=404, detail="Question not found")
    print(q.scores_json)
    return {
        "id": q.id,
        "question": q.question,
        "answer": q.answer,
        "alignment_score": q.alignment_score,
        "model_id": q.model_id,
        "req": json.loads(q.req_json),
        "scores": json.loads(q.scores_json)
    }



pdfmetrics.registerFont(UnicodeCIDFont('HeiseiMin-W3'))



@app.get("/api/questions/download/pdf")
def download_questions_pdf():
    # ✅ Unicode-safe font (Hindi + English)
    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiMin-W3"))

    # ✅ SAME DB ACCESS PATTERN AS /api/questions
    db = SessionLocal()
    rows = db.query(QuestionDB)\
        .order_by(QuestionDB.created_at.desc())\
        .all()
    db.close()

    buffer = io.BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=40,
        leftMargin=40,
        topMargin=40,
        bottomMargin=40
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="Q",
        fontName="HeiseiMin-W3",
        fontSize=11,
        leading=16,
        spaceAfter=10
    ))
    styles.add(ParagraphStyle(
        name="A",
        fontName="HeiseiMin-W3",
        fontSize=10,
        leading=14,
        leftIndent=20,
        spaceAfter=16
    ))

    story = []
    story.append(Paragraph("<b>BharatGen – Generated Questions</b>", styles["Title"]))
    story.append(Spacer(1, 20))

    for i, r in enumerate(rows, start=1):
        question_text = latex_to_text(r.question or "")
        answer_text   = latex_to_text(r.answer or "")

        story.append(Paragraph(
            f"<b>Q{i}.</b> {question_text}",
            styles["Q"]
        ))

        if answer_text:
            story.append(Paragraph(
                f"<b>Answer:</b> {answer_text}",
                styles["A"]
            ))


        if i % 5 == 0:
            story.append(PageBreak())

    doc.build(story)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={
            "Content-Disposition": "attachment; filename=bharatgen_questions.pdf"
        }
    )


@app.get("/viewq", response_class=HTMLResponse, include_in_schema=False)
def viewer():
    return """
<!DOCTYPE html>
<html>
<head>
<title>Questions Dashboard</title>

<style>
body{
background:#f8fafc;
font-family:Arial;
padding:40px
}

.card{
background:white;
padding:25px;
border-radius:12px;
box-shadow:0 10px 25px rgba(0,0,0,.05)
}

/* ---------- TABLE STYLE ---------- */

table{
    width:100%;
    border-collapse:separate;
    border-spacing:0;
    margin-top:10px;
    overflow:hidden;
    border-radius:12px;
    background:white;
}

/* header */
th{
    background:#f1f5f9;
    color:#475569;
    font-size:13px;
    text-transform:uppercase;
    letter-spacing:.5px;
    padding:14px 12px;
    border-bottom:1px solid #e2e8f0;
}

/* cells */
td{
    padding:14px 12px;
    border-bottom:1px solid #f1f5f9;
    font-size:14px;
}

/* zebra striping */
tbody tr:nth-child(even){
    background:#fafafa;
}

/* hover effect */
tbody tr:hover{
    background:#eef2ff;
    transition:background .2s;
}

/* preview text look */
tbody td:nth-child(2){
    color:#0f172a;
    font-weight:500;
}

/* alignment score badge look */
tbody td:nth-child(3){
    font-weight:600;
    color:#2563eb;
}

button{
background:#2563eb;
color:white;
border:none;
padding:6px 12px;
border-radius:6px;
cursor:pointer
}

button.secondary{
background:#dc2626;
}

button:disabled{
opacity:.4
}

#loadbtn{
margin-top:15px
}

#status{
margin-top:10px;
color:#64748b
}

.modal{
    display:none;
    position:fixed;
    inset:0;
    background:rgba(15,23,42,.55);
    backdrop-filter:blur(4px);
    align-items:center;
    justify-content:center;
    z-index:1000;
}

.modal-content{
    background:white;
    width:70%;
    max-width:900px;
    max-height:85vh;
    overflow-y:auto;
    border-radius:16px;
    padding:32px;
    box-shadow:0 20px 60px rgba(0,0,0,.25);
    animation:fadeUp .25s ease;
}

@keyframes fadeUp{
    from{opacity:0; transform:translateY(20px)}
    to{opacity:1; transform:translateY(0)}
}

.close{
    float:right;
    cursor:pointer;
    font-size:22px;
    font-weight:bold;
    color:#64748b;
}

.close:hover{
    color:#0f172a;
}

.meta{
    display:none;
    color:#475569;
    margin-top:16px;
    padding-top:16px;
    border-top:1px solid #e2e8f0;
}
</style>
</head>

<body>

<div class="card">

<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:15px">
    <h2 style="margin:0">Questions</h2>

    <div style="display:flex;gap:10px">
         <!-- <button class="secondary" onclick="downloadPDF()">⬇ Download PDF</button> -->
        <button onclick="window.location.href='/'">
            ← Home
        </button>
    </div>
</div>

<table>
<thead>
<tr>
<th>Q.No</th>
<th>Question Preview</th>
<th>Alignment</th>
<th>Action</th>
</tr>
</thead>

<tbody id="rows"></tbody>
</table>

<button id="loadbtn" onclick="load()">Load more</button>
<div id="status"></div>

</div>

<div class="modal" id="modal">
<div class="modal-content">
<span class="close" onclick="closeModal()">×</span>
<div id="modalBody"></div>
</div>
</div>

<script>
let offset=0;
const limit=25;
let qcounter=1;

const rows=document.getElementById("rows");
const status=document.getElementById("status");
const loadbtn=document.getElementById("loadbtn");
const modal=document.getElementById("modal");
const modalBody=document.getElementById("modalBody");

function prettyKey(k){
    return k.replace(/_/g," ")
            .replace(/\\b\\w/g,c=>c.toUpperCase());
}

function renderTable(obj){
    let html = `<table style="width:100%;margin-top:10px;border-collapse:separate;border-spacing:0;background:#f8fafc;border-radius:10px;overflow:hidden;font-size:14px">`;

    for(const k in obj){
        const v = obj[k];
        if(v !== null && v !== undefined){
            html += `
            <tr>
                <td style="padding:10px 14px;width:35%;color:#475569;font-weight:600;border-bottom:1px solid #e2e8f0">
                    ${prettyKey(k)}
                </td>
                <td style="padding:10px 14px;font-weight:500;color:#0f172a;border-bottom:1px solid #e2e8f0">
                    ${v}
                </td>
            </tr>`;
        }
    }

    html += "</table>";
    return html;
}

function load(){
fetch(`/api/questions?offset=${offset}&limit=${limit}`)
.then(r=>r.json())
.then(data=>{
if(data.length===0){
    status.textContent="All questions loaded.";
    loadbtn.disabled=true;
    return;
}

data.forEach(q=>{
    const tr=document.createElement("tr");
    const preview=q.question.substring(0,40)+"...";
    const pct = Math.round((q.alignment_score/5)*100);

    tr.innerHTML = `
    <td>${qcounter++}</td>
    <td>${preview}</td>
    <td style="font-weight:600;color:${
        pct < 33 ? '#dc2626' :
        pct <= 66 ? '#ca8a04' :
        '#16a34a'
    }">
        ${q.alignment_score}
    </td>
    <td><button onclick="openModal('${q.id}')">View Details</button></td>
    `;
    rows.appendChild(tr);
});

offset+=limit;
});
}

function openModal(id){
fetch("/api/question/"+id)
.then(r=>r.json())
.then(q=>{
const pct=Math.round((q.alignment_score/5)*100);

modalBody.innerHTML = `
<div style="margin-bottom:18px">
    <div style="font-size:18px;font-weight:600;color:#0f172a;margin-bottom:10px">
        ${q.question}
    </div>

    <div style="color:#334155;background:#f8fafc;padding:14px;border-radius:10px">
        ${q.answer}
    </div>

    <div style="margin-top:15px;font-weight:600;color:#2563eb">
        Alignment Score: ${q.alignment_score}/5 (${pct}%)
    </div>
</div>

<button onclick="toggle()" style="background:#2563eb;color:white;padding:8px 14px;border-radius:8px;border:none">
Show Metadata
</button>

<div class="meta" id="meta">
${renderTable(q.req)}
${renderTable(q.scores)}
</div>
`;

modal.style.display="flex";
});
}

function toggle(){
const meta=document.getElementById("meta");
meta.style.display=meta.style.display==="none"?"block":"none";
}

function closeModal(){
modal.style.display="none";
}

window.onclick=function(e){
if(e.target==modal) closeModal();
}

function downloadPDF(){
window.open("/api/questions/download/pdf","_blank");
}
offset = 0;
qcounter = 1;
rows.innerHTML = "";
load();


</script>

</body>
</html>
"""


def detect_language(text: str) -> str:
    # Unicode range for Devanagari
    devanagari_pattern = re.compile(r'[\u0900-\u097F]')
    english_pattern = re.compile(r'[A-Za-z]')

    has_devanagari = bool(devanagari_pattern.search(text))
    has_english = bool(english_pattern.search(text))

    if has_english and has_devanagari:
        return "hien"
    elif has_devanagari:
        return "hi"
    elif has_english:
        return "en"
    else:
        return "unknown"

from concurrent.futures import ThreadPoolExecutor


def _eval_vllm_a(req, q):
    ncert_score = param_ncert.evaluate(
        task_description=(
            "You are to determine whether the given question and answer pair "
            "is a standard NCERT 10th, 11th or 12th standard question or not."
        ),
        evaluation_parameter=(
            "You to rate how well it is aligned on a scale of 1 to 5. "
            "A score of 1 indicates low alignment while a score of 5 indicates high alignment."
        ),
        question=q['question'],
        answer=''
    )

    qtype_score = guardrails_qwen.evaluate(
        task_description=(
            f"You are to determine whether it is a/an {req.qType} question type or not."
        ),
        evaluation_parameter=(
            "You to rate whether the question satisfies all conditions on a scale of 1 to 2."
        ),
        question=q['question'],
        answer=q['answer']
    )

    guardrail_score = guardrails_qwen.evaluate(
        task_description=GUARDRAILS_PROMPT,
        evaluation_parameter=(
            "You to rate whether the question is appropriate or not on a scale of 1 to 2."
        ),
        question=q['question'],
        answer=q['answer']
    )

    return {
        "ncert": round(ncert_score,2),
        "qtype": round(qtype_score,2),
        "guard": round(guardrail_score,2),
    }


def _eval_vllm_b(req, q):
    validity_score = verification_llama.evaluate(
        task_description=(
            "You are to determine whether the given question and answer pair is valid or not. "
            "Try to solve the question without looking at the answer and then verify with the given answer."
        ),
        evaluation_parameter=(
            "You to rate whether the question is appropriate or not on a scale of 1 to 2."
        ),
        question=q['question'],
        answer=q['answer']
    )

    bloom_score = llama_bloom.evaluate(
        task_description=(
            f"You are to evaluate the DoK level alignment of a question.\n"
            f"The provided bloom level is {req.depth}."
        ),
        evaluation_parameter=(
            "You to rate how well it is aligned on a scale of 1 to 5."
        ),
        question=q['question'],
        answer=q['answer']
    )

    return {
        "validity": round(validity_score,2),
        "dok": round(bloom_score,2),
    }


def _eval_language(req, q):
    language_score = detect_language(q['question'] + '\n' + q['answer'])
    if language_score == req.language:
        return 2
    elif language_score == 'hien':
        return 1.5
    return 1


def get_alignment_score(req, q):
    print("===============Generating Scores============")

    scores_a = _eval_vllm_a(req, q)
    scores_b = _eval_vllm_b(req, q)
    language_score = _eval_language(req, q)

    result = {
        **scores_a,
        **scores_b,
        "language": language_score,
    }

    print(
        "===============Done generating Scores===="
        f"DoK : {result['dok']} "
        f"NCERT : {result['ncert']} "
        f"Guard : {result['guard']} "
        f"Validity : {result['validity']}===="
    )

    return result

from fastapi import BackgroundTasks

def process_scores_and_save(req, questions):
    for q in questions:
        try:
            scores = get_alignment_score(req, q)
            if(scores['guard']<1.5 or scores['validity']<1.5 or scores['qtype']<1.5 or scores['language']<1.5):
                q['alignment_score']=0.1
                # q['question']='Oops! We can\'t show this question. Try another one 😊'
                # error_metadata = (
                #     '\nErrors - ' +
                #     ('The question might be inappropriate/incomplete.\n' if scores['guard'] < 1.5 else '') +
                #     ('The question is not a valid NCERT question.' if scores['validity'] < 1.5 else '') +
                #     (f'The question is not a/an {req.qType} type question' if scores['qtype'] < 1.5 else '') +
                #     (f'The question is not in {req.language}' if scores['language'] < 1.5 else '')
                # )

                # q['question'] = q['question'] + '\n\n' + error_metadata
            else:
                q['alignment_score']=round((scores['ncert']+scores['dok'])/2,2)
            scores['is_rag'] = q['is_rag']
            # if(q['is_rag']):
            #     req.chunk=q['source_text']['topic_chunk']
            print("SAVING : ",req, q, scores, q.get("alignment_score"))
            save_question(req, q, scores, q.get("alignment_score"))

        except Exception as e:
            print("Async scoring failed:", e)

@app.post("/ask", tags=["Question Generation"])
async def ask_llm(req: QueryRequest, background_tasks: BackgroundTasks):
    """
    Generate NCERT-aligned academic assessment questions.
    
    This endpoint supports two modes:
    
    1. **Single Model Mode**: Uses one LLM to generate questions
    2. **Board/Council Mode**: Multiple LLMs collaborate to produce higher quality questions
    
    ## Request Parameters
    - **model_id**: The LLM model to use (e.g., 'gemini', 'gpt-4', 'param-1-2.9b-instruct')
    - **subject**: Subject area (e.g., 'Science', 'Mathematics')
    - **chapter**: Chapter name from the subject
    - **topic**: Specific topic within the chapter
    - **qType**: Question type (e.g., 'MCQ', 'Short Answer', 'Long Answer')
    - **depth**: Cognitive depth level ('Low', 'Medium', 'High')
    - **language**: Language for question generation ('en' or 'hi')
    - **board**: Optional board configuration for council flow
    
    ## Response
    Returns generated question(s) with answers in structured format.
    """
    print(req)
    try:
        # Determine if we should use board flow
        if req.board:
            # Validate board configuration
            if req.board.chairman_model_id in req.board.member_model_ids:
                print("[ /ask ] 400: Chairman model cannot be in member list")
                raise HTTPException(
                    status_code=400,
                    detail="Chairman model cannot be in member list"
                )
            if len(req.board.member_model_ids) == 0:
                print("[ /ask ] 400: At least one board member is required")
                raise HTTPException(
                    status_code=400,
                    detail="At least one board member is required"
                )
            
            # Run council flow
            council_result = await run_council_flow(
                chairman_model_id=req.board.chairman_model_id,
                member_model_ids=req.board.member_model_ids,
                language=req.language,
                subject=req.subject,
                chapter=req.chapter,
                theme=req.theme,
                qType=req.qType,
                depth=req.depth,
                num_questions=req.num_questions
            )
            
            # Parse final output to get questions
            questions = parse_ai_output(council_result["final_output"])
            # Fallback when synthesis didn't parse: use chairman's proposal
            if not questions:
                chairman_proposal = council_result.get("chairman_proposal") or ""
                if chairman_proposal:
                    questions = parse_ai_output(chairman_proposal)
                    if questions:
                        print("[Council] Using chairman proposal as fallback (synthesis output did not parse).")
            
            # Add board metadata and source text/meta to each question
            source_chunks = council_result.get("source_chunks")
            source_meta = council_result.get("source_meta")
            for q in questions:
                q["board_metadata"] = {
                    "chairman": req.board.chairman_model_id,
                    "members": req.board.member_model_ids,
                    "language": req.language,
                    "chairman_proposal": council_result["chairman_proposal"],
                    "member_opinions": council_result["member_opinions"]
                }
                if source_chunks:
                    q["source_text"] = source_chunks
                if source_meta:
                    q["source_meta"] = {"pdf_path": source_meta.get("source_path"), "page": source_meta.get("page")}

                scores=get_alignment_score(req,q)
                if(scores['guard']<1.5 or scores['validity']<1.5 or scores['structure']<1.5):
                    q['alignment_score']=0.0

                else:
                    q['alignment_score']=round((scores['ncert']+scores['dok'])/3,2)
            
            print(f"Council flow completed. Generated {len(questions)} questions.\n")
            print(questions)
            return questions
        
        # Fallback to single model (backward compatibility)
        elif req.model_id:
            # Build standard prompt
            lang = (req.language or "en").lower()
            if lang == "hi":
                lang_block = (
                    "### OUTPUT LANGUAGE\n"
                    "Write all Questions and Answers in Hindi (Devanagari script), while preserving LaTeX/math notation as-is.\n\n"
                )
            else:
                lang_block = (
                    "### OUTPUT LANGUAGE\n"
                    "Write all Questions and Answers in English.\n\n"
                )

            prompt = (
                "### ROLE\n"
                "Act as an expert Academic Assessment Designer specializing in NCERT/CBSE curriculum development. "
                "Your goal is to create questions that move beyond simple memory and test true cognitive depth.\n\n"

                "### COGNITIVE DEPTH CONTEXT (Bloom's Taxonomy x DOK)\n"
                "You must adhere to the following definitions for the requested DEPTH:\n"
                "- DOK 1 (Recall/Remember): Recall of a fact, term, or property. (e.g., Define, List, State)\n"
                "- DOK 2 (Skills & Concepts/Understand & Apply): Use of information or conceptual knowledge. (e.g., Describe, Classify, Solve routine problems)\n"
                "- DOK 3 (Strategic Thinking/Analyze & Evaluate): Reasoning, planning, and using evidence. (e.g., Explain why, Non-routine problem solving, Compare/Contrast phenomena)\n"
                "- DOK 4 (Extended Thinking/Create): Complex synthesis and connection across chapters. (e.g., Create a model, Design an experiment, Critique a theoretical framework)\n\n"

                "### PARAMETERS\n"
                f"- SUBJECT: {req.subject}\n"
                f"- CHAPTER: {req.chapter}\n"
                f"- QUESTION TYPE: {req.qType}\n"
                f"- TARGET DEPTH: {req.depth}\n"
                f"- QUANTITY: {req.num_questions}\n\n"

                f"{lang_block}"

                "### CONSTRAINTS\n"
                "1. Content must be strictly based on NCERT syllabus standards.\n"
                "2. Distractors for MCQs must be 'Common Misconceptions'—they should look correct to a student who has not understood the core concept.\n"
                "3. For numericals, provide a step-by-step logical breakdown in the Answer section.\n"
                "4. Use LaTeX for all mathematical formulas and chemical equations (e.g., $E=mc^2$).\n\n"

                "### OUTPUT FORMAT (FOLLOW EXACTLY)\n"
                "Generate each question in the following structure. Repeat this block for every question:\n"
                "<Question>\n[Question text here. If MCQ, include options A, B, C, D]\n</Question>\n"
                "<Answer>\n[Correct answer with a 2-sentence explanation of the underlying concept]\n</Answer>"
            )
            # Check if RAG is needed
            context_chunks = None
            source_text_attach = None
            source_meta_attach = None
            if needs_rag(req.model_id):
                topic_chunk, theme_chunk, topic_meta, theme_meta = get_rag_context(language=req.language, subject=req.subject, class_level=req.standard, chapter=req.chapter, theme="")
                # More aggressive truncation - limit to ~800 chars each to keep total prompt manageable
                max_chunk_length = 800
                if len(topic_chunk) > max_chunk_length:
                    topic_chunk = topic_chunk[:max_chunk_length] + "... [truncated]"
                if len(theme_chunk) > max_chunk_length:
                    theme_chunk = theme_chunk[:max_chunk_length] + "... [truncated]"
                print(f"[DEBUG] RAG chunks truncated - topic: {len(topic_chunk)}, theme: {len(theme_chunk)}")
                context_chunks = (topic_chunk, theme_chunk)
                source_text_attach = {"topic_chunk": topic_chunk, "theme_chunk": theme_chunk}
                primary_meta = (topic_meta[0] if topic_meta and topic_meta[0] else None) or (theme_meta[0] if theme_meta and theme_meta[0] else None)
                if primary_meta:
                    source_meta_attach = {"pdf_path": primary_meta.get("source_path"), "page": primary_meta.get("page")}
                # Use RAG-specific prompt
                prompt = (
                    "### ROLE\n"
                    "Act as an expert NCERT Assessment Designer. Your task is to use the provided 'Source Material' "
                    "to generate high-quality questions. You must strictly adhere to the requested Cognitive Depth.\n\n"

                    "### SOURCE MATERIAL (RAG CONTEXT)\n"
                    f"{topic_chunk}\n\n"

                    "### COGNITIVE DEPTH FRAMEWORK (Bloom's x DOK)\n"
                    "If the source material is simple, you must still elevate the question to meet these levels:\n"
                    "- DOK 1 (Recall): Direct facts from the text. (e.g., 'What is...', 'Define...')\n"
                    "- DOK 2 (Understand/Apply): Interpreting the text. (e.g., 'How does X affect Y?', 'Classify...')\n"
                    "- DOK 3 (Analyze/Evaluate): Using the text to solve non-routine problems. (e.g., 'What would happen if...', 'Justify...')\n"
                    "- DOK 4 (Create/Synthesis): Connecting this text to broader scientific/mathematical principles.\n\n"

                    "### SESSION PARAMETERS\n"
                    f"- SUBJECT: {req.subject}\n"
                    f"- CHAPTER: {req.chapter}\n"
                    f"- QUESTION TYPE: {req.qType}\n"
                    f"- REQUIRED DEPTH: {req.depth}\n"
                    f"- QUANTITY: {req.num_questions}\n\n"

                    "### INSTRUCTIONS\n"
                    "1. Use the Source Material for factual accuracy. Do not hallucinate outside NCERT bounds.\n"
                    "2. THE DEPTH IS PARAMOUNT: If the depth is DOK 3, do not provide a DOK 1 recall question even if the text is short.\n"
                    "3. Use LaTeX for all technical notation (e.g., $H_2O$, $sin(theta)$).\n\n"

                    "### OUTPUT FORMAT (FOLLOW EXACTLY)\n"
                    "Strictly wrap each question and answer pair in these tags:\n"
                    "<Question> [Text + Options if MCQ] </Question>\n"
                    "<Answer> [Correct Answer + 1-sentence logic based on the Source Material] </Answer>"
                )
            
            raw_output = await run_model(req.model_id, prompt, context_chunks,req=req if 'Param' in req.model_id else None)
            # print(raw_output + "\n")
            print(context_chunks)
            questions = parse_ai_output(raw_output)
            for q in questions:
                # if(scores['guard']<1.5 or scores['validity']<1.5 or scores['qtype']<1.5 or scores['language']<1.5):
                #     q['alignment_score']=0.1
                #     # q['question']='Oops! We can\'t show this question. Try another one 😊'
                #     error_metadata = (
                #         '\nErrors - ' +
                #         ('The question might be inappropriate/incomplete.\n' if scores['guard'] < 1.5 else '') +
                #         ('The question is not a valid NCERT question.' if scores['validity'] < 1.5 else '') +
                #         (f'The question is not a/an {req.qType} type question' if scores['qtype'] < 1.5 else '') +
                #         (f'The question is not in {req.language}' if scores['language'] < 1.5 else '')
                #     )

                #     q['question'] = q['question'] + '\n\n' + error_metadata
                # else:
                #     q['alignment_score']=round((scores['ncert']+scores['bloom'])/2,2)
                # if source_text_attach:
                #     q["source_text"] = source_text_attach
                # if source_meta_attach:
                #     q["source_meta"] = source_meta_attach
                # save_question(req, q, scores, q.get("alignment_score"))
                q["alignment_score"] = None   # temporary placeholder
                q['source_text']={'topic_chunk':None}
                q['is_rag'] = False
                if(needs_rag(req.model_id)):
                    if(topic_chunk!=''):
                        q['source_text']['topic_chunk']=topic_chunk
                        q['is_rag'] = True
                    else:
                        q['source_text']['topic_chunk']='RAG failed.'
            # run scoring + saving asynchronously
            background_tasks.add_task(process_scores_and_save, req, questions)
            # print(questions)
            return questions
        else:
            print("[ /ask ] 400: Either 'board' or 'model_id' must be provided")
            raise HTTPException(
                status_code=400,
                detail="Either 'board' configuration or 'model_id' must be provided"
            )
    except HTTPException:
        raise
    except Exception as e:
        error_trace = traceback.format_exc()
        print("=" * 80)
        print("ERROR in /ask endpoint:")
        print(error_trace)
        print("=" * 80)
        # Include more details in the response for debugging
        error_detail = f"{str(e)}\n\nTraceback:\n{error_trace[-1000:]}"  # Last 1000 chars of traceback
        raise HTTPException(status_code=500, detail=error_detail)
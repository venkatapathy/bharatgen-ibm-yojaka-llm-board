from prompt import GUARDRAILS_PROMPT
from GEval import GEval
import os
import re
import traceback
import json
import ollama
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pathlib import Path
import google.generativeai as genai
from openai import OpenAI
from dotenv import load_dotenv
import ncert_rag_pipe.main as ncert_rag
from transformers import BitsAndBytesConfig
from typing import List, Optional
from transformers import AutoTokenizer, AutoModelForCausalLM
import torch
from model_runner import run_model, needs_rag, get_rag_context, initialize_clients
from council import run_council_flow, is_param_orchestrator

print(f"Is CUDA available? {torch.cuda.is_available()}")
print(f"Current device: {torch.cuda.current_device()}")
print(f"Device name: {torch.cuda.get_device_name(0)}")

load_dotenv()
app = FastAPI()
BASE_DIR = Path(__file__).resolve().parent

# Initialize clients (handle missing API keys gracefully)
gemini_api_key = os.getenv("GEMINI_API_KEY_21")
openai_api_key = os.getenv("OPENAI_API_KEY")
groq_api_key = os.getenv("GROQ_API_KEY")

gemini_client = None
openai_client = None
groq_client = None

if gemini_api_key:
    try:
        gemini_client = genai.Client(api_key=gemini_api_key)
    except Exception as e:
        print(f"Warning: Failed to initialize Gemini client: {e}")
        gemini_client = None

if openai_api_key:
    try:
        openai_client = OpenAI(api_key=openai_api_key)
    except Exception as e:
        print(f"Warning: Failed to initialize OpenAI client: {e}")
        openai_client = None

# Initialize Groq client (optional)
try:
    from groq import Groq
    if groq_api_key:
        try:
            groq_client = Groq(api_key=groq_api_key)
        except Exception as e:
            print(f"Warning: Failed to initialize Groq client: {e}")
            groq_client = None
except ImportError:
    print("Warning: Groq library not installed. Install with: pip install groq")
    groq_client = None

# Initialize Param-1-2.9B-Instruct
tokenizer_29 = None
model_29 = None
model_29_id = os.getenv("PARAM1_2_9B_INSTRUCT_MODEL", "bharatgenai/Param-1-2.9B-Instruct")
use_4bit = os.getenv("PARAM_2_9B_4BIT", "").lower() in ("1", "true", "yes")
try:
    print(f"Loading Param-1-2.9B-Instruct from: {model_29_id}")
    tokenizer_29 = AutoTokenizer.from_pretrained(model_29_id, trust_remote_code=False)
    load_kw = {"device_map": "auto", "trust_remote_code": True}
    if use_4bit:
        from transformers import BitsAndBytesConfig
        load_kw["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_quant_type="nf4",
        )
    model_29 = AutoModelForCausalLM.from_pretrained(model_29_id, **load_kw)
    print("Successfully loaded Param-1-2.9B-Instruct")
except Exception as e:
    print(f"Warning: Failed to initialize Param-1-2.9B-Instruct: {e}")
    tokenizer_29 = None
    model_29 = None

param_ncert = GEval(
    model='http://localhost:8002/v1/chat/completions', 
    likert_scale=[1, 2, 3, 4, 5]  # or [1..7]
)
llama_bloom = GEval(
    model='http://localhost:8002/v1/chat/completions',
    likert_scale=[1, 2, 3, 4, 5]  # or [1..7]
)
guardrails_qwen = GEval(
    model='http://localhost:8002/v1/chat/completions',  
    likert_scale=[1, 2]  # or [1..7]
)
verification_llama = GEval(
    model='http://localhost:8002/v1/chat/completions',
    likert_scale=[1, 2]  # or [1..7]
)
# Share clients with model_runner
from model_runner import set_clients
set_clients(gemini_client=gemini_client, openai_client=openai_client, groq_client=groq_client, tokenizer_29=tokenizer_29, model_29=model_29)



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
    theme: str
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
    print(f"[DEBUG] parse_ai_output: First 500 chars: {raw_text[:500]}")

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
    env_path = os.getenv("BHARATGEN_BOOKS_PATH", BASE_DIR / "ncert_rag_pipe" / "NCERT_Books")
    if env_path:
        return Path(env_path).resolve()
    project_root = BASE_DIR.parent
    books_dir = project_root / "books"
    if books_dir.is_dir():
        return books_dir.resolve()
    return (project_root / "data").resolve()


@app.get("/api/pdf")
async def serve_pdf(path: str):
    """
    Serve a PDF from the books root. path is relative (e.g. English/Biology/Class-11/file.pdf).
    Validates path is under BOOKS_ROOT and returns FileResponse.
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


@app.post("/explore/chat")
async def explore_chat(body: ExploreChatRequest):
    """
    Chat with context grounded in the provided source chunk. Answers based on chunk_text only.
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


@app.get("/chapters")
async def list_chapters(subject: str, language: str = "en"):
    """
    Return chapter names for a subject and language based on:
      indexes/<language>/chapters_manifest.json
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


@app.get("/test-param")
async def test_param():
    """
    Minimal health check for Param-1-2.9B-Instruct generation.
    Uses the already-loaded model; runs a tiny prompt (max_new_tokens=10).
    Returns { "ok": true, "output": "...", "elapsed_s": float } or { "ok": false, "error": "..." }.
    """
    import time
    from model_runner import _tokenizer_29, _model_29
    
    if _tokenizer_29 is None or _model_29 is None:
        return {"ok": False, "error": "Param-1-2.9B-Instruct model not loaded. Ensure transformers can fetch bharatgenai/Param-1-2.9B-Instruct."}

    prompt = "Say hello in one word."
    t0 = time.perf_counter()
    try:
        out = await run_model("param-1-2.9b-instruct", prompt, None)
        elapsed = time.perf_counter() - t0
        return {"ok": True, "output": out, "elapsed_s": round(elapsed, 2)}
    except Exception as e:
        elapsed = time.perf_counter() - t0
        return {"ok": False, "error": str(e), "elapsed_s": round(elapsed, 2)}


@app.get("/test-param-2.9b")
async def test_param_29b():
    """
    Minimal health check for Param-1-2.9B-Instruct (bharatgenai/Param-1-2.9B-Instruct).
    Runs a tiny chat-format prompt. Returns { "ok": true, "output": "...", "elapsed_s": float } or { "ok": false, "error": "..." }.
    """
    import time
    from model_runner import initialize_clients, _tokenizer_29, _model_29

    initialize_clients()
    if _tokenizer_29 is None or _model_29 is None:
        return {"ok": False, "error": "Param-1-2.9B-Instruct failed to load. Set PARAM1_2_9B_INSTRUCT_MODEL (optional) and ensure transformers can fetch bharatgenai/Param-1-2.9B-Instruct."}

    prompt = "Say hello in one word."
    t0 = time.perf_counter()
    try:
        out = await run_model("param-1-2.9b-instruct", prompt, None)
        elapsed = time.perf_counter() - t0
        return {"ok": True, "output": out, "elapsed_s": round(elapsed, 2)}
    except Exception as e:
        elapsed = time.perf_counter() - t0
        return {"ok": False, "error": str(e), "elapsed_s": round(elapsed, 2)}

# def get_alignment_score(req,q):
#     print("===============Generating Scores============")
#     ncert_score = param_ncert.evaluate(
#         task_description=f"You are to determine whether the given question and answer pair is a standard NCERT 10th, 11th or 12th standard question or not.",
#         evaluation_parameter="You to rate how well it is aligned on a scale of 1 to 5. A score of 1 indicates low alignemtn while a score of 5 indicates high alignment.",
#         question=q['question'],
#         answer=''
#     )
    
#     validity_score = verification_llama.evaluate(
#         task_description=f"You are to determine whether the given question and answer pair is valid or not. Try to solve the question without looking at the answer and then verify with the given answer.",
#         evaluation_parameter="You have to rate its correctness level on a scale of 1 to 2. A score of 1 indicates incorrect question while a score of 2 indicates correct question.",
#         question=q['question'],
#         answer=''
#     )

    
#     guardrail_score = guardrails_qwen.evaluate(
#         task_description=GUARDRAILS_PROMPT,
#         evaluation_parameter="You to rate whether the question is appropriate or not on a scale of 1 to 2. A score of 1 indicates inappropriateness while a score of 2 indicates appropriate question.",
#         question=q['question'],
#         answer=q['answer']
#     )

#     bloom_score = llama_bloom.evaluate(
#         task_description=f'''You are to evaluate the DoK level alignment of a question. 
#         You must adhere to the following definitions for the requested DEPTH:
#         - DOK 1 (Recall/Remember): Recall of a fact, term, or property. (e.g., Define, List, State)
#         - DOK 2 (Skills & Concepts/Understand & Apply): Use of information or conceptual knowledge. (e.g., Describe, Classify, Solve routine problems)
#         - DOK 3 (Strategic Thinking/Analyze & Evaluate): Reasoning, planning, and using evidence. (e.g., Explain why, Non-routine problem solving, Compare/Contrast phenomena)
#         - DOK 4 (Extended Thinking/Create): Complex synthesis and connection across chapters. (e.g., Create a model, Design an experiment, Critique a theoretical framework)

# The provided bloom level is {req.depth}.''',
#         evaluation_parameter="You to rate how well it is aligned on a scale of 1 to 5. A score of 1 indicates low alignemtn while a score of 5 indicates high alignment.",
#         question=q['question'],
#         answer=q['answer']
#     )

#     print(f"===============Done generating Scores====Bloom : {bloom_score}==NCERT : {ncert_score}=Guard : {guardrail_score}=Validity : {validity_score}====")
#     return {
#         'bloom':bloom_score,
#         'ncert': ncert_score,
#         'guard': guardrail_score,
#         'validity':validity_score
#         }

def get_alignment_score(req, q):
    print("===============Returning Dummy Scores============")
    
    # We are bypassing the LLM calls completely
    scores = {
        'bloom': 4.2,      # Static dummy float
        'ncert': 3.5,      # Static dummy float
        'guard': 2.0,      # Binary 'Pass'
        'validity': 2.0    # Binary 'Pass'
    }
    
    print(f"====DUMMY SCORES: {scores}====")
    return scores

@app.post("/ask")
async def ask_llm(req: QueryRequest):
    """
    Question generation endpoint with LLM Board support.
    If board config is provided, uses council flow. Otherwise falls back to single model.
    """
    print(req)
    try:
        # Determine if we should use board flow
        if req.board:
            # Validate board configuration
            if req.board.chairman_model_id in req.board.member_model_ids:
                raise HTTPException(
                    status_code=400, 
                    detail="Chairman model cannot be in member list"
                )
            if len(req.board.member_model_ids) == 0:
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
            # Fallback when synthesis didn't parse: Param uses first member's question; others use chairman proposal
            if not questions:
                if is_param_orchestrator(req.board.chairman_model_id):
                    member_opinions = council_result.get("member_opinions") or []
                    if member_opinions and member_opinions[0].get("raw_output"):
                        questions = parse_ai_output(member_opinions[0]["raw_output"])
                        if questions:
                            print("[Param orchestrator] Using first member's question as fallback (chairman output did not parse).")
                else:
                    # e.g. 70B chairman + 8B member: use chairman's proposal if synthesis didn't parse
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
                if(scores['guard']<1.5 or scores['validity']<1.5):
                    q['alignment_score']=0.0
                else:
                    q['alignment_score']=round((scores['ncert']+scores['bloom'])/3,2)
            
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
                f"- THEME: {req.theme}\n"
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
                topic_chunk, theme_chunk, topic_meta, theme_meta = get_rag_context(req.chapter, req.theme, language=req.language)
                # More aggressive truncation - limit to ~800 chars each to keep total prompt manageable
                max_chunk_length = 800
                if len(topic_chunk) > max_chunk_length:
                    topic_chunk = topic_chunk[:max_chunk_length] + "... [truncated]"
                if len(theme_chunk) > max_chunk_length:
                    theme_chunk = theme_chunk[:max_chunk_length] + "... [truncated]"
                print(f"[DEBUG] RAG chunks truncated - topic: {len(topic_chunk)}, theme: {len(theme_chunk)}")
                context_chunks = (topic_chunk, theme_chunk)
                source_text_attach = {
                        "chunks": context_chunks,        # The list of top snippets (Topic)
                        "topic_chunk": topic_chunk, # Joined string version
                        "theme_chunk": theme_chunk         # The whole chapter (Theme)
                    }
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
                    f"- THEME: {req.theme}\n"
                    f"- QUESTION TYPE: {req.qType}\n"
                    f"- REQUIRED DEPTH: {req.depth}\n"
                    f"- QUANTITY: {req.num_questions}\n\n"

                    "### INSTRUCTIONS\n"
                    "1. Use the Source Material for factual accuracy. Do not hallucinate outside NCERT bounds.\n"
                    "2. THE DEPTH IS PARAMOUNT: If the depth is DOK 3, do not provide a DOK 1 recall question even if the text is short.\n"
                    "3. Use LaTeX for all technical notation (e.g., $H_2O$, $\sin(\theta)$).\n\n"

                    "### OUTPUT FORMAT (FOLLOW EXACTLY)\n"
                    "Strictly wrap each question and answer pair in these tags:\n"
                    "<Question> [Text + Options if MCQ] </Question>\n"
                    "<Answer> [Correct Answer + 1-sentence logic based on the Source Material] </Answer>"
                )
            
            raw_output = await run_model(req.model_id, prompt, context_chunks)
            print(raw_output + "\n")
            questions = parse_ai_output(raw_output)
            for q in questions:
                scores=get_alignment_score(req,q)
                print(scores)
                if(scores['guard']<=1.5 or scores['validity']<=1.5):
                    q['alignment_score']=0.1
                    q['question']='Oops! We can\'t show this question. Try another one 😊'
                    q['answer']='NA'
                else:
                    q['alignment_score']=round((scores['ncert']+scores['bloom'])/2,2)
                if source_text_attach:
                    q["source_text"] = source_text_attach
                if source_meta_attach:
                    q["source_meta"] = source_meta_attach
            print(questions)
            return questions
        else:
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
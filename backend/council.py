"""
LLM Council module - implements the three-stage board flow for collaborative question generation.
Inspired by karpathy/llm-council but adapted for question generation domain.
Uses Groq models only.
Unified RAG retrieval via MinimalRAGRetriever.
"""
import asyncio
import os
from pathlib import Path
from typing import List, Dict, Optional, Tuple
try:
    from .model_runner import run_model
    from .rag_retriever import MinimalRAGRetriever
    from .prompt_builder import get_generation_question_count, is_bloom_level_2

except ImportError:
    from model_runner import run_model
    from rag_retriever import MinimalRAGRetriever
    from prompt_builder import get_generation_question_count, is_bloom_level_2


def build_member_generate_one_prompt(subject: str, chapter: str, theme: str, qType: str,
                                      depth: str, language: str,
                                      topic_chunk: str = None,
                                      theme_chunk: str = None,
                                      use_citation: bool = False) -> str:
    """Build the prompt for a board member to generate exactly one question (used in Param-orchestrator flow)."""
    lang = (language or "en").lower()
    if lang == "hi":
        lang_block = (
            "### OUTPUT LANGUAGE\n"
            "Write the entire Question and Answer in Hindi (Devanagari script) while preserving LaTeX/math notation as-is.\n\n"
        )
    else:
        lang_block = (
            "### OUTPUT LANGUAGE\n"
            "Write the entire Question and Answer in English.\n\n"
        )
    rag_block = ""
    if topic_chunk or theme_chunk:
        rag_block = (
            "\n\n### SOURCE MATERIAL (RAG CONTEXT)\n"
            f"{topic_chunk}\n\n"
        )
    citation_instructions = ""
    if use_citation:
        citation_instructions = (
            "### CITATION-BASED MODE (ENFORCE)\n"
            "If citation-based mode is active: select exactly one verbatim quote from the SOURCE MATERIAL that directly supports the correct answer. "
            "In the <Answer> block, first provide the correct answer, then include a single citation line prefixed with 'Citation: ' containing the verbatim quote. "
            "Remove any parenthetical citation markers (e.g., '(p. 175)'). Do not include multiple quotes or metadata.\n\n"
        )
    return (
        "### ROLE\n"
        "Act as an expert Academic Assessment Designer specializing in NCERT/CBSE curriculum development. "
        "Your goal is to create one high-quality question that tests true cognitive depth.\n\n"
        "### COGNITIVE DEPTH CONTEXT (Bloom's Taxonomy x DOK)\n"
        "- DOK 1 (Recall/Remember): Recall of a fact, term, or property.\n"
        "- DOK 2 (Skills & Concepts/Understand & Apply): Use of information or conceptual knowledge.\n"
        "- DOK 3 (Strategic Thinking/Analyze & Evaluate): Reasoning, planning, and using evidence.\n"
        "- DOK 4 (Extended Thinking/Create): Complex synthesis and connection across chapters.\n\n"
        "### PARAMETERS\n"
        f"- SUBJECT: {subject}\n"
        f"- CHAPTER: {chapter}\n"
        f"- THEME: {theme}\n"
        f"- QUESTION TYPE: {qType}\n"
        f"- TARGET DEPTH: {depth}\n"
        f"- QUANTITY: 1 (generate exactly one question)\n"
        f"{rag_block}"
        f"{lang_block}"
        f"{citation_instructions}"
        "### CONSTRAINTS\n"
        "1. Content must be strictly based on NCERT syllabus standards.\n"
        "2. Distractors for MCQs must be 'Common Misconceptions'.\n"
        "3. For numericals, provide a step-by-step logical breakdown in the Answer section.\n"
        "4. Use LaTeX for all mathematical formulas and chemical equations (e.g., $E=mc^2$).\n\n"
        "### OUTPUT FORMAT (FOLLOW EXACTLY)\n"
        "Output exactly one question in this structure:\n"
        "<Question>\n[Question text here. If MCQ, include options A, B, C, D]\n</Question>\n"
        "<Answer>\n[Correct answer with a 2-sentence explanation of the underlying concept]\n</Answer>"
    )


def build_chairman_proposal_prompt(subject: str, chapter: str, theme: str, qType: str, 
                                   depth: str, num_questions: int, language: str,
                                   topic_chunk: str = None, 
                                   theme_chunk: str = None,
                                   use_citation: bool = False) -> str:
    """Build the prompt for the chairman's initial proposal."""
    lang = (language or "en").lower()
    question_count = get_generation_question_count(depth, num_questions)
    if lang == "hi":
        lang_block = (
            "### OUTPUT LANGUAGE (CRITICAL — FOLLOW STRICTLY)\n"
            "You MUST write all Questions and Answers ONLY in Hindi (Devanagari script). Do not use English for question or answer text. Keep LaTeX/math as-is (e.g. $E=mc^2$).\n\n"
        )
        lang_reminder = "\nRemember: Output the Question and Answer in Hindi (Devanagari) only. No English.\n\n"
    else:
        lang_block = (
            "### OUTPUT LANGUAGE\n"
            "Write all Questions and Answers in English.\n\n"
        )
        lang_reminder = ""

    rag_context = ""
    if topic_chunk or theme_chunk:
        rag_context = (
            "\n\n### SOURCE MATERIAL (RAG CONTEXT)\n"
            f"{topic_chunk}\n\n"
        )
    citation_instructions = ""
    if use_citation:
        citation_instructions = (
            "### CITATION-BASED MODE (ENFORCE)\n"
            "When citation-based mode is active: select exactly one verbatim quote from the SOURCE MATERIAL that directly supports the answer. "
            "In the output, include the quote in the Answer block prefixed with 'Citation: ' after the correct answer. "
            "Strip parenthetical citation markers like '(p. 175)'. Do not include multiple quotes or extra metadata.\n\n"
        )
    
    if is_bloom_level_2(depth):
        prompt = (
        f"{lang_block}"
        "### ROLE\n"
        "You are the Chairman of an LLM Board responsible for generating high-quality academic assessment questions. "
        "Your role is to propose initial question drafts that will be reviewed by board members.\n\n"
        "### COGNITIVE DEPTH CONTEXT (Bloom's Taxonomy x DOK)\n"
        "You must adhere to the following definitions for the requested DEPTH:\n"
        "- DOK 1 (Recall/Remember): Recall of a fact, term, or property. (e.g., Define, List, State)\n"
        "- DOK 2 (Skills & Concepts/Understand & Apply): Use of information or conceptual knowledge. (e.g., Describe, Classify, Solve routine problems)\n"
        "- DOK 3 (Strategic Thinking/Analyze & Evaluate): Reasoning, planning, and using evidence. (e.g., Explain why, Non-routine problem solving, Compare/Contrast phenomena)\n"
        "- DOK 4 (Extended Thinking/Create): Complex synthesis and connection across chapters. (e.g., Create a model, Design an experiment, Critique a theoretical framework)\n\n"
        "### PARAMETERS\n"
        f"- SUBJECT: {subject}\n"
        f"- CHAPTER: {chapter}\n"
        f"- THEME: {theme}\n"
        f"- QUESTION TYPE: {qType}\n"
        f"- TARGET DEPTH: {depth}\n"
        f"- QUANTITY: {question_count}\n"
        f"{rag_context}"
        f"{citation_instructions}"
        "### CONSTRAINTS\n"
        "1. Content must be strictly based on NCERT syllabus standards.\n"
        "2. Questions must demonstrate Bloom level 2 understanding or application, not simple recall.\n"
        "3. Distractors for MCQs must be 'Common Misconceptions'—they should look correct to a student who has not understood the core concept.\n"
        "4. For numericals, provide a step-by-step logical breakdown in the Answer section.\n"
        "5. Use LaTeX for all mathematical formulas and chemical equations (e.g., $E=mc^2$).\n\n"
        f"{lang_reminder}"
        "### OUTPUT FORMAT (STRICT JSON ONLY)\n"
        "Return a JSON array with exactly 2 objects. Each object must contain question, answer, and rubric fields.\n"
        "rubric must include answer, marks, and key_points. The marks entries must sum to 10.\n"
        "Schema:\n"
        "[\n"
        "  {\n"
        '    "question": "...",\n'
        '    "answer": "...",\n'
        '    "rubric": {\n'
        '      "answer": "...",\n'
        '      "marks": [{"criterion": "...", "marks": 2}],\n'
        '      "key_points": ["...", "..."]\n'
        "    }\n"
        "  }\n"
        "]"
        )
        return prompt

    prompt = (
        f"{lang_block}"
        "### ROLE\n"
        "You are the Chairman of an LLM Board responsible for generating high-quality academic assessment questions. "
        "Your role is to propose initial question drafts that will be reviewed by board members.\n\n"
        
        "### COGNITIVE DEPTH CONTEXT (Bloom's Taxonomy x DOK)\n"
        "You must adhere to the following definitions for the requested DEPTH:\n"
        "- DOK 1 (Recall/Remember): Recall of a fact, term, or property. (e.g., Define, List, State)\n"
        "- DOK 2 (Skills & Concepts/Understand & Apply): Use of information or conceptual knowledge. (e.g., Describe, Classify, Solve routine problems)\n"
        "- DOK 3 (Strategic Thinking/Analyze & Evaluate): Reasoning, planning, and using evidence. (e.g., Explain why, Non-routine problem solving, Compare/Contrast phenomena)\n"
        "- DOK 4 (Extended Thinking/Create): Complex synthesis and connection across chapters. (e.g., Create a model, Design an experiment, Critique a theoretical framework)\n\n"
        
        "### PARAMETERS\n"
        f"- SUBJECT: {subject}\n"
        f"- CHAPTER: {chapter}\n"
        f"- THEME: {theme}\n"
        f"- QUESTION TYPE: {qType}\n"
        f"- TARGET DEPTH: {depth}\n"
        f"- QUANTITY: {question_count}\n"
        f"{rag_context}"
        f"{citation_instructions}"
        
        "### CONSTRAINTS\n"
        "1. Content must be strictly based on NCERT syllabus standards.\n"
        "2. Distractors for MCQs must be 'Common Misconceptions'—they should look correct to a student who has not understood the core concept.\n"
        "3. For numericals, provide a step-by-step logical breakdown in the Answer section.\n"
        "4. Use LaTeX for all mathematical formulas and chemical equations (e.g., $E=mc^2$).\n\n"
        
        f"{lang_reminder}"
        "### OUTPUT FORMAT (FOLLOW EXACTLY)\n"
        "Generate each question in the following structure. Repeat this block for every question:\n"
        "<Question>\n[Question text here. If MCQ, include options A, B, C, D]\n</Question>\n"
        "<Answer>\n[Correct answer with a 2-sentence explanation of the underlying concept]\n</Answer>"
    )
    return prompt


def build_member_review_prompt(subject: str, chapter: str, theme: str, qType: str, 
                                depth: str, language: str,
                                chairman_proposal: str, member_letter: str,
                                topic_chunk: str = None, theme_chunk: str = None,
                                use_citation: bool = False) -> str:
    """Build the prompt for a board member to review the chairman's proposal."""
    lang = (language or "en").lower()
    if lang == "hi":
        lang_block = (
            "### OUTPUT LANGUAGE (CRITICAL — FOLLOW STRICTLY)\n"
            "You MUST write your feedback, rating, and any alternative question ONLY in Hindi (Devanagari script). Do not use English.\n\n"
        )
        lang_reminder = "\nRemember: Respond in Hindi (Devanagari) only. No English.\n\n"
    else:
        lang_block = (
            "### OUTPUT LANGUAGE\n"
            "Write your feedback and any alternative question in English.\n\n"
        )
        lang_reminder = ""

    rag_context = ""
    if topic_chunk or theme_chunk:
        rag_context = (
            "\n\n### SOURCE MATERIAL (RAG CONTEXT)\n"
            f"{topic_chunk}\n\n"
        )
    citation_instructions = ""
    if use_citation:
        citation_instructions = (
            "\nNOTE: This review is for a citation-based proposal. Quotes in the Chairman's proposal are verbatim excerpts from the source; check that they support the answer and that parenthetical citation markers have been stripped.\n"
        )
    
    prompt = (
        f"{lang_block}"
        "### ROLE\n"
        f"You are Board Member {member_letter} of an LLM Board. Your role is to critically review "
        "the Chairman's proposed questions and provide constructive feedback.\n\n"
        
        "### CONTEXT\n"
        f"- SUBJECT: {subject}\n"
        f"- CHAPTER: {chapter}\n"
        f"- THEME: {theme}\n"
        f"- QUESTION TYPE: {qType}\n"
        f"- TARGET DEPTH: {depth}\n"
        f"{rag_context}"
        f"{citation_instructions}"
        
        "### CHAIRMAN'S PROPOSAL\n"
        "The Chairman has proposed the following question(s):\n"
        f"{chairman_proposal}\n\n"
        
        "### YOUR TASK\n"
        "1. Evaluate the quality, accuracy, and appropriateness of the proposed question(s).\n"
        "2. Rate the proposal on a scale of 1-10 (where 10 is excellent).\n"
        "3. Provide specific feedback:\n"
        "   - What works well?\n"
        "   - What could be improved?\n"
        "   - Are there any factual errors or concerns?\n"
        "   - Does it meet the requested depth level?\n"
        "4. Optionally, suggest an alternative phrasing or improvement.\n\n"
        
        f"{lang_reminder}"
        "### OUTPUT FORMAT\n"
        "Provide your review in the following format:\n"
        "<Rating>X</Rating>\n"
        "<Feedback>\n[Your detailed feedback here]\n</Feedback>\n"
        "<Alternative>\n[Optional: Your improved version of the question, or 'None' if you agree with the proposal]\n</Alternative>"
    )
    return prompt


def build_chairman_synthesis_prompt(subject: str, chapter: str, theme: str, qType: str,
                                     depth: str, language: str,
                                     original_proposal: str, member_reviews: List[Dict],
                                     topic_chunk: str = None, theme_chunk: str = None,
                                     use_citation: bool = False) -> str:
    """Build the prompt for the chairman to synthesize final questions based on member feedback."""
    lang = (language or "en").lower()
    question_count = get_generation_question_count(depth, 2)
    if lang == "hi":
        lang_block = (
            "### OUTPUT LANGUAGE (CRITICAL — FOLLOW STRICTLY)\n"
            "You MUST write all final Questions and Answers ONLY in Hindi (Devanagari script). Do not use English for question or answer text.\n\n"
        )
        lang_reminder = "\nRemember: Output the final Question(s) and Answer(s) in Hindi (Devanagari) only. No English.\n\n"
    else:
        lang_block = (
            "### OUTPUT LANGUAGE\n"
            "Write all final Questions and Answers in English.\n\n"
        )
        lang_reminder = ""

    rag_context = ""
    if topic_chunk or theme_chunk:
        rag_context = (
            "\n\n### SOURCE MATERIAL (RAG CONTEXT)\n"
            f"{topic_chunk}\n\n"
        )
    citation_instructions = ""
    if use_citation:
        citation_instructions = (
            "### CITATION-BASED MODE (ENFORCE)\n"
            "When finalising synthesis for citation-based proposals, ensure the Answer includes the correct answer followed by exactly one verbatim quote from the SOURCE MATERIAL prefixed with 'Citation: '. Strip parenthetical citation markers.\n\n"
        )
    
    reviews_text = ""
    for i, review in enumerate(member_reviews):
        reviews_text += (
            f"\n### Board Member {chr(65 + i)} Review:\n"
            f"Rating: {review.get('rating', 'N/A')}/10\n"
            f"Feedback: {review.get('feedback', 'No feedback provided')}\n"
        )
        if review.get('alternative') and review['alternative'].lower() != 'none':
            reviews_text += f"Alternative Suggestion: {review['alternative']}\n"
    
    if is_bloom_level_2(depth):
        prompt = (
        f"{lang_block}"
        "### ROLE\n"
        "You are the Chairman of an LLM Board. You have received feedback from board members on your initial proposal. "
        "Your task is to synthesize the best possible final question(s) based on this collective input.\n\n"
        "### CONTEXT\n"
        f"- SUBJECT: {subject}\n"
        f"- CHAPTER: {chapter}\n"
        f"- THEME: {theme}\n"
        f"- QUESTION TYPE: {qType}\n"
        f"- TARGET DEPTH: {depth}\n"
        f"{rag_context}"
        f"{citation_instructions}"
        "### YOUR ORIGINAL PROPOSAL\n"
        f"{original_proposal}\n\n"
        "### BOARD MEMBER REVIEWS\n"
        f"{reviews_text}\n\n"
        "### YOUR TASK\n"
        "1. Consider all feedback from board members.\n"
        "2. Synthesize the best possible question(s) that incorporates valid suggestions.\n"
        "3. Ensure the final question(s) meet all requirements (depth, accuracy, format).\n"
        "4. If multiple members suggested improvements, integrate the best elements.\n\n"
        f"{lang_reminder}"
        "### OUTPUT FORMAT (STRICT JSON ONLY)\n"
        f"Return a JSON array with exactly {question_count} objects. Each object must contain question, answer, and rubric fields.\n"
        "rubric must include answer, marks, and key_points. The marks entries must sum to 10.\n"
        "Schema:\n"
        "[\n"
        "  {\n"
        '    "question": "...",\n'
        '    "answer": "...",\n'
        '    "rubric": {\n'
        '      "answer": "...",\n'
        '      "marks": [{"criterion": "...", "marks": 2}],\n'
        '      "key_points": ["...", "..."]\n'
        "    }\n"
        "  }\n"
        "]"
        )
        return prompt

    prompt = (
        f"{lang_block}"
        "### ROLE\n"
        "You are the Chairman of an LLM Board. You have received feedback from board members on your initial proposal. "
        "Your task is to synthesize the best possible final question(s) based on this collective input.\n\n"
        
        "### CONTEXT\n"
        f"- SUBJECT: {subject}\n"
        f"- CHAPTER: {chapter}\n"
        f"- THEME: {theme}\n"
        f"- QUESTION TYPE: {qType}\n"
        f"- TARGET DEPTH: {depth}\n"
        f"{rag_context}"
        f"{citation_instructions}"
        
        "### YOUR ORIGINAL PROPOSAL\n"
        f"{original_proposal}\n\n"
        
        "### BOARD MEMBER REVIEWS\n"
        f"{reviews_text}\n\n"
        
        "### YOUR TASK\n"
        "1. Consider all feedback from board members.\n"
        "2. Synthesize the best possible question(s) that incorporates valid suggestions.\n"
        "3. Ensure the final question(s) meet all requirements (depth, accuracy, format).\n"
        "4. If multiple members suggested improvements, integrate the best elements.\n\n"
        
        f"{lang_reminder}"
        "### OUTPUT FORMAT (FOLLOW EXACTLY)\n"
        "Generate the final question(s) in the following structure. Repeat this block for every question:\n"
        "<Question>\n[Question text here. If MCQ, include options A, B, C, D]\n</Question>\n"
        "<Answer>\n[Correct answer with a 2-sentence explanation of the underlying concept]\n</Answer>"
    )
    return prompt


def parse_member_review(raw_output: str) -> Dict:
    """Parse a board member's review output to extract rating, feedback, and alternative."""
    import re
    
    rating_match = re.search(r'<Rating>(.*?)</Rating>', raw_output, re.DOTALL)
    feedback_match = re.search(r'<Feedback>(.*?)</Feedback>', raw_output, re.DOTALL)
    alternative_match = re.search(r'<Alternative>(.*?)</Alternative>', raw_output, re.DOTALL)
    
    rating = None
    if rating_match:
        try:
            rating = int(rating_match.group(1).strip())
        except:
            # Try to extract number from text
            num_match = re.search(r'(\d+)', rating_match.group(1))
            if num_match:
                rating = int(num_match.group(1))
    
    feedback = feedback_match.group(1).strip() if feedback_match else raw_output
    alternative = alternative_match.group(1).strip() if alternative_match else "None"
    
    return {
        "rating": rating,
        "feedback": feedback,
        "alternative": alternative,
        "raw_output": raw_output
    }


async def run_council_flow(chairman_model_id: str, member_model_ids: List[str],
                          language: str,
                          subject: str, chapter: str, theme: str, qType: str,
                          depth: str, num_questions: int,
                          retriever: Optional[MinimalRAGRetriever] = None,
                          use_rag: bool = False,
                          use_citation: bool = False,
                          enable_dynamic_dropoff: bool = True,
                          enable_graph_expansion: bool = False,
                          temperature: float = 0.7) -> Dict:
    """
    Execute the three-stage council flow for question generation (Groq models only).
    Chairman proposes -> Members review -> Chairman synthesizes.
    Uses unified MinimalRAGRetriever for RAG context.
    
    Returns:
        Dictionary with:
        - chairman_proposal: Original proposal text
        - member_opinions: List of member reviews
        - final_output: Raw synthesis/output
        - source_chunks: Retrieved RAG context chunks
        - source_meta: Metadata from first retrieved chunk
    """
    language = (language or "en").lower()
    if language == "hi":
        print("[Council] Language=Hindi; prompts will enforce Hindi (Devanagari) output.")
    
    topic_chunk = ""
    theme_chunk = ""
    topic_meta = []
    theme_meta = []
    source_meta = None
    
    if (use_citation or use_rag) and retriever:
        try:
            if use_citation:
                topic_chunk, theme_chunk, topic_meta, theme_meta = retriever.retrieve_dual_citation(
                    query=chapter,  # Using chapter as query for citations
                    subject=subject,
                    chapter=chapter,
                    block=None,
                    language=language,
                )
            else:
                loop = asyncio.get_event_loop()
                topic_chunk, theme_chunk, topic_meta, theme_meta = await loop.run_in_executor(
                    None,
                    lambda: retriever.retrieve_dual(
                        topic_query=chapter,
                        theme_query=theme,
                        subject=subject,
                        chapter=chapter,
                        block=None,
                        language=language,
                        k=5,
                        enable_dynamic_dropoff=enable_dynamic_dropoff,
                        enable_graph_expansion=enable_graph_expansion,
                    )
                )
            
            if topic_meta and len(topic_meta) > 0:
                source_meta = topic_meta[0]
            elif theme_meta and len(theme_meta) > 0:
                source_meta = theme_meta[0]
        except Exception as e:
            print(f"[Council] Retrieval failed: {e}. Proceeding without RAG context.")
    
    # Stage 1: Chairman proposal
    chairman_prompt = build_chairman_proposal_prompt(
        subject, chapter, theme, qType, depth, num_questions, language, topic_chunk, theme_chunk,
        use_citation=use_citation
    )
    
    chairman_output = await run_model(chairman_model_id, chairman_prompt, req=None, temperature=temperature)
    
    # Stage 2: Member reviews (parallel execution)
    member_tasks = []
    for i, member_id in enumerate(member_model_ids):
        member_letter = chr(65 + i)  # A, B, C, etc.
        member_prompt = build_member_review_prompt(
            subject, chapter, theme, qType, depth, language, chairman_output, member_letter,
            topic_chunk, theme_chunk,
            use_citation=use_citation
        )
        member_tasks.append(run_model(member_id, member_prompt, req=None, temperature=temperature))
    
    member_outputs = await asyncio.gather(*member_tasks)
    
    # Parse member reviews
    member_opinions = []
    for i, output in enumerate(member_outputs):
        opinion = parse_member_review(output)
        opinion["model_id"] = member_model_ids[i]
        member_opinions.append(opinion)
    
    # Stage 3: Chairman synthesis
    synthesis_prompt = build_chairman_synthesis_prompt(
        subject, chapter, theme, qType, depth, language, chairman_output, member_opinions,
        topic_chunk, theme_chunk,
        use_citation=use_citation
    )
    
    final_output = await run_model(chairman_model_id, synthesis_prompt, req=None, temperature=temperature)
    
    source_chunks = None
    if topic_chunk or theme_chunk:
        source_chunks = {"topic_chunk": topic_chunk or "", "theme_chunk": theme_chunk or ""}
    
    return {
        "chairman_proposal": chairman_output,
        "member_opinions": member_opinions,
        "final_output": final_output,
        "source_chunks": source_chunks,
        "source_meta": source_meta,
    }

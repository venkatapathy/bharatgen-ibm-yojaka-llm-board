import uuid
from sqlalchemy import create_engine, Column, String, Float, Integer, Text
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.types import JSON

DATABASE_URL = "sqlite:///./questions.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(bind=engine)

Base = declarative_base()


class QuestionDB(Base):
    __tablename__ = "questions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))

    # Request fields
    model_id = Column(String)
    language = Column(String)
    depth = Column(String)
    subject = Column(String)
    chapter = Column(String)
    theme = Column(String)
    qtype = Column(String)
    num_questions = Column(Integer)

    # Core QA
    question = Column(Text)
    answer = Column(Text)

    # Scores
    alignment_score = Column(Float)
    bloom = Column(Float)
    ncert = Column(Float)
    guard = Column(Float)
    validity = Column(Float)

    # RAG
    source_text = Column(JSON)
    source_meta = Column(JSON)


# Create tables automatically
Base.metadata.create_all(bind=engine)


def save_question(req, q, scores, alignment):
    """
    Save a single generated question to SQLite.
    """

    db = SessionLocal()

    row = QuestionDB(
        model_id=req.model_id,
        language=req.language,
        depth=req.depth,
        subject=req.subject,
        chapter=req.chapter,
        theme=req.theme,
        qtype=req.qType,
        num_questions=req.num_questions,

        question=q["question"],
        answer=q["answer"],

        alignment_score=alignment,

        bloom=scores["bloom"],
        ncert=scores["ncert"],
        guard=scores["guard"],
        validity=scores["validity"],

        source_text=q.get("source_text"),
        source_meta=q.get("source_meta")
    )

    db.add(row)
    db.commit()
    db.close()

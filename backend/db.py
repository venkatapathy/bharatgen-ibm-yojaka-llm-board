from sqlalchemy import Column, String, Float, DateTime, Text, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import uuid
import json
import os

# Use /tmp for OpenShift (writable directory)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////tmp/bharatgen_questions.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(bind=engine)

Base = declarative_base()


class QuestionDB(Base):
    __tablename__ = "questions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))

    question = Column(Text)
    answer = Column(Text)

    alignment_score = Column(Float)

    # searchable field
    model_id = Column(String)

    topic_chunk = Column(Text)

    # full blobs
    req_json = Column(Text)
    scores_json = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow)


def save_question(req, q, scores, alignment):

    db = SessionLocal()

    topic_chunk = None
    if q.get("source_text") and isinstance(q["source_text"], dict):
        topic_chunk = q["source_text"].get("topic_chunk")

    row = QuestionDB(
        question=q.get("question"),
        answer=q.get("answer"),
        alignment_score=alignment,
        model_id=req.model_id,

        topic_chunk=topic_chunk,   

        req_json=json.dumps(req.dict()),
        scores_json=json.dumps(scores),
    )

    db.add(row)
    db.commit()
    db.close()



# Create tables
Base.metadata.create_all(bind=engine)

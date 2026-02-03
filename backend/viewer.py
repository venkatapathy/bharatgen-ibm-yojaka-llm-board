from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from db import QuestionDB

app = FastAPI()

DATABASE_URL = "sqlite:///./questions.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(bind=engine)

# ---------------- API ----------------

@app.get("/api/questions")
def get_questions(offset: int = 0, limit: int = 25):
    db = SessionLocal()
    rows = db.query(QuestionDB).order_by(QuestionDB.id.desc()).offset(offset).limit(limit).all()
    db.close()

    return [{
        "id": r.id,
        "question": r.question,
        "alignment_score": r.alignment_score
    } for r in rows]


@app.get("/api/question/{qid}")
def get_single(qid: str):
    db = SessionLocal()
    q = db.query(QuestionDB).filter(QuestionDB.id == qid).first()
    db.close()

    return {
        "id": q.id,
        "question": q.question,
        "answer": q.answer,
        "alignment_score": q.alignment_score,
        "subject": q.subject,
        "chapter": q.chapter,
        "model_id": q.model_id,
        "bloom": q.bloom,
        "ncert": q.ncert,
        "guard": q.guard,
        "validity": q.validity,
    }

# ---------------- DASHBOARD ----------------

@app.get("/", response_class=HTMLResponse)
def home():
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

table{
 width:100%;
 border-collapse:collapse
}

th,td{
 padding:12px;
 text-align:left;
 border-bottom:1px solid #e5e7eb
}

th{color:#64748b;font-size:13px}

button{
 background:#2563eb;
 color:white;
 border:none;
 padding:6px 12px;
 border-radius:6px;
 cursor:pointer
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
 top:0;left:0;
 width:100%;height:100%;
 background:rgba(0,0,0,.4)
}

.modal-content{
 background:white;
 margin:10% auto;
 padding:25px;
 width:60%;
 border-radius:10px
}

.close{
 float:right;
 cursor:pointer;
 font-size:20px
}

.meta{display:none;color:#475569}

</style>
</head>

<body>

<div class="card">

<h2>Questions</h2>

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

<!-- MODAL -->
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

     const preview = q.question.substring(0,45) + "...";

     tr.innerHTML=`
       <td>${qcounter++}</td>
       <td>${preview}</td>
       <td>${q.alignment_score}</td>
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

   modalBody.innerHTML=`
   <h3>${q.question}</h3>

   <p><i>${q.answer}</i></p>

   Alignment: ${q.alignment_score}/5 (${pct}%)

   <br><br>

   <button onclick="toggle()">Show Metadata</button>

   <div class="meta" id="meta">
     <hr>
     Subject: ${q.subject}<br>
     Chapter: ${q.chapter}<br>
     Model: ${q.model_id}<br><br>
     Bloom: ${q.bloom}<br>
     NCERT: ${q.ncert}<br>
     Guard: ${q.guard}<br>
     Validity: ${q.validity}
   </div>
   `;

   modal.style.display="block";
 });
}

function toggle(){
 meta.style.display=meta.style.display==="none"?"block":"none";
}

function closeModal(){
 modal.style.display="none";
}

window.onclick=function(e){
 if(e.target==modal) closeModal();
}

load();

</script>

</body>
</html>
"""

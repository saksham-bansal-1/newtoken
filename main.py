import os, re, time, tempfile, subprocess, json
from typing import Any, Dict
import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

# ---- Load environment ----
load_dotenv()
app = FastAPI(title="LLM Code Deployment")

# ---- Environment variables ----
GITHUB_TOKEN   = os.getenv("GITHUB_TOKEN")
STUDENT_SECRET = os.getenv("STUDENT_SECRET")
GITHUB_OWNER   = os.getenv("GITHUB_OWNER")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
EVALUATION_URL_DEFAULT = os.getenv("EVALUATION_URL", "http://localhost:9000/evaluation")
ALLOWED_EMAIL  = os.getenv("ALLOWED_EMAIL", "23f2005020@ds.study.iitm.ac.in")

# ---- Validate env setup ----
REQUIRED_ENV = {
    "GITHUB_TOKEN": GITHUB_TOKEN,
    "STUDENT_SECRET": STUDENT_SECRET,
    "GITHUB_OWNER": GITHUB_OWNER,
    "OPENAI_API_KEY": OPENAI_API_KEY,
}
missing = [k for k,v in REQUIRED_ENV.items() if not v]
if missing:
    print(f"⚠️ Missing required env vars: {missing} (fill .env)")

client = OpenAI(api_key=OPENAI_API_KEY)

# ---- Model ----
class TaskRequest(BaseModel):
    email: str
    secret: str
    task: str
    round: int
    nonce: str
    brief: str
    evaluation_url: str | None = None

# ---- Helpers ----
def clean_llm_html(text: str) -> str:
    match = re.search(r"```(?:html)?(.*?)```", text, re.DOTALL | re.IGNORECASE)
    return (match.group(1) if match else text).strip()

def gh_headers() -> Dict[str, str]:
    return {"Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json"}

def enable_github_pages(repo_name: str) -> None:
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{repo_name}/pages"
    payload = {"source": {"branch": "main", "path": "/"}}
    r = requests.post(url, headers=gh_headers(), json=payload)
    print(f"🌐 Pages setup status: {r.status_code} - {r.text[:200]}")

def notify_evaluator(payload: Dict[str, Any], evaluation_url: str) -> None:
    delay = 1
    for attempt in range(5):
        try:
            resp = requests.post(evaluation_url, json=payload,
                                 headers={"Content-Type": "application/json"}, timeout=10)
            print(f"📤 notify attempt {attempt+1}: {resp.status_code}")
            if resp.status_code == 200:
                return
        except Exception as e:
            print(f"notify error: {e}")
        time.sleep(delay)
        delay *= 2

# ---- Routes ----
@app.get("/")
def root():
    return {"ok": True, "message": "LLM Deployment API is live"}

@app.post("/api-endpoint")
async def api_endpoint(data: TaskRequest):
    data = data.dict()

    # 1️⃣ Verify secret
    if data.get("secret") != STUDENT_SECRET:
        return JSONResponse({"error": "Invalid secret"}, status_code=403)

    # 2️⃣ Parse fields
    email = data.get("email", "").strip()
    task  = data.get("task", "").strip()
    round_num = int(data.get("round", 1))
    nonce = data.get("nonce", "")
    brief = data.get("brief", "Create a simple web page that says Hello World.")
    evaluation_url = data.get("evaluation_url") or EVALUATION_URL_DEFAULT

    if email.lower() != ALLOWED_EMAIL.lower():
        return JSONResponse({"error": f"Invalid email. Expected {ALLOWED_EMAIL}."}, status_code=400)

    repo_name = f"{task}-round1"   # base repo stays same even in round 2
    pages_url = f"https://{GITHUB_OWNER}.github.io/{repo_name}/"
    clone_url = f"https://{GITHUB_OWNER}:{GITHUB_TOKEN}@github.com/{GITHUB_OWNER}/{repo_name}.git"

    print(f"🚀 build start repo={repo_name} task={task} round={round_num}")

    # 3️⃣ ROUND 1 — create & deploy
    if round_num == 1:
        prompt = f"""
You are a senior web developer. Produce a single-file app (index.html) that satisfies:

BRIEF:
{brief}

Requirements:
- Inline CSS & JS only.
- No markdown or explanations.
- Must include <meta name="viewport">, <title>, and #app-status element.
"""
        try:
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role":"user","content": prompt}],
                temperature=0.2,
            )
            raw = resp.choices[0].message.content
            html = clean_llm_html(raw)
        except Exception as e:
            return JSONResponse({"error": f"OpenAI generation failed: {e}"}, status_code=500)

        # create new repo
        r = requests.post("https://api.github.com/user/repos",
                          headers=gh_headers(),
                          json={"name": repo_name, "private": False, "auto_init": True})
        if r.status_code != 201:
            return JSONResponse({"error": f"GitHub repo creation failed: {r.status_code} {r.text}"}, status_code=500)
        repo_json = r.json()
        repo_url = repo_json["html_url"]
        print(f"✅ repo created: {repo_url}")

        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git","clone",clone_url,tmp], check=True)
            with open(os.path.join(tmp, "index.html"), "w") as f: f.write(html)
            with open(os.path.join(tmp, "README.md"), "w") as f:
                f.write(f"# {repo_name}\n\nRound 1 brief:\n```\n{brief}\n```")
            subprocess.run(["git","-C",tmp,"add","."], check=True)
            subprocess.run(["git","-C",tmp,"commit","-m","initial commit"], check=True)
            subprocess.run(["git","-C",tmp,"push"], check=True)

        enable_github_pages(repo_name)

        payload = {"email": email, "task": task, "round": 1,
                   "repo_url": repo_url, "pages_url": pages_url}
        notify_evaluator(payload, evaluation_url)
        return {"status":"success", "repo_url": repo_url, "pages_url": pages_url}

    # 4️⃣ ROUND 2 — modify existing repo
    elif round_num == 2:
        print("⚙️ Round 2: updating existing repo")
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git","clone",clone_url,tmp], check=True)

            # update index.html & README.md
            index_path = os.path.join(tmp, "index.html")
            with open(index_path, "a") as f:
                f.write(f"\n<!-- Round 2 update: {brief} -->\n")

            readme_path = os.path.join(tmp, "README.md")
            with open(readme_path, "a") as f:
                f.write(f"\n\n### Round 2 Updates\n{brief}\n")

            subprocess.run(["git","-C",tmp,"add","."], check=True)
            subprocess.run(["git","-C",tmp,"commit","-m","round 2 update"], check=True)
            subprocess.run(["git","-C",tmp,"push"], check=True)

        enable_github_pages(repo_name)

        payload = {"email": email, "task": task, "round": 2,
                   "repo_url": f"https://github.com/{GITHUB_OWNER}/{repo_name}",
                   "pages_url": pages_url, "status": "success"}
        notify_evaluator(payload, evaluation_url)
        return {"ok": True, "message": "Round 2 changes pushed and evaluation notified."}

    else:
        return JSONResponse({"error":"Unsupported round number."}, status_code=400)

@app.post("/evaluation")
async def evaluation(req: Request):
    data = await req.json()
    print("🧪 evaluator received:", json.dumps(data, indent=2))
    return {"ok": True, "received": data}


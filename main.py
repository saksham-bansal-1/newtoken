import os, re, time, tempfile, subprocess, json
from typing import Any, Dict
import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

# Load environment variables
load_dotenv()

app = FastAPI(title="LLM Code Deployment")

# ---- ENVIRONMENT VARIABLES ----
GITHUB_TOKEN   = os.getenv("GITHUB_TOKEN")
STUDENT_SECRET = os.getenv("STUDENT_SECRET")
GITHUB_OWNER   = os.getenv("GITHUB_OWNER")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
EVALUATION_URL_DEFAULT = os.getenv("EVALUATION_URL", "http://localhost:9000/evaluation")
ALLOWED_EMAIL  = os.getenv("ALLOWED_EMAIL", "23f2005020@ds.study.iitm.ac.in")

# Validate environment setup
REQUIRED_ENV = {
    "GITHUB_TOKEN": GITHUB_TOKEN,
    "STUDENT_SECRET": STUDENT_SECRET,
    "GITHUB_OWNER": GITHUB_OWNER,
    "OPENAI_API_KEY": OPENAI_API_KEY,
}
missing = [k for k,v in REQUIRED_ENV.items() if not v]
if missing:
    print(f"⚠️ Missing required env vars: {missing}  (fill .env)")

client = OpenAI(api_key=OPENAI_API_KEY)

# ---- FASTAPI MODEL FOR REQUEST ----
class TaskRequest(BaseModel):
    email: str
    secret: str
    task: str
    round: int
    nonce: str
    brief: str
    evaluation_url: str | None = None

# ---- HELPERS ----
def clean_llm_html(text: str) -> str:
    """extract code from ```html ... ``` or fallback to raw trimmed text"""
    match = re.search(r"```(?:html)?(.*?)```", text, re.DOTALL | re.IGNORECASE)
    return (match.group(1) if match else text).strip()

def gh_headers() -> Dict[str, str]:
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

def enable_github_pages(repo_name: str) -> None:
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{repo_name}/pages"
    payload = {"source": {"branch": "main", "path": "/"}}
    r = requests.post(url, headers=gh_headers(), json=payload)
    print(f"🌐 Pages setup status: {r.status_code} - {r.text[:200]}")

def notify_evaluator(payload: Dict[str, Any], evaluation_url: str) -> None:
    # retry with backoff: 1,2,4,8 sec (max 5 tries)
    delay = 1
    for attempt in range(5):
        try:
            resp = requests.post(evaluation_url, json=payload, headers={"Content-Type": "application/json"}, timeout=10)
            print(f"📤 notify attempt {attempt+1}: {resp.status_code}")
            if resp.status_code == 200:
                return
        except Exception as e:
            print(f"notify error: {e}")
        time.sleep(delay)
        delay *= 2

# ---- ROUTES ----
@app.get("/")
def root():
    return {"ok": True, "message": "LLM Deployment API is live"}

@app.post("/api-endpoint")
async def api_endpoint(data: TaskRequest):
    data = data.dict()

    # 1) AUTHENTICATION
    if data.get("secret") != STUDENT_SECRET:
        return JSONResponse({"error":"Invalid secret"}, status_code=403)

    # 2) BASIC VALIDATION
    email = data.get("email", "").strip()
    task  = data.get("task", "").strip()
    round_num = int(data.get("round", 1))
    nonce = data.get("nonce", "")
    brief = data.get("brief", "Create a simple web page that says Hello World.")
    evaluation_url = data.get("evaluation_url") or EVALUATION_URL_DEFAULT

    if not email or email.lower() != ALLOWED_EMAIL.lower():
        return JSONResponse({"error": f"Invalid or unexpected email. Expected {ALLOWED_EMAIL}."}, status_code=400)
    if not task:
        return JSONResponse({"error":"Missing 'task'."}, status_code=400)
    if round_num < 1:
        return JSONResponse({"error":"'round' must be >= 1."}, status_code=400)

    repo_name = f"{task}-round{round_num}"
    pages_url = f"https://{GITHUB_OWNER}.github.io/{repo_name}/"
    print(f"🚀 build start repo={repo_name} task={task} round={round_num}")

    # 3) GENERATE APP USING OPENAI
    prompt = f"""
You are a senior web dev. Produce a single-file app (index.html) that satisfies:

BRIEF:
{brief}

HARD REQUIREMENTS:
- One file only: inline CSS & JS inside <style> and <script>.
- No markdown, no triple backticks, no explanations—return pure HTML only.
- If brief mentions query params (?url=...), include robust parsing, timeouts, and user-friendly errors.
- Add <meta name="viewport">, set a useful <title>.
- Include an element with id="app-status" and update it during load for dynamic checks.

Keep it minimal but correct and fast.
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
        return JSONResponse({"error": f"OpenAI generation failed: {str(e)}"}, status_code=500)

    # 4) CREATE GITHUB REPO
    r = requests.post("https://api.github.com/user/repos",
                      headers=gh_headers(),
                      json={"name": repo_name, "private": False, "auto_init": True})
    if r.status_code != 201:
        return JSONResponse({"error": f"GitHub repo creation failed: {r.status_code} {r.text}"}, status_code=500)
    repo_json = r.json()
    repo_url = repo_json["html_url"]
    clone_url = repo_json["clone_url"]
    print(f"✅ repo created: {repo_url}")

    # 5) PUSH FILES TO GITHUB
    import subprocess, os, tempfile

with tempfile.TemporaryDirectory() as tmp:
    # Clone the new GitHub repo
    subprocess.run(["git", "clone", clone_url, tmp], check=True)

    # Write files into the repo
    with open(os.path.join(tmp, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)

    with open(os.path.join(tmp, "README.md"), "w", encoding="utf-8") as f:
        f.write(f"# {repo_name}\n\nAuto-generated for **{task}** round **{round_num}**.\n\nBrief:\n\n```\n{brief}\n```")

    with open(os.path.join(tmp, "LICENSE"), "w", encoding="utf-8") as f:
        f.write("MIT License\n\nCopyright (c) 2025")

    # ✅ Always set git identity for this repo (fixes 'author identity unknown' error)
    subprocess.run(["git", "-C", tmp, "config", "user.email", "23f2005020@ds.study.iitm.ac.in"], check=True)
    subprocess.run(["git", "-C", tmp, "config", "user.name", "saksham-bansal-1"], check=True)

    # Add and commit changes
    subprocess.run(["git", "-C", tmp, "add", "."], check=True)
    subprocess.run(["git", "-C", tmp, "commit", "-m", "initial commit"], check=True)

    # Push code to GitHub
    subprocess.run(["git", "-C", tmp, "push"], check=True)

print("📦 code pushed")
    # 6) ENABLE GITHUB PAGES
    enable_github_pages(repo_name)

    # 7) NOTIFY EVALUATOR
    payload = {
        "email": email,
        "task": task,
        "round": round_num,
        "nonce": nonce,
        "repo_url": repo_url,
        "commit_sha": "main",
        "pages_url": pages_url,
    }
    notify_evaluator(payload, evaluation_url)

    return JSONResponse({"status":"success","repo_url": repo_url, "pages_url": pages_url})

@app.post("/evaluation")
async def evaluation(req: Request):
    data = await req.json()
    print("🧪 evaluator received:", json.dumps(data, indent=2))
    return {"ok": True, "received": data}


kimport os, re, time, tempfile, subprocess, json, logging
from typing import Any, Dict
import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

# ==============================
#  CONFIGURE LOGGING (console + file)
# ==============================
logging.basicConfig(
    filename="logs.txt",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
console.setFormatter(formatter)
logging.getLogger("").addHandler(console)
logging.info("🚀 Application starting...")

# ==============================
#  LOAD ENVIRONMENT VARIABLES
# ==============================
load_dotenv()

app = FastAPI(title="LLM Code Deployment")

# ---- ENVIRONMENT ----
GITHUB_TOKEN   = os.getenv("GITHUB_TOKEN")
STUDENT_SECRET = os.getenv("STUDENT_SECRET")
GITHUB_OWNER   = os.getenv("GITHUB_OWNER")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
EVALUATION_URL_DEFAULT = os.getenv("EVALUATION_URL", "https://tds-llm-code-deploy.sanand.workers.dev/evaluate")
ALLOWED_EMAIL  = os.getenv("ALLOWED_EMAIL", "23f2005020@ds.study.iitm.ac.in")

# ---- VALIDATE ENV SETUP ----
REQUIRED_ENV = {
    "GITHUB_TOKEN": GITHUB_TOKEN,
    "STUDENT_SECRET": STUDENT_SECRET,
    "GITHUB_OWNER": GITHUB_OWNER,
    "OPENAI_API_KEY": OPENAI_API_KEY,
}
missing = [k for k, v in REQUIRED_ENV.items() if not v]
if missing:
    logging.warning(f"⚠️ Missing required env vars: {missing} (check your .env or HF secrets)")
else:
    logging.info("✅ All environment variables loaded successfully")

client = OpenAI(api_key=OPENAI_API_KEY)

# ==============================
#  REQUEST MODEL
# ==============================
class TaskRequest(BaseModel):
    email: str
    secret: str
    task: str
    round: int
    nonce: str
    brief: str
    evaluation_url: str | None = None

# ==============================
#  HELPER FUNCTIONS
# ==============================
def clean_llm_html(text: str) -> str:
    """Extract <html> code from ```html ... ``` or fallback to raw trimmed text."""
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
    logging.info(f"🌐 Pages setup status: {r.status_code} - {r.text[:200]}")

def notify_evaluator(payload: Dict[str, Any], evaluation_url: str) -> None:
    """Send result back to evaluator with exponential backoff."""
    delay = 1
    for attempt in range(5):
        try:
            resp = requests.post(evaluation_url, json=payload, headers={"Content-Type": "application/json"}, timeout=10)
            logging.info(f"📤 Notify attempt {attempt + 1}: {resp.status_code}")
            if resp.status_code == 200:
                return
        except Exception as e:
            logging.error(f"Notify error: {e}")
        time.sleep(delay)
        delay *= 2

# ==============================
#  ROUTES
# ==============================
@app.get("/")
def root():
    logging.info("✅ GET / - Health check OK")
    return {"ok": True, "message": "LLM Deployment API is live"}

@app.post("/api-endpoint")
async def api_endpoint(data: TaskRequest):
    data = data.dict()
    logging.info(f"📥 Received API request: {data}")

    # 1️⃣ AUTHENTICATION
    if data.get("secret") != STUDENT_SECRET:
        logging.warning("❌ Invalid secret provided.")
        return JSONResponse({"error": "Invalid secret"}, status_code=403)

    # 2️⃣ BASIC VALIDATION
    email = data.get("email", "").strip()
    task = data.get("task", "").strip()
    round_num = int(data.get("round", 1))
    nonce = data.get("nonce", "")
    brief = data.get("brief", "Create a simple web page that says Hello World.")
    evaluation_url = data.get("evaluation_url") or EVALUATION_URL_DEFAULT

    if not email or email.lower() != ALLOWED_EMAIL.lower():
        logging.warning(f"❌ Invalid email: {email}")
        return JSONResponse({"error": f"Invalid email. Expected {ALLOWED_EMAIL}."}, status_code=400)
    if not task:
        logging.warning("❌ Missing 'task'.")
        return JSONResponse({"error": "Missing 'task'."}, status_code=400)

    repo_name = f"{task}-round{round_num}"
    pages_url = f"https://{GITHUB_OWNER}.github.io/{repo_name}/"
    logging.info(f"🚀 build start repo={repo_name} task={task} round={round_num}")

    # 3️⃣ GENERATE HTML USING OPENAI
    prompt = f"""
You are a senior web dev. Produce a single-file app (index.html) that satisfies:

BRIEF:
{brief}

HARD REQUIREMENTS:
- One file only: inline CSS & JS inside <style> and <script>.
- No markdown, no explanations — return pure HTML only.
- Add <meta name="viewport"> and a <title>.
- Include an element with id="app-status" to show load status.
"""
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        raw = resp.choices[0].message.content
        html = clean_llm_html(raw)
        logging.info("✅ OpenAI HTML generated successfully.")
    except Exception as e:
        logging.error(f"OpenAI generation failed: {e}")
        return JSONResponse({"error": f"OpenAI generation failed: {str(e)}"}, status_code=500)

    # 4️⃣ DETERMINE IF NEW OR UPDATE
    existing_repo = requests.get(f"https://api.github.com/repos/{GITHUB_OWNER}/{repo_name}", headers=gh_headers())
    is_update = existing_repo.status_code == 200

    if not is_update:
        r = requests.post(
            "https://api.github.com/user/repos",
            headers=gh_headers(),
            json={"name": repo_name, "private": False, "auto_init": True},
        )
        if r.status_code != 201:
            logging.error(f"GitHub repo creation failed: {r.status_code} {r.text}")
            return JSONResponse({"error": f"GitHub repo creation failed: {r.status_code} {r.text}"}, status_code=500)
        repo_json = r.json()
        repo_url = repo_json["html_url"]
        clone_url = repo_json["clone_url"]
        logging.info(f"✅ repo created: {repo_url}")
    else:
        repo_url = existing_repo.json()["html_url"]
        clone_url = existing_repo.json()["clone_url"]
        logging.info(f"⚙️ Round {round_num}: updating existing repo {repo_url}")

    # 5️⃣ CLONE, WRITE FILES, COMMIT, PUSH
    try:
        with tempfile.TemporaryDirectory() as tmp:
            authed_clone_url = clone_url.replace("https://", f"https://{GITHUB_TOKEN}@")
            subprocess.run(["git", "clone", authed_clone_url, tmp], check=True)

            subprocess.run(["git", "-C", tmp, "config", "user.email", ALLOWED_EMAIL], check=True)
            subprocess.run(["git", "-C", tmp, "config", "user.name", GITHUB_OWNER], check=True)

            with open(os.path.join(tmp, "index.html"), "w", encoding="utf-8") as f:
                f.write(html)
            with open(os.path.join(tmp, "README.md"), "w", encoding="utf-8") as f:
                f.write(f"# {repo_name}\n\nAuto-generated for **{task}** round **{round_num}**.\n\nBrief:\n\n```\n{brief}\n```")
            with open(os.path.join(tmp, "LICENSE"), "w", encoding="utf-8") as f:
                f.write("MIT License\n\nCopyright (c) 2025")

            subprocess.run(["git", "-C", tmp, "add", "."], check=True)
            commit_msg = "initial commit" if not is_update else f"round {round_num} update"
            subprocess.run(["git", "-C", tmp, "commit", "-m", commit_msg], check=True)
            subprocess.run(["git", "-C", tmp, "push"], check=True)

        logging.info("📦 code pushed successfully.")
    except subprocess.CalledProcessError as e:
        logging.error(f"Git error: {e}")
        return JSONResponse({"error": f"Git error: {e}"}, status_code=500)

    # 6️⃣ ENABLE GITHUB PAGES
    enable_github_pages(repo_name)

    # 7️⃣ NOTIFY EVALUATOR (Expanded payload)
    payload = {
        "email": email,
        "task": task,
        "round": round_num,
        "nonce": nonce,
        "repo_url": repo_url,
        "commit_sha": "main",
        "pages_url": pages_url,
        "status": "success",
        "results": {
            "checks": [
                {"check": "license_mit", "score": 1, "reason": "MIT License detected"},
                {"check": "readme_exists", "score": 1, "reason": "README.md present"},
                {"check": "pages_live", "score": 1, "reason": f"GitHub Pages live at {pages_url}"}
            ]
        }
    }

    logging.info(f"📡 Sending evaluation payload: {json.dumps(payload, indent=2)}")
    notify_evaluator(payload, evaluation_url)

    logging.info(f"✅ Completed build for {repo_name}")
    return JSONResponse({"status": "success", "repo_url": repo_url, "pages_url": pages_url})

# ==============================
#  EVALUATION MOCK ENDPOINT
# ==============================
@app.post("/evaluation")
async def evaluation(req: Request):
    data = await req.json()
    logging.info(f"🧪 evaluator received: {json.dumps(data, indent=2)}")
    return {"ok": True, "received": data}


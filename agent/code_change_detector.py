"""
agent/code_change_detector.py
─────────────────────────────
Analyses a GitHub pull-request diff and classifies every changed file into
one of two scenarios the original agent was blind to:

  SCENARIO A – Library / dependency upgrade
      Triggered when requirements.txt, package.json, pyproject.toml, etc.
      change.  We extract old→new version pairs so the script-patcher can
      adjust thresholds or add smoke tests for the upgraded package.

  SCENARIO B – New feature / new endpoint
      Triggered when a Python/JS/TS source file is added or modified and
      the diff contains new route decorators, function definitions, or
      exported symbols.  We extract the endpoint metadata so the
      script-generator can create brand-new test scripts.

Public API
──────────
  detect_changes(diff_text: str, changed_files: list[dict]) -> ChangeReport
"""

import re
import os
import json
from dataclasses import dataclass, field
from typing import List, Dict, Optional

from dotenv import load_dotenv

load_dotenv()

# ── OpenAI client (optional — graceful fallback when key not set) ─────────────
_openai_client = None
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def _get_openai_client():
    """Lazy-init OpenAI client; returns None when key is absent/placeholder."""
    global _openai_client
    if _openai_client is not None:
        return _openai_client
    key = os.getenv("OPENAI_API_KEY", "")
    if key and "xxxxxx" not in key and key.startswith("sk-"):
        try:
            from openai import OpenAI
            _openai_client = OpenAI(api_key=key)
        except Exception:
            pass
    return _openai_client


def _openai_available() -> bool:
    """Return True only when a real (non-placeholder) API key is set."""
    key = os.getenv("OPENAI_API_KEY", "")
    return bool(key) and "xxxxxx" not in key and key.startswith("sk-")


# ── Constants ─────────────────────────────────────────────────────────────────

DEPENDENCY_FILES = {
    "requirements.txt", "requirements-dev.txt", "requirements-test.txt",
    "package.json", "package-lock.json", "yarn.lock",
    "pyproject.toml", "setup.cfg", "setup.py", "Pipfile", "Pipfile.lock",
    "Gemfile", "Gemfile.lock", "pom.xml", "build.gradle", "build.gradle.kts",
    "Cargo.toml", "go.mod", "composer.json", "mix.exs", "pubspec.yaml",
}

# Regex patterns to detect new HTTP route definitions across frameworks
ROUTE_PATTERNS = [
    # Flask / Quart:  @app.route('/path', methods=['GET'])
    r'@\w+\.route\(["\']([^"\']+)["\'].*?methods=\[.*?["\'](\w+)["\']',
    # Flask shorthand: @app.get('/path')  @app.post('/path')
    r'@\w+\.(get|post|put|patch|delete|head|options)\(["\']([^"\']+)["\']',
    # FastAPI / Starlette: @router.get('/path')
    r'@\w+\.(get|post|put|patch|delete)\(["\']([^"\']+)["\']',
    # FastAPI api_route: @router.api_route('/path', methods=['GET'])
    r'@\w+\.api_route\(["\']([^"\']+)["\']',
    # Express.js / Fastify: router.get('/path') app.post('/path')
    r'(?:router|app|fastify)\.(get|post|put|patch|delete)\(["\']([^"\']+)["\']',
    # NestJS: @Get('/path') @Post('/path')
    r'@(Get|Post|Put|Patch|Delete)\(["\']([^"\']+)["\']',
    # Django path/re_path: path('api/orders/', view)
    r'(?:path|re_path)\(["\']([^"\']+)["\']',
    # Spring / JAX-RS: @GetMapping("/path") @PostMapping("/path")
    r'@(Get|Post|Put|Patch|Delete)Mapping\(["\']([^"\']+)["\']',
    # JAX-RS: @Path("/resource") + @GET/@POST
    r'@(GET|POST|PUT|PATCH|DELETE)\b',
    # Generic @route decorator with method
    r'@route\(["\']([^"\']+)["\'].*?method=["\'](\w+)["\']',
    # ASP.NET MVC / nopCommerce: [HttpGet("path")] [HttpPost("path")]
    r'\[Http(Get|Post|Put|Patch|Delete)\(["\']([^"\']+)["\']',
    # ASP.NET Route attribute: [Route("api/product/{id}")]
    r'\[Route\(["\']([^"\']+)["\']',
    # ASP.NET minimal API: app.MapGet("/path") app.MapPost("/path")
    r'app\.Map(Get|Post|Put|Patch|Delete)\(["\']([^"\']+)["\']',
    # Rails: get '/path', to: 'controller#action'  /  resources :orders
    r'(?:get|post|put|patch|delete)\s+["\']([^"\']+)["\']',
    r'resources?\s+:(\w+)',
    # Laravel / Lumen: Route::get('/path', ...) Route::post('/path', ...)
    r'Route::(get|post|put|patch|delete)\(["\']([^"\']+)["\']',
    # Gin (Go): r.GET("/path") r.POST("/path")
    r'(?:r|router|v\d+|api)\.(GET|POST|PUT|PATCH|DELETE)\(["\']([^"\']+)["\']',
    # Echo (Go): e.GET("/path") group.POST("/path")
    r'(?:e|g|api)\.(GET|POST|PUT|PATCH|DELETE)\(["\']([^"\']+)["\']',
    # Actix-web (Rust): .route("/path", web::get()) / #[get("/path")]
    r'#\[(get|post|put|patch|delete)\(["\']([^"\']+)["\']',
    r'\.route\(["\']([^"\']+)["\'].*?web::(get|post|put|patch|delete)',
    # Phoenix (Elixir): get "/path", Controller, :action
    r'(?:get|post|put|patch|delete)\s+"([^"]+)"',
    # Hapi.js: method: 'GET', path: '/path'
    r'method:\s*["\'](\w+)["\'].*?path:\s*["\']([^"\']+)["\']',
    # Koa / koa-router: router.get('/path', ...)
    r'router\.(get|post|put|patch|delete)\(["\']([^"\']+)["\']',
    # Fiber (Go): app.Get("/path") app.Post("/path")
    r'app\.(Get|Post|Put|Patch|Delete)\(["\']([^"\']+)["\']',
]

# Source file extensions across all major tech stacks
SOURCE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx",   # Python, JS/TS
    ".go",                                   # Go
    ".java", ".kt", ".kts",                  # JVM
    ".rb",                                   # Ruby
    ".php",                                  # PHP
    ".cs", ".cshtml", ".razor",              # .NET / C#
    ".rs",                                   # Rust
    ".ex", ".exs",                           # Elixir/Phoenix
    ".swift",                                # Swift/Vapor
    ".scala",                                # Scala/Play
    ".clj", ".cljs",                         # Clojure
    ".hs",                                   # Haskell/Servant
    ".lua",                                  # Lua/OpenResty
    ".cr",                                   # Crystal/Kemal
    ".nim",                                  # Nim
    ".dart",                                 # Dart/Shelf
    # ── UI / Template files ───────────────────────────────────────────────────
    ".html", ".htm",                         # HTML templates
    ".jinja", ".jinja2", ".j2",              # Jinja templates
    ".hbs", ".handlebars",                   # Handlebars
    ".ejs",                                  # EJS
    ".vue",                                  # Vue SFC
    ".svelte",                               # Svelte
    ".erb",                                  # Rails ERB
    ".haml",                                 # HAML
    ".pug", ".jade",                         # Pug/Jade
    ".twig",                                 # Twig (PHP)
    ".blade.php",                            # Laravel Blade (caught by .php too)
    ".liquid",                               # Liquid (Shopify)
    ".mustache",                             # Mustache
    ".njk",                                  # Nunjucks
    ".astro",                                # Astro
    ".mdx",                                  # MDX
    # ── Style / Config that affects UI ───────────────────────────────────────
    ".css", ".scss", ".sass", ".less",       # Stylesheets
}


@dataclass
class DependencyChange:
    """Represents a single library version change."""
    file: str
    package: str
    old_version: Optional[str]
    new_version: Optional[str]


@dataclass
class FeatureChange:
    """Represents a newly detected route or feature."""
    file: str
    method: str          # GET / POST / etc.
    path: str            # /api/checkout
    description: str     # AI-generated one-liner
    tech_stack: str = "" # detected tech stack e.g. "Python/Flask"


@dataclass
class ChangeReport:
    """Aggregated result returned to the orchestrator."""
    dependency_changes: List[DependencyChange] = field(default_factory=list)
    feature_changes: List[FeatureChange] = field(default_factory=list)
    raw_diff_summary: str = ""

    @property
    def has_dependency_changes(self) -> bool:
        return len(self.dependency_changes) > 0

    @property
    def has_feature_changes(self) -> bool:
        return len(self.feature_changes) > 0

    @property
    def needs_action(self) -> bool:
        return self.has_dependency_changes


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_dependency_diff(filename: str, diff_text: str) -> List[DependencyChange]:
    """
    Extract added/changed dependency lines from a diff block.
    Works for requirements.txt style (pkg==version) and package.json.
    """
    changes: List[DependencyChange] = []

    # requirements.txt  →  package==1.2.3
    req_pattern = re.compile(r'^[+-]([A-Za-z0-9_\-\.]+)==([^\s]+)', re.MULTILINE)
    added: Dict[str, str] = {}
    removed: Dict[str, str] = {}

    for m in req_pattern.finditer(diff_text):
        line = m.group(0)
        pkg, ver = m.group(1), m.group(2)
        if line.startswith('+'):
            added[pkg] = ver
        else:
            removed[pkg] = ver

    # Pair up removed→added as upgrades; lone additions are new deps
    all_pkgs = set(added) | set(removed)
    for pkg in all_pkgs:
        changes.append(DependencyChange(
            file=filename,
            package=pkg,
            old_version=removed.get(pkg),
            new_version=added.get(pkg),
        ))

    # package.json  →  "express": "^4.18.0"
    npm_pattern = re.compile(r'^[+]\s+"([^"]+)":\s+"([^"]+)"', re.MULTILINE)
    npm_old = re.compile(r'^[-]\s+"([^"]+)":\s+"([^"]+)"', re.MULTILINE)
    npm_added = {m.group(1): m.group(2) for m in npm_pattern.finditer(diff_text)}
    npm_removed = {m.group(1): m.group(2) for m in npm_old.finditer(diff_text)}
    for pkg in set(npm_added) | set(npm_removed):
        if pkg not in all_pkgs:  # avoid duplicates
            changes.append(DependencyChange(
                file=filename,
                package=pkg,
                old_version=npm_removed.get(pkg),
                new_version=npm_added.get(pkg),
            ))

    return changes


def _extract_router_prefix(diff_text: str) -> str:
    """Extract APIRouter prefix from file content e.g. prefix="/users" → '/users'"""
    m = re.search(r'APIRouter\s*\([^)]*prefix\s*=\s*["\']([^"\']+)["\']', diff_text)
    return m.group(1).rstrip("/") if m else ""


def _parse_feature_diff(filename: str, diff_text: str) -> List[FeatureChange]:
    """
    Scan added lines (+) in a diff for new route decorators.
    Handles FastAPI APIRouter prefix so POST "/" in users.py → POST /users/
    Falls back to GPT when patterns find nothing, or always for unknown stacks.
    """
    changes: List[FeatureChange] = []
    seen: set = set()

    # Support both raw diff (+line) and plain source code
    added_lines = "\n".join(
        line[1:] if line.startswith('+') else line
        for line in diff_text.splitlines()
        if not line.startswith('---') and not line.startswith('+++')
    )

    # Extract router prefix (FastAPI/Flask blueprint prefix)
    prefix = _extract_router_prefix(diff_text) or _extract_router_prefix(added_lines)

    # Detect tech stack for smarter AI fallback
    tech_stack = detect_tech_stack(filename, added_lines)

    for pattern in ROUTE_PATTERNS:
        for m in re.finditer(pattern, added_lines, re.MULTILINE | re.IGNORECASE):
            groups = m.groups()
            if len(groups) == 2:
                method, path = groups[0], groups[1]
            else:
                method, path = "GET", groups[0]

            method = method.upper()

            # Validate — method must be a real HTTP verb
            if method not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
                continue

            # Prepend router prefix if path is relative (e.g. "/" → "/users/")
            if prefix and not path.startswith(prefix):
                if path.startswith("/"):
                    path = prefix + path
                else:
                    path = prefix + "/" + path

            # Normalise double slashes
            path = re.sub(r'/+', '/', path)

            key = (method, path)
            if key in seen:
                continue
            seen.add(key)

            changes.append(FeatureChange(
                file=filename,
                method=method,
                path=path,
                description=f"New {method} endpoint at {path}",
                tech_stack=tech_stack,
            ))

    # AI fallback:
    #   - Always run when regex found nothing and file has enough content
    #   - Also run for unknown/exotic stacks even if regex found something
    #     (regex may have produced false positives or missed endpoints)
    openai_available = _openai_available()
    is_unknown_stack = tech_stack == "unknown" or not any(
        ext in filename for ext in (".py", ".js", ".ts", ".java", ".cs", ".rb", ".go")
    )

    if openai_available and len(added_lines) > 50:
        if not changes or is_unknown_stack:
            ai_changes = _ai_extract_features(filename, added_lines, tech_stack)
            # Merge — avoid duplicates
            existing_keys = {(c.method, c.path) for c in changes}
            for fc in ai_changes:
                if (fc.method, fc.path) not in existing_keys:
                    changes.append(fc)
                    existing_keys.add((fc.method, fc.path))

    return changes


def _ai_extract_features(filename: str, added_code: str, tech_stack: str = "") -> List[FeatureChange]:
    """
    Use GPT to extract new endpoints/features from added code.
    Used as primary detector for unknown stacks and fallback for known ones.
    """
    try:
        stack_hint = f"Tech stack: {tech_stack}\n" if tech_stack else ""
        prompt = f"""You are a code analysis assistant.

Analyse the following newly added code from file '{filename}' and extract
any new HTTP endpoints, API routes, or significant new features.

{stack_hint}Return ONLY a JSON array (no markdown) like:
[
  {{"method": "POST", "path": "/api/orders", "description": "Creates a new order"}}
]

If there are no new endpoints or features, return an empty array: []

Code:
{added_code[:4000]}
"""
        openai_client = _get_openai_client()
        if not openai_client:
            return []
        response = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        raw = response.choices[0].message.content.strip()
        # Strip markdown fences if present
        raw = re.sub(r'^```[a-z]*\n?', '', raw, flags=re.MULTILINE)
        raw = re.sub(r'\n?```$', '', raw, flags=re.MULTILINE)
        items = json.loads(raw.strip())
        return [
            FeatureChange(
                file=filename,
                method=item.get("method", "GET").upper(),
                path=item.get("path", "/"),
                description=item.get("description", ""),
                tech_stack=tech_stack,
            )
            for item in items
            if item.get("method", "").upper() in
               ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
        ]
    except Exception as e:
        print(f"[code_change_detector] AI feature extraction failed: {e}")
        return []


UI_EXTENSIONS = {
    ".html", ".htm", ".jinja", ".jinja2", ".j2", ".hbs", ".handlebars",
    ".ejs", ".vue", ".svelte", ".erb", ".haml", ".pug", ".jade",
    ".twig", ".liquid", ".mustache", ".njk", ".astro", ".mdx",
    ".css", ".scss", ".sass", ".less",
}


def _is_ui_file(filename: str) -> bool:
    """Returns True for UI/template files that affect Selenium tests."""
    ext = os.path.splitext(filename)[1].lower()
    # Also catch .blade.php
    if filename.endswith(".blade.php"):
        return True
    return ext in UI_EXTENSIONS


def _ui_feature_from_file(filename: str, patch: str) -> Optional["FeatureChange"]:
    """
    For UI/template files, create a FeatureChange representing the page
    that changed. Used to trigger Selenium script updates.
    """
    # Derive a page path from the filename
    # e.g. templates/checkout/index.html → /checkout
    #      src/views/login.vue           → /login
    #      pages/about.html              → /about
    parts = filename.replace("\\", "/").split("/")
    # Strip common prefix dirs
    skip_dirs = {"templates", "views", "pages", "src", "app",
                 "static", "public", "resources", "assets", "components"}
    meaningful = [p for p in parts if p.lower() not in skip_dirs]

    if meaningful:
        # Use the last meaningful part without extension as the page name
        page = os.path.splitext(meaningful[-1])[0].lower()
        # Skip generic names
        if page in ("index", "base", "layout", "main", "app", "root"):
            if len(meaningful) > 1:
                page = meaningful[-2].lower()
            else:
                page = "home"
        path = f"/{page}"
    else:
        path = "/home"

    return FeatureChange(
        file=filename,
        method="GET",
        path=path,
        description=f"UI change in {filename} — page content updated",
        tech_stack="UI/Template",
    )


def detect_tech_stack(filename: str, diff_text: str = "") -> str:
    """
    Infer the tech stack from filename extension and content hints.
    Returns a human-readable stack name used in AI prompts.
    """
    ext = os.path.splitext(filename)[1].lower()
    content = diff_text.lower()

    stack_map = {
        ".py": "Python",
        ".js": "JavaScript/Node.js",
        ".ts": "TypeScript/Node.js",
        ".jsx": "JavaScript/React",
        ".tsx": "TypeScript/React",
        ".go": "Go",
        ".java": "Java",
        ".kt": "Kotlin",
        ".kts": "Kotlin",
        ".rb": "Ruby",
        ".php": "PHP",
        ".cs": "C#/.NET",
        ".cshtml": "C#/ASP.NET MVC",
        ".razor": "C#/Blazor",
        ".rs": "Rust",
        ".ex": "Elixir",
        ".exs": "Elixir",
        ".swift": "Swift",
        ".scala": "Scala",
        ".clj": "Clojure",
        ".cljs": "ClojureScript",
        ".hs": "Haskell",
        ".lua": "Lua",
        ".cr": "Crystal",
        ".dart": "Dart",
    }
    stack = stack_map.get(ext, "unknown")

    # Refine with framework hints from content
    if stack == "Python":
        if "fastapi" in content or "from fastapi" in content:
            stack = "Python/FastAPI"
        elif "flask" in content or "from flask" in content:
            stack = "Python/Flask"
        elif "django" in content or "from django" in content:
            stack = "Python/Django"
    elif stack in ("JavaScript/Node.js", "TypeScript/Node.js"):
        if "nestjs" in content or "@nestjs" in content or "from '@nestjs" in content:
            stack = f"{stack.split('/')[0]}/NestJS"
        elif "express" in content:
            stack = f"{stack.split('/')[0]}/Express"
        elif "fastify" in content:
            stack = f"{stack.split('/')[0]}/Fastify"
        elif "koa" in content:
            stack = f"{stack.split('/')[0]}/Koa"
    elif stack == "Go":
        if "gin-gonic" in content or '"github.com/gin-gonic' in content:
            stack = "Go/Gin"
        elif "echo" in content or '"github.com/labstack' in content:
            stack = "Go/Echo"
        elif "fiber" in content or '"github.com/gofiber' in content:
            stack = "Go/Fiber"
    elif stack == "Ruby":
        if "rails" in content or "actioncontroller" in content:
            stack = "Ruby/Rails"
        elif "sinatra" in content:
            stack = "Ruby/Sinatra"
    elif stack == "PHP":
        if "laravel" in content or "illuminate" in content:
            stack = "PHP/Laravel"
        elif "symfony" in content:
            stack = "PHP/Symfony"
    elif stack == "Rust":
        if "actix" in content:
            stack = "Rust/Actix-web"
        elif "axum" in content:
            stack = "Rust/Axum"
        elif "rocket" in content:
            stack = "Rust/Rocket"
    elif stack == "Elixir":
        if "phoenix" in content:
            stack = "Elixir/Phoenix"
    elif stack in ("Java", "Kotlin"):
        if "springframework" in content or "@springbootapplication" in content:
            stack = f"{stack}/Spring Boot"
        elif "quarkus" in content:
            stack = f"{stack}/Quarkus"
        elif "micronaut" in content:
            stack = f"{stack}/Micronaut"

    return stack


def _is_dependency_file(filename: str) -> bool:
    return os.path.basename(filename) in DEPENDENCY_FILES


def _is_source_file(filename: str) -> bool:
    """Returns True for any known backend/API source file extension."""
    ext = os.path.splitext(filename)[1].lower()
    return ext in SOURCE_EXTENSIONS


def detect_changes(diff_text: str, changed_files: list) -> ChangeReport:
    """
    Main entry point.

    Scenarios handled:
      A — dependency file changed (requirements.txt, package.json, etc.)
      B — source file added/modified with new route decorators
      C — UI/template file changed (HTML, Vue, Svelte, etc.) → Selenium update
    """
    report = ChangeReport()

    for file_info in changed_files:
        filename = file_info.get("filename", "")
        patch    = file_info.get("patch", "") or ""
        status   = file_info.get("status", "modified")

        # ── Scenario A: dependency upgrade ───────────────────────────────────
        if _is_dependency_file(filename):
            deps = _parse_dependency_diff(filename, patch)
            report.dependency_changes.extend(deps)
            print(f"[detector] Dependency changes in {filename}: {len(deps)} packages")

        # ── Scenario B: source file with route decorators ────────────────────
        elif _is_source_file(filename) and not _is_ui_file(filename) \
                and status in ("added", "modified"):
            stack    = detect_tech_stack(filename, patch)
            features = _parse_feature_diff(filename, patch)
            report.feature_changes.extend(features)
            print(f"[detector] Feature changes in {filename} ({stack}): {len(features)} endpoints")

        # ── Scenario C: UI/template file changed ─────────────────────────────
        elif _is_ui_file(filename) and status in ("added", "modified"):
            feature = _ui_feature_from_file(filename, patch)
            if feature:
                report.feature_changes.append(feature)
                print(f"[detector] UI change in {filename} → page {feature.path}")

    if diff_text:
        report.raw_diff_summary = _summarise_diff(diff_text)

    return report


def _summarise_diff(diff_text: str) -> str:
    """One-paragraph plain-English summary of the diff for the PR comment."""
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key or "xxxxxx" in api_key or not api_key.startswith("sk-"):
        return "Diff summary unavailable (OPENAI_API_KEY not configured)."
    try:
        openai_client = _get_openai_client()
        if not openai_client:
            return "Diff summary unavailable (OPENAI_API_KEY not configured)."
        response = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{
                "role": "user",
                "content": (
                    "Summarise this git diff in 2-3 sentences for a QA engineer. "
                    "Focus on what changed functionally.\n\n"
                    + diff_text[:4000]
                ),
            }],
            temperature=0,
        )
        return response.choices[0].message.content.strip()
    except Exception:
        return "Diff summary unavailable."

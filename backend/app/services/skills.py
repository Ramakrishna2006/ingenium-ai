"""Skill vocabulary and normalisation used by resume parsing and matching.

This is a deliberately transparent, editable dictionary: every match the system
reports can be traced to a canonical skill and the alias that was found.
"""
from __future__ import annotations

import re

# canonical skill -> aliases (lowercase). The canonical name itself is always an alias.
SKILL_ALIASES: dict[str, list[str]] = {
    "Python": ["python", "python3"],
    "Java": ["java"],
    "JavaScript": ["javascript", "js", "ecmascript"],
    "TypeScript": ["typescript", "ts"],
    "C#": ["c#", "csharp", ".net", "dotnet"],
    "C++": ["c++", "cpp"],
    "Go": ["golang"],
    "SQL": ["sql"],
    "PostgreSQL": ["postgresql", "postgres", "psql"],
    "MySQL": ["mysql"],
    "MongoDB": ["mongodb", "mongo"],
    "Redis": ["redis"],
    "Flask": ["flask"],
    "Django": ["django"],
    "FastAPI": ["fastapi"],
    "REST APIs": ["rest api", "rest apis", "restful", "rest"],
    "GraphQL": ["graphql"],
    "Spring Boot": ["spring boot", "spring"],
    "Node.js": ["node.js", "nodejs", "node"],
    "React": ["react", "react.js", "reactjs"],
    "Angular": ["angular"],
    "Vue.js": ["vue", "vue.js", "vuejs"],
    "HTML": ["html", "html5"],
    "CSS": ["css", "css3", "sass", "scss"],
    "Bootstrap": ["bootstrap"],
    "Git": ["git", "github", "gitlab"],
    "Docker": ["docker", "containers"],
    "Kubernetes": ["kubernetes", "k8s"],
    "AWS": ["aws", "amazon web services", "ec2", "s3"],
    "Azure": ["azure"],
    "GCP": ["gcp", "google cloud"],
    "Terraform": ["terraform"],
    "CI/CD": ["ci/cd", "cicd", "continuous integration", "github actions", "jenkins"],
    "Linux": ["linux", "unix", "bash", "shell scripting"],
    "Machine Learning": ["machine learning", "ml"],
    "Deep Learning": ["deep learning", "neural networks"],
    "NLP": ["nlp", "natural language processing"],
    "LLMs": ["llm", "llms", "large language models", "ollama", "prompt engineering"],
    "scikit-learn": ["scikit-learn", "sklearn"],
    "TensorFlow": ["tensorflow"],
    "PyTorch": ["pytorch"],
    "Pandas": ["pandas"],
    "NumPy": ["numpy"],
    "Data Analysis": ["data analysis", "data analytics", "analytics"],
    "Statistics": ["statistics", "statistical analysis"],
    "Data Visualization": ["data visualization", "data visualisation", "visualization"],
    "Power BI": ["power bi", "powerbi"],
    "Tableau": ["tableau"],
    "Excel": ["excel", "spreadsheets"],
    "ETL": ["etl", "data pipelines", "data pipeline"],
    "Spark": ["spark", "pyspark"],
    "Cybersecurity": ["cybersecurity", "cyber security", "information security", "infosec"],
    "Networking": ["networking", "tcp/ip", "network administration"],
    "Penetration Testing": ["penetration testing", "pentesting", "ethical hacking"],
    "SIEM": ["siem", "splunk"],
    "Firewalls": ["firewall", "firewalls"],
    "Testing": ["unit testing", "pytest", "junit", "test automation", "qa"],
    "Agile": ["agile", "scrum", "kanban"],
    "Project Management": ["project management", "pmp"],
    "Communication": ["communication", "presentation skills"],
    "Leadership": ["leadership", "team lead", "mentoring"],
    "Recruitment": ["recruitment", "recruiting", "talent acquisition", "sourcing"],
    "Onboarding": ["onboarding"],
    "HR Operations": ["hr operations", "hris", "payroll", "employee relations"],
    "Labour Law": ["labour law", "labor law", "employment law"],
    "Accounting": ["accounting", "bookkeeping", "gaap", "ifrs"],
    "Financial Analysis": ["financial analysis", "financial modelling", "financial modeling", "budgeting", "forecasting"],
    "Sales": ["sales", "business development"],
    "Marketing": ["marketing", "digital marketing", "seo"],
    "Customer Service": ["customer service", "customer support"],
    "Operations": ["operations management", "supply chain", "logistics"],
    "UI/UX Design": ["ui/ux", "ux", "ui design", "figma", "user experience"],
}

_ALIAS_TO_CANONICAL: dict[str, str] = {}
for _canon, _aliases in SKILL_ALIASES.items():
    _ALIAS_TO_CANONICAL[_canon.lower()] = _canon
    for _alias in _aliases:
        _ALIAS_TO_CANONICAL.setdefault(_alias.lower(), _canon)

# Short/ambiguous aliases that should only match as standalone tokens with care.
_AMBIGUOUS = {"go", "ts", "js", "ml", "qa", "rest", "node", "spring", "ux", "analytics", "visualization",
              "communication", "containers", "sales", "excel", "s3", "ec2"}


def normalize_skill(raw: str) -> str:
    """Map a free-text skill to its canonical name (or a tidy version of itself)."""
    value = re.sub(r"\s+", " ", (raw or "").strip())
    if not value:
        return ""
    return _ALIAS_TO_CANONICAL.get(value.lower(), value[:60])


def normalize_skill_list(values) -> list[str]:
    if isinstance(values, str):
        values = re.split(r"[,;\n]", values)
    seen, out = set(), []
    for v in values or []:
        canon = normalize_skill(str(v))
        if canon and canon.lower() not in seen:
            seen.add(canon.lower())
            out.append(canon)
    return out[:60]


def _alias_pattern(alias: str) -> re.Pattern:
    escaped = re.escape(alias)
    return re.compile(rf"(?<![A-Za-z0-9+#.]){escaped}(?![A-Za-z0-9+#])", re.IGNORECASE)


_PATTERNS = {alias: _alias_pattern(alias) for alias in _ALIAS_TO_CANONICAL}


def _ambiguous_ok(alias: str, token: str) -> bool:
    """Reduce false positives for short/common words (e.g. 'rest of the team', 'go to')."""
    if alias == "go":
        return token == "Go"
    if alias in ("node", "excel", "spring"):
        return token[:1].isupper()
    if len(alias) <= 4:
        return token.isupper()  # ML, JS, TS, QA, UX, REST, S3, EC2
    return True


def find_skill_mentions(text: str, skill: str) -> list[tuple[int, int]]:
    """Return character spans where ``skill`` (or one of its aliases) is mentioned in ``text``."""
    if not text:
        return []
    canon = normalize_skill(skill)
    aliases = {canon.lower(), *[a for a in SKILL_ALIASES.get(canon, [])]}
    if canon not in SKILL_ALIASES:
        aliases = {skill.lower()}
    spans = []
    for alias in aliases:
        pattern = _PATTERNS.get(alias) or _alias_pattern(alias)
        for m in pattern.finditer(text):
            if alias in _AMBIGUOUS and not _ambiguous_ok(alias, m.group(0)):
                continue
            spans.append((m.start(), m.end()))
    return sorted(spans)


def extract_skills_from_text(text: str) -> list[str]:
    found = []
    for canon in SKILL_ALIASES:
        if find_skill_mentions(text, canon):
            found.append(canon)
    return found


def evidence_snippet(text: str, span: tuple[int, int], radius: int = 70) -> str:
    start, end = span
    a, b = max(0, start - radius), min(len(text), end + radius)
    snippet = re.sub(r"\s+", " ", text[a:b]).strip()
    return ("…" if a > 0 else "") + snippet + ("…" if b < len(text) else "")

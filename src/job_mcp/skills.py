"""Finding tech skills in text, and telling a job's must-haves from its nice-to-haves.

SKILLS maps each skill's usual name to the patterns that mean it ("Postgres" and "PostgreSQL"
are both PostgreSQL). A few skills are also ordinary English words ("Go", "Rust", "Swift"), so
their patterns are written to only match the skill.

This module knows nothing about MCP, so it can be used and tested as plain Python.
"""

import re

# Patterns are case-insensitive unless they start with (?-i), and must match whole words.
SKILLS: dict[str, str] = {
    # Languages
    "Python": r"python",
    "Java": r"java(?!\s*script)",
    "JavaScript": r"javascript|(?-i:JS)|ecmascript",
    "TypeScript": r"typescript|(?-i:TS)",
    "C": r"(?-i:C)(?=\s*(?:/|,|and|or)\s*C\+\+)|ansi c|c programming",
    "C++": r"c\+\+|cpp",
    "C#": r"c#|\.net|dotnet",
    "Go": r"golang|(?-i:Go)(?=[ \t]*(?:,|/|\)|and\b|or\b|programming|language|$)|\.(?!\w))",
    "Rust": r"(?-i:Rust)",
    "Kotlin": r"kotlin",
    "Swift": r"(?-i:Swift)(?!\s*(?:pace|action|response))|swiftui",
    "Objective-C": r"objective-?c",
    "Scala": r"scala",
    "Ruby": r"ruby(?!\s+on)",
    "PHP": r"php",
    "R": r"(?-i:R)(?=\s*(?:,|/|and\b|or\b)\s*(?:python|sql|matlab|julia))|(?<=python,\s)(?-i:R)",
    "SQL": r"sql",
    "Bash": r"bash|shell scripting|shell scripts?",
    "HTML": r"html5?",
    "CSS": r"css3?",
    "Haskell": r"haskell",
    "OCaml": r"ocaml",
    "Elixir": r"elixir",
    "MATLAB": r"matlab",
    "Assembly": r"assembly language|assembly|x86|arm assembly",
    # Web and app frameworks
    "React": r"react(?:\.?js)?(?!\s+native)",
    "React Native": r"react native",
    "Next.js": r"next\.?js",
    "Vue": r"vue(?:\.?js)?",
    "Angular": r"angular(?:js)?",
    "Svelte": r"svelte(?:kit)?",
    "Node.js": r"node(?:\.?js)?",
    "Express": r"express\.?js",
    "Django": r"django",
    "Flask": r"flask",
    "FastAPI": r"fastapi",
    "Spring": r"spring boot|spring framework|(?-i:Spring)(?=\s*(?:,|/|and|or)\s*(?:java|hibernate))",
    "Ruby on Rails": r"ruby on rails|(?-i:Rails)",
    "Tailwind CSS": r"tailwind(?:\s*css)?",
    "GraphQL": r"graphql",
    "REST APIs": r"rest(?:ful)?(?:\s+apis?|\s+services?|\s+web services?)|restful",
    "gRPC": r"grpc",
    "Prisma": r"prisma",
    "iOS": r"(?-i:iOS)",
    "Android": r"android",
    "Flutter": r"flutter",
    # Data and databases
    "Databases": r"relational databases?|database design|databases?|data modeling",
    "PostgreSQL": r"postgres(?:ql)?",
    "MySQL": r"mysql",
    "SQLite": r"sqlite",
    "MongoDB": r"mongo(?:db)?",
    "Redis": r"redis",
    "Elasticsearch": r"elasticsearch|opensearch",
    "DynamoDB": r"dynamo(?:db)?",
    "Cassandra": r"cassandra",
    "Kafka": r"kafka",
    "Spark": r"(?:apache\s+)?spark|pyspark",
    "Hadoop": r"hadoop",
    "Airflow": r"airflow",
    "dbt": r"(?-i:dbt)",
    "Snowflake": r"snowflake",
    "BigQuery": r"bigquery",
    "Pandas": r"pandas",
    "NumPy": r"numpy",
    "Jupyter": r"jupyter",
    "Tableau": r"tableau",
    "Excel": r"(?-i:Excel)(?!\s+(?:in|at|as)\b)|spreadsheets?",
    "Full-text search": r"full[- ]text search|fts5?|bm25|search ranking|information retrieval",
    "ETL": r"etl|data pipelines?",
    "Statistics": r"statistics|statistical|a/b test(?:ing|s)?|experimentation",
    # Machine learning and AI
    "Machine learning": r"machine learning|(?-i:ML)",
    "Deep learning": r"deep learning|neural networks?",
    "PyTorch": r"pytorch|torch",
    "TensorFlow": r"tensorflow|keras",
    "JAX": r"(?-i:JAX)",
    "scikit-learn": r"scikit-learn|sklearn",
    "LLMs": r"llms?|large language models?|generative ai|genai",
    "NLP": r"nlp|natural language processing",
    "Computer vision": r"computer vision",
    "RAG": r"(?-i:RAG)|retrieval[- ]augmented",
    "Embeddings": r"embeddings?|vector search|semantic search|vector databases?",
    "MCP": r"model context protocol|(?-i:MCP)",
    "AI agents": r"ai agents?|agentic|tool use|function calling",
    "AI-assisted development": r"ai[- ]assisted (?:development|coding|programming|tools?)|"
                               r"github copilot|copilot|claude code|cursor (?:ide|ai)|ai coding (?:tools?|assistants?)",
    "Hugging Face": r"hugging\s*face|transformers library",
    "CUDA": r"cuda|gpu programming",
    # Cloud, infrastructure and tools
    "AWS": r"aws|amazon web services|ec2|s3|lambda",
    "GCP": r"gcp|google cloud",
    "Azure": r"azure",
    "Docker": r"docker|containers?",
    "Kubernetes": r"kubernetes|k8s",
    "Terraform": r"terraform|infrastructure as code",
    "Linux": r"linux|unix",
    "Git": r"git(?!hub|lab)",
    "GitHub": r"github(?!\s+actions)",
    "CI/CD": r"ci/cd|continuous integration|continuous delivery|continuous deployment|github actions",
    "Testing": r"unit tests?|unit testing|integration tests?|automated tests?|test-driven|tdd|test suites?",
    "pytest": r"pytest",
    "Jest": r"jest",
    "Playwright": r"playwright|cypress|selenium",
    "Observability": r"observability|monitoring|prometheus|grafana|datadog|opentelemetry",
    # Computer science
    "Data structures": r"data structures",
    "Algorithms": r"algorithms?",
    "Object-oriented programming": r"object[- ]oriented(?: programming| design)?|oop",
    "Distributed systems": r"distributed systems?",
    "System design": r"system design|systems design|software architecture",
    "Concurrency": r"concurrency|concurrent programming|multithreading|async(?:io|hronous)?(?: programming)?",
    "Networking": r"computer networks?|networking|tcp/ip|http(?:/\d)?|dns",
    "Operating systems": r"operating systems?",
    "Compilers": r"compilers?",
    "Computer architecture": r"computer architecture",
    "Security": r"application security|appsec|cryptography|security engineering|ssrf|owasp",
    "Web scraping": r"web scraping|web crawl(?:ing|er)|crawlers?|scraping",
    "APIs": r"apis?|sdks?",
    "Full-stack": r"full[- ]stack",
    "Frontend": r"front[- ]?end",
    "Backend": r"back[- ]?end",
    "Mobile": r"mobile (?:apps?|development|engineering)",
}

_COMPILED = {
    # MULTILINE so "$" in a pattern means the end of a line ("- Go" in a list).
    name: re.compile(rf"(?<![\w+#.]){'(?:' + pattern + ')'}(?![\w+#]|\.\w)",
                     re.IGNORECASE | re.MULTILINE)
    for name, pattern in SKILLS.items()
}

# Headings that start a job's must-have and nice-to-have parts.
_PREFERRED = re.compile(
    r"nice[- ]to[- ]have|preferred|bonus|plus\b|ideal(?:ly)?|extra credit|stand out|"
    r"even better|not required|would be great|desired", re.IGNORECASE)
_REQUIRED = re.compile(
    r"require|qualifications|must|minimum|basic|you have|you bring|you'll bring|you will bring|"
    r"looking for|about you|who you are|you'll need|you need|you might be|you may be|"
    r"what we need|skills|experience|you are|you're|ideal candidate|expect from you",
    re.IGNORECASE)
# Headings that start parts that aren't about the candidate at all (the company, benefits...).
_OTHER = re.compile(r"about (?:us|the company|the team)|benefits|perks|compensation|"
                    r"transparency|disclosure|accommodation|privacy|internship$|"
                    r"we offer|equal opportunity|pay range|salary|what you'll do|"
                    r"responsibilities|the role|what you will do|day to day", re.IGNORECASE)


def find(text: str) -> set[str]:
    """The skills mentioned in text."""
    return {name for name, pattern in _COMPILED.items() if pattern.search(text)}


def canonical(name: str) -> str | None:
    """A skill's usual name, from any way of writing it ("postgres" -> "PostgreSQL")."""
    for skill in sorted(find(name)):
        return skill
    return None


def _heading(line: str) -> bool:
    """Whether a line is a heading: marked as one, ending in a colon, or a short Title Case
    line ("Pay Transparency Disclosure")."""
    stripped = line.strip().strip("*#: ").strip()
    if line.startswith("## "):
        return True
    if not stripped or line.startswith("- ") or len(stripped) > 70:
        return False
    if line.rstrip().endswith(":"):
        return True
    words = stripped.split()
    return (2 <= len(words) <= 6 and stripped[-1] not in ".!?,;" and ":" not in stripped and
            all(word[0].isupper() or word.lower() in ("and", "of", "the", "to", "for", "&", "a", "in")
                for word in words))


def sections(description: str) -> dict[str, str]:
    """A job description split into "required" (the must-haves), "preferred" (nice-to-haves)
    and "other" (everything else: the company, the role, benefits...). A line in a must-have
    list that says "is a plus" is a nice-to-have."""
    parts = {"required": [], "preferred": [], "other": []}
    current = "other"
    listed = False  # Whether the current part has had list items yet.
    for line in description.splitlines():
        if current != "other" and listed and len(line) > 120 and not line.startswith("- "):
            # A long paragraph after a list, with no heading: the list is over ("At Figma, we
            # value...", the legal text at the end).
            current = "other"
        if _heading(line):
            listed = False
            if _PREFERRED.search(line):
                current = "preferred"
            elif _OTHER.search(line):
                current = "other"
            elif _REQUIRED.search(line):
                current = "required"
            else:
                current = "other"
            continue
        listed = listed or line.startswith("- ")
        # In a must-have list, any line saying "is a plus" is a nice-to-have; elsewhere only
        # one that starts by saying so ("Bonus: Rust"), not one about an annual bonus.
        if (_PREFERRED.search(line) if current != "other" else _PREFERRED.match(line.lstrip("- "))):
            parts["preferred"].append(line)
        else:
            parts[current].append(line)
    return {name: "\n".join(lines).strip() for name, lines in parts.items()}


# Skills so general that nearly every posting mentions them: never counted as a gap.
TOO_GENERAL = {"APIs", "Databases", "Testing", "Algorithms", "Data structures", "Git", "Backend",
               "Frontend", "Full-stack", "Networking", "Security", "Observability"}


def requirements(description: str) -> tuple[set[str], set[str]]:
    """The (required, preferred) skills of a job. If the description has no must-have part we
    can find, every skill outside the nice-to-haves counts as required."""
    parts = sections(description)
    preferred = find(parts["preferred"])
    if parts["required"]:
        required = find(parts["required"])
    else:
        required = find(parts["other"]) - preferred
    return required, preferred - required

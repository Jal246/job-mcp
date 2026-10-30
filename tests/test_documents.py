from job_mcp import documents
from job_mcp.profile import Project

from test_profile import ada, resume_of


def test_render_resume(tmp_path):
    path = tmp_path / "out" / "Ada_Resume.pdf"
    assert documents.render_resume(resume_of(ada()), path) == (1, 1.0)
    text = documents.pdf_text(path)
    # Real, extractable text, in order, as a screening system reads it.
    for words in ["ADA EXAMPLE", "github.com/ada", "EDUCATION", "Example University",
                  "TECHNICAL SKILLS", "Languages: Python, Go, SQL", "PROJECTS", "Payments API",
                  "1,200 requests per second", "EXPERIENCE", "Barista"]:
        assert words in text
    assert text.index("EDUCATION") < text.index("PROJECTS") < text.index("EXPERIENCE")


def test_render_resume_shrinks_to_fit_then_gives_up(tmp_path):
    long_bullet = "Built a notes app in Python that people found genuinely useful every day. " * 2

    def with_bullets(count: int):
        return resume_of(ada(), projects=[Project(name=f"Project {n}", bullets=[long_bullet] * 3)
                                          for n in range(count)])

    pages, scale = documents.render_resume(with_bullets(6), tmp_path / "a.pdf")
    assert pages == 1 and scale < 1  # Slightly too long: set smaller to fit.
    pages, scale = documents.render_resume(with_bullets(12), tmp_path / "b.pdf")
    assert pages == 2 and scale == documents.SCALES[-1]


def test_section_order(tmp_path):
    resume = resume_of(ada(), section_order=["projects", "education"])
    documents.render_resume(resume, tmp_path / "r.pdf")
    text = documents.pdf_text(tmp_path / "r.pdf")
    assert text.index("PROJECTS") < text.index("EDUCATION")
    assert "EXPERIENCE" not in text and "TECHNICAL SKILLS" not in text


def test_text_is_made_safe():
    assert documents._text("A → B & <C> ≥ 3") == "A -&gt; B &amp; &lt;C&gt; &gt;= 3"
    assert documents._text("emoji 🚀") == "emoji ?"


def test_render_cover_letter(tmp_path):
    path = tmp_path / "letter.pdf"
    pages = documents.render_cover_letter(resume_of(ada()), "Acme", ["First paragraph.", "Second."],
                                          "Dear Hiring Team,", "Sincerely,", "October 29, 2026", path)
    assert pages == 1
    text = documents.pdf_text(path)
    for words in ["ADA EXAMPLE", "October 29, 2026", "Acme Hiring Team", "Dear Hiring Team,",
                  "First paragraph.", "Sincerely,"]:
        assert words in text

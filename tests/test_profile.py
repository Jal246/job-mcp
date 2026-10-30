import pytest

from job_mcp import profile
from job_mcp.profile import (Education, Experience, Preferences, Profile, Project, Resume,
                             SkillGroup, check_resume)


def ada() -> Profile:
    """A made-up student's master profile."""
    return Profile(
        name="Ada Example", email="ada@example.com", phone="555-0100", location="London",
        links=["github.com/ada"],
        education=[Education(school="Example University", degree="BSc Computer Science",
                             dates="Expected May 2028", coursework=["Data Structures", "Databases"])],
        skills=[SkillGroup(name="Languages", items=["Python", "Go", "SQL"]),
                SkillGroup(name="Tools", items=["Git", "Docker"])],
        projects=[
            Project(name="Payments API", tech=["Go", "PostgreSQL"], url="github.com/ada/pay",
                    bullets=["Built a payments API in Go handling 1,200 requests per second.",
                             "Wrote 85 tests with a fake bank."]),
            Project(name="Notes", tech=["Python"], bullets=["Built a notes app in Python."]),
        ],
        experience=[Experience(organization="Cafe Example", title="Barista", dates="2024 - 2025",
                               bullets=["Served 200 customers a day."])],
        preferences=Preferences(graduation="2028-05", seasons=["Summer 2027"],
                                levels=["intern", "new_grad"], places=["Bangalore", "London", "Remote"],
                                work_countries=[], roles=["software", "data", "ml"]),
        extra_facts=["Won a hackathon in 2025 with a team of 4."],
    )


def resume_of(master: Profile, **changes) -> Resume:
    data = master.model_dump(exclude={"preferences", "extra_facts"})
    return Resume(**{**data, **changes})


def test_save_and_load(tmp_path):
    assert profile.load(tmp_path / "profile.json") is None
    profile.save(tmp_path / "data" / "profile.json", ada())
    assert profile.load(tmp_path / "data" / "profile.json") == ada()


def test_skills_of():
    assert profile.skills_of(ada()) >= {"Python", "Go", "SQL", "Git", "Docker", "PostgreSQL",
                                        "Data structures", "Databases"}


def test_a_faithful_resume_passes():
    master = ada()
    resume = resume_of(master, projects=[master.projects[0].model_copy(update={"bullets": [
        "Designed a Go payments API that handles 1,200 requests per second, with 85 tests."]})])
    assert check_resume(resume, master) == ([], [])


@pytest.mark.parametrize("changes, error", [
    ({"email": "ada@elsewhere.com"}, "name and email"),
    ({"links": ["github.com/someone"]}, "link 'github.com/someone'"),
    ({"education": [Education(school="MIT", degree="BSc", dates="2028")]}, "school 'MIT'"),
    ({"education": [Education(school="Example University", degree="PhD Computer Science",
                              dates="Expected May 2028")]}, "degree, dates and GPA"),
    ({"education": [Education(school="Example University", degree="BSc Computer Science",
                              dates="Expected May 2028", coursework=["Compilers"])]}, "coursework"),
    ({"experience": [Experience(organization="Google", title="Intern", dates="2025")]}, "employer 'Google'"),
    ({"experience": [Experience(organization="Cafe Example", title="Manager", dates="2024 - 2025")]},
     "title and dates"),
    ({"projects": [Project(name="Rocket", bullets=["Built a rocket."])]}, "project 'Rocket'"),
    ({"projects": [Project(name="Notes", url="github.com/ada/other")]}, "the link must be"),
    ({"skills": [SkillGroup(name="Languages", items=["Python", "Rust"])]}, "skills the profile never mentions: Rust"),
    ({"projects": [Project(name="Notes", bullets=["Built a notes app in Python with Kubernetes."])]},
     "skills the profile never mentions: Kubernetes"),
    ({"projects": [Project(name="Notes", bullets=["Built a notes app used by 5,000 people."])]},
     "numbers the profile never mentions: 5000"),
])
def test_made_up_facts_are_errors(changes, error):
    errors, _ = check_resume(resume_of(ada(), **changes), ada())
    assert any(error in e for e in errors), errors


def test_extra_facts_and_allowed_text_can_be_used():
    master = ada()
    resume = resume_of(master, projects=[master.projects[1].model_copy(update={"bullets": [
        "Built a notes app in Python after winning a hackathon in 2025 with a team of 4."]})])
    assert check_resume(resume, master)[0] == []
    resume = resume_of(master, projects=[master.projects[1].model_copy(update={"bullets": [
        "Built a notes app in Python for a class of 30."]})])
    assert check_resume(resume, master)[0] != []
    assert check_resume(resume, master, also_allowed="a class of 30")[0] == []


def test_style_warnings():
    master = ada()
    resume = resume_of(master, projects=[master.projects[1].model_copy(update={"bullets": [
        "Responsible for a notes app in Python.", "Built a notes app in Python. " * 10]})])
    errors, warnings = check_resume(resume, master)
    assert errors == []
    assert [w.split(" (")[0].split(":")[0] for w in warnings] == ["starts weakly", "too long"]

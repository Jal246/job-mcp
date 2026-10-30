"""The instructions the AI follows when it writes for you: tailored resumes, cover letters and
interview prep. They're the difference between a generic document and a good one, so they're
kept here, in one place, where they're easy to read and improve.
"""

IMPORT = """\
Turn this resume into a profile and call save_profile with it:
- Copy every fact: schools, degrees, dates, coursework, skills, projects (with their tech and
  links), jobs and certifications. Keep the wording of bullets; don't add facts that aren't here.
- The profile is a master list: it may hold more projects and bullets than fit on one page.
- If you know of more true facts (projects the user built with you, with real numbers), ask the
  user before adding them.
- Ask the user for preferences you can't tell from the resume: graduation month, internship
  seasons (e.g. "Summer 2027"), places (e.g. ["Bay Area", "Remote"]), the countries they can work
  in, and the kinds of roles they want.
"""

TAILOR = """\
## How to write it

Write a one-page resume for this job and call save_resume with it (and this job_id). It's
checked against the profile: a school, employer, project, link, skill or number that isn't in
the profile is rejected, so tailor by choosing and rewording, never by adding.

1. **Projects**: pick the 3 (at most 4) that best show what this job asks for (the ranking above
   is a start), most relevant first. Each gets 2-3 bullets.
2. **Bullets**: start each with a strong past-tense verb (Built, Designed, Cut, Implemented),
   then what you did, how (the tech), and the result, with the profile's numbers. Under about
   200 characters, so it fits on two lines. Where the job uses a different word for something
   the profile shows ("RESTful services" for "REST APIs"), use the job's word. Lead with the
   bullets that match the job's required skills.
3. **Skills**: only skills in the profile. Put the ones this job asks for first in each group,
   and drop ones that are irrelevant to it. Never list a missing skill, even if it's required.
4. **Experience**: keep every job (gaps look worse than unrelated work); 1-2 bullets each,
   chosen and reworded for what transfers (teaching, communication, coordination).
5. **Sections**: section_order puts the strongest first. For a student, usually education,
   skills, projects, experience, certifications.
6. Keep the name, email, phone and links exactly as in the profile.

If save_resume says the page overflows, cut the weakest bullet and try again.
"""

COVER_LETTER = """\
## How to write it

Write the letter's body as 3-4 paragraphs (250-350 words in all) and call save_cover_letter
with them and this job_id. The greeting, date, signature and contact header are added for you.

1. **Opening (2-3 sentences)**: the role, and one specific, true reason this company: something
   from the job description above (its product, its customers, a problem the team works on).
   Never invent facts about the company, and don't open with "I am writing to express my
   interest" or "I am excited to apply".
2. **One or two stories (the middle)**: pick the projects that best match the job's required
   skills. For each: the problem, what you built and how, and a real number from the profile.
   Tie each one to something the job asks for, in the job's words.
3. **Why you, briefly**: what you'd bring from the rest of the profile (other experience,
   coursework, how fast you learn new tools: the profile shows several projects built in
   weeks). If a required skill is missing, you may say you're keen to learn it; never claim it.
4. **Close (1-2 sentences)**: availability (the season they want), a thank-you, and that you'd
   welcome a conversation.

Use only facts from the profile and the job description; numbers are checked. Plain, confident,
specific language; no clichés ("passionate", "team player", "fast-paced", "leverage").
"""

INTERVIEW_PREP = """\
## How to use this

Make an interview prep sheet for the user from the job and profile above:

1. **What they'll test**: the technical topics most likely to come up, from the required skills
   (for internships, usually data structures and algorithms plus the job's main language and
   area). For each, 2-3 practice questions, easiest first.
2. **Project deep dives**: for each project to talk about, the questions an interviewer will
   ask ("why did you choose X?", "what was the hardest bug?", "how would you scale it?", "how
   did you test it?") and what a strong answer covers. The user must be able to explain every
   line of their resume and every number on it in their own words.
3. **Behavioral questions**: 5 likely ones (a challenge you overcame, working with others,
   learning something new fast, a mistake, why this company), each with which part of the
   profile makes the best STAR story (Situation, Task, Action, Result).
4. **Gaps to close**: for each missing required skill, what to learn first and a small way to
   show it (an exercise or a feature in one of their projects) in the time before the
   interview.
5. **Questions to ask them**: 3-4 good ones, specific to this team and role.

Then offer to run a mock interview: ask one question at a time and give feedback on each answer.
"""

# Phrases that make a cover letter sound like every other cover letter.
CLICHES = ("i am writing to express my interest", "i am excited to apply", "passionate",
           "team player", "hard-working", "hardworking", "fast-paced", "leverage my",
           "to whom it may concern", "synergy", "go-getter", "think outside the box",
           "i believe i would be a great fit", "dynamic environment")

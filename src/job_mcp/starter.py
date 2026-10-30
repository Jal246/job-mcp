"""A starter watch list: tech companies known to post on Greenhouse, Lever or Ashby (each board
checked to exist in October 2026). add_companies(starter_list=True) adds them all.

Companies move between job boards now and then; a board that stops existing shows up as an
error in list_companies, and remove_company drops it.
"""

from job_mcp.boards import Board

STARTER = {
    # Greenhouse
    "Airbnb": Board("greenhouse", "airbnb"),
    "Anthropic": Board("greenhouse", "anthropic"),
    "Asana": Board("greenhouse", "asana"),
    "Brex": Board("greenhouse", "brex"),
    "Cloudflare": Board("greenhouse", "cloudflare"),
    "Coinbase": Board("greenhouse", "coinbase"),
    "Databricks": Board("greenhouse", "databricks"),
    "Datadog": Board("greenhouse", "datadog"),
    "Discord": Board("greenhouse", "discord"),
    "Dropbox": Board("greenhouse", "dropbox"),
    "Duolingo": Board("greenhouse", "duolingo"),
    "Elastic": Board("greenhouse", "elastic"),
    "Figma": Board("greenhouse", "figma"),
    "GitLab": Board("greenhouse", "gitlab"),
    "Groww": Board("greenhouse", "groww"),
    "Lyft": Board("greenhouse", "lyft"),
    "MongoDB": Board("greenhouse", "mongodb"),
    "Pinterest": Board("greenhouse", "pinterest"),
    "Reddit": Board("greenhouse", "reddit"),
    "Robinhood": Board("greenhouse", "robinhood"),
    "Scale AI": Board("greenhouse", "scaleai"),
    "Stripe": Board("greenhouse", "stripe"),
    "Twilio": Board("greenhouse", "twilio"),
    "Vercel": Board("greenhouse", "vercel"),
    # Lever
    "CRED": Board("lever", "cred"),
    "Meesho": Board("lever", "meesho"),
    "Palantir": Board("lever", "palantir"),
    "Spotify": Board("lever", "spotify"),
    # Ashby
    "Cohere": Board("ashby", "cohere"),
    "Linear": Board("ashby", "linear"),
    "Notion": Board("ashby", "notion"),
    "OpenAI": Board("ashby", "openai"),
    "Perplexity": Board("ashby", "perplexity"),
    "Plaid": Board("ashby", "plaid"),
    "PostHog": Board("ashby", "posthog"),
    "Ramp": Board("ashby", "ramp"),
    "Replit": Board("ashby", "replit"),
    "Sentry": Board("ashby", "sentry"),
    "Supabase": Board("ashby", "supabase"),
}

"""
Sentinel AI — the observability platform.

Everything in this package watches `rag/` from the outside. Nothing in
`rag/` imports anything from here, and nothing in here changes what the
chatbot returns to a user. If you deleted this whole package, the
chatbot would work identically -- it just wouldn't be observable.
"""

from dotenv import load_dotenv
load_dotenv()

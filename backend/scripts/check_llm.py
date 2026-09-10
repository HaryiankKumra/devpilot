"""Send one small review request to the configured provider and report back.

The fastest way to answer "is my API key working?" without opening a pull
request and waiting for a webhook to make its way through the queue.

    docker compose exec worker python scripts/check_llm.py

Or, outside Docker, from the `backend` directory:

    python scripts/check_llm.py

It uses the same factory, the same prompt builder and the same schema as the
real pipeline, so a pass here means the pipeline's LLM step works -- not merely
that a key is syntactically valid.

**This calls the provider for real.** In `anthropic` mode that costs money; the
diff below is deliberately tiny to keep it to a fraction of a cent. In `gemini`
mode it consumes one request from the free tier. In `mock` mode it costs
nothing and proves only that the wiring is intact.
"""

from __future__ import annotations

import sys
import time

from app.core.config import LLMMode, get_settings
from app.integrations.llm.factory import build_llm_provider
from app.integrations.llm.provider import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMError,
    LLMInvalidResponseError,
    LLMRateLimitError,
    LLMRefusalError,
)

# A deliberately small diff with one obvious real problem in it, so a working
# model has something to find and an empty result is itself informative.
SAMPLE_DIFF = """\
--- a/app/auth.py
+++ b/app/auth.py
@@ -1,3 +1,9 @@
+def login(request):
+    username = request.args.get("username")
+    password = request.args.get("password")
+    query = "SELECT * FROM users WHERE name = '" + username + "'"
+    row = db.execute(query).fetchone()
+    return row is not None and row["password"] == password
"""

SYSTEM_PROMPT = (
    "You are a senior engineer reviewing a pull request. Report only problems "
    "the diff actually shows. Cite the file and the line you are commenting on."
)

USER_PROMPT = (
    "Review this pull request.\n\n"
    "Changed file: app/auth.py\n"
    "Lines added: 1-6\n\n"
    f"```diff\n{SAMPLE_DIFF}```\n"
)


def main() -> int:
    settings = get_settings()

    print(f"mode      : {settings.llm_mode.value}")
    print(f"model     : {settings.llm_model}")
    if settings.llm_mode is LLMMode.GEMINI:
        print(f"keys      : {len(settings.gemini_api_key_list)} configured")
    if settings.llm_mode is LLMMode.MOCK:
        print("\nNote: mock mode calls no provider. Set DEVPILOT_LLM_MODE=gemini")
        print("      (or anthropic) to test a real key.\n")

    try:
        provider = build_llm_provider(settings)
    except LLMConfigurationError as exc:
        print(f"\nNOT CONFIGURED: {exc}")
        return 2

    print("\nsending one review request...")
    started = time.monotonic()

    try:
        result = provider.review(system_prompt=SYSTEM_PROMPT, user_prompt=USER_PROMPT)
    except LLMAuthenticationError as exc:
        # Permanent: the key is wrong, revoked, or lacks access to the model.
        print(f"\nAUTH FAILED: {exc}")
        print("The key was rejected. Retrying will not help -- check the value.")
        return 3
    except LLMRateLimitError as exc:
        print(f"\nRATE LIMITED: {exc}")
        print(f"Every configured key is throttled. Retry in {exc.retry_after_seconds}s.")
        return 4
    except LLMRefusalError as exc:
        # Worth distinguishing: the provider worked, the model declined.
        print(f"\nREFUSED: {exc}")
        print("The provider is reachable and the key is valid; the model declined.")
        return 5
    except LLMInvalidResponseError as exc:
        print(f"\nBAD RESPONSE: {exc}")
        return 6
    except LLMError as exc:
        print(f"\nFAILED: {type(exc).__name__}: {exc}")
        return 1

    elapsed = time.monotonic() - started
    review = result.review

    print(f"\nOK in {elapsed:.1f}s")
    print(f"model reported : {result.model_name}")
    print(f"tokens         : {result.usage.prompt_tokens} in, {result.usage.completion_tokens} out")
    print(f"summary        : {review.summary[:160]}")
    print(f"findings       : {len(review.findings)}")

    for finding in review.findings:
        line = finding.line if finding.line is not None else "-"
        print(f"  [{finding.severity.value:8}] {finding.file}:{line}  {finding.title}")
        print(f"             confidence {finding.confidence:.2f}  ({finding.category.value})")

    if not review.findings and settings.llm_mode is not LLMMode.MOCK:
        # Not a failure, but the sample diff has a SQL injection in it, so an
        # empty result usually means the model is weaker than the task needs.
        # Mock mode is excluded: it restates static analysis, which is not run
        # here, so zero findings is the expected result rather than a signal.
        print("\nNote: the sample diff contains a SQL injection. No findings")
        print("      suggests the configured model is struggling with this task.")

    return 0


if __name__ == "__main__":
    sys.exit(main())

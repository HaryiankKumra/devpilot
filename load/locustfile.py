"""Load tests for DevPilot.

Run against a stack you are willing to load, never production:

    pip install locust
    locust -f load/locustfile.py --host http://localhost:8000

    # Headless, with a report:
    locust -f load/locustfile.py --host http://localhost:8000 \\
        --users 50 --spawn-rate 5 --run-time 2m --headless --html load/report.html

Three profiles, matching the three shapes of traffic DevPilot actually sees:

* `ReadUser` -- the dashboard. Authenticated, read-only, and by far the most
  common. This is the one whose P95 a person feels.
* `WebhookSender` -- GitHub. Unauthenticated, write-heavy, bursty, and the only
  traffic DevPilot cannot slow down by asking.
* `AuthUser` -- login. Rare, but deliberately expensive: Argon2 is slow by
  design, so a login flood costs far more CPU per request than a read.

**Rate limiting will interfere.** That is the point of measuring: a 429 is a
successful defence, not a failed request, so the profiles below count them
separately rather than as errors. To measure raw capacity instead, run the API
with `DEVPILOT_RATE_LIMIT_ENABLED=false`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import random
import time
import uuid

from locust import HttpUser, between, events, task

# Must match DEVPILOT_GITHUB_WEBHOOK_SECRET on the target, or every webhook is
# correctly rejected with 401 and the test measures nothing but signature
# verification.
WEBHOOK_SECRET = os.environ.get(
    "DEVPILOT_GITHUB_WEBHOOK_SECRET", "local-dev-webhook-secret-change-for-real-github"
)

PASSWORD = "a-load-test-password"


def unique_email() -> str:
    return f"load-{uuid.uuid4().hex[:16]}@example.com"


def sign(body: bytes) -> str:
    """The signature GitHub would send for this body."""
    digest = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


class ReadUser(HttpUser):
    """A person with the dashboard open.

    Registers once on start, then reads. Weighted highest because dashboard
    reads are the traffic whose latency anyone actually experiences.
    """

    weight = 10
    # Think time between actions: a user reading a page, not a tight loop.
    wait_time = between(1, 4)

    def on_start(self) -> None:
        """Create an account and hold its token for the session.

        Registration is itself rate limited, and a throttled sign-up leaves the
        user with no token -- every later read then returns 401 and the run
        reports a wall of failures that say nothing about read capacity. So a
        429 here is retried with backoff, and a user that still cannot
        authenticate stops rather than generating noise.
        """
        self.headers: dict[str, str] = {}

        for attempt in range(5):
            email = unique_email()
            registration = self.client.post(
                "/api/v1/auth/register",
                json={"email": email, "password": PASSWORD},
                name="POST /auth/register",
                catch_response=True,
            )
            with registration as response:
                if response.status_code == 429:
                    # The limiter doing its job, not a failure.
                    response.success()
                    time.sleep(2 ** attempt)
                    continue
                if not response.ok:
                    return

            login = self.client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": PASSWORD},
                name="POST /auth/login",
                catch_response=True,
            )
            with login as response:
                if response.status_code == 429:
                    response.success()
                    time.sleep(2 ** attempt)
                    continue
                if response.ok:
                    self.headers = {
                        "Authorization": f"Bearer {response.json()['access_token']}"
                    }
                    return

        # Could not authenticate within the limiter's budget. Reading without a
        # token would only produce 401s that measure nothing.
        self.environment.runner.logger.warning(
            "load: a read user could not authenticate; it will idle"
        ) if self.environment.runner else None

    @task(5)
    def list_reviews(self) -> None:
        """The dashboard's own request."""
        if not self.headers:
            return

        with self.client.get(
            "/api/v1/reviews?limit=20",
            headers=self.headers,
            name="GET /reviews",
            catch_response=True,
        ) as response:
            # A 429 means the limiter worked. Counting it as a failure would
            # make a successful defence look like an outage.
            if response.status_code == 429:
                response.success()

    @task(3)
    def list_repositories(self) -> None:
        if not self.headers:
            return

        with self.client.get(
            "/api/v1/repositories",
            headers=self.headers,
            name="GET /repositories",
            catch_response=True,
        ) as response:
            if response.status_code == 429:
                response.success()

    @task(1)
    def health(self) -> None:
        """Exempt from rate limiting, so this is the honest floor for latency."""
        self.client.get("/health", name="GET /health")


class WebhookSender(HttpUser):
    """GitHub delivering pull request events.

    Unauthenticated, bursty, and write-heavy: each delivery inserts a row and
    may queue a job. This is the traffic DevPilot cannot ask to slow down.
    """

    weight = 3
    wait_time = between(0.5, 2)

    @task
    def deliver_pull_request(self) -> None:
        number = random.randint(1, 500)
        payload = {
            "action": "synchronize",
            "number": number,
            "pull_request": {
                "id": 800_000_000 + number,
                "number": number,
                "title": f"Load test pull request {number}",
                "state": "open",
                "draft": False,
                "merged": False,
                "user": {"id": 4_242_009, "login": "load-tester"},
                "head": {"ref": "feature/load", "sha": uuid.uuid4().hex + "0" * 8},
                "base": {"ref": "main", "sha": "b" * 40},
            },
            "repository": {
                "id": 900_000_001,
                "name": "checkout-service",
                "full_name": "devpilot-demo/checkout-service",
                "private": True,
                "default_branch": "main",
            },
            "installation": {"id": 10_000_001},
            "sender": {"id": 4_242_100, "login": "load-tester"},
        }
        body = json.dumps(payload).encode()

        self.client.post(
            "/api/v1/webhooks/github",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "pull_request",
                # Unique per delivery: a repeated id is correctly treated as a
                # duplicate and does no work, which would flatter the numbers.
                "X-GitHub-Delivery": str(uuid.uuid4()),
                "X-Hub-Signature-256": sign(body),
            },
            name="POST /webhooks/github",
        )


class AuthUser(HttpUser):
    """Repeated logins.

    Rare in real traffic but disproportionately expensive: Argon2 is
    deliberately slow, so this profile shows how few logins per second it takes
    to saturate CPU -- and why the auth endpoint has its own tight limit.
    """

    weight = 1
    wait_time = between(2, 5)

    def on_start(self) -> None:
        self.email = unique_email()
        self.client.post(
            "/api/v1/auth/register",
            json={"email": self.email, "password": PASSWORD},
            name="POST /auth/register",
        )

    @task
    def login(self) -> None:
        with self.client.post(
            "/api/v1/auth/login",
            json={"email": self.email, "password": PASSWORD},
            name="POST /auth/login",
            catch_response=True,
        ) as response:
            if response.status_code == 429:
                response.success()


@events.test_stop.add_listener
def report(environment, **_kwargs) -> None:  # type: ignore[no-untyped-def]
    """Print the percentiles the README asks for.

    Locust's own summary reports these too; this prints them in one block so a
    headless run can be pasted into a report without hunting through output.
    """
    stats = environment.stats.total
    if not stats.num_requests:
        return

    duration = max(1e-9, time.time() - (environment.stats.start_time or time.time()))
    failure_rate = stats.num_failures / stats.num_requests * 100

    print("\n=== DevPilot load test ===")
    print(f"Requests      : {stats.num_requests}")
    print(f"Failures      : {stats.num_failures} ({failure_rate:.2f}%)")
    print(f"Throughput    : {stats.num_requests / duration:.1f} req/s")
    print(f"P50 latency   : {stats.get_response_time_percentile(0.50):.0f} ms")
    print(f"P95 latency   : {stats.get_response_time_percentile(0.95):.0f} ms")
    print(f"P99 latency   : {stats.get_response_time_percentile(0.99):.0f} ms")
    print("==========================\n")

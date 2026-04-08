import hashlib
import hmac
from fastapi import APIRouter, Request, HTTPException, Header
from app.core.config import get_settings
from app.core.dedup import is_duplicate_delivery
from app.core.logging import get_logger
from app.tasks import review as review_tasks

router = APIRouter()
logger = get_logger(__name__)


def verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify GitHub webhook HMAC-SHA256 signature."""
    expected = "sha256=" + hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/webhook/github", status_code=202)
async def github_webhook(
    request: Request,
    x_hub_signature_256: str = Header(None),
    x_github_event: str = Header(None),
    x_github_delivery: str = Header(None),
):
    settings = get_settings()
    payload = await request.body()

    if not x_hub_signature_256 or not verify_signature(
        payload, x_hub_signature_256, settings.github_webhook_secret
    ):
        logger.warning("webhook_invalid_signature", delivery_id=x_github_delivery)
        raise HTTPException(status_code=401, detail="Invalid signature")

    if x_github_event != "pull_request":
        return {"detail": "event ignored"}

    data = await request.json()
    action = data.get("action")
    if action not in ("opened", "synchronize"):
        return {"detail": "action ignored"}

    # Idempotency: skip if GitHub retried the same delivery
    if x_github_delivery and is_duplicate_delivery(x_github_delivery):
        logger.info("webhook_duplicate_skipped", delivery_id=x_github_delivery)
        return {"detail": "duplicate delivery ignored"}

    pr_number = data["pull_request"]["number"]
    repo_full_name = data["repository"]["full_name"]

    # Immediately dispatch to Celery, return 202 within GitHub's 10s window
    review_tasks.run_review.delay(repo_full_name, pr_number)
    logger.info(
        "review_task_queued",
        repo=repo_full_name,
        pr=pr_number,
        delivery_id=x_github_delivery,
    )

    return {"detail": "review task queued"}

"""One phone job, start to finish: media in, post or reel, confirmation, first comment, cleanup.

Outcomes:
- ``published``: confirmed on the phone. ``comment`` says what happened to the first comment.
- ``failed``: stopped before the publish tap; the job may be retried.
- ``uncertain``: the publish tap happened but the post was not confirmed. Never published again
  automatically; the account's profile is checked first.
- ``blocked``: logged out or stopped by Facebook; the account needs review.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Callable

from backend.services.adb_client import AdbError

from phone.device import KEY_HOME, PhoneError, media_mime
from phone.flows import (COMMENT_WAITS, AccountBlocked, CommentUncertain, Flows, PostItem, PublishUncertain,
                         StepFailed)


EXTENSION = {"image/jpeg": ".jpg", "image/png": ".png", "video/mp4": ".mp4"}


def _media_name(path: str) -> str:
    return f"job_{uuid.uuid4().hex[:12]}{EXTENSION[media_mime(path)]}"


async def _put_media(flows: Flows, path: str) -> str:
    device = flows.device
    name = _media_name(path)

    async def human():
        try:
            return await asyncio.to_thread(device.put_media, path, name)
        except (PhoneError, AdbError) as error:
            raise StepFailed(f"media: {error}") from error

    async def shortcut():
        try:
            return await asyncio.to_thread(device.put_media_shell, path, name)
        except (PhoneError, AdbError) as error:
            raise StepFailed(f"media by shell: {error}") from error
    return await flows.hybrid("add_media", human, shortcut)


async def run_post(device, item: PostItem, media_path: str, log: Callable[..., None], *,
                   human_only: bool = False, comment_now: bool = True,
                   before_publish: Callable[[], None] = lambda: None) -> dict:
    """Posts `item` with the media at `media_path`. With `comment_now` the first comment follows
    right away (the phone stays held through the three looks); otherwise it is left "pending" for a
    follow-up `run_comment` once the phone is free."""
    flows = Flows(device, log, human_only=human_only)
    result: dict = {"status": "failed", "kind": item.kind, "stage": "media", "comment": "not_requested"}
    media_uri, tapped = None, None
    begun = device.now()
    try:
        media_uri = await _put_media(flows, media_path)
        result["stage"] = "open"
        await flows.open_feed()
        result["stage"] = "compose"
        await flows.start_post(item, media_uri, media_mime(media_path))
        result["stage"] = "publish"
        tapped = await flows.publish(before_publish)
        result["stage"] = "confirm"
        result["publish_seconds"] = round(await flows.wait_published(item, tapped))
        result["status"] = "published"
        log(event="published", kind=item.kind, seconds=result["publish_seconds"])
        if item.comment:
            result["comment"] = "pending"
            if comment_now:
                result["stage"] = "comment"
                result.update(await _comment(flows, item, tapped))
    except AccountBlocked as blocked:
        # After the publish tap the post may exist even though the account is now stopped.
        result.update(status="uncertain" if tapped else "blocked", reason=str(blocked), account_blocked=True)
    except PublishUncertain as unconfirmed:
        result.update(status="uncertain", reason=str(unconfirmed))
    except Exception as failure:  # a dropped stream counts too: after the tap it is never "failed"
        result.update(status="uncertain" if tapped else "failed", reason=f"{type(failure).__name__}: {failure}"[:400])
    finally:
        result["fallbacks"] = flows.fallbacks
        result["seconds"] = round(device.now() - begun)
        log(event=result["status"], stage=result["stage"], reason=result.get("reason"))
        await _clean_up(flows, result, media_uri)
    return result


async def run_comment(device, item: PostItem, log: Callable[..., None], *, published_ago: float | None = None,
                      human_only: bool = False) -> dict:
    """The first comment of an already published post: a follow-up job. `published_ago` is the time
    since the publish tap: the three looks keep their timing when it is recent; a later start looks
    at once and allows posts as old as that. Without it, the post is looked for at any age."""
    flows = Flows(device, log, human_only=human_only)
    result: dict = {"status": "published", "kind": item.kind, "stage": "comment"}
    try:
        if published_ago is None:
            result.update(await _comment(flows, item, device.now(), waits=(0, 25, 25), max_age_minutes=10 ** 6))
        elif published_ago < COMMENT_WAITS[0]:
            result.update(await _comment(flows, item, device.now() - published_ago))
        else:
            result.update(await _comment(flows, item, device.now(), waits=(0, 25, 25),
                                         max_age_minutes=published_ago / 60 + 4))
    except AccountBlocked as blocked:
        result.update(comment="blocked", reason=str(blocked), account_blocked=True)
    finally:
        result["fallbacks"] = flows.fallbacks
        await _home(flows)
    return result


async def run_get_ready(device, log: Callable[..., None]) -> dict:
    """Setup's first-use pass: Facebook's first screens answered, nothing posted. Taps only."""
    flows = Flows(device, log, human_only=True)
    try:
        await flows.get_ready()
        result = {"status": "ready"}
    except AccountBlocked as blocked:
        result = {"status": "blocked", "reason": str(blocked), "account_blocked": True}
    except Exception as failure:
        result = {"status": "failed", "reason": f"{type(failure).__name__}: {failure}"[:400]}
    await _home(flows)
    return result


async def _comment(flows: Flows, item: PostItem, published_at: float, waits=COMMENT_WAITS,
                   max_age_minutes: float | None = None) -> dict:
    try:
        looks = await flows.find_and_comment(item, published_at, waits, max_age_minutes)
        flows.log(event="commented", looks=looks)
        return {"comment": "posted", "comment_looks": looks}
    except CommentUncertain as unseen:
        flows.log(event="comment_uncertain", reason=str(unseen)[:300])
        return {"comment": "uncertain", "comment_reason": str(unseen)[:300]}
    except (StepFailed, PhoneError, AdbError) as failure:
        flows.log(event="comment_skipped", reason=str(failure)[:300])
        try:
            await flows.discard_until_feed()
        except StepFailed:
            pass
        return {"comment": "skipped", "comment_reason": str(failure)[:300]}


async def _clean_up(flows: Flows, result: dict, media_uri: str | None) -> None:
    device = flows.device
    if result["status"] == "failed":
        try:
            await flows.discard_until_feed()  # the composer may still hold the media
        except (StepFailed, AccountBlocked) as cleanup:
            flows.log(event="cleanup_failed", reason=str(cleanup)[:200])
    if media_uri:
        if result["status"] == "uncertain":
            # An upload may still be running from it; the uncertain review removes it later.
            result["media_left"] = media_uri
            flows.log(event="media_kept", reason="publish not confirmed", uri=media_uri)
        else:
            try:
                await asyncio.to_thread(device.delete_media, media_uri)
            except (PhoneError, AdbError) as error:
                result["media_left"] = media_uri
                flows.log(event="media_not_removed", reason=str(error)[:200])
    await _home(flows)


async def _home(flows: Flows) -> None:
    try:
        await flows.device.key(KEY_HOME)
    except Exception as error:  # the stream may already be gone; leaving the app open is harmless
        flows.log(event="home_failed", reason=str(error)[:200])

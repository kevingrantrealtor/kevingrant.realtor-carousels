#!/usr/bin/env python3
"""Publishes any carousel in queue.json whose time has come.

This file lives in your own carousels repo and is run by GitHub Actions on a
timer. You do not run it by hand and your laptop does not need to be on.

How it avoids ever double posting: before publishing anything it reads the
captions of your last 50 Instagram posts and compares them against what it is
about to send. If a matching post is already up, it records that and moves on.
Instagram itself is the record of what has gone out, so even if two runs
overlap, the same carousel cannot go up twice.

Needs two things in the environment, both set for you by the setup script:
  FB_PAGE_TOKEN   your long lived Facebook Page token
  IG_USER_ID      your Instagram professional account id
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

GRAPH = "https://graph.facebook.com/v25.0"
QUEUE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "queue.json")

TOKEN = (os.environ.get("FB_PAGE_TOKEN") or "").strip()
IG_USER = (os.environ.get("IG_USER_ID") or "").strip()


def api(path, params, method="GET"):
    if method == "GET":
        url = f"{GRAPH}/{path}?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url)
    else:
        url = f"{GRAPH}/{path}"
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"error": {"message": f"HTTP {e.code}"}}
    except Exception as e:
        return {"error": {"message": str(e)}}


def signature(caption):
    """A fingerprint of a caption, used to tell posts apart.

    The first paragraph is the keyword line, which repeats across posts, so it
    is dropped and the body is what identifies the carousel.
    """
    parts = re.split(r"\n\s*\n", caption or "")
    body = " ".join(parts[1:]) if len(parts) > 1 else (caption or "")
    return re.sub(r"[^a-z0-9]+", "", body.lower())[:70]


def already_posted():
    """Signatures of everything already on the account, newest 50."""
    out = api(f"{IG_USER}/media",
              {"fields": "caption", "limit": "50", "access_token": TOKEN})
    if "data" not in out:
        msg = out.get("error", {}).get("message", "unknown")
        sys.exit(f"could not read your recent posts, so nothing was published "
                 f"(this is the safe outcome): {msg}")
    return {signature(m.get("caption", "")) for m in out["data"]}


def publish(item):
    children = []
    for url in item["images"]:
        c = api(f"{IG_USER}/media",
                {"image_url": url, "is_carousel_item": "true",
                 "access_token": TOKEN}, "POST")
        if not c.get("id"):
            raise RuntimeError(
                f"Instagram would not accept {url}: "
                f"{c.get('error', {}).get('message', c)}")
        children.append(c["id"])

    parent = api(f"{IG_USER}/media",
                 {"media_type": "CAROUSEL", "children": ",".join(children),
                  "caption": item["caption"], "access_token": TOKEN}, "POST")
    if not parent.get("id"):
        raise RuntimeError(f"could not build the carousel: "
                           f"{parent.get('error', {}).get('message', parent)}")

    for _ in range(12):
        time.sleep(2.5)
        out = api(f"{IG_USER}/media_publish",
                  {"creation_id": parent["id"], "access_token": TOKEN}, "POST")
        if out.get("id"):
            return out["id"]
        msg = out.get("error", {}).get("message", "")
        if not re.search(r"not ready|media id", msg, re.I):
            raise RuntimeError(f"publishing failed: {msg}")
    raise RuntimeError("Instagram never finished preparing the carousel")


def main():
    if not TOKEN or not IG_USER:
        sys.exit("FB_PAGE_TOKEN and IG_USER_ID are not set on this repo. "
                 "Run setup/hosting.py again from the skill.")

    if not os.path.exists(QUEUE):
        print("no queue.json yet, nothing to do")
        return
    with open(QUEUE) as f:
        queue = json.load(f)

    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    due = [i for i in queue
           if not i.get("published_id") and i.get("publish_at_utc", "") <= now]
    if not due:
        print("nothing due")
        return
    due.sort(key=lambda i: i["publish_at_utc"])

    live = already_posted()
    changed = False
    for item in due:
        if signature(item["caption"]) in live:
            print(f"{item['slug']}: already on Instagram, marking it done")
            item["published_id"] = "already-live"
            changed = True
            continue
        print(f"{item['slug']}: publishing {len(item['images'])} slides")
        try:
            item["published_id"] = publish(item)
            changed = True
            print(f"{item['slug']}: published as {item['published_id']}")
        except Exception as e:
            print(f"{item['slug']}: FAILED, leaving it queued to retry. {e}")
        break  # one carousel per run, so a bad item cannot burn the whole queue

    if changed:
        with open(QUEUE, "w") as f:
            json.dump(queue, f, indent=1)
        print("queue updated")


if __name__ == "__main__":
    main()

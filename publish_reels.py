#!/usr/bin/env python3
"""Posts your scheduled reels: two trials, then the winner to your main feed.

This file lives in your own GitHub repo and runs on a timer in GitHub's cloud,
so your laptop can be shut. You never run it by hand.

Each story's versions go out as TRIAL reels (shown to people who don't follow
you) at the times you picked. Then the winners go to your MAIN feed, one of
two ways (you pick, in setup/posting.py):
  A vs B      a few days after a story's trial B, its two versions are
              compared and the better one goes to your main feed.
  best of day all the trials that went out on one day compete, and the best
              ones (as many as you have main-feed times) go to your main feed
              a set number of days later. One spot per story at most.
Better = more comments, then more views (when your token can read views), then
more likes. A trial with no likes and no comments never goes to the main feed.
A winner is uploaded again as its own new post, not Instagram's "share to main
feed" button on the trial. Video files are deleted from GitHub once done.

It works through everything due, one post at a time, and saves after each, so
a problem with one reel cannot block the rest. Your posting schedule (how many
a day, which days) is set on your computer with setup/posting.py; this just
posts what comes due. Instagram's own limit is about 50 posts a day.
Before posting, it reads your recent posts and skips anything already up, so a
reel can never go out twice.

Needs FB_PAGE_TOKEN and IG_USER_ID (repository secrets the setup stored) and
GH_TOKEN (GitHub provides it to the timer).
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

GRAPH = "https://graph.facebook.com/v25.0"
RUPLOAD = "https://rupload.facebook.com/ig-api-upload/v25.0"
HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE = os.path.join(HERE, "reels.json")
TOKEN = (os.environ.get("FB_PAGE_TOKEN") or "").strip()
IG_USER = (os.environ.get("IG_USER_ID") or "").strip()
REPO = os.environ.get("GITHUB_REPOSITORY", "")


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


def api(path, params, method="GET", base=GRAPH):
    params = {**params, "access_token": TOKEN}
    if method == "GET":
        req = urllib.request.Request(f"{base}/{path}?" + urllib.parse.urlencode(params))
    else:
        req = urllib.request.Request(f"{base}/{path}", data=urllib.parse.urlencode(params).encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"error": {"message": f"HTTP {e.code}"}}
    except Exception as e:
        return {"error": {"message": str(e)}}


def err(out):
    return (out.get("error") or {}).get("message") or json.dumps(out)[:300]


def signature(caption):
    """Paragraph two is the spoken hook, different for every version, so it identifies the post."""
    parts = [p for p in re.split(r"\n\s*\n", caption or "") if p.strip()]
    body = parts[1] if len(parts) > 1 else (caption or "")
    return re.sub(r"[^a-z0-9]+", "", body.lower())[:70]


def live_posts():
    out = api(f"{IG_USER}/media", {"fields": "id,caption,is_shared_to_feed,media_product_type", "limit": "60"})
    if "data" not in out:
        sys.exit(f"could not read your recent posts, so nothing was posted (the safe outcome): {err(out)}")
    return out["data"]


def gh(*args):
    return subprocess.run(["gh", *args], capture_output=True, text=True)


def download(tag, asset):
    d = os.path.join("/tmp", "reels")
    os.makedirs(d, exist_ok=True)
    r = gh("release", "download", tag, "-R", REPO, "-p", asset, "-D", d, "--clobber")
    path = os.path.join(d, asset)
    if r.returncode or not os.path.exists(path):
        raise RuntimeError(f"could not download {asset} from GitHub: {r.stderr.strip()}")
    return path


def upload_reel(path, caption, trial):
    params = {"media_type": "REELS", "upload_type": "resumable", "caption": caption}
    if trial:
        # MANUAL: Instagram never moves a trial to the main feed by itself. The
        # winner goes to the main feed as its own separate upload instead.
        params["trial_params"] = json.dumps({"graduation_strategy": "MANUAL"})
    c = api(f"{IG_USER}/media", params, "POST")
    if not c.get("id"):
        raise RuntimeError(f"Instagram would not start the upload: {err(c)}")
    size = os.path.getsize(path)
    req = urllib.request.Request(f"{RUPLOAD}/{c['id']}", data=open(path, "rb").read(), method="POST",
                                 headers={"Authorization": f"OAuth {TOKEN}", "offset": "0", "file_size": str(size)})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            up = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"the video upload failed: HTTP {e.code} {e.read().decode()[:300]}")
    if not up.get("success", True):
        raise RuntimeError(f"the video upload failed: {up}")
    for _ in range(60):                     # up to ten minutes for Instagram to process it
        time.sleep(10)
        st = api(c["id"], {"fields": "status_code,status"})
        code = st.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram could not process the video: {st.get('status')}")
    else:
        raise RuntimeError("Instagram took too long to process the video; it will try again next run")
    for _ in range(6):
        pub = api(f"{IG_USER}/media_publish", {"creation_id": c["id"]}, "POST")
        if pub.get("id"):
            return pub["id"]
        if not re.search(r"not ready|media id", err(pub), re.I):
            raise RuntimeError(f"publishing failed: {err(pub)}")
        time.sleep(10)
    raise RuntimeError("Instagram never finished preparing the reel")


def score(media_id):
    f = api(media_id, {"fields": "like_count,comments_count"})
    views = None
    ins = api(f"{media_id}/insights", {"metric": "views"})
    for m in ins.get("data", []):
        if m.get("name") == "views":
            views = (m.get("values") or [{}])[0].get("value", m.get("total_value", {}).get("value"))
    return {"comments": f.get("comments_count", 0) or 0, "likes": f.get("like_count", 0) or 0, "views": views}


def cleanup(story):
    for v in story["versions"]:
        gh("release", "delete", v["tag"], "-R", REPO, "-y", "--cleanup-tag")
    story["files_deleted"] = True


def cleanup_version(v):
    gh("release", "delete", v["tag"], "-R", REPO, "-y", "--cleanup-tag")
    v["file_deleted"] = True


def rank_key(v):
    sc = v["score"]
    return (sc["comments"], sc["views"] or 0, sc["likes"])


def flopped(v):
    return v["score"]["comments"] == 0 and v["score"]["likes"] == 0


def day_batches(queue):
    """Best-of-the-day stories: every trial version grouped by the day it posted on."""
    out = {}
    for s in queue:
        if s.get("cancelled") or s.get("pick") != "day":
            continue
        for v in s["versions"]:
            out.setdefault(v["batch"], []).append((s, v))
    return out


def next_job(queue, tried=()):
    t = now()
    stories = sorted(queue, key=lambda s: s["versions"][0]["trial_at_utc"])
    for s in stories:
        if s.get("cancelled"):
            continue
        for v in s["versions"]:
            if not v.get("media_id") and not v.get("failed") and v["trial_at_utc"] <= t and f"t:{s['slug']}:{v['v']}" not in tried:
                return "trial", s, v
    # best of the day: judge a day's trials once its first main time comes and they have all gone out
    for key, items in sorted(day_batches(queue).items()):
        vs = [v for _, v in items]
        if (not all(v.get("judged") for v in vs) and min(vs[0]["pick_at_utc"]) <= t
                and all(v.get("media_id") or v.get("failed") for v in vs) and f"j:{key}" not in tried):
            return "judge", key, items
    for s in stories:
        if s.get("cancelled"):
            continue
        if s.get("pick") == "day":
            for v in s["versions"]:
                if v.get("picked") and not v.get("main_done") and v["main_at_utc"] <= t and f"d:{s['slug']}:{v['v']}" not in tried:
                    return "daymain", s, v
            continue
        m = s.setdefault("main", {})
        if (all(v.get("media_id") or v.get("failed") for v in s["versions"]) and not m.get("done")
                and s["main_at_utc"] <= t and f"m:{s['slug']}" not in tried):
            return "main", s, None
    return None, None, None


def post_trial(story, ver, live):
    sig = signature(ver["caption"])
    hit = next((m for m in live if signature(m.get("caption", "")) == sig), None)
    if hit:
        print(f"{story['slug']} {ver['v']}: already on Instagram, marking it done")
        ver["media_id"], ver["posted_at_utc"] = hit["id"], now()
        return
    print(f"{story['slug']} {ver['v']}: posting as a trial reel")
    try:
        ver["media_id"] = upload_reel(download(ver["tag"], ver["asset"]), ver["caption"], trial=True)
        ver["posted_at_utc"] = now()
        print(f"  posted {ver['media_id']}")
    except Exception as e:
        ver["tries"] = ver.get("tries", 0) + 1
        ver["last_error"] = str(e)[:400]
        if ver["tries"] >= 3 or re.search(r"trial", str(e), re.I):
            ver["failed"] = True
        print(f"  FAILED ({ver['tries']} of 3): {e}")


def post_main(label, ver, live):
    """Upload a winning version again as its own main-feed post. Returns (media id, error)."""
    sig = signature(ver["caption"])
    hit = next((x for x in live if signature(x.get("caption", "")) == sig and x.get("is_shared_to_feed")), None)
    if hit:
        return hit["id"], None
    print(f"{label}: posting to the main feed")
    try:
        return upload_reel(download(ver["tag"], ver["asset"]), ver["caption"], trial=False), None
    except Exception as e:
        print(f"  FAILED: {e}")
        return None, str(e)[:400]


def judge_day(queue, key, items):
    """Rank one day's trials and give its best ones that day's main-feed times, one per story."""
    slots = sorted(items[0][1]["pick_at_utc"])
    posted = [(s, v) for s, v in items if v.get("media_id")]
    for _, v in posted:
        v["score"] = score(v["media_id"])
    # a story already headed to (or on) the main feed never gets a second spot
    used = {s["slug"] for s in queue if s.get("pick") == "day" and any(v.get("picked") for v in s["versions"])}
    picks = []
    for s, v in sorted(posted, key=lambda sv: rank_key(sv[1]), reverse=True):
        if len(picks) == len(slots):
            break
        if s["slug"] in used or flopped(v):
            continue
        picks.append((s, v))
        used.add(s["slug"])
    for i, (s, v) in enumerate(picks):
        v.update(picked=True, main_at_utc=slots[i])
    for s, v in items:
        v["judged"] = True
        if not v.get("picked"):
            v["result"] = "not picked for the main feed" if v.get("media_id") else "never went out"
            cleanup_version(v)
    names = ", ".join(f"{s['slug']} {v['v']}" for s, v in picks) or "none (no trial got a like or a comment)"
    print(f"day {key}: {len(posted)} trials judged, main feed picks: {names}")


def main():
    if not TOKEN or not IG_USER:
        sys.exit("FB_PAGE_TOKEN and IG_USER_ID are not set on this repo. Run setup/hosting.py again from the skill.")
    if not os.path.exists(QUEUE):
        print("no reels.json yet, nothing to do")
        return
    queue = json.load(open(QUEUE))
    began, handled, tried = time.time(), 0, set()
    # everything due, one at a time, for up to ~15 minutes (the timer gives each run 30)
    while time.time() - began < 15 * 60:
        kind, story, ver = next_job(queue, tried)
        if not kind:
            break
        if kind == "trial":
            tried.add(f"t:{story['slug']}:{ver['v']}")
            post_trial(story, ver, live_posts())
        elif kind == "judge":
            tried.add(f"j:{story}")
            judge_day(queue, story, ver)
        elif kind == "daymain":
            tried.add(f"d:{story['slug']}:{ver['v']}")
            mid, err_ = post_main(f"{story['slug']} {ver['v']}", ver, live_posts())
            if mid:
                ver.update(main_done=True, main_media_id=mid, main_posted_at_utc=now(), result="posted to the main feed")
                cleanup_version(ver)
            else:
                ver["main_tries"] = ver.get("main_tries", 0) + 1
                ver["last_error"] = err_
                if ver["main_tries"] >= 3:
                    ver.update(main_done=True, result=f"main feed post failed 3 times: {err_[:200]}")
                    cleanup_version(ver)
        else:
            tried.add(f"m:{story['slug']}")
            m = story["main"]
            posted = [v for v in story["versions"] if v.get("media_id")]
            if not posted:
                m.update(done=True, result="no trial went out, so nothing goes to the main feed")
            else:
                for v in posted:
                    v["score"] = score(v["media_id"])
                best = max(posted, key=lambda v: (*rank_key(v), v["v"] == "A"))
                if all(flopped(v) for v in posted):
                    m.update(done=True, result="both trials got no likes or comments, so it stays off the main feed")
                else:
                    print(f"{story['slug']}: version {best['v']} won {best['score']}")
                    mid, err_ = post_main(story["slug"], best, live_posts())
                    if mid:
                        m.update(done=True, winner=best["v"], media_id=mid, posted_at_utc=now(), result="posted to the main feed")
                    else:
                        m["tries"] = m.get("tries", 0) + 1
                        m["last_error"] = err_
                        if m["tries"] >= 3:
                            m.update(done=True, result=f"main feed post failed 3 times: {err_[:200]}")
            print(f"{story['slug']}: {m.get('result', 'will retry')}")
            if m.get("done"):
                cleanup(story)
        json.dump(queue, open(QUEUE, "w"), indent=1)   # save after each, so progress survives a crash
        handled += 1
    print(f"{handled} handled this run" if handled else "nothing due")


if __name__ == "__main__":
    main()

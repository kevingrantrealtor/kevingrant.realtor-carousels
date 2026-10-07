#!/usr/bin/env python3
"""Posts your scheduled reels: two trials, then the winner to your main feed.

This file lives in your own GitHub repo and runs on a timer in GitHub's cloud,
so your laptop can be shut. You never run it by hand.

For each story in reels.json:
  1. Version A goes out as a TRIAL reel (shown to people who don't follow you).
  2. Version B goes out as a trial a few days later.
  3. A few days after that, it compares the two: more comments wins; a tie goes
     to more views (when your token can read views), then more likes. The
     winner is uploaded again as its own new post on your MAIN feed. It is not
     Instagram's "share to main feed" button on the trial; it is a separate post.
     If both trials got nothing at all, nothing goes to your main feed.
  4. The video files are deleted from GitHub once the story is done.

One thing per run, so a problem with one reel cannot burn the whole queue.
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


def next_job(queue):
    t = now()
    for s in sorted(queue, key=lambda s: s["versions"][0]["trial_at_utc"]):
        if s.get("cancelled"):
            continue
        for v in s["versions"]:
            if not v.get("media_id") and not v.get("failed") and v["trial_at_utc"] <= t:
                return "trial", s, v
        m = s.setdefault("main", {})
        if all(v.get("media_id") or v.get("failed") for v in s["versions"]) and not m.get("done") and s["main_at_utc"] <= t:
            return "main", s, None
    return None, None, None


def main():
    if not TOKEN or not IG_USER:
        sys.exit("FB_PAGE_TOKEN and IG_USER_ID are not set on this repo. Run setup/hosting.py again from the skill.")
    if not os.path.exists(QUEUE):
        print("no reels.json yet, nothing to do")
        return
    queue = json.load(open(QUEUE))
    kind, story, ver = next_job(queue)
    if not kind:
        print("nothing due")
        return
    live = live_posts()

    if kind == "trial":
        sig = signature(ver["caption"])
        hit = next((m for m in live if signature(m.get("caption", "")) == sig), None)
        if hit:
            print(f"{story['slug']} {ver['v']}: already on Instagram, marking it done")
            ver["media_id"], ver["posted_at_utc"] = hit["id"], now()
        else:
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
    else:
        m = story["main"]
        posted = [v for v in story["versions"] if v.get("media_id")]
        if not posted:
            m.update(done=True, result="no trial went out, so nothing goes to the main feed")
        else:
            for v in posted:
                v["score"] = score(v["media_id"])
            best = max(posted, key=lambda v: (v["score"]["comments"], v["score"]["views"] or 0, v["score"]["likes"], v["v"] == "A"))
            flop = all(v["score"]["comments"] == 0 and v["score"]["likes"] == 0 for v in posted)
            if flop:
                m.update(done=True, result="both trials got no likes or comments, so it stays off the main feed")
            else:
                sig = signature(best["caption"])
                hit = next((x for x in live if signature(x.get("caption", "")) == sig and x.get("is_shared_to_feed")), None)
                if hit:
                    m.update(done=True, winner=best["v"], media_id=hit["id"], result="already on the main feed")
                else:
                    print(f"{story['slug']}: version {best['v']} won {best['score']}, posting it to the main feed")
                    try:
                        m["media_id"] = upload_reel(download(best["tag"], best["asset"]), best["caption"], trial=False)
                        m.update(done=True, winner=best["v"], posted_at_utc=now(), result="posted to the main feed")
                    except Exception as e:
                        m["tries"] = m.get("tries", 0) + 1
                        m["last_error"] = str(e)[:400]
                        if m["tries"] >= 3:
                            m.update(done=True, result=f"main feed post failed 3 times: {str(e)[:200]}")
                        print(f"  FAILED: {e}")
        print(f"{story['slug']}: {m.get('result', 'will retry')}")
        if m.get("done"):
            cleanup(story)

    json.dump(queue, open(QUEUE, "w"), indent=1)
    print("reels.json updated")


if __name__ == "__main__":
    main()

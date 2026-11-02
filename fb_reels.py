#!/usr/bin/env python3
"""Copies each main-feed reel to your Facebook Page as a Facebook Reel.

Kevin's call (11/2/26): every reel that goes to the Instagram main feed also
goes to the Page, with the same caption. Trials never go to Facebook.

This file is ours, not the bangers skill's, so the skill's updates never
overwrite it. It runs right after the reel poster (after-reels.yml) and reads
reels.json without ever writing to it. What went to Facebook is kept in
fb_reels.json.

The video comes from the Instagram post itself (its media_url), because the
reel poster deletes the GitHub copy as soon as the Instagram post is up.

How it avoids double posting: before posting it reads the Page's newest 50
reels and compares captions. A reel more than a day past its Instagram post
is never sent late.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

GRAPH = "https://graph.facebook.com/v25.0"
RUPLOAD = "https://rupload.facebook.com/video-upload/v25.0"
HERE = os.path.dirname(os.path.abspath(__file__))
REELS = os.path.join(HERE, "reels.json")
DONE = os.path.join(HERE, "fb_reels.json")

TOKEN = (os.environ.get("FB_PAGE_TOKEN") or "").strip()
FB_FROM = "2026-11-02T20:00:00"     # main-feed reels before this went to Instagram only
LATE_HOURS = 24
TRIES = 3


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


def request(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {},
                                 method="POST" if data is not None else "GET")
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


def api(path, params, method="GET"):
    params = {**params, "access_token": TOKEN}
    if method == "GET":
        return request(f"{GRAPH}/{path}?" + urllib.parse.urlencode(params))
    return request(f"{GRAPH}/{path}", urllib.parse.urlencode(params).encode())


def err(out):
    return (out.get("error") or {}).get("message") or json.dumps(out)[:300]


def signature(caption):
    """Same fingerprint the carousel publisher uses: the caption minus its first (keyword) line."""
    parts = re.split(r"\n\s*\n", caption or "")
    body = " ".join(parts[1:]) if len(parts) > 1 else (caption or "")
    return re.sub(r"[^a-z0-9]+", "", body.lower())[:70]


def on_page():
    """Signatures of the Page's newest 50 reels, or None when Facebook will not say."""
    out = api("me/video_reels", {"fields": "description", "limit": "50"})
    if "data" not in out:
        print(f"facebook: could not read your Page's reels, so nothing went there "
              f"(this is the safe outcome): {err(out)}")
        return None
    return {signature(v.get("description", "")) for v in out["data"]}


def post_reel(video_url, caption):
    """Start, hand Facebook the video link, publish. Returns the video id."""
    start = api("me/video_reels", {"upload_phase": "start"}, "POST")
    vid = start.get("video_id")
    if not vid:
        raise RuntimeError(f"Facebook would not start the upload: {err(start)}")
    up = request(f"{RUPLOAD}/{vid}", b"", {"Authorization": f"OAuth {TOKEN}", "file_url": video_url})
    if not up.get("success"):
        raise RuntimeError(f"Facebook could not fetch the video: {err(up)}")
    fin = api("me/video_reels", {"upload_phase": "finish", "video_id": vid,
                                  "video_state": "PUBLISHED", "description": caption}, "POST")
    if not fin.get("success"):
        raise RuntimeError(f"Facebook would not publish the reel: {err(fin)}")
    for _ in range(30):                     # up to five minutes to process
        st = api(vid, {"fields": "status"}).get("status") or {}
        if st.get("video_status") in ("ready", "published") or \
                (st.get("publishing_phase") or {}).get("status") == "complete":
            break
        if st.get("video_status") == "error":
            raise RuntimeError(f"Facebook could not process the video: {json.dumps(st)[:300]}")
        time.sleep(10)
    return vid


def due(reels, done, cut):
    """Main-feed reels on Instagram that still need to go to the Page."""
    out = []
    for s in reels:
        for v in s.get("versions", []):
            mid = v.get("main_media_id")
            at = v.get("main_posted_at_utc") or ""
            key = f"{s['slug']}:{v['v']}"
            if not mid or v.get("trial_missed") or at < FB_FROM:
                continue
            rec = done.get(key, {})
            if rec.get("fb_id") or rec.get("gave_up"):
                continue
            if at < cut:
                done[key] = {**rec, "gave_up": "more than a day late, not sent to Facebook"}
                continue
            out.append((key, mid, v.get("caption", "")))
    return out


def main():
    if not TOKEN:
        sys.exit("FB_PAGE_TOKEN is not set on this repo.")
    if not os.path.exists(REELS):
        print("no reels.json yet, nothing to do")
        return
    reels = json.load(open(REELS, encoding="utf8"))
    done = json.load(open(DONE, encoding="utf8")) if os.path.exists(DONE) else {}
    before = json.dumps(done, sort_keys=True)
    cut = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - LATE_HOURS * 3600))

    todo = due(reels, done, cut)
    if not todo:
        print("facebook reels: nothing due")
    else:
        live = on_page()
        for key, mid, caption in todo if live is not None else []:
            rec = done.setdefault(key, {})
            if signature(caption) in live:
                print(f"{key}: already on your Page, marking it done")
                rec["fb_id"] = "already-live"
                continue
            media = api(mid, {"fields": "media_url,caption"})
            caption = media.get("caption") or caption
            print(f"{key}: posting to your Page as a Facebook Reel")
            try:
                if not media.get("media_url"):
                    raise RuntimeError(f"Instagram would not give the video link: {err(media)}")
                rec["fb_id"] = post_reel(media["media_url"], caption)
                rec["posted_at_utc"] = now()
                print(f"{key}: on your Page as {rec['fb_id']}")
            except Exception as e:
                rec["tries"] = rec.get("tries", 0) + 1
                rec["last_error"] = str(e)[:400]
                print(f"{key}: Facebook FAILED ({rec['tries']} of {TRIES}). {e}")
                if rec["tries"] >= TRIES:
                    rec["gave_up"] = "failed 3 times"
            break  # one per run, like the carousels

    if json.dumps(done, sort_keys=True) != before:
        json.dump(done, open(DONE, "w", encoding="utf8"), indent=1)
        print("fb_reels.json updated")


if __name__ == "__main__":
    main()

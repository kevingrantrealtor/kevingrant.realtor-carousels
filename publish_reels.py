#!/usr/bin/env python3
"""Posts your scheduled reels: each story's trials, then the winner to your main feed.

This file lives in your own GitHub repo and runs on a timer in GitHub's cloud,
so your laptop can be shut. You never run it by hand.

Each story has three versions (A, B and C, one per kept hook). They go out
as TRIAL reels (shown to people who don't follow you), one a day, at the times
you picked. Every trial of a story goes out before that story's main post,
because a trial posted after its own main post gets flagged as spam. Then the
winners go to your MAIN feed, one of two ways (you pick, in setup/posting.py):
  story       about a day after a story's last trial, its versions are
              compared and the best one goes to your main feed.
  best of day every story whose last trial went out on one day competes, and
              the best ones (as many as you have main-feed times) go to your
              main feed. One spot per story at most.
Every trial is read once it is about a day old, so versions of different ages
compare fairly. When your versions end on different keywords (one per offer),
each version counts only the comments that ask for ITS OWN keyword. A version
that ends on the follow ask counts no keyword comments. Best = keyword comments first (a lead of 2 or more), then views
(a lead of 15% or more, when your token can read views), then the most
comments, views and likes. A trial with no likes and no comments never goes to
the main feed. A winner is uploaded again as its own new post, not Instagram's
"share to main feed" button on the trial. Video files are deleted from GitHub
once done.

It works through everything due, one post at a time, and saves after each, so
a problem with one reel cannot block the rest. Your posting schedule (how many
a day, which days) is set on your computer with setup/posting.py; this just
posts what comes due. Instagram's own limit is about 50 posts a day.
Before posting, it reads your recent posts and skips anything already up, so a
reel can never go out twice.

Instagram gives trial reels to professional accounts with 200 followers or
more. The account must also be public and allowed to be recommended (Instagram
app: Settings, Account status). On an account without them Instagram takes a
"trial" and puts it on the main feed with no error. So before any trial this
checks your follower count and HOLDS every trial while you are under 200. After
each trial it asks Instagram where the reel landed. One that landed on the main
feed stops every trial that has not gone out. Nothing reaches your main feed by
accident twice.

Each reel goes to Instagram as a link to its video in this repo's Releases, the
same way Ryan's own trials go out. Instagram fetches the video from that link,
so this repo has to stay public. (Before 10/27/26 the poster sent the file
another way, and Instagram put those trials on the main feed.)

A post more than a day past its time is skipped, never sent. A hold or a paused
timer can leave posts waiting for days, and old news all at once looks like
spam.

Needs FB_PAGE_TOKEN and IG_USER_ID (repository secrets the setup stored) and
GH_TOKEN (GitHub provides it to the timer).
"""
import calendar
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
HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE = os.path.join(HERE, "reels.json")
TOKEN = (os.environ.get("FB_PAGE_TOKEN") or "").strip()
IG_USER = (os.environ.get("IG_USER_ID") or "").strip()
REPO = os.environ.get("GITHUB_REPOSITORY", "")
TRIAL_FOLLOWERS = 200       # Instagram's floor for trial reels on a public professional account
LATE_HOURS = 24             # a post this far past its time is skipped, never sent
TURN_ON = "python3 scripts/schedule_reel.py --trials-on --yes"


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


def release_url(tag, asset):
    """The public link to a video in this repo's Releases. Instagram fetches the reel from it.

    GitHub answers it with a redirect to a short-lived signed link, and
    Instagram follows that (tested on Ryan's account 10/27/26). Checked first
    so a missing video or a private repo gives a plain reason."""
    q = urllib.parse.quote
    url = f"https://github.com/{REPO}/releases/download/{q(tag)}/{q(asset)}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=60) as r:
            if r.status == 200:
                return url
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception as e:
        raise RuntimeError(f"could not reach the video link on GitHub: {e}")
    raise RuntimeError(f"the video link on GitHub does not open (HTTP {code}). The posts repo must stay public "
                       "and the video's release must still be there.")


def upload_reel(video_url, caption, trial):
    """Make the reel from its video link, wait for Instagram to process it, publish it. Returns the media id.

    By video_url, the way Meta documents trial_params and the way Ryan's own
    poster gets real trials. The old resumable upload sent trials to the main
    feed (Kevin, 10/8/26)."""
    params = {"media_type": "REELS", "video_url": video_url, "caption": caption}
    if trial:
        # MANUAL: Instagram never moves a trial to the main feed by itself. The
        # winner goes to the main feed as its own separate upload instead.
        params["trial_params"] = json.dumps({"graduation_strategy": "MANUAL"})
    else:
        params["share_to_feed"] = "true"
    c = api(f"{IG_USER}/media", params, "POST")
    if not c.get("id"):
        raise RuntimeError(f"Instagram would not start the upload: {err(c)}")
    for _ in range(60):                     # up to ten minutes for Instagram to fetch and process it
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


def trial_block(queue):
    """Why trials must wait, or None when they may go out."""
    open_stories = [s for s in queue if not s.get("cancelled")]
    for s in open_stories:
        for v in s["versions"]:
            if v.get("trial_missed") and not v.get("trial_missed_ack"):
                return (f"Instagram put {s['slug']} {v['v']} on your main feed instead of trials. Every trial is on "
                        "hold. Check Settings, Account status in Instagram. Then start a new reel and look for the "
                        f"Trial switch on the last screen before Share. If you see it, ask Claude to run {TURN_ON}")
    if any(s.get("trials_ok") for s in open_stories):
        return None                         # the agent confirmed the Trial switch is in their Instagram app
    me = api(IG_USER, {"fields": "followers_count"})
    n = me.get("followers_count")
    if isinstance(n, int) and n < TRIAL_FOLLOWERS:
        return (f"You have {n:,} followers. Instagram gives trial reels to professional accounts with "
                f"{TRIAL_FOLLOWERS:,} or more. Below that it posts a trial straight to your main feed. Every trial "
                f"is on hold. If your Instagram app shows the Trial switch anyway, ask Claude to run {TURN_ON}")
    return None


def late_cut():
    """The UTC time a day before now. A post due before it is too late to send."""
    t = calendar.timegm(time.strptime(now(), "%Y-%m-%dT%H:%M:%S")) - LATE_HOURS * 3600
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t))


def skip_late(queue):
    """Skip every post more than a day past its time. True when anything changed.

    A hold, a paused timer or a GitHub outage can leave posts waiting for days.
    Old news all at once looks like spam, and a "trial" from before a hold may
    already sit on the main feed."""
    cut, changed = late_cut(), False
    late = "skipped: its time passed more than a day ago (posting was off or on hold)"
    for s in queue:
        if s.get("cancelled"):
            continue
        for v in s["versions"]:
            if not v.get("media_id") and not v.get("failed") and v["trial_at_utc"] < cut:
                v.update(failed=True, late=True, last_error=late)
                changed = True
            if s.get("pick") == "day" and v.get("picked") and not v.get("main_done") and v["main_at_utc"] < cut:
                v.update(main_done=True, late=True, result=late.replace("its time", "its main-feed time"))
                cleanup_version(v)
                changed = True
        m = s.setdefault("main", {})
        if s.get("pick") != "day" and not m.get("done") and s["main_at_utc"] < cut:
            m.update(done=True, late=True, result=late.replace("its time", "its main-feed time"))
            cleanup(s)
            changed = True
    return changed


def set_holds(queue, reason):
    """Mark (or clear) the hold on every trial that has not gone out. True when anything changed."""
    changed = False
    for s in queue:
        if s.get("cancelled"):
            continue
        for v in s["versions"]:
            if v.get("media_id") or v.get("failed"):
                continue
            if reason and v.get("hold") != reason:
                v["hold"] = reason
                changed = True
            elif not reason and "hold" in v:
                del v["hold"]
                changed = True
    return changed


def landed_as_trial(media_id):
    """True = a trial, False = it is on the main feed, None = Instagram would not say."""
    for _ in range(3):
        m = api(media_id, {"fields": "is_shared_to_feed"})
        if "is_shared_to_feed" in m:
            return m["is_shared_to_feed"] is False
        time.sleep(5)
    return None


def score(media_id):
    f = api(media_id, {"fields": "like_count,comments_count"})
    views = None
    ins = api(f"{media_id}/insights", {"metric": "views"})
    for m in ins.get("data", []):
        if m.get("name") == "views":
            views = (m.get("values") or [{}])[0].get("value", m.get("total_value", {}).get("value"))
    return {"comments": f.get("comments_count", 0) or 0, "likes": f.get("like_count", 0) or 0, "views": views}


def keyword_of(story, ver):
    """The keyword this version said on camera. Stories scheduled before 10/23/26 have one, on the story."""
    return ver["keyword"] if "keyword" in ver else story.get("keyword")


def keyword_count(media_id, keyword):
    """How many comments ask for the offer by its keyword, or None when Instagram will not say."""
    if not keyword:
        return None
    out = api(f"{media_id}/comments", {"fields": "text", "limit": "100"})
    if "data" not in out:
        return None
    return sum(1 for c in out["data"] if re.search(rf"\b{re.escape(keyword)}\b", c.get("text", ""), re.I))


def age_hours(v):
    try:
        posted = time.mktime(time.strptime(v["posted_at_utc"], "%Y-%m-%dT%H:%M:%S"))
        return max(0.0, (time.mktime(time.gmtime()) - posted) / 3600)
    except (KeyError, ValueError):
        return 24.0


def snapshot_day1(queue):
    """Read every trial once it is about a day old, so the winner is judged on its first day. True when anything changed."""
    changed = False
    for s in queue:
        if s.get("cancelled"):
            continue
        for v in s["versions"]:
            if not v.get("media_id") or v.get("day1") or v.get("trial_missed"):
                continue
            age = age_hours(v)
            if 24 <= age < 80:
                v["day1"] = {**score(v["media_id"]), "kw": keyword_count(v["media_id"], keyword_of(s, v)),
                             "age_h": round(age, 1)}
                changed = True
    return changed


def day1(story, v):
    """A trial's numbers at about one day old (scaled to 24 hours), read now if it is younger than that."""
    snap = v.get("day1")
    if not snap:
        snap = {**(v.get("score") or score(v["media_id"])), "age_h": round(age_hours(v), 1)}
        if "kw" not in snap:
            snap["kw"] = keyword_count(v["media_id"], keyword_of(story, v))
    k = 24 / max(snap.get("age_h") or 24, 6)
    if snap.get("kw") is not None:
        asks = snap["kw"]
    elif not keyword_of(story, v) and any(keyword_of(story, x) for x in story.get("versions", [])):
        asks = 0                            # a follow ending asks for no keyword; its comments are not asks
    else:
        asks = snap.get("comments", 0)      # no keyword anywhere, or Instagram would not list the comments
    return {"asks": asks * min(k, 1.5), "views": (snap.get("views") or 0) * k, "likes": snap.get("likes", 0) * k,
            "has_views": snap.get("views") is not None}


def best_version(story, posted):
    """Ryan's rule (10/22/26): keyword comments decide by a lead of 2+, then views by 15%+, then the most of everything."""
    nums = {v["v"]: day1(story, v) for v in posted}
    by_asks = sorted(posted, key=lambda v: -nums[v["v"]]["asks"])
    if len(posted) > 1 and nums[by_asks[0]["v"]]["asks"] - nums[by_asks[1]["v"]]["asks"] >= 2:
        return by_asks[0], "keyword comments", nums
    if len(posted) > 1 and all(nums[v["v"]]["has_views"] for v in posted):
        by_views = sorted(posted, key=lambda v: -nums[v["v"]]["views"])
        if nums[by_views[0]["v"]]["views"] / max(1.0, nums[by_views[1]["v"]]["views"]) >= 1.15:
            return by_views[0], "views", nums
    best = max(posted, key=lambda v: (rank_key(nums[v["v"]]), -ord(v["v"][0])))
    return best, "too close; the most of everything", nums


def cleanup(story):
    for v in story["versions"]:
        gh("release", "delete", v["tag"], "-R", REPO, "-y", "--cleanup-tag")
    story["files_deleted"] = True


def cleanup_version(v):
    gh("release", "delete", v["tag"], "-R", REPO, "-y", "--cleanup-tag")
    v["file_deleted"] = True


def rank_key(n):
    return (round(n["asks"], 2), round(n["views"], 2), round(n["likes"], 2))


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


def next_job(queue, tried=(), trials=True):
    t = now()
    stories = sorted(queue, key=lambda s: s["versions"][0]["trial_at_utc"])
    for s in stories:
        if s.get("cancelled") or not trials:
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
        ver["media_id"] = upload_reel(release_url(ver["tag"], ver["asset"]), ver["caption"], trial=True)
        ver["posted_at_utc"] = now()
        print(f"  posted {ver['media_id']}")
        ver["landed_as_trial"] = landed_as_trial(ver["media_id"])
        if ver["landed_as_trial"] is False:
            missed_trial(story, ver)
    except Exception as e:
        ver["tries"] = ver.get("tries", 0) + 1
        ver["last_error"] = str(e)[:400]
        if ver["tries"] >= 3 or re.search(r"trial", str(e), re.I):
            ver["failed"] = True
        print(f"  FAILED ({ver['tries']} of 3): {e}")


def missed_trial(story, ver):
    """Instagram posted a trial to the main feed. That post IS this story's main post; never post it again."""
    print("  NOT A TRIAL: Instagram put it on the main feed. Holding every other trial.")
    ver["trial_missed"] = True
    note = "Instagram posted this trial straight to the main feed (the account has no trial reels)"
    if story.get("pick") == "day":
        ver.update(judged=True, picked=True, main_done=True, main_media_id=ver["media_id"], result=note)
    else:
        story.setdefault("main", {}).update(done=True, winner=ver["v"], media_id=ver["media_id"], result=note)
        for v in story["versions"]:
            if not v.get("media_id") and not v.get("failed"):
                v.update(failed=True, last_error=f"skipped: version {ver['v']} is already on the main feed")
        cleanup(story)


def post_main(label, ver, live):
    """Upload a winning version again as its own main-feed post. Returns (media id, error)."""
    sig = signature(ver["caption"])
    hit = next((x for x in live if signature(x.get("caption", "")) == sig and x.get("is_shared_to_feed")), None)
    if hit:
        return hit["id"], None
    print(f"{label}: posting to the main feed")
    try:
        return upload_reel(release_url(ver["tag"], ver["asset"]), ver["caption"], trial=False), None
    except Exception as e:
        print(f"  FAILED: {e}")
        return None, str(e)[:400]


def judge_day(queue, key, items):
    """Rank one day's stories and give their best versions that day's main-feed times, one per story."""
    slots = sorted(items[0][1]["pick_at_utc"])
    posted = [(s, v) for s, v in items if v.get("media_id")]
    for _, v in posted:
        v["score"] = score(v["media_id"])
    nums = {(s["slug"], v["v"]): day1(s, v) for s, v in posted}
    # a story already headed to (or on) the main feed never gets a second spot
    used = {s["slug"] for s in queue if s.get("pick") == "day" and any(v.get("picked") for v in s["versions"])}
    # a story with a trial still to go out cannot reach the main feed yet (a trial after its main is flagged as spam)
    waiting = {s["slug"] for s in queue if not s.get("cancelled")
               and any(not v.get("media_id") and not v.get("failed") for v in s["versions"])}
    picks = []
    for s, v in sorted(posted, key=lambda sv: rank_key(nums[(sv[0]["slug"], sv[1]["v"])]), reverse=True):
        if len(picks) == len(slots):
            break
        if s["slug"] in used or s["slug"] in waiting or flopped(v):
            continue
        picks.append((s, v))
        used.add(s["slug"])
    for i, (s, v) in enumerate(picks):
        v.update(picked=True, main_at_utc=slots[i])
    for s, v in items:
        v["judged"] = True
        if not v.get("picked"):
            v["result"] = ("never went out" if not v.get("media_id") else
                           "not picked: its story still had a trial to go out" if s["slug"] in waiting else
                           "not picked for the main feed")
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
    block = trial_block(queue)
    if set_holds(queue, block) | snapshot_day1(queue) | skip_late(queue):
        json.dump(queue, open(QUEUE, "w"), indent=1)
    if block:
        print(f"TRIALS ON HOLD: {block}")
    # everything due, one at a time, for up to ~15 minutes (the timer gives each run 30)
    while time.time() - began < 15 * 60:
        if skip_late(queue):                # a day's judging can pick a main whose time is long gone
            json.dump(queue, open(QUEUE, "w"), indent=1)
        kind, story, ver = next_job(queue, tried, trials=not block)
        if not kind:
            break
        if kind == "trial":
            tried.add(f"t:{story['slug']}:{ver['v']}")
            post_trial(story, ver, live_posts())
            if ver.get("trial_missed"):
                block = trial_block(queue)
                set_holds(queue, block)
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
                if all(flopped(v) for v in posted):
                    m.update(done=True, result="no trial got a like or a comment, so it stays off the main feed")
                else:
                    best, why, nums = best_version(story, posted)
                    m["why"] = why
                    print(f"{story['slug']}: version {best['v']} won on {why}: "
                          + "; ".join(f"{k} {n['asks']:.0f} asks, {n['views']:.0f} views at a day old" for k, n in nums.items()))
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

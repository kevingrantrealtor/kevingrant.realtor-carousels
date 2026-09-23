# Carousels for Kevin Grant

This repository holds Instagram carousels waiting to go out, and the timer that
posts them.

- `slides/` the images, served publicly so Instagram can fetch them
- `queue.json` what is scheduled and when, in UTC
- `publish_queue.py` the publisher, run every ten minutes by GitHub Actions

Everything here is created by the pax-carousels skill. You do not need to edit
it by hand. To see what is scheduled, ask Claude: **what is queued?**

The Instagram token is an encrypted repository secret, not a file in here.

Slides are public because Instagram has to be able to read them. Never commit a
client's private information, a full street address tied to a client, or an
individual home's sale price.

# Shark Fair Play v87 — website API

The website is static Cloudflare Pages. It cannot execute the original Python v87 review. This repository contains `fairplay_web_api.py`, a small HTTP adapter which calls `fairplay_method3.review` without changing the review algorithm or using a chess engine.

## Deploy

Deploy this repository to a persistent Python hosting service (for example, a Render Web Service or equivalent) with:

- Build: `pip install -r requirements-web.txt`
- Start: `uvicorn fairplay_web_api:app --host 0.0.0.0 --port $PORT`
- Health check: `/health`
- At least two Python worker threads; one API process is recommended for the in-memory job queue.

Do **not** run the Discord bot as part of the web service. The web adapter imports the existing Method 3 modules directly.

After the HTTPS API is reachable, edit the website repository `Yupfih83/script.js`: set `API_URL` to the exact API origin. The static website will then start a job and poll for completion.

## Constraints

- Review calculations are exactly those of the imported original v87 module.
- The frontend's nine panels are visualizations of available diagnostic values, **not** a reproduction of nine Discord chart images.
- A restart clears in-memory jobs. Only two simultaneous reviews are accepted.
- The username is transmitted to Chess.com and the backend. HTTP providers may keep access logs; review your hosting provider's privacy settings.
- Avoid putting Discord tokens or other secrets in website files. The web service needs no Discord token.
- Public traffic can exhaust service capacity; add hosting-layer rate limits and abuse protection before heavy public use.

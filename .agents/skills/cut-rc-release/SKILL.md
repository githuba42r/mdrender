---
name: cut-rc-release
description: Cut a local RC (release-candidate) docker image of the MDRender push server and push it to GHCR. Use when: the user asks to "cut an RC", build/release a release-candidate push-server image, or publish a candidate image before a stable release. Image-only - no git tag, no GitHub release, no version.properties churn.
---

# cut-rc-release

Cut a local RC image of the push server and push it to GHCR. This bypasses the
Actions pipeline entirely; the pipeline is only used for stable releases
(a push to master runs release.yml, which cuts a full release - never push
master merely to ship an RC).

## Prerequisites

- Repo: `/home/philg/src/AndroidStudioProjects/MDRender`; image context: `./server`.
- GHCR auth is stored in `~/.docker/config.json` (user `githuba42r`). To log
  in or refresh the (classic PAT, `write:packages`) credential:
  ```
  source ~/.bashrc-envvars && printf '%s' "$GH_PAT" | docker login ghcr.io -u githuba42r --password-stdin
  ```
- Pushing requires the local docker daemon running.

## Steps

1. Run the test suite (the RC gate; CI is bypassed, so the local run is the
   only gate the image gets):
   ```
   cd server && .venv/bin/python -m pytest
   ```
   Stop if anything fails - never build an RC from a red tree.
2. Determine the RC tag. Base version = the values in `version.properties`
   (`VERSION_MAJOR.MINOR.PATCH`). The RC number is the next free one among
   the existing GHCR tags `<base>-rc.*`:
   ```
   curl -sS "https://ghcr.io/token?scope=repository:githuba42r/mdrender-server:pull&service=ghcr.io" \
     | python3 -c "import json,sys;tok=json.load(sys.stdin)['token'];import urllib.request;\
r=urllib.request.Request('https://ghcr.io/v2/githuba42r/mdrender-server/tags/list',headers={'Authorization':'Bearer '+tok});\
print(json.load(urllib.request.urlopen(r))['tags'])"
   ```
   The base version is *not* bumped for an RC (an RC of the current base is
   still `<base>-rc.N`); rc.N just counts candidates.
3. Build and tag (both the fixed tag and the moving `rc` tag):
   ```
   docker build -t ghcr.io/githuba42r/mdrender-server:<base>-rc.<N> \
                -t ghcr.io/githuba42r/mdrender-server:rc ./server
   ```
4. Push both tags:
   ```
   docker push ghcr.io/githuba42r/mdrender-server:<base>-rc.<N>
   docker push ghcr.io/githuba42r/mdrender-server:rc
   ```
   On `denied: denied` the stored token expired/lost scope - refresh it as in
   Prerequisites and retry.
5. Record the digest that was pushed, then hand off to the
   `deploy-mdrender-rc` skill to get it onto `oracle-cloud`.

## Rules

- Do **not** run `./release.sh` or `scripts/semver-bump.sh` as part of this -
  an RC touches only the GHCR image.
- Do **git commit** the changes being RC'd (build from a clean tree), but do
  **not** `git push` - a push to master triggers the stable release pipeline.
- Never push `latest` from a local build; `latest` belongs to the CI-cut
  stable releases.

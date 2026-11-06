# Signing key of the catalog and the patch feed

RunR trusts an OCI runtime catalog or a patch feed only when its signature verifies with the
public key configured on the runtime source. This repository signs both with one
[cosign](https://docs.sigstore.dev/cosign/) key pair: the private key is a GitHub Actions secret,
the public key is [`cosign.pub`](../cosign.pub) in the repository root, which you give RunR.

Without the secret `COSIGN_PRIVATE_KEY` the publish workflow skips the catalog and the feed; the
images are still published and the repository still works as a **git** source (a git source needs
no key, except for its patch feed).

## Generate the key pair

On your machine, with cosign 2.x installed:

```bash
cd /tmp                                  # anywhere outside the repository
export COSIGN_PASSWORD="$(openssl rand -base64 30)"
echo "$COSIGN_PASSWORD"                  # keep it: it is the second secret
cosign generate-key-pair                 # writes cosign.key (private) and cosign.pub
```

Never commit `cosign.key` (`.gitignore` lists it).

## Store it

1. Repository → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**:
   - `COSIGN_PRIVATE_KEY`: the whole content of `cosign.key`, including the
     `-----BEGIN ENCRYPTED SIGSTORE PRIVATE KEY-----` lines
   - `COSIGN_PASSWORD`: the password from above

   or with the GitHub CLI:

   ```bash
   gh secret set COSIGN_PRIVATE_KEY < cosign.key
   gh secret set COSIGN_PASSWORD --body "$COSIGN_PASSWORD"
   ```

2. Replace [`cosign.pub`](../cosign.pub) with the generated `cosign.pub` and commit it.
3. Keep `cosign.key` and the password in your password manager, then delete the local copy.

The next push to `main` publishes and signs
`ghcr.io/<owner>/runr/sample-executor-catalog:stable` and
`ghcr.io/<owner>/runr/sample-executor-patches:feed`.

## How the signature looks

`scripts/push-signed.sh` signs the manifest digest of the artifact with
`cosign sign --key … --tlog-upload=false`: a classic `sha256-<digest>.sig` signature next to the
artifact in the same repository, no transparency log entry (RunR does not look one up). It then
verifies it with `cosign.pub` before it moves the tag, so the tag only ever names a signed document.
Check a published one yourself:

```bash
cosign verify --key cosign.pub --insecure-ignore-tlog=true \
  ghcr.io/<owner>/runr/sample-executor-catalog:stable
```

## Rotate the key

1. Generate a new pair and set both secrets.
2. Commit the new `cosign.pub`; the next run signs with the new key.
3. Change the public key on every RunR source that reads this catalog or feed (Console →
   Build & Run → Runtimes → Sources → the source → Edit, or `PUT /api/runtime-sources/{id}` with `public_key`).
   Until then those sources report `invalid_signature` and keep the runtimes of their last good
   read.

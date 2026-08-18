#!/bin/bash
#
# deploy-local.sh — push the host repo's committed state into the running local
# demo stack.
#
# ---------------------------------------------------------------------------
# USAGE
# ---------------------------------------------------------------------------
#   docker/deploy-local.sh
#       Deploy the host's current branch.
#
#   docker/deploy-local.sh --force
#       Deploy even though the HOST working tree has uncommitted tracked
#       changes. Those changes are still NOT deployed — only committed state
#       is. This flag only silences the refusal.
#
#   docker/deploy-local.sh --discard-container-changes
#       Deploy even though the CONTAINER clone has modifications to tracked
#       files. They are backed up to a patch file on the host first, then
#       overwritten by the reset. Untracked files never block the deploy.
#
#   docker/deploy-local.sh --help
#
# Environment overrides: CRM_CONTAINER (default crm-local-frappe-1),
# CRM_SITE (default crm.localhost).
#
# ---------------------------------------------------------------------------
# WHY THIS EXISTS
# ---------------------------------------------------------------------------
# The container does NOT serve the bind-mounted host repo. `bench get-app
# /workspace/app` (see init-local.sh) made a SEPARATE clone at
# /home/frappe/frappe-bench/apps/crm, with a git remote called `upstream`
# pointing back at the bind mount /workspace/app. That clone therefore drifts
# from the host checkout: a host commit changes nothing that the demo serves
# until someone fetches and resets the container clone.
#
# This drift caused a real incident — the demo box kept serving an older CRM
# while the host repo looked correct, and the difference was invisible from the
# host side. Use this script instead of ad-hoc `docker exec` commands.
#
# ---------------------------------------------------------------------------
# WHAT THIS CHANGES (read before running)
# ---------------------------------------------------------------------------
#   * The container clone is reset --hard. Modifications to TRACKED files there
#     are backed up to a patch file and then OVERWRITTEN; the script refuses
#     without --discard-container-changes. Untracked files in the clone are
#     left alone by checkout -f and reset --hard, so they are reported but not
#     backed up and not gated on.
#   * `bench migrate` MUTATES THE SITE DATABASE: it runs patches and applies
#     schema changes, and it is not reversible. A database backup is taken
#     immediately before it, and its path is printed.
#   * No tracked file in the host repository is modified, and no host commit,
#     branch or index entry is touched. The ONE thing the script writes to the
#     host is a rescue patch at the repo root, `.deploy-backup-<epoch>.patch`,
#     and only when the container clone was dirty. That name is gitignored.
#
# ---------------------------------------------------------------------------
# VERIFIED STATE OF THE RUNNING STACK (2026-08-18, read-only commands)
# ---------------------------------------------------------------------------
#   $ docker exec crm-local-frappe-1 \
#       git -C /home/frappe/frappe-bench/apps/crm remote -v
#   upstream	/workspace/app (fetch)
#   upstream	/workspace/app (push)
#
#   $ docker exec crm-local-frappe-1 git -C /home/frappe/frappe-bench/apps/crm \
#       config --get-all remote.upstream.fetch
#   +refs/heads/main:refs/remotes/upstream/main
#
#   $ docker exec crm-local-frappe-1 \
#       git -C /home/frappe/frappe-bench/apps/crm branch -r
#     upstream/HEAD -> upstream/main
#     upstream/main
#
# That refspec is SINGLE-BRANCH: a bare `git fetch upstream` only ever updates
# upstream/main, so `upstream/<any-other-branch>` never comes into existence and
# the checkout below would die with "Needed a single revision". The fetch is
# therefore given an explicit refspec for the branch being deployed.

set -Eeuo pipefail

CONTAINER="${CRM_CONTAINER:-crm-local-frappe-1}"
APP_DIR="/home/frappe/frappe-bench/apps/crm"
BENCH_DIR="/home/frappe/frappe-bench"
SITE="${CRM_SITE:-crm.localhost}"
REMOTE="upstream"

# Written out literally rather than sliced out of the header with sed: a line
# range or a pattern range silently prints the wrong block the moment the header
# is edited, and that is exactly what --help must never do.
usage() {
    cat <<'USAGE'
deploy-local.sh — push the host repo's committed state into the local demo stack.

Usage:
  docker/deploy-local.sh
      Deploy the host's current branch.

  docker/deploy-local.sh --force
      Deploy even though the HOST working tree has uncommitted tracked changes.
      Those changes are still NOT deployed; only committed state is.

  docker/deploy-local.sh --discard-container-changes
      Deploy even though the CONTAINER clone has modifications to tracked
      files. They are backed up to a patch file on the host first, then
      overwritten by the reset. Untracked files in the clone survive the reset
      and never block the deploy.

  docker/deploy-local.sh --help

Environment overrides:
  CRM_CONTAINER   container name  (default: crm-local-frappe-1)
  CRM_SITE        bench site name (default: crm.localhost)

This script MUTATES the site database via `bench migrate`. A database backup is
taken immediately before that step and its path is printed.
USAGE
}

FORCE=0
DISCARD_CONTAINER=0
for arg in "$@"; do
    case "$arg" in
        --force) FORCE=1 ;;
        --discard-container-changes) DISCARD_CONTAINER=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown argument: $arg" >&2; echo "Try --help." >&2; exit 2 ;;
    esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ---------------------------------------------------------------------------
# Failure reporting. Every phase leaves the stack in a different state, and
# guessing which one is why a half-finished deploy is hard to clean up.
# ---------------------------------------------------------------------------
PHASE="startup"
DB_BACKUP_NOTE="no database backup was taken yet"
CLONE_BACKUP_NOTE=""

# The state report is a function so that an explicit `exit` mid-flight gives the
# operator the same guidance as a trapped failure. An `exit` does NOT fire the
# ERR trap, and losing the report in the backup phase — the one phase where the
# recovery steps actually matter — is how a half-deployed demo stays broken.
report_phase_state() {
    case "$PHASE" in
        startup|preflight|container-dirty-check)
            echo "Nothing was changed. The container still serves what it served before." >&2
            ;;
        fetch)
            echo "The container clone was NOT reset. Nothing was changed." >&2
            ;;
        reset)
            echo "The container clone may be PARTIALLY reset: new code, old assets," >&2
            echo "old schema. Do not use the demo until this script completes." >&2
            ;;
        backup)
            echo "The database backup failed, so migrate was NOT attempted." >&2
            echo "The container now has NEW code with the OLD schema and OLD assets." >&2
            echo "Fix the backup problem and re-run this script." >&2
            ;;
        migrate)
            echo "Migrate failed. The schema may be PARTIALLY migrated." >&2
            echo "Restore the pre-migrate database backup: $DB_BACKUP_NOTE" >&2
            echo "  docker exec -w $BENCH_DIR $CONTAINER \\" >&2
            echo "    bench --site $SITE --force restore <backup-path>" >&2
            ;;
        yarn-install|build)
            echo "Migrate is DONE but the asset build FAILED: the container now has" >&2
            echo "new code and a new schema with OLD assets. The UI will be wrong." >&2
            echo "Fix the build and re-run this script; the migrate step is idempotent." >&2
            echo "Pre-migrate database backup: $DB_BACKUP_NOTE" >&2
            ;;
        clear-cache|restart)
            echo "Code, schema and assets are all deployed. Only the final cache clear" >&2
            echo "or restart failed. Re-run this script, or restart the container by hand:" >&2
            echo "  docker restart $CONTAINER" >&2
            ;;
    esac
    if [ -n "$CLONE_BACKUP_NOTE" ]; then
        echo >&2
        echo "Container-clone work was backed up to: $CLONE_BACKUP_NOTE" >&2
    fi
}

on_error() {
    local status=$?
    echo >&2
    echo "==========================================================" >&2
    echo "DEPLOY FAILED in phase: $PHASE (exit status $status)" >&2
    echo "==========================================================" >&2
    report_phase_state
    exit "$status"
}

# Deliberate mid-flight abort: report the same state the trap would.
fail() {
    echo "$1" >&2
    echo >&2
    echo "==========================================================" >&2
    echo "DEPLOY ABORTED in phase: $PHASE" >&2
    echo "==========================================================" >&2
    report_phase_state
    exit "${2:-1}"
}

trap on_error ERR

# ---------------------------------------------------------------------------
# (a) Host state
# ---------------------------------------------------------------------------
PHASE="preflight"

BRANCH="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)"
SHA="$(git -C "$REPO_ROOT" rev-parse HEAD)"

echo "Host repo:  $REPO_ROOT"
echo "Host HEAD:  $BRANCH @ $SHA"
echo "Container:  $CONTAINER"
echo "Site:       $SITE"
echo

if [ "$BRANCH" = "HEAD" ]; then
    echo "ERROR: the host repo is in detached HEAD state." >&2
    echo "Check out a branch before you deploy." >&2
    exit 1
fi

# Only tracked files matter: the container clone can never see untracked ones.
if ! git -C "$REPO_ROOT" diff-index --quiet HEAD --; then
    echo "WARNING: the host working tree has uncommitted changes to tracked files:" >&2
    git -C "$REPO_ROOT" diff-index --name-status HEAD -- >&2
    echo >&2
    echo "The container clones COMMITTED state only, so these changes would NOT" >&2
    echo "be deployed. Commit them first, or re-run with --force." >&2
    if [ "$FORCE" -ne 1 ]; then
        exit 1
    fi
    echo "--force given: continuing with the committed state only." >&2
    echo
fi

if ! docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null | grep -q true; then
    echo "ERROR: container '$CONTAINER' is not running." >&2
    echo "Start the stack first: docker-compose -f docker/docker-compose.local.yml up -d" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# (b) Protect any uncommitted work inside the container clone
# ---------------------------------------------------------------------------
PHASE="container-dirty-check"

echo "==> Container clone status"
docker exec "$CONTAINER" git -C "$APP_DIR" status --short

# Untracked files are reported but NEVER gated on. `git checkout -f` and
# `git reset --hard` do not remove them — only `git clean` would, and this
# script never runs it. Gating on them would make the gate unsatisfiable: the
# clone permanently carries untracked build artefacts and scratch files, so
# every run would refuse and write yet another rescue patch.
CLONE_UNTRACKED="$(docker exec "$CONTAINER" git -C "$APP_DIR" ls-files --others --exclude-standard)"
if [ -n "$CLONE_UNTRACKED" ]; then
    echo
    echo "Untracked files in the container clone (these SURVIVE the reset"
    echo "untouched and are not backed up, because nothing destroys them):"
    printf '%s\n' "$CLONE_UNTRACKED" | sed 's/^/    /'
fi

# Only modifications to TRACKED files are at risk, because only those are what
# checkout -f and reset --hard overwrite.
CLONE_DIRTY="$(docker exec "$CONTAINER" git -C "$APP_DIR" status --porcelain --untracked-files=no)"
if [ -n "$CLONE_DIRTY" ]; then
    # Hotfixes really do get typed straight into the container. The reset below
    # would overwrite them without trace, so take a patch first — onto the bind
    # mount, which is the host repo, so it survives the container.
    BACKUP_NAME=".deploy-backup-$(date +%s).patch"
    HOST_BACKUP="$REPO_ROOT/$BACKUP_NAME"

    echo
    echo "==> Backing up uncommitted container-clone work"
    # `-i` is load bearing: without it docker exec gives the command /dev/null
    # for stdin, `bash -s` reads nothing, and the backup silently never happens
    # immediately before the reset destroys the work it was meant to save.
    docker exec -i "$CONTAINER" bash -s -- "$APP_DIR" "/workspace/app/$BACKUP_NAME" <<'INNER'
set -eu
app_dir="$1"
out="$2"
{
    echo "# deploy-local.sh backup of uncommitted work in the container clone"
    echo "# taken:  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "# repo:   $app_dir"
    echo "# head:   $(git -C "$app_dir" rev-parse HEAD)"
    echo "#"
    echo "# This patch holds the TRACKED modifications only. Untracked files are"
    echo "# listed below for context; they are NOT in this patch and they are NOT"
    echo "# destroyed by the reset, which leaves untracked paths alone."
    git -C "$app_dir" ls-files --others --exclude-standard | sed 's/^/#   /'
    echo "#"
    echo "# ===== staged changes: git diff --staged ====="
    git -C "$app_dir" diff --staged
    echo "# ===== unstaged changes: git diff ====="
    git -C "$app_dir" diff
} > "$out"
INNER

    # Never take the script's word for it. The reset is irreversible, so prove
    # the backup landed on the host before offering to destroy anything.
    if [ ! -s "$HOST_BACKUP" ]; then
        fail "ERROR: the backup file was not written to $HOST_BACKUP.
Refusing to touch the container clone."
    fi

    CLONE_BACKUP_NOTE="$HOST_BACKUP"
    echo
    echo "**********************************************************"
    echo "* BACKUP WRITTEN:"
    echo "*   $HOST_BACKUP"
    echo "* Apply it later with:"
    echo "*   git apply $HOST_BACKUP"
    echo "* It holds the TRACKED modifications listed above. Untracked"
    echo "* files are named in the patch header only, and they survive"
    echo "* the reset untouched."
    echo "**********************************************************"
    echo

    if [ "$DISCARD_CONTAINER" -ne 1 ]; then
        echo "REFUSING to continue: the container clone has modifications to tracked" >&2
        echo "files that the reset would overwrite. They are backed up (path above)." >&2
        echo "Re-run with --discard-container-changes once you have checked it." >&2
        exit 1
    fi
    echo "--discard-container-changes given: the tracked modifications above will"
    echo "be overwritten."
    echo
fi

# ---------------------------------------------------------------------------
# (c) Sync the container clone to the host branch
# ---------------------------------------------------------------------------
PHASE="fetch"

echo "==> Fetching $BRANCH from $REMOTE"
# Explicit refspec: the clone's configured refspec is single-branch (main only),
# so a bare `git fetch upstream` would never create upstream/$BRANCH.
docker exec "$CONTAINER" git -C "$APP_DIR" \
    fetch "$REMOTE" "+refs/heads/$BRANCH:refs/remotes/$REMOTE/$BRANCH"

echo
echo "==> Diff stat: container HEAD vs $REMOTE/$BRANCH"
docker exec "$CONTAINER" git -C "$APP_DIR" \
    diff --stat "HEAD..$REMOTE/$BRANCH" || true

PHASE="reset"
echo
echo "==> Checking out $BRANCH and resetting to $REMOTE/$BRANCH"
# `-B` also covers the first deploy of a branch the clone never saw. `-f`
# discards edits made inside the container, which would otherwise abort the
# checkout and leave the clone half-deployed.
docker exec "$CONTAINER" git -C "$APP_DIR" checkout -f -B "$BRANCH" "$REMOTE/$BRANCH"
docker exec "$CONTAINER" git -C "$APP_DIR" reset --hard "$REMOTE/$BRANCH"
docker exec "$CONTAINER" git -C "$APP_DIR" --no-pager log -1 --oneline

# ---------------------------------------------------------------------------
# (d) Back up the database, then migrate
# ---------------------------------------------------------------------------
PHASE="backup"

echo
echo "==> Database backup (taken before migrate, which is not reversible)"
docker exec -w "$BENCH_DIR" "$CONTAINER" bench --site "$SITE" backup
# `$SITE` is passed as a positional argument, not interpolated into the script
# string: it is user-settable through CRM_SITE, and argv keeps it a value rather
# than shell source. `$1` inside the single-quoted body is the inner shell's.
DB_BACKUP_NOTE="$(docker exec -w "$BENCH_DIR" "$CONTAINER" \
    bash -c 'ls -1t "sites/$1/private/backups/"*-database.sql.gz 2>/dev/null | head -1' \
    _ "$SITE")"
if [ -z "$DB_BACKUP_NOTE" ]; then
    fail "ERROR: the backup command reported success but no backup file was found."
fi
echo "Backup (inside the container): $BENCH_DIR/$DB_BACKUP_NOTE"
DB_BACKUP_NOTE="$BENCH_DIR/$DB_BACKUP_NOTE"

PHASE="migrate"
echo
echo "==> bench migrate"
docker exec -w "$BENCH_DIR" "$CONTAINER" bench --site "$SITE" migrate

# ---------------------------------------------------------------------------
# (e) Install frontend dependencies, then build
# ---------------------------------------------------------------------------
PHASE="yarn-install"

echo
echo "==> yarn install (frontend)"
# `bench build` does not install npm dependencies. A deploy that adds one — this
# repo added idb-keyval as a direct dependency — fails the build without this.
# `--frozen-lockfile` keeps yarn from rewriting yarn.lock inside the clone: that
# would leave the clone dirty, and the container-dirty gate above would then
# refuse the NEXT deploy over a file this script wrote itself. It also fails
# loudly when package.json and yarn.lock disagree, which is the right moment.
docker exec -e NODE_OPTIONS=--max-old-space-size=4096 "$CONTAINER" \
    yarn --cwd "$APP_DIR/frontend" install --frozen-lockfile

PHASE="build"
echo
echo "==> bench build --app crm"
# The default heap is too small for this frontend; the build OOMs without it.
docker exec -w "$BENCH_DIR" -e NODE_OPTIONS=--max-old-space-size=4096 \
    "$CONTAINER" bench build --app crm

PHASE="clear-cache"
echo
echo "==> bench clear-cache"
docker exec -w "$BENCH_DIR" "$CONTAINER" bench --site "$SITE" clear-cache

# ---------------------------------------------------------------------------
# (f) Restart
# ---------------------------------------------------------------------------
PHASE="restart"
echo
echo "==> Restarting $CONTAINER"
# bench serve runs under honcho and does not reliably pick up changed Python or
# rebuilt assets. A container restart is the only dependable reload.
docker restart "$CONTAINER"

trap - ERR
echo
echo "Deployed $BRANCH @ $SHA to $CONTAINER."
echo "Pre-migrate database backup: $DB_BACKUP_NOTE"
if [ -n "$CLONE_BACKUP_NOTE" ]; then
    echo "Discarded container-clone work was saved to: $CLONE_BACKUP_NOTE"
fi
echo "The bench takes a few seconds to come back up on http://localhost:8000"

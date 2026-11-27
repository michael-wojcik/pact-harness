#!/usr/bin/env bash
# publish-npm.sh -- publish the bridge package (@pact-harness/dsh) to npm.
#
# Preconditions, both checked before anything is published:
#   1. npm is logged in as an account that can publish to the package scope.
#   2. The scope org exists and the account belongs to it. Only the account
#      owner can create it, at https://www.npmjs.com/org/create.
#
# The version in adapters/dsh/package.json must not already be on the registry.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PACKAGE="$ROOT/adapters/dsh"
cd "$PACKAGE"

command -v npm >/dev/null 2>&1 || { echo "publish-npm: npm is not installed" >&2; exit 2; }
command -v node >/dev/null 2>&1 || { echo "publish-npm: node is not installed" >&2; exit 2; }

ACCOUNT="$(npm whoami 2>/dev/null || true)"
if [ -z "$ACCOUNT" ]; then
  echo "publish-npm: npm is not logged in; run 'npm login' first" >&2
  exit 2
fi

NAME="$(node -p "require('./package.json').name")"
VERSION="$(node -p "require('./package.json').version")"
SCOPE="${NAME%%/*}"
SCOPE="${SCOPE#@}"

if ! npm org ls "$SCOPE" >/dev/null 2>&1; then
  echo "publish-npm: the @$SCOPE scope is not available to $ACCOUNT." >&2
  echo "  create the org at https://www.npmjs.com/org/create (free for public packages)," >&2
  echo "  then rerun this script." >&2
  exit 2
fi

if npm view "$NAME@$VERSION" version >/dev/null 2>&1; then
  echo "publish-npm: $NAME@$VERSION is already published; bump the version first" >&2
  exit 2
fi

echo "publish-npm: $NAME@$VERSION as $ACCOUNT"
node --test test/
npm publish --access public
echo "publish-npm: published https://www.npmjs.com/package/$NAME/v/$VERSION"

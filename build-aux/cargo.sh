#!/bin/sh
set -e

MANIFEST_PATH="$1"
TARGET_DIR="$2"
OUTPUT="$3"

source "$HOME/.cargo/env" 2>/dev/null || true
cargo build --manifest-path "$MANIFEST_PATH" --target-dir "$TARGET_DIR" --bin grammata
cp "$TARGET_DIR/debug/grammata" "$OUTPUT"

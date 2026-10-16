#!/bin/sh
# Healthy when the leaf exists and has not expired.
set -eu

LEAF="/pki/live/nginx-ui/fullchain.pem"

if [ ! -f "$LEAF" ]; then
  exit 1
fi

openssl x509 -in "$LEAF" -checkend 0 -noout

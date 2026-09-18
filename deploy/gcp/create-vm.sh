#!/usr/bin/env bash
# Create the Always Free e2-micro VM that runs the bot.
#
# Run this in Google Cloud Shell (https://shell.cloud.google.com), which has gcloud installed
# and is already logged in. Pick the project in the Cloud Shell project selector first, or pass
# its ID as the first argument.
#
#   bash create-vm.sh [PROJECT_ID]
#
# Free-tier rules this follows: one non-preemptible e2-micro, in us-west1, us-central1, or
# us-east1, on a standard persistent disk of up to 30 GB.
set -euo pipefail

PROJECT="${1:-$(gcloud config get-value project 2>/dev/null)}"
ZONE="${ZONE:-us-central1-a}"
NAME="${NAME:-gaminator}"

if [[ -z "$PROJECT" ]]; then
  echo "No project selected. Pass a project ID: bash create-vm.sh my-project-id" >&2
  exit 1
fi
case "$ZONE" in
  us-west1-*|us-central1-*|us-east1-*) ;;
  *) echo "Zone $ZONE is outside the free-tier regions (us-west1, us-central1, us-east1)." >&2; exit 1 ;;
esac

echo "Project: $PROJECT   Zone: $ZONE   VM: $NAME"
gcloud services enable compute.googleapis.com --project "$PROJECT"

gcloud compute instances create "$NAME" \
  --project "$PROJECT" \
  --zone "$ZONE" \
  --machine-type e2-micro \
  --provisioning-model STANDARD \
  --image-family debian-12 \
  --image-project debian-cloud \
  --boot-disk-size 30GB \
  --boot-disk-type pd-standard \
  --shielded-secure-boot \
  --labels app=gaminator

cat <<MSG

VM created. Next, open a shell on it and run the setup script:

  gcloud compute ssh $NAME --zone $ZONE --project $PROJECT

then on the VM:

  curl -fsSL https://raw.githubusercontent.com/<you>/gaminator/main/deploy/gcp/setup-vm.sh -o setup-vm.sh
  bash setup-vm.sh https://github.com/<you>/gaminator.git

(The setup script prints what to do after that.)
MSG

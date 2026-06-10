---
id: gcp-deploy
title: Demo SaaS hosting
description: Where the public Demo SaaS runs today (Hetzner Cloud, docker-compose) and why the earlier GCP/Terraform runbook was retired.
sidebar_label: Demo SaaS hosting
sidebar_position: 3
---

# Demo SaaS hosting

The public Demo SaaS runs on a single **Hetzner Cloud ARM server** (CAX31,
8 vCPU / 16 GB) using the same `docker-compose.yml` stack as a self-hosted
production install, fronted by a TLS-terminating reverse proxy on the host.

:::info This page replaced the GCP runbook
An earlier version of this page described a GCP deployment (Cloud Run +
Cloud SQL + Memorystore) driven by a `terraform/` module. That plan was
retired before launch — the worker's long-running scan processes and shared
workspace volume do not fit serverless runtimes, and the projected cost was
several times higher. The Terraform module was never shipped: there is no
`terraform/` directory in this repository.
:::

## What runs where

| Concern | Demo SaaS choice |
| --- | --- |
| Compute | One Hetzner CAX31 (ARM64) VPS |
| Orchestration | `docker-compose.yml` — the same production bundle documented in [Install](docker-compose.md) |
| TLS / ingress | Reverse proxy on the host (ports 80/443) |
| Backups | Daily `scripts/backup.sh` via a systemd timer, shipped off-host |
| Demo data reset | Nightly demo re-seed via a systemd timer |

Because the Demo SaaS is a stock docker-compose install, there is no
separate cloud-specific runbook to follow: the [installation
guide](docker-compose.md), [upgrade guide](upgrade.md), and
[backup/restore guide](../admin-guide/backup-and-restore.md) apply as-is.
A dedicated operator runbook covering the Hetzner-specific pieces
(provisioning, cloud-init, systemd timers, off-host backup) is being
prepared and will be linked here when it lands.

## Demo accounts

Seeded demo credentials and the nightly reset behaviour are documented in
[Live demo](live-demo.md). The demo organization is recreated from scratch
every night — anything you create there is discarded.

## Production deployments

The Demo SaaS exists to showcase the product. For real data, deploy
on-premises with [docker-compose](docker-compose.md) or on Kubernetes with
the [Helm chart](helm.md).

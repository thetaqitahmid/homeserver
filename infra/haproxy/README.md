# HAProxy Configuration

## Rationale

This HAProxy instance serves as the primary entry point for the
homeserver infrastructure. It acts as a unified reverse proxy that
allows services from both Docker and Kubernetes environments to be
exposed behind a single, cohesive frontend. By using HAProxy with
SNI-based routing, the following can be achieved:

- **Centralize TLS traffic routing** across multiple backend
  environments
- **Route traffic dynamically** based on the requested domain to either
  Docker or Kubernetes services
- **Maintain a single point of entry** for external clients while
  distributing load across heterogeneous backends
- **Preserve backend certificate management** by passing TLS through
  without terminating it
- **Note**: TLS termination and certificate management are not handled in this
  setup; SSL/TLS traffic is passed through to backend services

## Overview

HAProxy is used as a reverse proxy and load balancer to route incoming
HTTPS traffic to the appropriate backend services in the homeserver
setup.

This HAProxy configuration implements SNI (Server Name Indication)
based routing to direct traffic to either the Kubernetes cluster or
Docker backend based on the requested domain.

## Global Settings

- **Logging**: Logs are written to syslog at `/dev/log` (local0) and
  localhost (local2)
- **Admin Socket**: Accessible at `/run/haproxy/admin.sock` for
  statistics and administration
- **Max Connections**: 1,000 concurrent connections
- **User/Group**: Runs as `haproxy` user and group

## Default Timeout Settings

- **Connect Timeout**: 5 seconds
- **Client Timeout**: 3600 seconds (1 hour)
- **Server Timeout**: 3600 seconds (1 hour)

## Frontend Configuration

The HAProxy frontend listens on port 443 (HTTPS) and TCP mode is used
for SSL/TLS traffic.

### Connection Rate Limiting

New TCP connections are limited to approximately 100 per 10 seconds per
source IP using a stick table. Connections exceeding the limit are
rejected before TLS inspection. This does not limit HTTP request rates
within established connections. Clients sharing a NAT address or an
upstream proxy share the same limit.

### SNI-Based Routing

Traffic is routed based on the SSL SNI (Server Name Indication)
hostname:

**Kubernetes Backend** (`k8s_backend`):

- Domains ending with `.mydomain.com`

**Docker Backend** (`docker_backend`):

- Domains ending with `.docker.mydomain.com`

## Backend Configuration

### Kubernetes Backend

- **Server**: `k8s-ingress` at `192.168.1.141:443`
- **Mode**: TCP
- **Health Checks**: Enabled (10s interval, 3 failures to mark
  down, 2 successes to mark up)

### Docker Backend

- **Server**: `docker-proxy` at `192.168.1.135:443`
- **Mode**: TCP
- **Health Checks**: Enabled (10s interval, 3 failures to mark
  down, 2 successes to mark up)

## Usage

The SSL hello packet is automatically inspected to determine the SNI
hostname, and the connection is routed to the appropriate backend
service. Connections with SNI outside the configured domain suffixes or
without SNI are rejected. Connections that do not provide a usable TLS
ClientHello within the 5-second inspection window are also rejected.
There is no default backend. The apex `mydomain.com` is not included in
the suffix rules.

## Notes

- TCP mode is used to preserve SSL/TLS encryption end-to-end
- Domain patterns marked with `# example` are placeholders and should
  be customized for the setup
- The TCP routing logs can be monitored via journald for debugging and
  verification purposes. `journalctl -u haproxy -f`

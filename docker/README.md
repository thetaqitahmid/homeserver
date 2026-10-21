# Homeserver Notes

# Immich

The Compose stack is in `immich.yaml`. See [Immich deployment and operations](immich/README.md) for storage paths, setup, validation, CPU compatibility, backups, and public routing requirements.

# List of Service Running on Homeserver

- Plex
- Home Assistant

# List of Basic CLI tools installed on server

    - ca-certificates
    - curl
    - gnupg
    - lsb-release
    - ntp
    - ncdu
    - net-tools
    - apache2-utils
    - apt-transport-https
    - htop

# Firewall Rules (Currently Disabled)

I am using ufw to set different firewall rules. As I go I will update the rules

    ```
    sudo ufw default allow outgoing
    sudo ufw default allow incoming
    sudo ufw allow from 192.168.1.0/24
    sudo ufw allow 443
    sudo ufw allow 80
    sudo ufw enable
    ```

# Hardware Transcoding for Jellyfin

The media stream applications such as Jellyfin and Plex uses transcoding to
convert video format which might be necessary if the end user device does not
support some video formats or resolution. If hardware transcoding is not enabled
Plex/Jellyfin uses software based transcoding which is resource intensive.

Most of the new CPU/GPU support HW based transcoding. For our Ryzen 5 2500U
processor, we have HW transcoding. Here is the process to enable it:

    ```
    sudo apt-get update
    sudo apt-get install vainfo mesa-va-drivers libva2 libva-utils

    # Run the following command to make sure VA API is working properly
    vainfo

    # Add the following to services to the Jellyfin container compose file
    devices:
      - /dev/dri/renderD128:/dev/dri/renderD128  # VA-API device for hardware acceleration
    group_add:
      - video  # Add the container to the 'video' group

    ```

# Traefik Reverse proxy

- Traefik is modern HTTP reverse proxy and load balancerthat can be used to route
  traffic to different internal containers or ports based on subdomain name.

- In addition to that It can also automatically handle SSL certificate genertion
  and renewal for HTTPS automatically handle SSL certificate genertion
  and renewal for HTTPS.

## Configuration

In order to get wildcard certificates from LetsEncrypt, I will be using DNS challange
method. DNS challange method is one of the methods provided by LetsEncrypt to verify
the ownership of the domain by adding specific DNS records.

To do that with cloudflare, I have created a new API token with name _CF_DNS_API_TOKEN_
and saved it as docker secret under ~/docker/secrets directory

```
# To save the appdata for traefik3, created the following folders
mkdir -p ~/docker/appdata/traefik3/acme
mkdir -p ~/docker/appdata/traefik3/rules/udms

# To save teh LetsEncrypt certificate, created the following file
touch acme.json
chmod 600 acme.json  # without 600, Traefik will not start

# To save logs, created following files
touch traefik.log
touch access.log
```

After creating the Docker Compose file, add these TLS options like this:

```
# Under DOCKERDIR/appdata/traefik3/rules/udms/tls-opts.yml
tls:
  options:
    tls-opts:
      minVersion: VersionTLS12
      cipherSuites:
        - TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256
        - TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256
        - TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384
        - TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384
        - TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305
        - TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305
        - TLS_AES_128_GCM_SHA256
        - TLS_AES_256_GCM_SHA384
        - TLS_CHACHA20_POLY1305_SHA256
        - TLS_FALLBACK_SCSV # Client is doing version fallback. See RFC 7507
      curvePreferences:
        - CurveP521
        - CurveP384
      sniStrict: true
```

Add the middleware Basic Auth:

```
# Under DOCKERDIR/appdata/traefik3/rules/udms/middlewares-basic-auth.yml
http:
  middlewares:
    middlewares-basic-auth:
      basicAuth:
        # users:
        #   - "user:password"
        usersFile: "/run/secrets/basic_auth_credentials"
        realm: "Traefik 3 Basic Auth"

```

Add middleware rate limited to prevent DDoS attack

```
# Under DOCKERDIR/appdata/traefik3/rules/udms/middlewares-rate-limit.yaml
http:
  middlewares:
    middlewares-rate-limit:
      rateLimit:
        average: 100
        burst: 50
```

Add secure headers middleware

```
# Under DOCKERDIR/appdata/traefik3/rules/udms/middlewares-secure-headers.yaml
http:
  middlewares:
    middlewares-secure-headers:
      headers:
        accessControlAllowMethods:
          - GET
          - OPTIONS
          - PUT
        accessControlMaxAge: 100
        hostsProxyHeaders:
          - "X-Forwarded-Host"
        stsSeconds: 63072000
        stsIncludeSubdomains: true
        stsPreload: true
        # forceSTSHeader: true # This is a good thing but it can be tricky. Enable after everything works.
        customFrameOptionsValue: SAMEORIGIN # https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/X-Frame-Options
        contentTypeNosniff: true
        browserXssFilter: true
        referrerPolicy: "same-origin"
        permissionsPolicy: "camera=(), microphone=(), geolocation=(), payment=(), usb=(), vr=()"
        customResponseHeaders:
          X-Robots-Tag: "none,noarchive,nosnippet,notranslate,noimageindex," # disable search engines from indexing home server
          server: "" # hide server info from visitors
```

## Networking

Create a default Bridge network for the Traefik

# Wireguard VPN setup

In order for qbittorrent container to use the wireguard VPN tunnel
wireguard container has been added to the qbittorrent docker compose
file.

- qbittorrent container depends on the wireguard container. If
  wireguard container is down, qbittorrent network will not work.

- Since, qbittorrent is using the wireguard container, port 9500
  has been forwared to the host 9500 port from the wireguard container

- qbittorrent is using wireguard network interface. So, to access
  the qbittorrent GUI, iptables rules had to be setup. Also, when the pc restarts
  the wireguard container IP might change.

  ```
  # Forward traffic coming to port 9500 on the host to port 9500 on the WireGuard container
  sudo iptables -t nat -A PREROUTING -p tcp --dport 9500 -j DNAT --to-destination 172.18.0.6:9500

  # Forward traffic from the WireGuard container back to the host's port 9500
  sudo iptables -t nat -A POSTROUTING -p tcp -d 172.18.0.6 --dport 9500 -j MASQUERADE
  ```

- We can check the host ip geolocation by the following command. In that way
  we can verify VPN is working.

  ```
  docker exec -it qbittorrent curl ipinfo.io

  {
    "ip": "1.2.3.4",
    "hostname": "1.2.3.4.in-addr.arpa",
    "city": "Amsterdam",
    "region": "North Holland",
    "country": "NL",
    "loc": "55.3740,41.8897",
    "org": "Some Company",
    "postal": "1234",
    "timezone": "Europe/Amsterdam",
    "readme": "https://ipinfo.io/missingauth"
  }
  ```

- We can check the wireguard VPN connection status with the following command

  ```
  docker exec -it wireguard wg

  interface: wg0
    public key: <public key>
    private key: (hidden)
    listening port: 56791
    fwmark: 0xca6c

  peer: <public key>
    preshared key: (hidden)
    endpoint: <ip>:51820
    allowed ips: 0.0.0.0/0, ::/0
    latest handshake: 1 minute, 47 seconds ago
    transfer: 12.69 MiB received, 822.64 KiB sent
    persistent keepalive: every 15 seconds
  ```

# FAQ

1. How to get the plex claim?
   -> Go the the url and login: https://www.plex.tv/claim/

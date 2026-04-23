#!/usr/bin/env bash
set -euo pipefail

SERVER_IP="${1:-124.221.83.225}"
CERT_DIR="$(cd "$(dirname "$0")" && pwd)/certs"

mkdir -p "$CERT_DIR"

cat > "$CERT_DIR/openssl-ip.cnf" <<EOF
[req]
default_bits = 2048
prompt = no
default_md = sha256
x509_extensions = v3_req
distinguished_name = dn

[dn]
C = CN
ST = Guangdong
L = Shenzhen
O = Agent Server
OU = Private SSL
CN = ${SERVER_IP}

[v3_req]
subjectAltName = @alt_names
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth

[alt_names]
IP.1 = ${SERVER_IP}
EOF

openssl req -x509 -nodes -days 825 -newkey rsa:2048 \
  -keyout "$CERT_DIR/privkey.pem" \
  -out "$CERT_DIR/fullchain.pem" \
  -config "$CERT_DIR/openssl-ip.cnf"

echo "已生成私有证书："
echo "  证书: $CERT_DIR/fullchain.pem"
echo "  私钥: $CERT_DIR/privkey.pem"
echo "  IP:   $SERVER_IP"

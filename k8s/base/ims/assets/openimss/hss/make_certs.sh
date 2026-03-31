#!/bin/sh

if [ 1 -ne $# ]
then
    echo You must specify output directory : ./make_certs.sh ./freeDiameter

    exit;
fi

TARGET_DIR="$(cd "$1" && pwd)"

WORKDIR="${TMPDIR:-/tmp}/fd-certs"
rm -rf "${WORKDIR}"
mkdir -p "${WORKDIR}/demoCA"
echo 01 > "${WORKDIR}/demoCA/serial"
touch "${WORKDIR}/demoCA/index.txt.attr"
touch "${WORKDIR}/demoCA/index.txt"
cd "${WORKDIR}" || exit 1

# Generate .rnd if it does not exist
openssl rand -out /root/.rnd -hex 256

# CA self certificate
openssl req -new -batch -x509 -days 3650 -nodes -newkey rsa:1024 -out "${TARGET_DIR}/cacert.pem" -keyout "${WORKDIR}/cakey.pem" -subj /CN=ca.DIAMETER_REALM/C=KO/ST=Seoul/L=Nowon/O=Open5GS/OU=Tests

#hss
openssl genrsa -out "${TARGET_DIR}/hss.key.pem" 1024
openssl req -new -batch -out "${WORKDIR}/hss.csr.pem" -key "${TARGET_DIR}/hss.key.pem" -subj /CN=HSS_DIAMETER_IDENTITY/C=KO/ST=Seoul/L=Nowon/O=Open5GS/OU=Tests
openssl ca -cert "${TARGET_DIR}/cacert.pem" -days 3650 -keyfile "${WORKDIR}/cakey.pem" -in "${WORKDIR}/hss.csr.pem" -out "${TARGET_DIR}/hss.cert.pem" -outdir "${WORKDIR}" -batch

rm -rf "${WORKDIR}"

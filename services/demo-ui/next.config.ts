import type { NextConfig } from "next";

const imsNamespace = process.env.IMS_NAMESPACE ?? "ims-runtime";
const controlPlaneProxyUrl =
  process.env.CONTROL_PLANE_PROXY_URL?.replace(/\/$/, "") ??
  `http://control-plane.${imsNamespace}.svc.cluster.local:8080`;

const nextConfig: NextConfig = {
  env: {
    NEXT_PUBLIC_IMS_PROJECT: process.env.NEXT_PUBLIC_IMS_PROJECT ?? process.env.IMS_PROJECT ?? "ims-demo",
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${controlPlaneProxyUrl}/:path*`,
      },
    ];
  },
};

export default nextConfig;

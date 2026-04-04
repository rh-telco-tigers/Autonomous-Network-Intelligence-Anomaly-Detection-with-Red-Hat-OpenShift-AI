import type { NextConfig } from "next";

const controlPlaneProxyUrl =
  process.env.CONTROL_PLANE_PROXY_URL?.replace(/\/$/, "") ??
  "http://control-plane.ims-demo-lab.svc.cluster.local:8080";

const nextConfig: NextConfig = {
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

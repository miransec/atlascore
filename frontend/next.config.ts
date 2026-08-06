import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // Disable x-powered-by header
  poweredByHeader: false,
  // Strict mode for React
  reactStrictMode: true,
};

export default nextConfig;
